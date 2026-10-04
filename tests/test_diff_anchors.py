"""Unified patch anchors use only counted payload on the stated side."""

import pytest


def patch(body, old="main.py", new="main.py"):
    return f"diff --git a/{old} b/{new}\n--- a/{old}\n+++ b/{new}\n{body}"


def anchors(*values):
    from scrutare.findings import Anchor

    return frozenset(Anchor(*value) for value in values)


def test_context_additions_deletions_are_side_aware():
    from scrutare.findings import parse_diff

    data = patch("@@ -4,3 +8,3 @@ function\n context\n-old\n+new\n tail\n")
    assert parse_diff(data) == anchors(
        ("main.py", 4, "LEFT"), ("main.py", 5, "LEFT"), ("main.py", 6, "LEFT"),
        ("main.py", 8, "RIGHT"), ("main.py", 9, "RIGHT"), ("main.py", 10, "RIGHT"),
    )


def test_multiple_files_hunks_bytes_crlf_and_no_newline_markers():
    from scrutare.findings import parse_diff

    data = patch("@@ -1 +1 @@\n-old\n+new\n"
                 "@@ -10,0 +11,2 @@\n+a\n+b\n\\ No newline at end of file\n")
    data += patch("@@ -2,2 +2,0 @@\n-a\n-b\n\\ No newline at end of file\n",
                  "second.py", "second.py")
    assert parse_diff(data.replace("\n", "\r\n").encode()) == anchors(
        ("main.py", 1, "LEFT"), ("main.py", 1, "RIGHT"),
        ("main.py", 11, "RIGHT"), ("main.py", 12, "RIGHT"),
        ("second.py", 2, "LEFT"), ("second.py", 3, "LEFT"),
    )


@pytest.mark.parametrize("data,expected", [
    ("diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n"
     "+++ b/new.py\n@@ -0,0 +1,2 @@\n+a\n+b\n",
     (("new.py", 1, "RIGHT"), ("new.py", 2, "RIGHT"))),
    ("diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n"
     "+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n", (("old.py", 1, "LEFT"),)),
    ("diff --git a/old.py b/new.py\nsimilarity index 70%\nrename from old.py\n"
     "rename to new.py\n--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-old\n+new\n",
     (("new.py", 1, "LEFT"), ("new.py", 1, "RIGHT"))),
])
def test_new_deleted_and_renamed_paths(data, expected):
    from scrutare.findings import parse_diff

    assert parse_diff(data) == anchors(*expected)


@pytest.mark.parametrize("encoded,decoded", [
    ('space name.py', 'space name.py'),
    ('"caf\\303\\251.py"', 'café.py'),
    ('"tab\\tname.py"', 'tab\tname.py'),
    ('"line\\nname.py"', 'line\nname.py'),
    ('"back\\\\slash.py"', 'back\\slash.py'),
    ('"quote\\\"name.py"', 'quote"name.py'),
    ('"bell\\aname.py"', 'bell\aname.py'),
])
def test_git_filename_decoding(encoded, decoded):
    from scrutare.findings import parse_diff

    def prefixed(prefix):
        return f'"{prefix}/{encoded[1:]}' if encoded.startswith('"') else f"{prefix}/{encoded}"

    old, new = prefixed("a"), prefixed("b")
    data = f"diff --git {old} {new}\n--- {old}\n+++ {new}\n@@ -1 +1 @@\n x\n"
    assert parse_diff(data) == anchors((decoded, 1, "LEFT"), (decoded, 1, "RIGHT"))


@pytest.mark.parametrize("data", [
    "", "diff --git a/img.png b/img.png\nindex abc..def 100644\n"
    "Binary files a/img.png and b/img.png differ\n",
    "diff --git a/img.png b/img.png\nGIT binary patch\nliteral 1\nAc${Nk\n\n",
    "diff --git a/a b/a\nold mode 100644\nnew mode 100755\n",
    "diff --git a/old b/new\nsimilarity index 100%\nrename from old\nrename to new\n",
    "diff --git a/empty b/empty\nnew file mode 100644\nindex 000..abc\n",
])
def test_binary_and_metadata_only_changes_have_no_anchors(data):
    from scrutare.findings import parse_diff

    assert parse_diff(data) == frozenset()


