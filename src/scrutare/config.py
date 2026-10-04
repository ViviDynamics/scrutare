"""Immutable configuration settings and YAML loading."""

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from scrutare.personas.names import BUILTIN_PERSONA_NAMES

Strategy = Literal["panel", "iterative", "debate"]
Provider = Literal["anthropic", "openai"]
PostMode = Literal["review", "comment"]
Category = Literal["correctness", "security", "regression", "style", "consistency", "docs"]
CATEGORIES: tuple[Category, ...] = (
    "correctness",
    "security",
    "regression",
    "style",
    "consistency",
    "docs",
)


class ConfigError(ValueError):
    """An invalid configuration with a safe field-path diagnostic."""


@dataclass(frozen=True)
class PersonaDefinition:
    name: str
    system_prompt: str


@dataclass(frozen=True)
class RoundSettings:
    max: int = 3


@dataclass(frozen=True)
class BudgetSettings:
    per_persona_tokens: int = 100000
    review_max_tokens: int = 500000


@dataclass(frozen=True)
class ModelRail:
    provider: Provider
    base_url: str | None
    model: str


@dataclass(frozen=True)
class ModelSettings:
    default: ModelRail
    overrides: tuple[tuple[str, ModelRail], ...] = ()

    def for_persona(self, name: str) -> ModelRail:
        return next((rail for persona, rail in self.overrides if persona == name), self.default)


@dataclass(frozen=True)
class VerdictSettings:
    blocking_categories: tuple[Category, ...] = ("correctness", "security", "regression")
    advisory_categories: tuple[Category, ...] = ("style", "consistency", "docs")


@dataclass(frozen=True)
class PathSettings:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ("docs/**", "*.md")


@dataclass(frozen=True)
class GitHubSettings:
    post_mode: PostMode = "review"
    human_reviewers: tuple[str, ...] = ()
    paths: PathSettings = PathSettings()


@dataclass(frozen=True)
class ReviewConfig:
    strategy: Strategy
    rounds: RoundSettings
    personas: tuple[str | PersonaDefinition, ...]
    budgets: BudgetSettings
    models: ModelSettings
    verdict: VerdictSettings
    github: GitHubSettings

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "rounds": asdict(self.rounds),
            "personas": [
                asdict(p) if isinstance(p, PersonaDefinition) else p for p in self.personas
            ],
            "budgets": asdict(self.budgets),
            "models": {
                "default": asdict(self.models.default),
                "overrides": {name: asdict(rail) for name, rail in self.models.overrides},
            },
            "verdict": {
                "blocking_categories": list(self.verdict.blocking_categories),
                "advisory_categories": list(self.verdict.advisory_categories),
            },
            "github": {
                "post_mode": self.github.post_mode,
                "human_reviewers": list(self.github.human_reviewers),
                "paths": {
                    "include": list(self.github.paths.include),
                    "exclude": list(self.github.paths.exclude),
                },
            },
        }


def _mapping(value: object, path: str, fields: tuple[str, ...] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path or 'config'}: expected a mapping")
    for key in value:
        if not isinstance(key, str):
            raise ConfigError(f"{path or 'config'}: field keys must be strings")
        if fields is not None and key not in fields:
            raise ConfigError(f"{_field(path, key)}: unknown field; allowed: {', '.join(fields)}")
    return value


