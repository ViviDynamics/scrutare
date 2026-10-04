"""Section identity and exact source bytes share the anchor parser."""

from dataclasses import FrozenInstanceError

import pytest

from scrutare.findings import Anchor, FindingError, parse_diff


@pytest.mark.parametrize("data,old,new,want", [
    (b"diff --git a/main.py b/main.py\r\n--- a/main.py\r\n+++ b/main.py\r\n"
     b"@@ -1 +1 @@\r\n-old\r\n\\ No newline at end of file\r\n+new\r\n"
     b"\\ No newline at end of file", "main.py", "main.py",
     frozenset({Anchor("main.py", 1, "LEFT"), Anchor("main.py", 1, "RIGHT")})),
    (b"diff --git a/space b/part.py b/space b/part.py\n--- a/space b/part.py\t\n"
     b"+++ b/space b/part.py\t\n@@ -1 +1 @@\n x\n", "space b/part.py", "space b/part.py",
     frozenset({Anchor("space b/part.py", 1, "LEFT"), Anchor("space b/part.py", 1, "RIGHT")})),
    (b'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
     b'--- "a/caf\\303\\251.py"\n+++ "b/caf\\303\\251.py"\n@@ -1 +1 @@\n x\n',
     "café.py", "café.py", frozenset({Anchor("café.py", 1, "LEFT"),
                                      Anchor("café.py", 1, "RIGHT")})),
    (b"diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n"
     b"+++ b/new.py\n@@ -0,0 +1 @@\n+new\n", None, "new.py",
     frozenset({Anchor("new.py", 1, "RIGHT")})),
    (b"diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n"
     b"+++ /dev/null\n@@ -1 +0,0 @@\n-old\n", "old.py", None,
     frozenset({Anchor("old.py", 1, "LEFT")})),
    (b"diff --git a/old.py b/new.py\nsimilarity index 70%\nrename from old.py\n"
     b"rename to new.py\n--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-old\n+new\n",
     "old.py", "new.py", frozenset({Anchor("new.py", 1, "LEFT"),
                                     Anchor("new.py", 1, "RIGHT")})),
    (b"diff --git a/old.py b/new.py\nsimilarity index 100%\ncopy from old.py\n"
     b"copy to new.py\n", "old.py", "new.py", frozenset()),
    (b"diff --git a/old b/new\nsimilarity index 100%\nrename from old\nrename to new\n",
     "old", "new", frozenset()),
    (b"diff --git a/a b/a\nold mode 100644\nnew mode 100755\n", "a", "a", frozenset()),
    (b"diff --git a/empty b/empty\nnew file mode 100644\nindex 000..abc\n",
     None, "empty", frozenset()),
    (b"diff --git a/empty b/empty\ndeleted file mode 100644\nindex abc..000\n",
     "empty", None, frozenset()),
    (b"diff --git a/img b/img\nindex abc..def 100644\n"
     b"Binary files a/img and b/img differ\n", "img", "img", frozenset()),
    (b"diff --git a/img b/img\nnew file mode 100644\n"
     b"Binary files /dev/null and b/img differ\n", None, "img", frozenset()),
    (b"diff --git a/img b/img\ndeleted file mode 100644\n"
     b"Binary files a/img and /dev/null differ\n", "img", None, frozenset()),
    (b"diff --git a/img b/img\nGIT binary patch\nliteral 1\nAc${Nk\n\n",
     "img", "img", frozenset()),
])
def test_sections_keep_exact_bytes_identity_and_anchors(data, old, new, want):
    from scrutare.findings import parse_diff_sections

    sections = parse_diff_sections(data)
    assert len(sections) == 1
    section = sections[0]
    assert (section.old_path, section.new_path, section.file, section.data, section.anchors) == (
        old, new, new or old, data, want,
    )
    assert parse_diff(data) == want


def test_section_order_and_line_endings_are_preserved_for_text_and_bytes():
    from scrutare.findings import parse_diff_sections

    first = b"diff --git a/b b/b\r\nold mode 100644\r\nnew mode 100755\r\n"
    second = b"diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n x"
    for data in (first + second, (first + second).decode()):
        sections = parse_diff_sections(data)
        assert tuple(section.file for section in sections) == ("b", "a")
        assert tuple(section.data for section in sections) == (first, second)
        assert parse_diff(data) == frozenset({Anchor("a", 1, "LEFT"), Anchor("a", 1, "RIGHT")})
        with pytest.raises(FrozenInstanceError):
            sections[0].new_path = "other"


@pytest.mark.parametrize("data", [b"", ""])
def test_empty_diff_has_no_sections(data):
    from scrutare.findings import parse_diff_sections

    assert parse_diff_sections(data) == ()


