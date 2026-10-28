"""Immutable configuration settings and YAML loading."""

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from scrutare.personas.definition import PersonaDefinition as PersonaDefinition
from scrutare.personas.names import DEFAULT_PERSONA_NAMES, SELECTABLE_PERSONA_NAMES

Strategy = Literal["panel", "iterative", "debate"]
Provider = Literal["anthropic", "openai"]
StructuredOutput = Literal["json_schema"]
STRUCTURED_OUTPUT_MODES: tuple[StructuredOutput, ...] = ("json_schema",)
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
    # Opt-in decode-time schema constraint, sent through nare's OpenAI rail only.
    structured_output: StructuredOutput | None = None
    # Opt-in per-request output token cap, passed to nare as --max-tokens on every request.
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        _structured_output(self.structured_output, self.provider, "structured_output")
        _max_output_tokens(self.max_output_tokens, "max_output_tokens")

    def record(self, *, structured_output: bool = False,
               max_output_tokens: bool = False) -> dict[str, Any]:
        """The rail as recorded; each opt-in field appears only when it is set or requested."""
        record: dict[str, Any] = {"provider": self.provider, "base_url": self.base_url,
                                  "model": self.model}
        if structured_output or self.structured_output is not None:
            record["structured_output"] = self.structured_output
        if max_output_tokens or self.max_output_tokens is not None:
            record["max_output_tokens"] = self.max_output_tokens
        return record


@dataclass(frozen=True)
class ModelSettings:
    default: ModelRail
    overrides: tuple[tuple[str, ModelRail], ...] = ()

    def for_persona(self, name: str) -> ModelRail:
        return next((rail for persona, rail in self.overrides if persona == name), self.default)

    @property
    def structured_output(self) -> bool:
        """Whether any rail opts in; then every rail records the field explicitly."""
        return any(rail.structured_output is not None
                   for rail in (self.default, *(rail for _, rail in self.overrides)))

    @property
    def caps_output(self) -> bool:
        """Whether any rail caps its output; then every rail records the field explicitly."""
        return any(rail.max_output_tokens is not None
                   for rail in (self.default, *(rail for _, rail in self.overrides)))

    def rail_record(self, rail: ModelRail) -> dict[str, Any]:
        """One rail recorded with every opt-in field any rail of these settings uses."""
        return rail.record(structured_output=self.structured_output,
                           max_output_tokens=self.caps_output)

    def record(self) -> dict[str, Any]:
        return {"default": self.rail_record(self.default),
                "overrides": {name: self.rail_record(rail) for name, rail in self.overrides}}


@dataclass(frozen=True)
class VerdictSettings:
    blocking_categories: tuple[Category, ...] = ("correctness", "security", "regression")
    advisory_categories: tuple[Category, ...] = ("style", "consistency", "docs")

    def __post_init__(self) -> None:
        blocking = _categories(self.blocking_categories, "verdict.blocking_categories")
        advisory = _categories(self.advisory_categories, "verdict.advisory_categories")
        if set(blocking) & set(advisory):
            raise ConfigError(
                "verdict: blocking_categories and advisory_categories must not overlap"
            )
        missing = set(CATEGORIES) - set(blocking) - set(advisory)
        if missing:
            raise ConfigError(f"verdict: category partition missing: {', '.join(sorted(missing))}")
        object.__setattr__(self, "blocking_categories", blocking)
        object.__setattr__(self, "advisory_categories", advisory)


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
class ContextSettings:
    enabled: bool = False
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    related_paths: tuple[str, ...] = ()
    max_file_bytes: int = 65536
    max_total_bytes: int = 1048576
    max_files: int = 128
    max_tree_requests: int = 256
    line_counts: bool = False

    def record(self) -> dict[str, Any]:
        """Settings as persisted; the opt-in line-count key exists only when chosen."""
        result = asdict(self)
        if not self.line_counts:
            del result["line_counts"]
        return result


@dataclass(frozen=True)
class AnalysisSettings:
    enabled: bool = False


@dataclass(frozen=True)
class InspectionSettings:
    procedures: Literal["baseline", "v1"] = "baseline"


OverrunPolicy = Literal["void", "fallback"]


def _overrun(value: object, path: str) -> None:
    if not isinstance(value, str) or value not in ("void", "fallback"):
        raise ConfigError(f"{path}: expected void or fallback")


