"""Replay readers retain source evidence and reject unsafe artifact shapes."""

import json
import os
from copy import deepcopy

import pytest

from scrutare.config import VerdictSettings
from scrutare.findings.dedupe import MergedFinding
from scrutare.findings.models import Anchor, Finding
from scrutare.findings.verdict import Exhaustion, derive_verdict


def merged_groups():
    anchor = Anchor("src/café.py", 2, "LEFT")
    first = Finding(anchor, "style", " Wrong\tresult ", " Keep spacing ", "junior-dev")
    security = Finding(anchor, "security", "Wrong result", "Private data 🔒", "security")
    return (
        MergedFinding(anchor, "Wrong  result", (first, security, first)),
        MergedFinding(Anchor("README.md", 5), "Missing example", (
            Finding(Anchor("README.md", 5), "docs", "Missing example", "Cannot run", "docs"),
        )),
        # Equal groups must survive as separate recorded occurrences.
        MergedFinding(anchor, "Wrong  result", (first, security, first)),
    )


def test_findings_reconstruct_every_group_source_and_original_text_in_order():
    from scrutare.replay.artifacts import parse_findings

    groups = merged_groups()
    recovered, issues = parse_findings([group.to_dict() for group in groups])
    assert recovered == groups
    assert recovered[0].problem == "Wrong  result"
    assert recovered[0].sources[0].problem == " Wrong\tresult "
    assert recovered[0].sources[0].reason == " Keep spacing "
    assert len(recovered) == 3
    assert len(recovered[0].sources) == 3
    assert recovered[1].anchor.side == "RIGHT"
    assert issues == ()


def test_findings_empty_array_is_valid():
    from scrutare.replay.artifacts import parse_findings

    assert parse_findings([]) == ((), ())


def test_source_fields_own_categories_personas_reasons_when_summaries_disagree():
    from scrutare.replay.artifacts import parse_findings
    from scrutare.replay.models import AuditIssue

    group = merged_groups()[0].to_dict()
    group.update(categories=["docs"], personas=["stale"], reasons=["stale reason"])
    recovered, issues = parse_findings([group])
    assert recovered[0].categories == ("security", "style")
    assert recovered[0].personas == ("junior-dev", "security")
    assert recovered[0].reasons == (" Keep spacing ", "Private data 🔒")
    assert issues == tuple(
        AuditIssue("findings_summary_disagrees", f"findings[0].{field}", "difference")
        for field in ("categories", "personas", "reasons")
    )


@pytest.mark.parametrize("field", ["file", "line", "side", "problem", "categories",
                                  "personas", "reasons", "sources"])
def test_findings_require_all_established_group_fields(field):
    from scrutare.replay.artifacts import parse_findings
    from scrutare.replay.models import ReplayError

    group = merged_groups()[0].to_dict()
    del group[field]
    with pytest.raises(ReplayError):
        parse_findings([group])


@pytest.mark.parametrize("field", ["file", "line", "side", "category", "problem", "reason",
                                  "persona"])
def test_findings_require_all_established_source_fields(field):
    from scrutare.replay.artifacts import parse_findings
    from scrutare.replay.models import ReplayError

    group = merged_groups()[0].to_dict()
    del group["sources"][0][field]
    with pytest.raises(ReplayError):
        parse_findings([group])