@pytest.mark.parametrize("data", [
    b"diff --git a/space b/part b/space b/part\nold mode 100644\nnew mode 100755\n",
    b"diff --git a/a b/b\nrename from a\nrename to other\n",
    b"diff --git a/a b/b\nrename from a\n",
    b"diff --git a/a b/b\nrename from a\ncopy to b\n",
    b"diff --git a/a b/b\nrename from a\nrename from a\nrename to b\n",
    b"diff --git a/a b/a\nnew file mode 100644\ndeleted file mode 100644\n",
    b"diff --git a/a b/a\nnew file mode 100644\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n x\n",
    b"diff --git a/a b/a\ndeleted file mode 100644\n"
    b"Binary files a/a and b/a differ\n",
    b"diff --git a/space b/part b/space b/part\n"
    b"rename from space b/part\nrename to space b/part\n--- a/space\n"
    b"+++ b/part b/space b/part\n@@ -1 +1 @@\n x\n",
    b"arbitrary text\n", b"diff --cc a\n", b"\xff", 1,
])
def test_malformed_or_ambiguous_sections_fail_closed(data):
    from scrutare.findings import parse_diff_sections

    with pytest.raises(FindingError):
        parse_diff_sections(data)


@pytest.mark.parametrize("old,new,data,anchors", [
    (None, None, b"", frozenset()), ("../a", "a", b"", frozenset()),
    ("a", "a", "", frozenset()), ("a", "a", b"", set()),
    ("a", "a", b"", frozenset({1})),
    ("a", "a", b"", frozenset({Anchor("other", 1)})),
])
def test_direct_section_constructor_validates_immutable_typed_data(old, new, data, anchors):
    from scrutare.findings import DiffSection

    with pytest.raises(FindingError):
        DiffSection(old, new, data, anchors)


@pytest.mark.parametrize("data", [
    b"diff --git a/old b/new\nrename from old\nrename to new\n"
    b"--- /dev/null\n+++ b/new\n@@ -0,0 +1 @@\n+new\n",
    b"diff --git a/old b/new\ncopy from old\ncopy to new\n"
    b"Binary files a/old and /dev/null differ\n",
])
def test_movement_metadata_cannot_name_an_absent_side(data):
    from scrutare.findings import parse_diff_sections

    with pytest.raises(FindingError):
        parse_diff_sections(data)


@pytest.mark.parametrize("data,old,new", [
    (b"diff --git a/space b/part b/space b/part\n"
     b"rename from space b/part\nrename to space b/part\n",
     "space b/part", "space b/part"),
    (b'diff --git "a/old\\tname" "b/new\\tname"\n'
     b'rename from "old\\tname"\nrename to "new\\tname"\n',
     "old\tname", "new\tname"),
    (b"diff --git a/space b/part b/space b/part\n"
     b"Binary files a/space b/part and b/space b/part differ\n",
     "space b/part", "space b/part"),
    (b"diff --git a/img b/img\nnew file mode 100644\n"
     b"GIT binary patch\nliteral 1\nAc${Nk\n\n", None, "img"),
    (b"diff --git a/old b/new\ncopy from old\ncopy to new\n"
     b"--- a/old\n+++ b/new\n@@ -1 +1 @@\n x\n", "old", "new"),
])
def test_metadata_and_binary_markers_disambiguate_identity(data, old, new):
    from scrutare.findings import parse_diff_sections

    section, = parse_diff_sections(data)
    assert (section.old_path, section.new_path, section.data) == (old, new, data)


def test_selected_sections_retain_only_selected_exact_bytes():
    from scrutare.config import PathSettings
    from scrutare.engine.paths import parse_changed_files, select_changed_files
    from scrutare.findings import parse_diff_sections

    excluded = b"diff --git a/docs/a b/src/a\nrename from docs/a\nrename to src/a\n"
    first = b"diff --git a/space name b/space name\r\nold mode 100644\r\nnew mode 100755\r\n"
    second = b"diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n"
    second += b"+++ b/new.py\n@@ -0,0 +1 @@\n+code\n\\ No newline at end of file"
    files = parse_changed_files([
        {"filename": "src/a", "previous_filename": "docs/a", "status": "renamed"},
        {"filename": "space name", "status": "modified"},
        {"filename": "new.py", "status": "added"},
    ])
    selected = {file.filename for file in select_changed_files(files, PathSettings())}
    sections = parse_diff_sections(excluded + first + second)
    selected_data = b"".join(section.data for section in sections if section.file in selected)
    assert selected_data == first + second