@dataclass(frozen=True)
class AssessmentSettings:
    enabled: bool = False
    tokens: int = 2000
    # What a measured assessor overrun does: void the review (default), or fall back to the
    # anchor-validated findings as a degraded, never-approving verdict.
    overrun: OverrunPolicy = "void"

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ConfigError("findings.assessment.enabled: expected boolean")
        if type(self.tokens) is not int or self.tokens <= 0:
            raise ConfigError("findings.assessment.tokens: expected positive integer")
        _overrun(self.overrun, "findings.assessment.overrun")
        if self.overrun != "void" and not self.enabled:
            raise ConfigError("findings.assessment.overrun: requires enabled assessment")

    def record(self) -> dict[str, Any]:
        """Canonical form; the opt-in policy is omitted at its default."""
        result: dict[str, Any] = {"enabled": self.enabled, "tokens": self.tokens}
        if self.overrun != "void":
            result["overrun"] = self.overrun
        return result


@dataclass(frozen=True)
class DiscussionSettings:
    enabled: bool = False
    # A separate discussion allowance reserved before discovery. None reuses the remaining
    # discovery and assessor grants, the original policy.
    tokens: int | None = None
    overrun: OverrunPolicy = "void"

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ConfigError("findings.discussion.enabled: expected boolean")
        if self.tokens is not None and (type(self.tokens) is not int or self.tokens <= 0):
            raise ConfigError("findings.discussion.tokens: expected positive integer")
        _overrun(self.overrun, "findings.discussion.overrun")
        if (self.tokens is not None or self.overrun != "void") and not self.enabled:
            raise ConfigError("findings.discussion: allowance and overrun require enabled")

    def record(self) -> dict[str, Any]:
        result: dict[str, Any] = {"enabled": self.enabled}
        if self.tokens is not None:
            result["tokens"] = self.tokens
        if self.overrun != "void":
            result["overrun"] = self.overrun
        return result


@dataclass(frozen=True)
class FindingSettings:
    evidence: Literal["legacy", "v2"] = "legacy"
    assessment: AssessmentSettings = AssessmentSettings()
    discussion: DiscussionSettings = DiscussionSettings()
    # Opt-in: one tool-free turn to correct machine-describable citation defects (#91).
    citation_correction: bool = False
    protocol_correction: bool = False

    def __post_init__(self) -> None:
        if type(self.protocol_correction) is not bool:
            raise ConfigError("findings.protocol_correction: expected boolean")
        if self.protocol_correction and self.evidence != "v2":
            raise ConfigError("findings.protocol_correction: requires evidence v2")
        if self.evidence not in ("legacy", "v2"):
            raise ConfigError("findings.evidence: expected legacy or v2")
        if type(self.citation_correction) is not bool:
            raise ConfigError("findings.citation_correction: expected boolean")
        if self.citation_correction and self.evidence != "v2":
            raise ConfigError("findings.citation_correction: requires evidence v2")


@dataclass(frozen=True)
class RoutingRule:
    persona: str | PersonaDefinition
    paths: tuple[str, ...]


@dataclass(frozen=True)
class RoutingGroup:
    name: str
    implementation: tuple[str, ...] = ()
    callers: tuple[str, ...] = ()
    contracts: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutingSettings:
    enabled: bool = False
    mode: Literal["auto", "manual"] = "auto"
    force: tuple[str, ...] = ()
    rules: tuple[RoutingRule, ...] = ()
    groups: tuple[RoutingGroup, ...] = ()


@dataclass(frozen=True)
class ReplySettings:
    """Who may contest a posted finding by replying, and whether Scrutare answers."""

    enabled: bool = False
    trust: Literal["write", "anyone"] = "write"

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ConfigError("replies.enabled: expected boolean")
        if self.trust not in ("write", "anyone"):
            raise ConfigError("replies.trust: expected one of write, anyone")