@pytest.mark.parametrize("body", [
    "@@ -1,2 +1,2 @@\n x\n",                       # truncated
    "@@ -1 +1 @@\n x\n extra\n",                   # overlong
    "@@ -1 +1 @@\n-a\n",                           # one side truncated
    "@@ -1 +1 @@\n+a\n+b\n",                       # right exceeds count
    "@@ -x +1 @@\n x\n", "@@ -1,-1 +1 @@\n x\n", # malformed counts
    "@@ -0 +1 @@\n x\n",                          # invalid nonempty range
    "@@@ -1,1 -1,1 +1,1 @@@\n x\n",               # combined
    "@@ -1 +1 @@\nx\n",                           # missing payload prefix
    "@@ -1 +1 @@\n\\ No newline at end of file\n x\n",
    "@@ -1 +1 @@\n x\n\\ unexpected marker\n",
    "@@ -1 +1 @@\n x\n\\ No newline at end of file\n\\ No newline at end of file\n",
])
def test_malformed_hunks_fail_closed(body):
    from scrutare.findings import FindingError, parse_diff

    with pytest.raises(FindingError, match="diff"):
        parse_diff(patch(body))


@pytest.mark.parametrize("data", [
    "diff --cc main.py\n", "diff --combined main.py\n",
    "@@ -1 +1 @@\n x\n", "arbitrary text\n",
    "diff --git a/a b/a\n--- a/a\n@@ -1 +1 @@\n x\n",
    "diff --git a/a b/a\n+++ b/a\n@@ -1 +1 @@\n x\n",
    "diff --git a/a b/a\n--- /dev/null\n+++ /dev/null\n@@ -0,0 +0,0 @@\n",
    "diff --git a/a b/a\n--- a/other\n+++ b/a\n@@ -1 +1 @@\n x\n",
    "diff --git a/a b/a\nunknown metadata\n",
    b"\xff", 3,
])
def test_malformed_structure_or_encoding_fails_closed(data):
    from scrutare.findings import FindingError, parse_diff

    with pytest.raises(FindingError):
        parse_diff(data)


@pytest.mark.parametrize("path", ["../secret", "a/../secret", "/absolute", "a//b",
                                  "./b", "a/"])
def test_unsafe_or_badly_encoded_patch_paths_have_safe_diagnostics(path):
    from scrutare.findings import FindingError, parse_diff

    with pytest.raises(FindingError) as error:
        parse_diff(patch("@@ -1 +1 @@\n x\n", path, path))
    assert "secret" not in str(error.value)


def test_no_anchors_outside_hunks_or_on_other_side():
    from scrutare.findings import Anchor, parse_diff

    result = parse_diff(patch("@@ -20,0 +21 @@\n+new\n"))
    assert result == anchors(("main.py", 21, "RIGHT"))
    assert Anchor("main.py", 20, "LEFT") not in result


@pytest.mark.parametrize("name", [r'bad\000name', r'bad\qname', r'bad\400name',
                                  r'bad\377name', 'bad"name'])
def test_invalid_git_quoted_filenames(name):
    from scrutare.findings import FindingError, parse_diff

    data = (f'diff --git "a/{name}" "b/{name}"\n'
            f'--- "a/{name}"\n+++ "b/{name}"\n@@ -1 +1 @@\n x\n')
    with pytest.raises(FindingError):
        parse_diff(data)


@pytest.mark.parametrize("body", [
    "@@ -1 +1 @@\n x\n@@ -1 +2 @@\n x\n",
    "@@ -2 +2 @@\n x\n@@ -1 +3 @@\n x\n",
])
def test_overlapping_or_out_of_order_hunks_are_rejected(body):
    from scrutare.findings import FindingError, parse_diff

    with pytest.raises(FindingError):
        parse_diff(patch(body))


