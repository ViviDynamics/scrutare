"""The versioned self-hosted profile fills opt-in options, records them and replays (#133)."""

import json
import socket
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from test_replay import bundle
from test_replay_structured_output import marker_line
from test_review_inputs import capture as capture

from scrutare.config import ConfigError, explain_config, parse_config
from scrutare.engine.nare_session import _rail_argv, _rail_options
from scrutare.engine.session_models import NareCapability
from scrutare.interfaces.cli import main
from scrutare.profiles import PROFILES
from scrutare.replay import replay_run

RAIL = ("models:\n  default:\n    provider: openai\n"
        "    base_url: https://litellm.example/v1\n    model: onprem/m\n")
PROFILED = "profile: self-hosted@1\n" + RAIL
# Every condition holds: evidence v2, the post-session check and independent assessment.
SHAPE = ("context: {enabled: true}\n"
         "findings:\n  evidence: v2\n  assessment: {enabled: true}\n")
# The same settings written out by hand, as the profile resolves them.
EXPLICIT = (
    "models:\n  default:\n    provider: openai\n    base_url: https://litellm.example/v1\n"
    "    model: onprem/m\n    structured_output: json_schema\n    max_output_tokens: 20000\n"
    "    proxy_num_retries: 0\n  preflight: {route: true}\n"
    "context: {enabled: true, line_counts: true}\n"
    "findings:\n  evidence: v2\n  protocol_correction: true\n"
    "  assessment: {enabled: true, tokens: 50000}\n")
CAPABLE = NareCapability("2026.10.31", 2, structured_output=("json_schema",),
                         proxy_num_retries=True)


def resolved(config, path):
    value = config
    for name in path.split("."):
        value = getattr(value, name)
    return value


def test_version_one_values_never_change():
    # A change to any value is a new version, so a pinned repository keeps its behavior.
    assert [(v.path, v.value, v.when) for v in PROFILES["self-hosted"][1]] == [
        ("models.default.structured_output", "json_schema", "always"),
        ("models.default.max_output_tokens", 20000, "always"),
        ("models.default.proxy_num_retries", 0, "always"),
        ("models.preflight.route", True, "always"),
        ("context.line_counts", True, "evidence_v2"),
        ("findings.protocol_correction", True, "post_session_v2"),
        ("findings.assessment.tokens", 50000, "assessment"),
    ]
    assert list(PROFILES) == ["self-hosted"] and list(PROFILES["self-hosted"]) == [1]


def test_the_profile_resolves_to_exactly_the_explicit_settings():
    config = parse_config(PROFILED + SHAPE)
    assert config.profile == "self-hosted@1"
    assert replace(config, profile=None) == parse_config(EXPLICIT)


@pytest.mark.parametrize("path,value", [
    ("models.default.structured_output", "json_schema"),
    ("models.default.max_output_tokens", 20000),
    ("models.default.proxy_num_retries", 0),
    ("models.preflight.route", True),
    ("context.line_counts", True),
    ("findings.protocol_correction", True),
    ("findings.assessment.tokens", 50000),
])
def test_each_value_is_filled_field_by_field(path, value):
    config, plain = parse_config(PROFILED + SHAPE), parse_config(RAIL + SHAPE)
    assert resolved(config, path) == value
    assert resolved(plain, path) != value


@pytest.mark.parametrize("path", [
    "models.default.request_retries", "models.default.request_retry_seconds",
    "models.default.cache_breakpoints", "models.default.effort", "models.capacity",
    "models.preflight.request",
    "findings.citation_correction", "findings.citation_check", "findings.max_corrections",
    "findings.cited_reads", "findings.assessment.overrun", "findings.discussion",
    "budgets", "github",
])
def test_options_without_evidence_keep_their_defaults(path):
    profiled = parse_config(PROFILED + SHAPE)
    assert resolved(profiled, path) == resolved(parse_config(RAIL + SHAPE), path)
    # An enabled check states its bound; the profile keeps the default 30 seconds.
    assert profiled.models.preflight.timeout_seconds == 30