@dataclass(frozen=True)
class ReviewConfig:
    strategy: Strategy
    rounds: RoundSettings
    personas: tuple[str | PersonaDefinition, ...]
    budgets: BudgetSettings
    models: ModelSettings
    verdict: VerdictSettings
    github: GitHubSettings
    inspection: InspectionSettings = InspectionSettings()
    context: ContextSettings = ContextSettings()
    findings: FindingSettings = FindingSettings()
    analysis: AnalysisSettings = AnalysisSettings()
    routing: RoutingSettings = RoutingSettings()
    replies: ReplySettings = ReplySettings()

    def __post_init__(self) -> None:
        if self.replies.enabled and self.strategy != "iterative":
            # Only iterative keeps the finding pool and round bound a contest needs.
            raise ConfigError("replies.enabled: requires strategy iterative")
        if self.findings.evidence == "v2" and not self.context.enabled:
            raise ConfigError("findings.evidence: v2 requires context.enabled")
        if self.findings.assessment.enabled:
            if self.findings.evidence != "v2":
                raise ConfigError("findings.assessment: requires evidence v2")
            if (self.findings.assessment.tokens + (self.findings.discussion.tokens or 0)
                    >= self.budgets.review_max_tokens):
                raise ConfigError("findings.assessment.tokens: must leave discovery capacity")
            if self.findings.assessment.overrun != "void" and self.strategy == "iterative":
                # Iterative re-derives its verdict from assessment state kept across pushes.
                raise ConfigError("findings.assessment.overrun: requires strategy panel or debate")
        if self.findings.discussion.enabled and (
                self.strategy != "debate" or not self.findings.assessment.enabled):
            raise ConfigError("findings.discussion: requires assessed v2 debate")
        if self.findings.citation_correction and any(
                rail.provider != "openai"
                for rail in (self.models.default, *(rail for _, rail in self.models.overrides))):
            # The correction resumes a transcript holding tool_use blocks with no tools offered,
            # which Anthropic refuses; nare's OpenAI rail omits the empty tool list.
            raise ConfigError("findings.citation_correction: requires provider openai on every "
                              "model rail")
        if self.findings.protocol_correction and any(
                rail.provider != "openai"
                for rail in (self.models.default, *(rail for _, rail in self.models.overrides))):
            # The correction resumes a transcript holding tool_use blocks with no tools offered,
            # which Anthropic refuses; nare's OpenAI rail omits the empty tool list.
            raise ConfigError("findings.protocol_correction: requires provider openai on every "
                              "model rail")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "strategy": self.strategy,
            "rounds": asdict(self.rounds),
            "personas": [
                asdict(p) if isinstance(p, PersonaDefinition) else p for p in self.personas
            ],
            "budgets": asdict(self.budgets),
            "models": self.models.record(),
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
        if self.context != ContextSettings():
            result["context"] = self.context.record()
            for field in ("include", "exclude", "related_paths"):
                result["context"][field] = list(result["context"][field])
        if self.inspection.procedures != "baseline":
            result["inspection"] = asdict(self.inspection)
        if self.findings != FindingSettings():
            result["findings"] = {"evidence": self.findings.evidence}
            if self.findings.assessment != AssessmentSettings():
                result["findings"]["assessment"] = self.findings.assessment.record()
            if self.findings.discussion != DiscussionSettings():
                result["findings"]["discussion"] = self.findings.discussion.record()
            if self.findings.protocol_correction:
                result["findings"]["protocol_correction"] = True
            if self.findings.citation_correction:
                result["findings"]["citation_correction"] = True
        if self.analysis.enabled:
            result["analysis"] = asdict(self.analysis)
        if self.routing != RoutingSettings():
            result["routing"] = {
                "enabled": self.routing.enabled, "mode": self.routing.mode,
                "force": list(self.routing.force),
                "rules": [{"persona": asdict(rule.persona)
                           if isinstance(rule.persona, PersonaDefinition) else rule.persona,
                           "paths": list(rule.paths)} for rule in self.routing.rules],
                "groups": [{"name": group.name, **{role: list(getattr(group, role))
                            for role in ("implementation", "callers", "contracts", "tests")}}
                           for group in self.routing.groups],
            }
        if self.replies != ReplySettings():
            result["replies"] = asdict(self.replies)
        return result


def _finding_settings(value: object) -> FindingSettings:
    fields = _mapping(value, "findings", ("evidence", "assessment", "discussion",
                                          "citation_correction", "protocol_correction"))
    assessment = _mapping(fields.get("assessment", {}), "findings.assessment",
                          ("enabled", "tokens", "overrun"))
    return FindingSettings(cast(Literal["legacy", "v2"], _choice(
        fields.get("evidence", "legacy"), "findings.evidence", ("legacy", "v2"))),
        AssessmentSettings(**assessment), DiscussionSettings(**_mapping(
            fields.get("discussion", {}), "findings.discussion",
            ("enabled", "tokens", "overrun"))),
        fields.get("citation_correction", False), fields.get("protocol_correction", False))