@pytest.mark.parametrize("field,value", [
    ("file", "../secret"), ("line", True), ("line", 0), ("side", "secret-side"),
    ("problem", " "), ("categories", "security"), ("categories", ["secret-category"]),
    ("personas", [True]), ("reasons", [None]), ("sources", []), ("sources", {}),
])
def test_findings_reject_invalid_group_values_without_exposing_them(field, value):
    from scrutare.replay.artifacts import parse_findings
    from scrutare.replay.models import ReplayError

    group = merged_groups()[0].to_dict()
    group[field] = value
    with pytest.raises(ReplayError) as error:
        parse_findings([group])
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("field,value", [
    ("file", "../secret"), ("file", "other.py"), ("line", True), ("line", 3),
    ("side", "RIGHT"), ("category", "secret-category"), ("problem", "different secret"),
    ("reason", " "), ("persona", False), ("persona", ""),
])
def test_findings_reject_invalid_or_mismatched_sources(field, value):
    from scrutare.replay.artifacts import parse_findings
    from scrutare.replay.models import ReplayError

    group = deepcopy(merged_groups()[0].to_dict())
    group["sources"][0][field] = value
    with pytest.raises(ReplayError) as error:
        parse_findings([group])
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("location", ["group", "source"])
def test_findings_reject_unknown_fields_with_safe_diagnostics(location):
    from scrutare.replay.artifacts import parse_findings
    from scrutare.replay.models import ReplayError

    group = merged_groups()[0].to_dict()
    target = group if location == "group" else group["sources"][0]
    target["secret-field"] = "secret-value"
    with pytest.raises(ReplayError) as error:
        parse_findings([group])
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("value", [None, {}, "secret", [None], [True]])
def test_findings_require_a_bare_array_of_groups(value):
    from scrutare.replay.artifacts import parse_findings
    from scrutare.replay.models import ReplayError

    with pytest.raises(ReplayError) as error:
        parse_findings(value)
    assert "secret" not in str(error.value)


def test_decoder_accepts_utf8_text_and_supplementary_unicode():
    from scrutare.replay.artifacts import decode_artifact

    assert decode_artifact('{"café":"🔒"}'.encode(), artifact="findings.json") == {"café": "🔒"}
    assert decode_artifact(b'"\\ud83d\\udd12"', artifact="findings.json") == "🔒"


@pytest.mark.parametrize("data", [
    b'{"secret":1,"secret":2}', b'{"ok":{"secret":1,"secret":2}}',
    b'{"secret":NaN}', b'[Infinity]', b'[-Infinity]', b'"\\ud800"', b'"\\udfff"',
    b'{"\\ud800":1}', b'"\xff"', b'{secret', b'{} trailing-secret', b'\xef\xbb\xbf{}',
    b'["\\ud800", "secret"]', b'[1e999]', b'[-1e999]',
])
def test_decoder_rejects_ambiguous_or_unencodable_json_safely(data):
    from scrutare.replay.artifacts import decode_artifact
    from scrutare.replay.models import ReplayError

    with pytest.raises(ReplayError) as error:
        decode_artifact(data, artifact="findings.json")
    assert "findings.json" in str(error.value)
    assert "secret" not in str(error.value)
    assert error.value.__suppress_context__


def test_decoder_reports_deep_nesting_safely():
    from scrutare.replay.artifacts import decode_artifact
    from scrutare.replay.models import ReplayError

    with pytest.raises(ReplayError) as error:
        decode_artifact(b"[" * 5000 + b"0" + b"]" * 5000, artifact="findings.json")
    assert "findings.json" in str(error.value)


def test_decoder_depth_check_ignores_quoted_and_escaped_delimiters():
    from scrutare.replay.artifacts import decode_artifact

    text = '[{\\"' * 5000 + '}]"' * 5000
    assert decode_artifact(json.dumps({"problem": text}).encode(), artifact="findings.json") == {
        "problem": text,
    }


def test_decoder_rejects_deep_objects_after_escaped_quotes_safely():
    from scrutare.replay.artifacts import decode_artifact
    from scrutare.replay.models import ReplayError

    data = b'{"text":"escaped \\" quote","nested":' * 5000 + b'0' + b'}' * 5000
    with pytest.raises(ReplayError):
        decode_artifact(data, artifact="config.json")


def test_decoder_diagnostics_never_echo_untrusted_artifact_label():
    from scrutare.replay.artifacts import decode_artifact
    from scrutare.replay.models import ReplayError

    with pytest.raises(ReplayError) as error:
        decode_artifact(b"secret-invalid", artifact="secret/path")
    assert "secret" not in str(error.value)


