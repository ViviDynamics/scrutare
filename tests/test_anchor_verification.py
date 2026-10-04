"""Pure verification captures the diff and permits one supplied correction round."""

from dataclasses import FrozenInstanceError

import pytest

from scrutare.findings import Anchor, Finding, FindingError


def finding(anchor, *, problem="Wrong result", persona="security", category="security"):
    return Finding(anchor, category, problem, "Fails for empty input", persona)


def test_checks_exact_file_line_and_side_against_captured_index():
    from scrutare.findings import check_anchors

    valid = finding(Anchor("src/main.py", 7, "LEFT"))
    wrong_file = finding(Anchor("missing.py", 7, "LEFT"))
    wrong_line = finding(Anchor("src/main.py", 8, "LEFT"))
    wrong_side = finding(Anchor("src/main.py", 7))
    anchors = frozenset({valid.anchor})
    check = check_anchors(iter((wrong_file, valid, wrong_line, wrong_side)), anchors)
    assert check.anchors is anchors
    assert check.accepted == (valid,)
    assert check.reanchor_requests == (wrong_file, wrong_line, wrong_side)
    assert check.findings == (wrong_file, valid, wrong_line, wrong_side)


def test_corrected_findings_preserve_evidence_and_original_iteration_order():
    from scrutare.findings import ReanchorCorrection, check_anchors, finish_reanchor

    first = finding(Anchor("old.py", 1), category="correctness", persona="senior-dev")
    valid = finding(Anchor("main.py", 4))
    last = finding(Anchor("missing.py", 2), problem="  Another issue\n", persona="custom")
    anchors = frozenset({valid.anchor, Anchor("main.py", 6, "LEFT")})
    check = check_anchors((first, valid, last), anchors)
    result = finish_reanchor(check, iter((
        ReanchorCorrection(last, Anchor("main.py", 6, "LEFT")),
        ReanchorCorrection(first, valid.anchor),
    )))
    assert result.accepted == (
        Finding(valid.anchor, "correctness", "Wrong result", "Fails for empty input", "senior-dev"),
        valid,
        Finding(Anchor("main.py", 6, "LEFT"), "security", "  Another issue\n",
                "Fails for empty input", "custom"),
    )
    assert result.dropped == ()


def test_missing_and_still_invalid_corrections_are_terminal_auditable_drops():
    from scrutare.findings import ReanchorCorrection, check_anchors, finish_reanchor

    missing = finding(Anchor("private\nfile.py", 9), problem="sensitive-secret")
    invalid = finding(Anchor("old.py", 2))
    check = check_anchors((missing, invalid), frozenset({Anchor("main.py", 4)}))
    result = finish_reanchor(check, (ReanchorCorrection(invalid, Anchor("main.py", 99)),))
    assert tuple(drop.original for drop in result.dropped) == (missing, invalid)
    assert tuple(drop.reason for drop in result.dropped) == (
        "missing_correction", "invalid_correction",
    )
    assert result.accepted == ()
    assert not hasattr(result, "reanchor_requests")
    assert all("sensitive-secret" not in drop.reason for drop in result.dropped)
    with pytest.raises(FindingError, match="check"):
        finish_reanchor(result)


def test_no_corrections_drops_requested_findings_and_keeps_valid_findings():
    from scrutare.findings import check_anchors, finish_reanchor

    invalid = finding(Anchor("main.py", 9))
    valid = finding(Anchor("main.py", 4))
    result = finish_reanchor(check_anchors((invalid, valid), frozenset({valid.anchor})))
    assert result.accepted == (valid,)
    assert tuple(drop.original for drop in result.dropped) == (invalid,)
    assert result.dropped[0].reason == "missing_correction"


@pytest.mark.parametrize("original_kind", ["accepted", "unrequested", "changed_evidence"])
def test_corrections_only_name_requested_unchanged_originals(original_kind):
    from scrutare.findings import ReanchorCorrection, check_anchors, finish_reanchor

    valid = finding(Anchor("main.py", 4))
    invalid = finding(Anchor("main.py", 9))
    original = {
        "accepted": valid,
        "unrequested": finding(Anchor("other.py", 1)),
        "changed_evidence": finding(invalid.anchor, problem="sensitive-secret"),
    }[original_kind]
    check = check_anchors((valid, invalid), frozenset({valid.anchor}))
    with pytest.raises(FindingError, match="original") as error:
        finish_reanchor(check, (ReanchorCorrection(original, valid.anchor),))
    assert "sensitive-secret" not in str(error.value)


@pytest.mark.parametrize("second_anchor", [Anchor("main.py", 4), Anchor("main.py", 5)])
def test_multiple_corrections_for_equal_originals_are_rejected(second_anchor):
    from scrutare.findings import ReanchorCorrection, check_anchors, finish_reanchor

    invalid = finding(Anchor("old.py", 9))
    equal_original = finding(Anchor("old.py", 9))
    check = check_anchors((invalid, equal_original), frozenset({Anchor("main.py", 4)}))
    with pytest.raises(FindingError, match="correction"):
        finish_reanchor(check, (
            ReanchorCorrection(invalid, Anchor("main.py", 4)),
            ReanchorCorrection(equal_original, second_anchor),
        ))


def test_duplicate_originals_share_one_request_and_preserve_corrected_occurrences():
    from scrutare.findings import ReanchorCorrection, check_anchors, finish_reanchor

    invalid = finding(Anchor("old.py", 9))
    valid = finding(Anchor("main.py", 4), problem="Other issue")
    equal_original = finding(Anchor("old.py", 9))
    check = check_anchors((invalid, valid, equal_original, valid), frozenset({valid.anchor}))
    assert check.accepted == (valid, valid)
    assert check.reanchor_requests == (invalid,)
    corrected = finding(valid.anchor)
    result = finish_reanchor(check, (ReanchorCorrection(equal_original, valid.anchor),))
    assert result.accepted == (corrected, valid, corrected, valid)
    assert result.dropped == ()
    dropped = finish_reanchor(check)
    assert tuple(drop.original for drop in dropped.dropped) == (invalid, equal_original)