def _analysis_settings(value: object, context: ContextSettings) -> AnalysisSettings:
    fields = _mapping(value, "analysis", ("enabled",))
    enabled = fields.get("enabled", False)
    if type(enabled) is not bool or (enabled and not context.enabled):
        raise ConfigError("analysis.enabled: expected boolean; enabled analysis requires context")
    return AnalysisSettings(enabled)


def _context_settings(value: object) -> ContextSettings:
    fields = _mapping(value, "context", tuple(ContextSettings.__dataclass_fields__))
    defaults = ContextSettings()
    enabled = fields.get("enabled", False)
    if type(enabled) is not bool:
        raise ConfigError("context.enabled: expected a boolean")
    paths = {}
    for name in ("include", "exclude", "related_paths"):
        entries = _strings(fields.get(name, []), f"context.{name}")
        for entry in entries:
            if (entry.startswith("/") or "\\" in entry or "\x00" in entry
                    or any(part in ("", ".", "..") for part in entry.split("/"))):
                raise ConfigError(f"context.{name}: expected safe repository-relative paths")
            if name == "related_paths" and any(char in entry for char in "*?["):
                raise ConfigError("context.related_paths: expected explicit paths, not patterns")
        if len(set(entries)) != len(entries):
            raise ConfigError(f"context.{name}: duplicate path")
        paths[name] = entries
    limits = {name: _positive(fields.get(name, getattr(defaults, name)), f"context.{name}")
              for name in ("max_file_bytes", "max_total_bytes", "max_files", "max_tree_requests")}
    line_counts = fields.get("line_counts", False)
    if type(line_counts) is not bool:
        raise ConfigError("context.line_counts: expected a boolean")
    return ContextSettings(enabled, paths["include"], paths["exclude"], paths["related_paths"],
                           limits["max_file_bytes"], limits["max_total_bytes"],
                           limits["max_files"], limits["max_tree_requests"], line_counts)



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
            name = _choice(item, path, SELECTABLE_PERSONA_NAMES)
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


def _structured_output(value: object, provider: str, path: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or value not in STRUCTURED_OUTPUT_MODES:
        raise ConfigError(f"{path}: expected one of {', '.join(STRUCTURED_OUTPUT_MODES)} or null")
    if provider != "openai":
        raise ConfigError(f"{path}: requires provider openai")


def _max_output_tokens(value: object, path: str) -> None:
    if value is not None and (type(value) is not int or value <= 0):
        raise ConfigError(f"{path}: expected a positive integer or null")


def _rail(value: object, path: str, default: ModelRail | None = None) -> ModelRail:
    fields = _mapping(value, path, ("provider", "base_url", "model", "structured_output",
                                    "max_output_tokens"))
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
    structured = fields.get("structured_output", default.structured_output if default else None)
    try:
        _structured_output(structured, provider, f"{path}.structured_output")
    except ConfigError as error:
        if "structured_output" in fields or default is None:
            raise
        raise ConfigError(f"{error} (inherited from models.default; set it to null "
                          "to clear it)") from None
    cap = fields.get("max_output_tokens", default.max_output_tokens if default else None)
    _max_output_tokens(cap, f"{path}.max_output_tokens")
    return ModelRail(provider, base_url, model, cast("StructuredOutput | None", structured),
                     cast("int | None", cap))


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
    if not isinstance(value, tuple):
        raise ConfigError(f"{path}: expected a tuple")
    result = []
    for i, item in enumerate(value):
        item_path = f"{path}[{i}]"
        category = _choice(item, item_path, CATEGORIES)
        if category in result:
            raise ConfigError(f"{item_path}: duplicate category")
        result.append(category)
    return tuple(category for category in CATEGORIES if category in result)


def _verdict(value: object) -> VerdictSettings:
    fields = _mapping(value, "verdict", ("blocking_categories", "advisory_categories"))
    defaults = VerdictSettings()
    blocking = tuple(
        _list(
            fields.get("blocking_categories", list(defaults.blocking_categories)),
            "verdict.blocking_categories",
        )
    )
    advisory = tuple(
        _list(
            fields.get("advisory_categories", list(defaults.advisory_categories)),
            "verdict.advisory_categories",
        )
    )
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
        ("strategy", "rounds", "personas", "budgets", "models", "verdict", "github",
         "inspection", "context", "findings", "analysis", "routing", "replies"),
    )
    strategy = cast(
        Strategy,
        _choice(raw.get("strategy", "panel"), "strategy", ("panel", "iterative", "debate")),
    )
    rounds = _mapping(raw.get("rounds", {}), "rounds", ("max",))
    budgets = _mapping(
        raw.get("budgets", {}), "budgets", ("per_persona_tokens", "review_max_tokens")
    )
    personas = _personas(raw.get("personas", list(DEFAULT_PERSONA_NAMES)))
    inspection = _mapping(raw.get("inspection", {}), "inspection", ("procedures",))
    context = _context_settings(raw.get("context", {}))
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
        InspectionSettings(cast(Literal["baseline", "v1"], _choice(
            inspection.get("procedures", "baseline"), "inspection.procedures",
            ("baseline", "v1"),
        ))),
        context,
        _finding_settings(raw.get("findings", {})),
        _analysis_settings(raw.get("analysis", {}), context),
        _routing(raw.get("routing", {})),
        _replies(raw.get("replies", {})),
    )