def test_space_paths_with_multiple_possible_git_header_separators():
    from scrutare.findings import parse_diff

    path = "space b/part.py"
    data = f"diff --git a/{path} b/{path}\n--- a/{path}\t\n+++ b/{path}\t\n@@ -1 +1 @@\n x\n"
    assert parse_diff(data) == anchors((path, 1, "LEFT"), (path, 1, "RIGHT"))


@pytest.mark.parametrize("old,new,body", [
    ("/dev/null", "b/new", "@@ -1 +1 @@\n-old\n+new\n"),
    ("a/new", "/dev/null", "@@ -1 +1 @@\n-old\n+new\n"),
])
def test_null_file_sides_cannot_contribute_payload(old, new, body):
    from scrutare.findings import FindingError, parse_diff

    data = f"diff --git a/new b/new\n--- {old}\n+++ {new}\n{body}"
    with pytest.raises(FindingError):
        parse_diff(data)


def test_binary_sections_do_not_hide_malformed_text_hunks():
    from scrutare.findings import FindingError, parse_diff

    with pytest.raises(FindingError):
        parse_diff("diff --git a/img b/img\nGIT binary patch\nliteral 1\n"
                   "Ac${Nk\n\n@@ -1 +1 @@\n x\n")


def test_adjacent_hunks_can_insert_after_preceding_old_line():
    from scrutare.findings import parse_diff

    data = patch("@@ -1 +1 @@\n-old\n+new\n@@ -1,0 +2 @@\n+extra\n")
    assert parse_diff(data) == anchors(("main.py", 1, "LEFT"), ("main.py", 1, "RIGHT"),
                                      ("main.py", 2, "RIGHT"))


def test_huge_count_header_has_safe_finding_error_on_all_python_versions():
    from scrutare.findings import FindingError, parse_diff

    with pytest.raises(FindingError, match="diff"):
        parse_diff(patch("@@ -1," + "9" * 5000 + " +1 @@\n x\n"))


@pytest.mark.parametrize("body", [
    "@@ -1,2 +1,2 @@\n x\n\\ No newline at end of file\n tail\n",
    "@@ -1,2 +1 @@\n-a\n\\ No newline at end of file\n-b\n+new\n",
])
def test_no_newline_markers_close_their_payload_sides(body):
    from scrutare.findings import FindingError, parse_diff

    with pytest.raises(FindingError):
        parse_diff(patch(body))


@pytest.mark.parametrize("data", [
    "diff --git a/img b/img\nBinary files a/../secret and b/img differ\n",
    "diff --git a/img b/img\nBinary files a/other and b/img differ\n",
])
def test_binary_file_markers_validate_paths(data):
    from scrutare.findings import FindingError, parse_diff

    with pytest.raises(FindingError):
        parse_diff(data)


@pytest.mark.parametrize("body", [
    "@@ -1 +1 @@\n x\n\\ No newline at end of file\n@@ -2 +2 @@\n y\n",
    "@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n"
    "@@ -2 +2 @@\n x\n",
])
def test_no_newline_markers_close_sides_across_hunks(body):
    from scrutare.findings import FindingError, parse_diff

    with pytest.raises(FindingError):
        parse_diff(patch(body))


def test_no_newline_markers_between_deleted_and_added_eof_lines():
    from scrutare.findings import parse_diff

    data = patch("@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n"
                 "\\ No newline at end of file\n")
    assert parse_diff(data) == anchors(("main.py", 1, "LEFT"), ("main.py", 1, "RIGHT"))


def test_hunk_counts_use_git_ascii_digits():
    from scrutare.findings import FindingError, parse_diff

    with pytest.raises(FindingError):
        parse_diff(patch("@@ -١ +١ @@\n x\n"))
