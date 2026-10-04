"""Configuration boundary tests with literal, independently specified expectations."""

import subprocess
import sys
from dataclasses import FrozenInstanceError

import pytest
import yaml

from scrutare.config import ConfigError, PersonaDefinition, VerdictSettings, parse_config

MINIMAL = "models:\n  default:\n    model: test-model\n"
SPEC_EXAMPLE = """
strategy: panel
rounds: {max: 3}
personas: [senior-dev, junior-dev, security, devops]
budgets: {per_persona_tokens: 100000, review_max_tokens: 500000}
models:
  default: {provider: anthropic, base_url: null, model: test-model}
  overrides:
    security: {provider: openai, base_url: https://litellm.internal/v1, model: secure-model}
verdict:
  blocking_categories: [correctness, security, regression]
  advisory_categories: [style, consistency, docs]
github:
  post_mode: review
  human_reviewers: []
  paths: {include: [], exclude: ["docs/**", "*.md"]}
"""


def test_programmatic_verdict_settings_canonicalize_immutable_category_order():
    settings = VerdictSettings(
        ("docs", "style"), ("consistency", "regression", "security", "correctness")
    )
    assert settings.blocking_categories == ("style", "docs")
    assert settings.advisory_categories == ("correctness", "security", "regression", "consistency")
    with pytest.raises(FrozenInstanceError):
        settings.blocking_categories = ()


@pytest.mark.parametrize("field", ["blocking_categories", "advisory_categories"])
@pytest.mark.parametrize("value", [None, [], {}, "secret-category", 1, True])
def test_programmatic_verdict_settings_require_tuples(field, value):
    with pytest.raises(ConfigError, match=f"verdict.{field}: expected a tuple") as error:
        VerdictSettings(**{field: value})
    assert "secret-category" not in str(error.value)


@pytest.mark.parametrize("field", ["blocking_categories", "advisory_categories"])
@pytest.mark.parametrize("value", [None, [], {}, "secret-category", 1, True, " "])
def test_programmatic_verdict_settings_reject_invalid_categories(field, value):
    with pytest.raises(ConfigError, match=rf"verdict.{field}\[0\]") as error:
        VerdictSettings(**{field: (value,)})
    assert "secret-category" not in str(error.value)


@pytest.mark.parametrize("field", ["blocking_categories", "advisory_categories"])
def test_programmatic_verdict_settings_reject_duplicate_categories(field):
    with pytest.raises(ConfigError, match=rf"verdict.{field}\[1\]: duplicate category"):
        VerdictSettings(**{field: ("style", "style")})


@pytest.mark.parametrize("blocking,advisory,diagnostic", [
    (("correctness", "security", "regression", "style"),
     ("style", "consistency", "docs"), "must not overlap"),
    (("correctness", "security"), ("style", "consistency", "docs"),
     "partition missing: regression"),
    ((), (), "partition missing"),
])
def test_programmatic_verdict_settings_require_complete_disjoint_partition(
    blocking, advisory, diagnostic
):
    with pytest.raises(ConfigError, match=diagnostic):
        VerdictSettings(blocking, advisory)


def test_minimal_config_resolves_spec_defaults():
    from scrutare.config import parse_config

    config = parse_config(MINIMAL)
    assert config.strategy == "panel"
    assert config.rounds.max == 3
    assert config.personas == ("senior-dev", "junior-dev", "security", "devops")
    assert config.budgets.per_persona_tokens == 100000
    assert config.budgets.review_max_tokens == 500000
    assert config.models.default.provider == "anthropic"
    assert config.models.default.base_url is None
    assert config.models.default.model == "test-model"
    assert config.models.for_persona("security") == config.models.default
    assert config.verdict.blocking_categories == ("correctness", "security", "regression")
    assert config.verdict.advisory_categories == ("style", "consistency", "docs")
    assert config.github.post_mode == "review"
    assert config.github.human_reviewers == ()
    assert config.github.paths.include == ()
    assert config.github.paths.exclude == ("docs/**", "*.md")
    with pytest.raises(FrozenInstanceError):
        config.strategy = "debate"


def test_complete_spec_example_resolves_override():
    from scrutare.config import parse_config

    config = parse_config(SPEC_EXAMPLE.encode())
    rail = config.models.for_persona("security")
    assert (rail.provider, rail.base_url, rail.model) == (
        "openai",
        "https://litellm.internal/v1",
        "secure-model",
    )
    assert config.models.for_persona("devops").model == "test-model"