def test_reader_returns_exact_regular_file_bytes(tmp_path):
    from scrutare.replay.artifacts import read_artifact

    path = tmp_path / "findings.json"
    path.write_bytes(b"\xff arbitrary bytes\n")
    assert read_artifact(path) == b"\xff arbitrary bytes\n"


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "dangling", "fifo"])
def test_reader_rejects_nonregular_files_and_symlinks_safely(tmp_path, kind):
    from scrutare.replay.artifacts import read_artifact
    from scrutare.replay.models import ReplayError

    path = tmp_path / "secret-artifact"
    if kind == "directory":
        path.mkdir()
    elif kind in ("symlink", "dangling"):
        target = tmp_path / "secret-target"
        if kind == "symlink":
            target.write_bytes(b"secret-value")
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    with pytest.raises(ReplayError) as error:
        read_artifact(path)
    assert "secret" not in str(error.value)
    assert error.value.__suppress_context__


def policy_document():
    return {
        "strategy": "debate", "rounds": {"max": 3},
        "verdict": {
            "blocking_categories": ["regression", "security", "correctness"],
            "advisory_categories": ["docs", "consistency", "style"],
        },
        "models": {"default": {"provider": "obsolete", "model": "unavailable"}},
        "future-option": {"secret": "ignored unrelated data"},
    }


def test_policy_projects_complete_partition_and_preserves_raw_capture():
    from scrutare.replay.artifacts import parse_policy

    document = policy_document()
    policy = parse_policy(document)
    assert policy.settings == VerdictSettings()
    assert policy.strategy == "debate"
    assert policy.round_limit == 3
    assert policy.document == document
    assert policy.document["verdict"]["blocking_categories"] == [
        "regression", "security", "correctness",
    ]


def test_policy_bound_fields_are_optional_without_inventing_defaults():
    from scrutare.replay.artifacts import parse_policy

    policy = parse_policy({"verdict": policy_document()["verdict"]})
    assert policy.strategy is None
    assert policy.round_limit is None
    assert policy.settings == VerdictSettings()


@pytest.mark.parametrize("field", ["blocking_categories", "advisory_categories"])
def test_policy_requires_both_captured_category_arrays(field):
    from scrutare.replay.artifacts import parse_policy
    from scrutare.replay.models import ReplayError

    document = policy_document()
    del document["verdict"][field]
    with pytest.raises(ReplayError):
        parse_policy(document)


@pytest.mark.parametrize("field,value", [
    ("strategy", "secret-strategy"), ("strategy", None), ("strategy", True),
    ("rounds", {"max": True}), ("rounds", {"max": 0}), ("rounds", {"max": 3.0}),
    ("rounds", {"max": "secret-bound"}), ("rounds", {"max": None}), ("rounds", []),
    ("verdict", {}), ("verdict", None),
])
def test_policy_rejects_invalid_deciding_fields_safely(field, value):
    from scrutare.replay.artifacts import parse_policy
    from scrutare.replay.models import ReplayError

    document = policy_document()
    document[field] = value
    with pytest.raises(ReplayError) as error:
        parse_policy(document)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("blocking,advisory", [
    (["security"], ["docs"]),
    (["security", "security", "correctness", "regression"], ["docs", "style", "consistency"]),
    (["security", "correctness", "regression"], ["security", "docs", "style", "consistency"]),
    (["secret-category"], ["docs", "style", "consistency"]),
    ("security", ["docs", "style", "consistency"]),
    ([True], ["docs", "style", "consistency"]),
])
def test_policy_uses_verdict_settings_validation(blocking, advisory):
    from scrutare.replay.artifacts import parse_policy
    from scrutare.replay.models import ReplayError

    with pytest.raises(ReplayError) as error:
        parse_policy({"verdict": {"blocking_categories": blocking,
                                 "advisory_categories": advisory}})
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("data", [None, [], True, "secret", {}])
def test_policy_requires_captured_document_and_verdict_partition(data):
    from scrutare.replay.artifacts import parse_policy
    from scrutare.replay.models import ReplayError

    with pytest.raises(ReplayError) as error:
        parse_policy(data)
    assert "secret" not in str(error.value)