@pytest.mark.parametrize("field,value,expected", [
    ("structured_output", "tool_call", "tool_call"),
    ("structured_output", "null", None),
    ("max_output_tokens", "12000", 12000),
    ("max_output_tokens", "null", None),
    ("proxy_num_retries", "2", 2),
    ("proxy_num_retries", "null", None),
])
def test_a_rail_field_the_file_sets_overrides_the_profile(field, value, expected):
    config = parse_config(PROFILED + f"    {field}: {value}\n")
    assert getattr(config.models.default, field) == expected
    assert parse_config(json.dumps(config.to_dict())) == config


@pytest.mark.parametrize("text,path,expected", [
    ("  preflight: {route: false}\n", "models.preflight.route", False),
    ("context: {enabled: true, line_counts: false}\nfindings: {evidence: v2}\n",
     "context.line_counts", False),
    ("context: {enabled: true}\nfindings: {evidence: v2, protocol_correction: false}\n",
     "findings.protocol_correction", False),
    ("context: {enabled: true}\n"
     "findings: {evidence: v2, assessment: {enabled: true, tokens: 9000}}\n",
     "findings.assessment.tokens", 9000),
])
def test_any_other_field_the_file_sets_overrides_the_profile(text, path, expected):
    config = parse_config(PROFILED + text)
    assert resolved(config, path) == expected
    # Recorded explicitly, so the record does not fill the cleared value again.
    assert parse_config(json.dumps(config.to_dict())) == config
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


def test_overrides_inherit_filled_rail_fields_and_keep_their_own():
    config = parse_config(PROFILED + "  overrides:\n    security: {model: other}\n"
                          "    devops: {max_output_tokens: null, structured_output: null}\n")
    security, devops = (config.models.for_persona(name) for name in ("security", "devops"))
    assert (security.structured_output, security.max_output_tokens,
            security.proxy_num_retries) == ("json_schema", 20000, 0)
    assert (devops.structured_output, devops.max_output_tokens,
            devops.proxy_num_retries) == (None, None, 0)
    assert parse_config(json.dumps(config.to_dict())) == config


def test_conditions_keep_a_legacy_review_valid_and_leave_review_shape_fields_alone():
    config = parse_config(PROFILED)
    assert config.findings.evidence == "legacy"
    assert not config.context.line_counts and not config.findings.protocol_correction
    assert config.findings.assessment.tokens == 2000


def test_the_session_check_takes_the_place_of_the_correction_turn():
    config = parse_config(PROFILED + "context: {enabled: true}\n"
                          "findings: {evidence: v2, citation_check: session}\n")
    assert config.findings.citation_check == "session"
    assert not config.findings.protocol_correction
    assert config.context.line_counts


@pytest.mark.parametrize("value,message", [
    ("secret-name@1", "unknown profile name; expected one of self-hosted@1"),
    ("self-hosted@7", "unknown version of self-hosted; expected one of self-hosted@1"),
    ("self-hosted", "expected NAME@VERSION, one of self-hosted@1"),
    ("self-hosted@", "expected NAME@VERSION"),
    ("self-hosted@01", "expected NAME@VERSION"),
    ("Self-Hosted@1", "expected NAME@VERSION"),
    ("1", "expected NAME@VERSION"),
    ("null", "expected NAME@VERSION"),
    ("[self-hosted@1]", "expected NAME@VERSION"),
])
def test_unknown_or_malformed_profiles_are_refused_at_load(value, message):
    with pytest.raises(ConfigError, match=r"^profile: " + message) as error:
        parse_config(f"profile: {value}\n" + RAIL)
    assert "secret" not in str(error.value)


def test_an_anthropic_default_rail_is_refused():
    with pytest.raises(ConfigError, match=r"^profile: self-hosted@1 requires provider openai "
                       r"on every rail; models.default is anthropic$"):
        parse_config("profile: self-hosted@1\nmodels:\n  default: {model: m}\n")


def test_an_anthropic_override_is_refused():
    with pytest.raises(ConfigError, match=r"requires provider openai on every rail; "
                       r"models.overrides.security is anthropic$"):
        parse_config(PROFILED + "  overrides:\n    security: {provider: anthropic}\n")


def test_the_file_is_validated_on_its_own_first():
    with pytest.raises(ConfigError, match=r"^models.default.unexpected: unknown field"):
        parse_config(PROFILED + "    unexpected: 1\n")


