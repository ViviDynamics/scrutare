"""Finding boundaries reject untrusted authority and invalid typed values."""

from collections import UserDict
from dataclasses import FrozenInstanceError

import pytest

from scrutare.config import CATEGORIES


def wire(**changes):
    return {
        "file": "src/main.py",
        "line": 7,
        "category": "correctness",
        "problem": "  Wrong result\n",
        "reason": "Fails for empty input",
        **changes,
    }


def test_wire_parses_all_categories_preserving_text_and_owned_persona():
    from scrutare.findings import Anchor, Finding, parse_finding

    for category in CATEGORIES:
        finding = parse_finding(UserDict(wire(category=category)), persona="security")
        assert finding == Finding(
            Anchor("src/main.py", 7), category, "  Wrong result\n",
            "Fails for empty input", "security",
        )
        assert hash(finding)


def test_explicit_left_side_and_frozen_types():
    from scrutare.findings import Anchor, parse_finding

    finding = parse_finding(wire(side="LEFT"), persona="custom")
    assert finding.anchor == Anchor("src/main.py", 7, "LEFT")
    with pytest.raises(FrozenInstanceError):
        finding.problem = "changed"
    with pytest.raises(FrozenInstanceError):
        finding.anchor.line = 10


@pytest.mark.parametrize("field,value", [
    ("file", ""), ("file", "/absolute"), ("file", "../escape"),
    ("file", "src/../escape"), ("file", "./main.py"), ("file", "src//main.py"),
    ("file", "src/"), ("file", "bad\x00name"), ("file", 42),
    ("line", True), ("line", False), ("line", 0), ("line", -1),
    ("line", 1.0), ("line", "1"), ("line", None),
    ("side", "left"), ("side", "BOTH"), ("side", None),
    ("category", "approval"), ("category", ""), ("category", []),
    ("problem", ""), ("problem", "  \n"), ("problem", 1),
    ("reason", None), ("reason", "\t"),
])
def test_invalid_wire_fields_have_safe_field_diagnostics(field, value):
    from scrutare.findings import FindingError, parse_finding

    with pytest.raises(FindingError, match=field):
        parse_finding(wire(**{field: value}), persona="security")


@pytest.mark.parametrize("field", ["persona", "severity", "approval", "verdict", "secret\nkey"])
def test_extra_wire_fields_are_rejected_without_echoing_untrusted_names(field):
    from scrutare.findings import FindingError, parse_finding

    with pytest.raises(FindingError, match="field") as error:
        parse_finding(wire(**{field: "sensitive-secret"}), persona="security")
    assert "sensitive-secret" not in str(error.value)
    assert "secret\nkey" not in str(error.value)


@pytest.mark.parametrize("field", ["file", "line", "category", "problem", "reason"])
def test_required_wire_fields(field):
    from scrutare.findings import FindingError, parse_finding

    data = wire()
    del data[field]
    with pytest.raises(FindingError, match=field):
        parse_finding(data, persona="security")


@pytest.mark.parametrize("value", [[], None, "not a mapping", {1: "value"}])
def test_invalid_wire_shape(value):
    from scrutare.findings import FindingError, parse_finding

    with pytest.raises(FindingError):
        parse_finding(value, persona="security")


@pytest.mark.parametrize("persona", ["", "  ", 12, None])
def test_caller_persona_is_validated(persona):
    from scrutare.findings import FindingError, parse_finding

    with pytest.raises(FindingError, match="persona"):
        parse_finding(wire(), persona=persona)


@pytest.mark.parametrize("changes", [
    {"file": "../escape"}, {"line": True}, {"line": 0}, {"side": "bad"},
])
def test_direct_anchor_construction_validates(changes):
    from scrutare.findings import Anchor, FindingError

    with pytest.raises(FindingError):
        Anchor(**{"file": "main.py", "line": 1, **changes})


@pytest.mark.parametrize("changes", [
    {"anchor": "main.py"}, {"category": "approval"}, {"category": []},
    {"problem": ""}, {"reason": None}, {"persona": " "},
])
def test_direct_finding_construction_validates(changes):
    from scrutare.findings import Anchor, Finding, FindingError

    with pytest.raises(FindingError):
        Finding(**{
            "anchor": Anchor("main.py", 1), "category": "security",
            "problem": "Issue", "reason": "Explanation", "persona": "security",
            **changes,
        })


@pytest.mark.parametrize("path", ["space name.py", "tab\tname.py", "line\nname.py",
                                  "back\\slash.py", "café.py", "C:\\file.py"])
def test_posix_relative_filename_characters_are_preserved(path):
    from scrutare.findings import Anchor, parse_finding

    assert parse_finding(wire(file=path), persona="security").anchor == Anchor(path, 7)