def saved_document(*, exhausted=False):
    exhaustion = Exhaustion("debate", 3, 3) if exhausted else None
    return derive_verdict(merged_groups(), VerdictSettings(), exhaustion=exhaustion).to_dict()


def saved_bytes(document):
    return json.dumps(document, ensure_ascii=False).encode()


@pytest.mark.parametrize("exhausted", [False, True])
def test_saved_verdict_retains_original_bytes_and_validated_document(exhausted):
    from scrutare.replay.artifacts import parse_saved_verdict

    document = saved_document(exhausted=exhausted)
    raw = saved_bytes(document) + b"  \n"
    saved = parse_saved_verdict(raw)
    assert saved.raw == raw
    assert saved.document == document
    assert saved.exhaustion == (Exhaustion("debate", 3, 3) if exhausted else None)


def test_saved_verdict_retains_stale_classification_summary_and_policy_fields():
    from scrutare.replay.artifacts import parse_saved_verdict

    document = saved_document()
    document.update(verdict="approve", rule="no_blocking_findings")
    document["config"] = {
        "blocking_categories": ["correctness", "regression"],
        "advisory_categories": ["security", "style", "consistency", "docs"],
    }
    document["findings"][0].update(blocking=False, blocking_categories=["correctness"],
                                   categories=["docs"], personas=["stale"], reasons=["stale"])
    saved = parse_saved_verdict(saved_bytes(document))
    assert saved.document == document
    assert saved.document["findings"][0]["blocking"] is False
    assert saved.exhaustion is None


def test_explicit_exhaustion_is_retained_even_when_recorded_classification_differs():
    from scrutare.replay.artifacts import parse_policy, parse_saved_verdict, validate_exhaustion

    document = saved_document(exhausted=True)
    document.update(verdict="approve", rule="no_blocking_findings")
    saved = parse_saved_verdict(saved_bytes(document))
    assert saved.document["verdict"] == "approve"
    assert validate_exhaustion(saved, parse_policy(policy_document())) == Exhaustion("debate", 3, 3)


@pytest.mark.parametrize("schema", [True, False, 1.0, "1", 0, 2, None])
def test_saved_verdict_requires_schema_exact_actual_integer_one(schema):
    from scrutare.replay.artifacts import parse_saved_verdict
    from scrutare.replay.models import ReplayError

    document = saved_document()
    document["schema_version"] = schema
    with pytest.raises(ReplayError):
        parse_saved_verdict(saved_bytes(document))


@pytest.mark.parametrize("field", ["schema_version", "verdict", "rule", "config", "findings"])
def test_saved_verdict_requires_all_normal_fields(field):
    from scrutare.replay.artifacts import parse_saved_verdict
    from scrutare.replay.models import ReplayError

    document = saved_document()
    del document[field]
    with pytest.raises(ReplayError):
        parse_saved_verdict(saved_bytes(document))


@pytest.mark.parametrize("field,value", [
    ("verdict", "secret-status"), ("verdict", None), ("rule", "secret-rule"), ("rule", True),
    ("config", {}), ("config", None), ("findings", {}), ("findings", None),
    ("exhaustion", None),
])
def test_saved_verdict_rejects_unknown_or_invalid_shape_safely(field, value):
    from scrutare.replay.artifacts import parse_saved_verdict
    from scrutare.replay.models import ReplayError

    document = saved_document()
    document[field] = value
    with pytest.raises(ReplayError) as error:
        parse_saved_verdict(saved_bytes(document))
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("location", ["document", "config", "finding", "source", "exhaustion"])
def test_saved_verdict_rejects_unknown_fields_at_every_established_level(location):
    from scrutare.replay.artifacts import parse_saved_verdict
    from scrutare.replay.models import ReplayError

    document = saved_document(exhausted=True)
    target = {
        "document": document, "config": document["config"],
        "finding": document["findings"][0], "source": document["findings"][0]["sources"][0],
        "exhaustion": document["exhaustion"],
    }[location]
    target["secret-field"] = "secret-value"
    with pytest.raises(ReplayError) as error:
        parse_saved_verdict(saved_bytes(document))
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("field", ["blocking", "blocking_categories"])
def test_saved_verdict_requires_recorded_group_classification_fields(field):
    from scrutare.replay.artifacts import parse_saved_verdict
    from scrutare.replay.models import ReplayError

    document = saved_document()
    del document["findings"][0][field]
    with pytest.raises(ReplayError):
        parse_saved_verdict(saved_bytes(document))