def test_distinct_personas_require_distinct_corrections():
    from scrutare.findings import ReanchorCorrection, check_anchors, finish_reanchor

    first = finding(Anchor("old.py", 9))
    second = finding(first.anchor, persona="devops")
    anchor = Anchor("main.py", 4)
    check = check_anchors((first, second), frozenset({anchor}))
    assert check.reanchor_requests == (first, second)
    result = finish_reanchor(check, (ReanchorCorrection(first, anchor),))
    assert result.accepted == (finding(anchor),)
    assert tuple(drop.original for drop in result.dropped) == (second,)


def test_correction_is_validated_against_original_diff_without_an_index_argument():
    from scrutare.findings import ReanchorCorrection, check_anchors, finish_reanchor

    invalid = finding(Anchor("old.py", 9))
    check = check_anchors((invalid,), frozenset({Anchor("main.py", 4)}))
    other_diff_anchor = Anchor("different.py", 4)
    result = finish_reanchor(check, (ReanchorCorrection(invalid, other_diff_anchor),))
    assert result.accepted == ()
    assert result.dropped[0].reason == "invalid_correction"


def test_empty_generators_are_supported():
    from scrutare.findings import check_anchors, finish_reanchor

    check = check_anchors(iter(()), frozenset())
    assert check.accepted == check.reanchor_requests == check.findings == ()
    result = finish_reanchor(check, iter(()))
    assert result.accepted == result.dropped == ()


def test_records_and_their_containers_are_immutable():
    from scrutare.findings import ReanchorCorrection, check_anchors, finish_reanchor

    invalid = finding(Anchor("old.py", 9))
    check = check_anchors((invalid,), frozenset())
    correction = ReanchorCorrection(invalid, Anchor("main.py", 4))
    result = finish_reanchor(check, (correction,))
    for record, field, replacement in (
        (check, "anchors", frozenset({correction.anchor})),
        (check, "findings", ()),
        (correction, "anchor", invalid.anchor),
        (result, "accepted", (invalid,)),
        (result.dropped[0], "reason", "changed"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, replacement)
    assert hash(check) and hash(correction) and hash(result)


@pytest.mark.parametrize("anchors", [{Anchor("main.py", 4)}, [Anchor("main.py", 4)],
                                    frozenset({"sensitive-secret"})])
def test_check_rejects_mutable_or_untyped_anchor_indexes(anchors):
    from scrutare.findings import check_anchors

    with pytest.raises(FindingError, match="anchors") as error:
        check_anchors((), anchors)
    assert "sensitive-secret" not in str(error.value)


def test_check_rejects_nonfinding_items_without_echoing_them():
    from scrutare.findings import check_anchors

    with pytest.raises(FindingError, match="findings") as error:
        check_anchors(iter(("sensitive-secret",)), frozenset())
    assert "sensitive-secret" not in str(error.value)


@pytest.mark.parametrize("changes", [
    {"original": "sensitive-secret"}, {"anchor": "sensitive-secret"},
])
def test_direct_correction_constructor_validates_fields(changes):
    from scrutare.findings import ReanchorCorrection

    with pytest.raises(FindingError) as error:
        ReanchorCorrection(**{
            "original": finding(Anchor("old.py", 9)), "anchor": Anchor("main.py", 4),
            **changes,
        })
    assert "sensitive-secret" not in str(error.value)


@pytest.mark.parametrize("changes", [
    {"anchors": set()}, {"anchors": frozenset({"secret"})},
    {"accepted": []}, {"accepted": ("secret",)}, {"reanchor_requests": []},
    {"findings": []}, {"findings": ("secret",)},
    {"accepted": (finding(Anchor("main.py", 4)),)},
    {"reanchor_requests": (finding(Anchor("main.py", 4)),)},
    {"findings": (finding(Anchor("main.py", 4)),)},
])
def test_direct_check_constructor_rejects_mutable_untyped_or_inconsistent_state(changes):
    from scrutare.findings import AnchorCheck

    with pytest.raises(FindingError):
        AnchorCheck(**{
            "anchors": frozenset(), "accepted": (), "reanchor_requests": (), "findings": (),
            **changes,
        })


@pytest.mark.parametrize("changes", [
    {"accepted": []}, {"accepted": ("secret",)}, {"dropped": []}, {"dropped": ("secret",)},
])
def test_direct_terminal_result_constructor_rejects_mutable_or_untyped_state(changes):
    from scrutare.findings import VerificationResult

    with pytest.raises(FindingError):
        VerificationResult(**{"accepted": (), "dropped": (), **changes})


@pytest.mark.parametrize("changes", [
    {"original": "sensitive-secret"}, {"reason": "sensitive-secret"},
])
def test_direct_drop_constructor_validates_original_and_safe_reason(changes):
    from scrutare.findings import DroppedFinding

    with pytest.raises(FindingError) as error:
        DroppedFinding(**{
            "original": finding(Anchor("old.py", 9)), "reason": "missing_correction", **changes,
        })
    assert "sensitive-secret" not in str(error.value)


def test_finish_rejects_untyped_corrections():
    from scrutare.findings import check_anchors, finish_reanchor

    with pytest.raises(FindingError, match="corrections") as error:
        finish_reanchor(check_anchors((), frozenset()), iter(("sensitive-secret",)))
    assert "sensitive-secret" not in str(error.value)