def test_load_config_reads_bytes_from_path(tmp_path):
    from scrutare.config import load_config

    path = tmp_path / "scrutare.yaml"
    path.write_bytes(MINIMAL.encode())
    assert load_config(path).models.default.model == "test-model"


def configured(**fields):
    return {"models": {"default": {"model": "test-model"}}, **fields}


@pytest.mark.parametrize(
    "fields,path,accepted",
    [
        ({"models": {"default": {}}}, "models.default.model", "nonempty string"),
        ({"models": {}}, "models.default.model", "nonempty string"),
        ({"strategy": "secret-unknown"}, "strategy", "panel, iterative, debate"),
        ({"strategy": 1}, "strategy", "string"),
        (
            {"models": {"default": {"model": "m", "provider": "secret-provider"}}},
            "models.default.provider",
            "anthropic, openai",
        ),
        ({"github": {"post_mode": "unknown"}}, "github.post_mode", "review, comment"),
        ({"personas": ["unknown"]}, "personas[0]", "senior-dev"),
        ({"personas": []}, "personas", "at least one"),
        ({"personas": ["security", "security"]}, "personas[1]", "duplicate"),
        (
            {"personas": ["security", {"name": "security", "system_prompt": "secret-prompt"}]},
            "personas[1].name",
            "duplicate",
        ),
        ({"personas": [{}]}, "personas[0].name", "nonempty string"),
        ({"personas": [{"name": "custom"}]}, "personas[0].system_prompt", "nonempty string"),
        (
            {"personas": [{"name": "../../secret", "system_prompt": "secret-prompt"}]},
            "personas[0].name",
            "slug",
        ),
        ({"personas": [5]}, "personas[0]", "mapping"),
        (
            {"models": {"default": {"model": "m"}, "overrides": {"absent": {}}}},
            "models.overrides.absent",
            "configured persona",
        ),
        (
            {
                "personas": ["security"],
                "models": {"default": {"model": "m"}, "overrides": {"devops": {}}},
            },
            "models.overrides.devops",
            "configured persona",
        ),
    ],
)
def test_invalid_fields_have_actionable_safe_diagnostics(fields, path, accepted):
    with pytest.raises(ConfigError) as error:
        parse_config(yaml.safe_dump(configured(**fields)))
    message = str(error.value)
    assert path in message
    assert accepted in message
    assert "secret" not in message


@pytest.mark.parametrize(
    "path",
    [
        "rounds",
        "budgets",
        "models",
        "models.default",
        "models.overrides",
        "verdict",
        "github",
        "github.paths",
        "models.overrides.security",
    ],
)
@pytest.mark.parametrize("value", [None, [], "wrong", 3])
def test_sections_require_mappings(path, value):
    raw = configured()
    set_field(raw, path, value)
    with pytest.raises(ConfigError, match=path):
        parse_config(yaml.safe_dump(raw))


def set_field(raw, path, value):
    section = raw
    parts = path.split(".")
    for part in parts[:-1]:
        section = section.setdefault(part, {})
    section[parts[-1]] = value


@pytest.mark.parametrize(
    "path",
    [
        "personas",
        "github.human_reviewers",
        "github.paths.include",
        "github.paths.exclude",
        "verdict.blocking_categories",
        "verdict.advisory_categories",
    ],
)
@pytest.mark.parametrize("value", [None, {}, "wrong", 3])
def test_collection_fields_require_lists(path, value):
    raw = configured()
    set_field(raw, path, value)
    with pytest.raises(ConfigError, match=path):
        parse_config(yaml.safe_dump(raw))


@pytest.mark.parametrize(
    "path",
    [
        "rounds.max",
        "budgets.per_persona_tokens",
        "budgets.review_max_tokens",
    ],
)
@pytest.mark.parametrize("value", [None, True, False, 1.0, 0, -1, "3", [], {}])
def test_bounds_require_positive_integers_excluding_booleans(path, value):
    raw = configured()
    set_field(raw, path, value)
    with pytest.raises(ConfigError, match=path):
        parse_config(yaml.safe_dump(raw))


@pytest.mark.parametrize(
    "path",
    [
        "models.default.model",
        "models.default.provider",
        "models.overrides.security.model",
        "models.overrides.security.provider",
        "github.post_mode",
        "strategy",
    ],
)
@pytest.mark.parametrize("value", [None, "", "  ", True, 5, [], {}])
def test_required_strings_reject_invalid_values(path, value):
    raw = configured()
    set_field(raw, path, value)
    with pytest.raises(ConfigError, match=path):
        parse_config(yaml.safe_dump(raw))