def _replies(value: object) -> ReplySettings:
    fields = _mapping(value, "replies", ("enabled", "trust"))
    return ReplySettings(fields.get("enabled", False), cast(
        Literal["write", "anyone"],
        _choice(fields.get("trust", "write"), "replies.trust", ("write", "anyone"))))


def load_config(path: Path) -> ReviewConfig:
    """Read and validate one config file, exposing only safe diagnostics."""
    try:
        data = path.read_bytes()
    except (OSError, ValueError):
        raise ConfigError("config: cannot read configuration file") from None
    return parse_config(data)


def _routing(value: object) -> RoutingSettings:
    fields = _mapping(value, "routing", ("enabled", "mode", "force", "rules", "groups"))
    enabled = fields.get("enabled", False)
    if type(enabled) is not bool:
        raise ConfigError("routing.enabled: expected boolean")
    mode = cast(Literal["auto", "manual"], _choice(
        fields.get("mode", "auto"), "routing.mode", ("auto", "manual")))
    rules = []
    names = set[str]()
    for i, item in enumerate(_list(fields.get("rules", []), "routing.rules")):
        path = f"routing.rules[{i}]"
        rule = _mapping(item, path, ("persona", "paths"))
        try:
            persona = _personas([rule.get("persona")])[0]
        except ConfigError:
            raise ConfigError(f"{path}.persona: expected builtin or inline procedure") from None
        name = persona if isinstance(persona, str) else persona.name
        if name in names:
            raise ConfigError(f"{path}.persona: duplicate routed procedure")
        names.add(name)
        patterns = _routing_patterns(rule.get("paths", []), path + ".paths")
        if not patterns:
            raise ConfigError(f"{path}.paths: expected at least one pattern")
        rules.append(RoutingRule(persona, patterns))
    force = _strings(fields.get("force", []), "routing.force")
    if (len(set(force)) != len(force)
            or set(force) - (set(SELECTABLE_PERSONA_NAMES) | names)):
        raise ConfigError("routing.force: expected unique declared procedure names")
    groups = []
    seen = set[str]()
    roles = ("implementation", "callers", "contracts", "tests")
    for i, item in enumerate(_list(fields.get("groups", []), "routing.groups")):
        path = f"routing.groups[{i}]"
        group = _mapping(item, path, ("name", *roles))
        name = _string(group.get("name"), path + ".name")
        if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name) or name in seen:
            raise ConfigError(f"{path}.name: expected unique lowercase slug")
        seen.add(name)
        groups.append(RoutingGroup(name, *(_routing_patterns(group.get(role, []), path + "." + role)
                                         for role in roles)))
    return RoutingSettings(enabled, mode, force, tuple(rules), tuple(groups))


def _routing_patterns(value: object, path: str) -> tuple[str, ...]:
    patterns = _strings(value, path)
    for pattern in patterns:
        if (pattern.startswith("/") or "\\" in pattern or "\x00" in pattern
                or any(p in ("", ".", "..") for p in pattern.split("/"))):
            raise ConfigError(f"{path}: expected safe repository-relative patterns")
    return patterns