def test_an_error_on_a_filled_value_names_the_profile():
    with pytest.raises(ConfigError, match=r"^findings.assessment.tokens: must leave discovery "
                       r"capacity \(value from profile self-hosted@1; set the field in the "
                       r"file to override it\)$"):
        parse_config(PROFILED + "budgets: {review_max_tokens: 50000}\n" + SHAPE)


def test_a_configuration_without_a_profile_records_and_launches_as_before():
    config = parse_config(RAIL)
    assert config.profile is None
    # The exact config.json bytes a review writes, as before this change.
    assert json.dumps(config.to_dict(), indent=2) + "\n" == json.dumps({
        "strategy": "panel", "rounds": {"max": 3},
        "personas": ["senior-dev", "junior-dev", "security", "devops"],
        "budgets": {"per_persona_tokens": 100000, "review_max_tokens": 500000},
        "models": {"default": {"provider": "openai", "base_url": "https://litellm.example/v1",
                               "model": "onprem/m"}, "overrides": {}},
        "verdict": {"blocking_categories": ["correctness", "security", "regression"],
                    "advisory_categories": ["style", "consistency", "docs"]},
        "github": {"post_mode": "review", "human_reviewers": [],
                   "paths": {"include": [], "exclude": ["docs/**", "*.md"]}},
    }, indent=2) + "\n"
    rail = config.models.default
    assert _rail_argv(rail, CAPABLE) + _rail_options(rail) == [
        "--provider", "openai", "--model=onprem/m", "--stream",
        "--base-url", "https://litellm.example/v1"]


def test_a_profiled_rail_sends_the_argv_of_its_explicit_settings():
    profiled = parse_config(PROFILED).models.default
    explicit = parse_config(EXPLICIT).models.default
    assert profiled == explicit
    assert _rail_argv(profiled, CAPABLE) + _rail_options(profiled) == [
        "--provider", "openai", "--model=onprem/m", "--stream",
        "--base-url", "https://litellm.example/v1", "--structured-output", "json_schema",
        "--max-tokens", "20000", "--proxy-num-retries", "0"]


def test_the_record_names_the_profile_and_parses_back_to_the_same_settings():
    config = parse_config(PROFILED + SHAPE)
    record = config.to_dict()
    assert next(iter(record)) == "profile" and record["profile"] == "self-hosted@1"
    assert record["models"]["preflight"] == {"route": True, "request": False,
                                             "timeout_seconds": 30}
    assert record["context"]["line_counts"] is True
    assert record["findings"]["protocol_correction"] is True
    assert record["findings"]["assessment"] == {"enabled": True, "tokens": 50000}
    assert parse_config(json.dumps(record)) == config
    assert parse_config(yaml.safe_dump(record)) == config


def test_this_repository_switches_with_one_line():
    path = Path(__file__).resolve().parents[1] / "scrutare.yaml"
    config = parse_config(b"profile: self-hosted@1\n" + path.read_bytes())
    assert config.models.default.structured_output == "json_schema"
    assert config.models.default.max_output_tokens == 20000
    assert config.models.preflight.route