@pytest.mark.parametrize(
    "path",
    [
        "github.human_reviewers",
        "github.paths.include",
        "github.paths.exclude",
    ],
)
@pytest.mark.parametrize("value", [None, True, 5, {}, [], "", "  "])
def test_string_lists_reject_invalid_members(path, value):
    raw = configured()
    set_field(raw, path, [value])
    with pytest.raises(ConfigError, match=path):
        parse_config(yaml.safe_dump(raw))


@pytest.mark.parametrize(
    "path",
    [
        "unexpected",
        "rounds.unexpected",
        "budgets.unexpected",
        "models.unexpected",
        "models.default.unexpected",
        "models.overrides.security.unexpected",
        "verdict.unexpected",
        "github.unexpected",
        "github.paths.unexpected",
    ],
)
def test_unknown_fields_rejected_at_each_section(path):
    raw = configured()
    set_field(raw, path, "secret-value")
    with pytest.raises(ConfigError) as error:
        parse_config(yaml.safe_dump(raw))
    assert path in str(error.value)
    assert "unknown field" in str(error.value)
    assert "secret-value" not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", ""),
        ("name", None),
        ("name", 3),
        ("name", "has spaces"),
        ("system_prompt", ""),
        ("system_prompt", "  "),
        ("system_prompt", 3),
        ("system_prompt", None),
        ("unexpected", "secret-prompt"),
    ],
)
def test_inline_definition_validation(field, value):
    persona = {"name": "custom", "system_prompt": "secret-prompt", field: value}
    with pytest.raises(ConfigError) as error:
        parse_config(yaml.safe_dump(configured(personas=[persona])))
    assert f"personas[0].{field}" in str(error.value)
    assert "secret-prompt" not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "",
        " ",
        "https://",
        "ftp://example.com",
        "example.com",
        "http:///path",
        "http://[",
        "https://host:wrong",
        "https://host:99999",
        "https://bad host",
        4,
        [],
        {},
        True,
    ],
)
@pytest.mark.parametrize("path", ["models.default.base_url", "models.overrides.security.base_url"])
def test_model_urls_require_http_or_https_host(path, url):
    raw = configured()
    set_field(raw, path, url)
    with pytest.raises(ConfigError) as error:
        parse_config(yaml.safe_dump(raw))
    assert path in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "http://localhost:8000/v1",
        "http://127.0.0.1:11434",
        "https://models.internal/v1",
        "http://[::1]:8000/v1",
    ],
)
def test_local_and_https_model_urls_are_allowed(url):
    config = parse_config(yaml.safe_dump({"models": {"default": {"model": "m", "base_url": url}}}))
    assert config.models.default.base_url == url


@pytest.mark.parametrize(
    "blocking,advisory,path",
    [
        (["unknown"], ["style", "consistency", "docs"], "verdict.blocking_categories[0]"),
        (["correctness", "security", "regression"], ["unknown"], "verdict.advisory_categories[0]"),
        (
            ["correctness", "correctness", "security", "regression"],
            ["style", "consistency", "docs"],
            "verdict.blocking_categories[1]",
        ),
        (
            ["correctness", "security", "regression"],
            ["style", "style", "consistency", "docs"],
            "verdict.advisory_categories[1]",
        ),
        (
            ["correctness", "security", "regression", "style"],
            ["style", "consistency", "docs"],
            "verdict",
        ),
        (["correctness", "security"], ["style", "consistency", "docs"], "verdict"),
        ([None], ["style", "consistency", "docs"], "verdict.blocking_categories[0]"),
    ],
)
def test_category_partition_validation(blocking, advisory, path):
    raw = configured(verdict={"blocking_categories": blocking, "advisory_categories": advisory})
    with pytest.raises(ConfigError) as error:
        parse_config(yaml.safe_dump(raw))
    assert path in str(error.value)


def test_category_reclassification_is_normalized_and_roundtrips():
    raw = configured(
        verdict={
            "blocking_categories": ["docs", "security"],
            "advisory_categories": ["style", "regression", "consistency", "correctness"],
        }
    )
    config = parse_config(yaml.safe_dump(raw))
    assert config.verdict.blocking_categories == ("security", "docs")
    assert config.verdict.advisory_categories == (
        "correctness",
        "regression",
        "style",
        "consistency",
    )
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