@pytest.mark.parametrize("field,value", [
    ("blocking", 1), ("blocking", "secret"), ("blocking_categories", "security"),
    ("blocking_categories", ["secret-category"]), ("sources", []), ("line", True),
])
def test_saved_verdict_rejects_malformed_recorded_findings(field, value):
    from scrutare.replay.artifacts import parse_saved_verdict
    from scrutare.replay.models import ReplayError

    document = saved_document()
    document["findings"][0][field] = value
    with pytest.raises(ReplayError) as error:
        parse_saved_verdict(saved_bytes(document))
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("field", ["strategy", "rounds_completed", "round_limit", "converged"])
def test_saved_exhaustion_requires_all_four_explicit_fields(field):
    from scrutare.replay.artifacts import parse_saved_verdict
    from scrutare.replay.models import ReplayError

    document = saved_document(exhausted=True)
    del document["exhaustion"][field]
    with pytest.raises(ReplayError):
        parse_saved_verdict(saved_bytes(document))


@pytest.mark.parametrize("field,value", [
    ("strategy", "secret"), ("rounds_completed", True), ("rounds_completed", 2),
    ("rounds_completed", 4), ("rounds_completed", 3.0), ("round_limit", True),
    ("round_limit", 0), ("round_limit", 3.0), ("converged", True), ("converged", 0),
    ("converged", None),
])
def test_saved_exhaustion_requires_false_and_exact_positive_bound(field, value):
    from scrutare.replay.artifacts import parse_saved_verdict
    from scrutare.replay.models import ReplayError

    document = saved_document(exhausted=True)
    document["exhaustion"][field] = value
    with pytest.raises(ReplayError) as error:
        parse_saved_verdict(saved_bytes(document))
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("field,value", [
    ("verdict", "escalated"), ("rule", "rounds_exhausted_without_convergence"),
])
def test_saved_escalated_classification_never_invents_missing_exhaustion(field, value):
    from scrutare.replay.artifacts import parse_saved_verdict
    from scrutare.replay.models import ReplayError

    document = saved_document()
    document[field] = value
    with pytest.raises(ReplayError, match="exhaustion"):
        parse_saved_verdict(saved_bytes(document))


@pytest.mark.parametrize("change", ["missing-strategy", "missing-rounds", "strategy", "bound"])
def test_exhaustion_must_match_captured_strategy_and_bound(change):
    from scrutare.replay.artifacts import parse_policy, parse_saved_verdict, validate_exhaustion
    from scrutare.replay.models import ReplayError

    document = policy_document()
    if change == "missing-strategy":
        del document["strategy"]
    elif change == "missing-rounds":
        del document["rounds"]
    elif change == "strategy":
        document["strategy"] = "panel"
    else:
        document["rounds"]["max"] = 4
    saved = parse_saved_verdict(saved_bytes(saved_document(exhausted=True)))
    with pytest.raises(ReplayError):
        validate_exhaustion(saved, parse_policy(document))


def test_normal_saved_verdict_requires_no_bound_evidence():
    from scrutare.replay.artifacts import parse_policy, parse_saved_verdict, validate_exhaustion

    saved = parse_saved_verdict(saved_bytes(saved_document()))
    policy = parse_policy({"verdict": policy_document()["verdict"]})
    assert validate_exhaustion(saved, policy) is None