def _field(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: expected a nonempty string")
    return value


def _choice(value: object, path: str, choices: tuple[str, ...]) -> str:
    value = _string(value, path)
    if value not in choices:
        raise ConfigError(f"{path}: expected one of {', '.join(choices)}")
    return value


def _positive(value: object, path: str) -> int:
    if type(value) is not int or value <= 0:
        raise ConfigError(f"{path}: expected a positive integer")
    return value


def _list(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"{path}: expected a list")
    return value


def _strings(value: object, path: str) -> tuple[str, ...]:
    return tuple(_string(item, f"{path}[{i}]") for i, item in enumerate(_list(value, path)))


def _personas(value: object) -> tuple[str | PersonaDefinition, ...]:
    items = _list(value, "personas")
    if not items:
        raise ConfigError("personas: expected at least one persona")
    result: list[str | PersonaDefinition] = []
    seen = set[str]()
    for i, item in enumerate(items):
        path = f"personas[{i}]"
        if isinstance(item, str):
            name = _choice(item, path, BUILTIN_PERSONA_NAMES)
            persona: str | PersonaDefinition = name
        else:
            definition = _mapping(item, path, ("name", "system_prompt"))
            path += ".name"
            name = _string(definition.get("name"), path)
            if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name):
                raise ConfigError(
                    f"{path}: expected a lowercase slug using letters, digits and hyphens"
                )
            persona = PersonaDefinition(
                name, _string(definition.get("system_prompt"), f"personas[{i}].system_prompt")
            )
        if name in seen:
            raise ConfigError(f"{path}: duplicate persona name")
        seen.add(name)
        result.append(persona)
    return tuple(result)


def _rail(value: object, path: str, default: ModelRail | None = None) -> ModelRail:
    fields = _mapping(value, path, ("provider", "base_url", "model"))
    provider = cast(
        Provider,
        _choice(
            fields.get("provider", default.provider if default else "anthropic"),
            f"{path}.provider",
            ("anthropic", "openai"),
        ),
    )
    model = _string(fields.get("model", default.model if default else None), f"{path}.model")
    base_url = fields.get("base_url", default.base_url if default else None)
    if base_url is not None:
        base_url = _string(base_url, f"{path}.base_url")
        try:
            parsed = urlsplit(base_url)
            valid = (
                parsed.scheme in ("http", "https")
                and bool(parsed.hostname)
                and not any(char.isspace() for char in base_url)
            )
            parsed.port  # Validate an optional port without changing the URL.
        except ValueError:
            valid = False
        if not valid:
            raise ConfigError(f"{path}.base_url: expected an HTTP(S) URL with a host or null")
    return ModelRail(provider, base_url, model)


def _models(value: object, personas: tuple[str | PersonaDefinition, ...]) -> ModelSettings:
    fields = _mapping(value, "models", ("default", "overrides"))
    default = _rail(fields.get("default", {}), "models.default")
    overrides = _mapping(fields.get("overrides", {}), "models.overrides", None)
    names = {p.name if isinstance(p, PersonaDefinition) else p for p in personas}
    result = []
    for name, rail in sorted(overrides.items()):
        path = f"models.overrides.{name}"
        if name not in names:
            raise ConfigError(f"{path}: expected a configured persona name")
        result.append((name, _rail(rail, path, default)))
    return ModelSettings(default, tuple(result))


def _categories(value: object, path: str) -> tuple[Category, ...]:
    result = []
    for i, item in enumerate(_list(value, path)):
        item_path = f"{path}[{i}]"
        category = _choice(item, item_path, CATEGORIES)
        if category in result:
            raise ConfigError(f"{item_path}: duplicate category")
        result.append(category)
    return tuple(category for category in CATEGORIES if category in result)


def _verdict(value: object) -> VerdictSettings:
    fields = _mapping(value, "verdict", ("blocking_categories", "advisory_categories"))
    defaults = VerdictSettings()
    blocking = _categories(
        fields.get("blocking_categories", list(defaults.blocking_categories)),
        "verdict.blocking_categories",
    )
    advisory = _categories(
        fields.get("advisory_categories", list(defaults.advisory_categories)),
        "verdict.advisory_categories",
    )
    if set(blocking) & set(advisory):
        raise ConfigError("verdict: blocking_categories and advisory_categories must not overlap")
    missing = set(CATEGORIES) - set(blocking) - set(advisory)
    if missing:
        raise ConfigError(f"verdict: category partition missing: {', '.join(sorted(missing))}")
    return VerdictSettings(blocking, advisory)