def test_empty_category_side_is_valid_when_other_side_covers_vocabulary():
    raw = configured(
        verdict={
            "blocking_categories": [],
            "advisory_categories": [
                "docs",
                "style",
                "security",
                "consistency",
                "regression",
                "correctness",
            ],
        }
    )
    config = parse_config(yaml.safe_dump(raw))
    assert config.verdict.blocking_categories == ()
    assert config.verdict.advisory_categories == (
        "correctness",
        "security",
        "regression",
        "style",
        "consistency",
        "docs",
    )


def test_model_override_inherits_omissions_and_null_clears_url():
    raw = configured(
        models={
            "default": {"provider": "openai", "base_url": "http://localhost:8000/v1", "model": "m"},
            "overrides": {
                "security": {"base_url": None},
                "devops": {"model": "other"},
                "junior-dev": {},
                "senior-dev": {"provider": "anthropic"},
            },
        }
    )
    config = parse_config(yaml.safe_dump(raw))
    assert config.models.for_persona("security").base_url is None
    assert config.models.for_persona("security").model == "m"
    assert config.models.for_persona("security").provider == "openai"
    assert config.models.for_persona("devops").base_url == "http://localhost:8000/v1"
    assert config.models.for_persona("devops").model == "other"
    assert config.models.for_persona("junior-dev") == config.models.default
    assert config.models.for_persona("senior-dev").provider == "anthropic"


def test_canonical_settings_include_custom_personas_and_effective_defaults():
    raw = configured(
        strategy="debate",
        rounds={"max": 4},
        budgets={"per_persona_tokens": 1, "review_max_tokens": 2},
        personas=[{"name": "custom", "system_prompt": "Keep this prompt.\n"}],
        models={"default": {"model": "m"}, "overrides": {"custom": {"model": "c"}}},
        github={
            "post_mode": "comment",
            "human_reviewers": ["alice"],
            "paths": {"include": ["src/**"], "exclude": []},
        },
    )
    config = parse_config(yaml.safe_dump(raw))
    assert config.personas == (PersonaDefinition("custom", "Keep this prompt.\n"),)
    canonical = config.to_dict()
    assert canonical == {
        "strategy": "debate",
        "rounds": {"max": 4},
        "personas": raw["personas"],
        "budgets": {"per_persona_tokens": 1, "review_max_tokens": 2},
        "models": {
            "default": {"provider": "anthropic", "base_url": None, "model": "m"},
            "overrides": {"custom": {"provider": "anthropic", "base_url": None, "model": "c"}},
        },
        "verdict": {
            "blocking_categories": ["correctness", "security", "regression"],
            "advisory_categories": ["style", "consistency", "docs"],
        },
        "github": {
            "post_mode": "comment",
            "human_reviewers": ["alice"],
            "paths": {"include": ["src/**"], "exclude": []},
        },
    }
    assert parse_config(yaml.safe_dump(canonical)) == config
    canonical["github"]["paths"]["exclude"].append("mutated")
    assert config.github.paths.exclude == ()


@pytest.mark.parametrize("strategy", ["panel", "iterative", "debate"])
def test_all_approved_strategies_are_accepted(strategy):
    assert parse_config(yaml.safe_dump(configured(strategy=strategy))).strategy == strategy


@pytest.mark.parametrize(
    "data",
    [
        "",
        "null",
        "[]",
        "5",
        "secret-value",
        "models: [secret-value",
        "[secret-value",
        "!!python/object/apply:os.system ['secret-value']",
        "models: !secret-tag value",
        b"models: \xff",
        "models: {}\n---\nmodels: {}",
        "&loop {rounds: *loop}",
        "models: {default: {model: !!int secret-value}}",
        "models: {default: {model: !!bool secret-value}}",
        "models: {default: {model: !!timestamp 2026-99-99}}",
        "models: {default: {model: !!timestamp secret-value}}",
        'models: {default: {model: !!int ""}}',
    ],
)
def test_yaml_failures_are_config_errors_without_source_values(data):
    with pytest.raises(ConfigError) as error:
        parse_config(data)
    message = str(error.value)
    assert "config" in message
    assert len(message) < 250
    assert "secret" not in message
    assert "os.system" not in message