def test_a_profiled_capture_prepares_with_agreeing_snapshots(capture):
    # Preparation refuses a capture whose config.yaml, config.json and settings disagree.
    models = yaml.safe_load(RAIL)["models"]
    document = {"profile": "self-hosted@1", "personas": ["senior-dev"], "models": models,
                "budgets": {"per_persona_tokens": 40, "review_max_tokens": 40}}
    (capture / "config.yaml").write_text(json.dumps(document))
    config = parse_config(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    from scrutare.engine.review_inputs import prepare_review_inputs
    prepare_review_inputs(capture, config)


def recorded_session(path, *, cap="20000"):
    directory = path / "sessions" / "senior-dev" / "attempt-0001"
    directory.mkdir(parents=True)
    rail = {"provider": "openai", "base_url": "https://litellm.example/v1", "model": "onprem/m",
            "structured_output": "json_schema", "max_output_tokens": 20000,
            "proxy_num_retries": 0}
    argv = ["nare", "run", "prompt", "--max-turns", "6", "--structured-output", "json_schema",
            "--max-tokens", cap, "--proxy-num-retries", "0"]
    (directory / "invocation.json").write_text(json.dumps(
        {"schema_version": 1, "persona": "senior-dev", "argv": argv, "rail": rail}))
    (directory / "result.json").write_text(json.dumps({
        "schema_version": 1, "status": "complete", "reason": "done",
        "structured_output": {"mode": "json_schema", "constrained_turns": 1},
        "max_output_tokens": {"limit": 20000, "reached": False}}))
    (directory / "stdout.jsonl").write_text(marker_line() + "\n" + json.dumps(
        {"type": "result", "status": "done", "stop_reason": "end_turn"}) + "\n")


def profiled_run(path):
    bundle(path)
    text = "profile: self-hosted@1\nstrategy: debate\n" + RAIL
    (path / "config.yaml").write_text(text)
    (path / "config.json").write_text(json.dumps(parse_config(text).to_dict()))


def test_replay_of_a_profiled_run_audits_the_profile_values(tmp_path):
    profiled_run(tmp_path)
    recorded_session(tmp_path)
    result = replay_run(tmp_path)
    assert [(issue.code, issue.path) for issue in result.issues] == []
    assert result.exit_code == 0


def test_replay_refuses_a_profiled_run_that_did_not_send_the_profile_cap(tmp_path):
    profiled_run(tmp_path)
    recorded_session(tmp_path, cap="12000")
    codes = {issue.code for issue in replay_run(tmp_path).issues}
    assert "max_output_tokens_unrecorded" in codes


@pytest.fixture
def offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("config show must not open a connection")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def show(tmp_path, capsys, text):
    path = tmp_path / "scrutare.yaml"
    path.write_text(text)
    before = sorted(p.name for p in tmp_path.iterdir())
    code = main(["config", "show", "--config", str(path)])
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # Read-only.
    out = capsys.readouterr()
    return code, out


def test_config_show_prints_each_option_and_where_it_came_from(tmp_path, capsys, offline):
    text = (PROFILED + "    max_output_tokens: 12000\n"
            "  overrides:\n    security: {structured_output: null}\n")
    code, out = show(tmp_path, capsys, text)
    assert code == 0 and out.err == ""
    document = json.loads(out.out)
    assert document["profile"] == "self-hosted@1"
    assert document["config"] == parse_config(text).to_dict()
    assert document["options"] == [
        {"path": "models.default.structured_output", "value": "json_schema",
         "source": "profile"},
        {"path": "models.overrides.security.structured_output", "value": None,
         "source": "file"},
        {"path": "models.default.max_output_tokens", "value": 12000, "source": "file"},
        {"path": "models.overrides.security.max_output_tokens", "value": 12000,
         "source": "file"},
        {"path": "models.default.proxy_num_retries", "value": 0, "source": "profile"},
        {"path": "models.overrides.security.proxy_num_retries", "value": 0,
         "source": "profile"},
        {"path": "models.preflight.route", "value": True, "source": "profile"},
        {"path": "context.line_counts", "value": False, "source": "inactive"},
        {"path": "findings.protocol_correction", "value": False, "source": "inactive"},
        {"path": "findings.assessment.tokens", "value": 2000, "source": "inactive"},
    ]


def test_config_show_without_a_profile_prints_the_canonical_configuration(
        tmp_path, capsys, offline):
    code, out = show(tmp_path, capsys, RAIL)
    assert code == 0
    assert json.loads(out.out) == {"profile": None, "options": [],
                                   "config": parse_config(RAIL).to_dict()}
    assert explain_config(RAIL) == json.loads(out.out)


def test_config_show_refuses_an_invalid_file_with_nothing_on_stdout(tmp_path, capsys, offline):
    code, out = show(tmp_path, capsys, "profile: self-hosted@9\n" + RAIL)
    assert code == 1 and out.out == ""
    assert out.err == ("scrutare: profile: unknown version of self-hosted; expected one of "
                       "self-hosted@1\n")


def test_config_show_refuses_a_missing_file(tmp_path, capsys):
    assert main(["config", "show", "--config", str(tmp_path / "absent.yaml")]) == 1
    out = capsys.readouterr()
    assert out.out == "" and "cannot read config file" in out.err