def _github(value: object) -> GitHubSettings:
    fields = _mapping(value, "github", ("post_mode", "human_reviewers", "paths"))
    paths = _mapping(fields.get("paths", {}), "github.paths", ("include", "exclude"))
    return GitHubSettings(
        cast(
            PostMode,
            _choice(fields.get("post_mode", "review"), "github.post_mode", ("review", "comment")),
        ),
        _strings(fields.get("human_reviewers", []), "github.human_reviewers"),
        PathSettings(
            _strings(paths.get("include", []), "github.paths.include"),
            _strings(paths.get("exclude", ["docs/**", "*.md"]), "github.paths.exclude"),
        ),
    )


def _check_yaml(node: Node, path: str, ancestors: set[int], checked: set[int]) -> None:
    node_id = id(node)
    if node_id in ancestors:
        raise ConfigError("config: recursive YAML aliases are not supported")
    if node_id in checked:
        return
    ancestors.add(node_id)
    if isinstance(node, MappingNode):
        seen = set[str]()
        for key, value in node.value:
            if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str":
                raise ConfigError(f"{path or 'config'}: field keys must be strings")
            child_path = _field(path, key.value)
            if key.value in seen:
                raise ConfigError(f"{child_path}: duplicate key")
            seen.add(key.value)
            _check_yaml(value, child_path, ancestors, checked)
    elif isinstance(node, SequenceNode):
        for i, item in enumerate(node.value):
            _check_yaml(item, f"{path}[{i}]", ancestors, checked)
    ancestors.remove(node_id)
    checked.add(node_id)


def _load_yaml(data: bytes | str) -> object:
    loader = None
    try:
        loader = yaml.SafeLoader(data)
        node = loader.get_single_node()
        if node is None:
            return None
        _check_yaml(node, "", set(), set())
        try:
            return loader.construct_document(node)
        except (ValueError, KeyError, IndexError, AttributeError):
            # Native YAML scalar constructors sometimes bypass YAMLError.
            raise ConfigError("config: invalid YAML scalar") from None
    except yaml.YAMLError as error:
        location = ""
        if isinstance(error, yaml.MarkedYAMLError) and error.problem_mark is not None:
            mark = error.problem_mark
            location = f" at line {mark.line + 1}, column {mark.column + 1}"
        raise ConfigError(f"config: invalid or unsupported YAML{location}") from None
    except RecursionError:
        raise ConfigError("config: YAML nesting is too deep") from None
    finally:
        if loader is not None:
            loader.dispose()


def parse_config(data: bytes | str) -> ReviewConfig:
    """Validate safe YAML and resolve defaults without any external requests."""
    raw = _mapping(
        _load_yaml(data),
        "",
        ("strategy", "rounds", "personas", "budgets", "models", "verdict", "github"),
    )
    strategy = cast(
        Strategy,
        _choice(raw.get("strategy", "panel"), "strategy", ("panel", "iterative", "debate")),
    )
    rounds = _mapping(raw.get("rounds", {}), "rounds", ("max",))
    budgets = _mapping(
        raw.get("budgets", {}), "budgets", ("per_persona_tokens", "review_max_tokens")
    )
    personas = _personas(raw.get("personas", list(BUILTIN_PERSONA_NAMES)))
    return ReviewConfig(
        strategy,
        RoundSettings(_positive(rounds.get("max", 3), "rounds.max")),
        personas,
        BudgetSettings(
            _positive(budgets.get("per_persona_tokens", 100000), "budgets.per_persona_tokens"),
            _positive(budgets.get("review_max_tokens", 500000), "budgets.review_max_tokens"),
        ),
        _models(raw.get("models", {}), personas),
        _verdict(raw.get("verdict", {})),
        _github(raw.get("github", {})),
    )


def load_config(path: Path) -> ReviewConfig:
    """Read and validate one config file, exposing only safe diagnostics."""
    try:
        data = path.read_bytes()
    except (OSError, ValueError):
        raise ConfigError("config: cannot read configuration file") from None
    return parse_config(data)