@pytest.mark.parametrize(
    "data,path",
    [
        ("strategy: panel\nstrategy: debate\n", "strategy"),
        ("rounds: {max: 2, max: 3}", "rounds.max"),
        ("budgets: {per_persona_tokens: 1, per_persona_tokens: 2}", "budgets.per_persona_tokens"),
        ("models: {default: {}, default: {}}", "models.default"),
        ("models: {default: {model: secret-one, model: secret-two}}", "models.default.model"),
        ("models: {overrides: {security: {}, security: {}}}", "models.overrides.security"),
        (
            "models: {overrides: {security: {provider: openai, provider: anthropic}}}",
            "models.overrides.security.provider",
        ),
        (
            "verdict: {blocking_categories: [], blocking_categories: []}",
            "verdict.blocking_categories",
        ),
        ("github: {post_mode: review, post_mode: comment}", "github.post_mode"),
        ("github: {paths: {include: [], include: []}}", "github.paths.include"),
        (
            "personas: [{name: secret-one, name: secret-two, system_prompt: secret-prompt}]",
            "personas[0].name",
        ),
    ],
)
def test_duplicate_yaml_keys_are_rejected_with_full_path(data, path):
    with pytest.raises(ConfigError) as error:
        parse_config(data)
    assert path in str(error.value)
    assert "duplicate key" in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "data,path",
    [
        ("1: secret-value", "config"),
        ("null: secret-value", "config"),
        ("? [a, b]\n: secret-value", "config"),
        ("rounds: {true: secret-value}", "rounds"),
        ("models: {default: {false: secret-value}}", "models.default"),
        ("models: {overrides: {1: secret-value}}", "models.overrides"),
        ("personas: [{name: custom, system_prompt: secret-prompt, 3: bad}]", "personas[0]"),
    ],
)
def test_nonstring_yaml_keys_are_rejected_before_construction(data, path):
    with pytest.raises(ConfigError) as error:
        parse_config(data)
    assert path in str(error.value)
    assert "keys must be strings" in str(error.value)
    assert "secret" not in str(error.value)


def test_yaml_aliases_cannot_hide_duplicate_keys():
    data = (
        "models:\n  default: &rail {model: secret-one, model: secret-two}\n"
        "  overrides:\n    security: *rail\n"
    )
    with pytest.raises(ConfigError, match="models.default.model: duplicate key"):
        parse_config(data)


def test_large_acyclic_alias_graph_reaches_schema_validation_promptly():
    data = "a0: &a0 [secret-leaf]\n" + "".join(
        f"a{i}: &a{i} [*a{i - 1}, *a{i - 1}]\n" for i in range(1, 41)
    )
    # A compact graph must not expand into trillions of validation visits.
    # Isolate the parse so a regression is killed, with ample CI startup time.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from scrutare.config import ConfigError, parse_config

try:
    parse_config(sys.stdin.read())
except ConfigError as error:
    print(error)
else:
    raise AssertionError("Expected schema validation to reject unknown fields")
""",
        ],
        input=data,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert "a0: unknown field" in result.stdout
    assert "secret-leaf" not in result.stdout
    assert result.stderr == ""


def test_nonrecursive_mapping_and_sequence_aliases_are_valid_config():
    config = parse_config(
        "models:\n"
        "  default: &rail {provider: openai, model: alias-model}\n"
        "  overrides: {security: *rail, devops: *rail}\n"
        "github:\n"
        "  paths: {include: &paths [src/**, tests/**], exclude: *paths}\n"
    )
    for name in ("security", "devops"):
        rail = config.models.for_persona(name)
        assert (rail.provider, rail.base_url, rail.model) == ("openai", None, "alias-model")
    assert config.github.paths.include == ("src/**", "tests/**")
    assert config.github.paths.exclude == ("src/**", "tests/**")


@pytest.mark.parametrize(
    "data",
    [
        "models: &loop {default: *loop}",
        "github: {paths: {include: &loop [*loop]}}",
        "github: &loop {paths: {include: [*loop]}}",
    ],
)
def test_recursive_mapping_and_sequence_aliases_are_rejected(data):
    with pytest.raises(ConfigError, match="config: recursive YAML aliases are not supported"):
        parse_config(data)


def test_missing_model_is_actionable():
    with pytest.raises(ConfigError, match="models.default.model: expected a nonempty string"):
        parse_config("{}")


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_load_config_io_errors_are_concise_config_errors(tmp_path, kind):
    from scrutare.config import load_config

    path = tmp_path / "scrutare.yaml"
    if kind == "directory":
        path.mkdir()
    with pytest.raises(ConfigError, match="config: cannot read configuration file"):
        load_config(path)


def test_invalid_path_is_a_config_error():
    from pathlib import Path

    from scrutare.config import load_config

    with pytest.raises(ConfigError, match="config: cannot read configuration file"):
        load_config(Path("bad\x00path"))
