"""File selection follows the documented POSIX glob and rename policy."""

from dataclasses import FrozenInstanceError

import pytest

from scrutare.config import ConfigError, PathSettings
from scrutare.findings import FindingError


def select(names, include=(), exclude=()):
    from scrutare.engine.paths import parse_changed_files, select_changed_files

    files = parse_changed_files([{"filename": name, "status": "modified"} for name in names])
    selected = select_changed_files(files, PathSettings(include, exclude))
    return tuple(file.filename for file in selected)


def test_default_excludes_docs_and_markdown_at_every_depth():
    from scrutare.engine.paths import parse_changed_files, select_changed_files

    names = ("docs/index.py", "docs/deep/code.py", "README.md", "src/nested/notes.md",
             "src/main.py", "Docs/main.py", "src/README.MD", "other/docs/main.py")
    files = parse_changed_files([{"filename": name, "status": "modified"} for name in names])
    assert tuple(file.filename for file in select_changed_files(files, PathSettings())) == (
        "src/main.py", "Docs/main.py", "src/README.MD", "other/docs/main.py",
    )


@pytest.mark.parametrize("pattern,names,want", [
    ("src/**/*.py", ("src/a.py", "src/deep/a.py", "src/a/b/c.py", "other/a.py"),
     ("src/a.py", "src/deep/a.py", "src/a/b/c.py")),
    ("**/test_?.py", ("test_a.py", "a/b/test_é.py", "a/test_ab.py"),
     ("test_a.py", "a/b/test_é.py")),
    ("src/*/a.py", ("src/a.py", "src/x/a.py", "src/x/y/a.py"), ("src/x/a.py",)),
    ("src/**/deep/**/a.py", ("src/deep/a.py", "src/x/deep/y/z/a.py", "src/a.py"),
     ("src/deep/a.py", "src/x/deep/y/z/a.py")),
    ("*.py", ("a.py", "a/b.py", "a/b.PY"), ("a.py", "a/b.py")),
    ("[ab]?.py", ("a1.py", "deep/bé.py", "c1.py", "aa/b.py"), ("a1.py", "deep/bé.py")),
    ("[!a]*.py", ("a.py", "b.py", "deep/c.py"), ("b.py", "deep/c.py")),
    ("[[]draft[]].md", ("[draft].md", "deep/[draft].md", "draft.md"),
     ("[draft].md", "deep/[draft].md")),
    ("literal[*][?].py", ("literal*?.py", "deep/literal*?.py", "literalxy.py"),
     ("literal*?.py", "deep/literal*?.py")),
    ("!keep.py", ("keep.py", "!keep.py", "deep/!keep.py"), ("!keep.py", "deep/!keep.py")),
    ("café?.py", ("café1.py", "deep/caféé.py", "Café1.py"), ("café1.py", "deep/caféé.py")),
    ("src/a**b.py", ("src/ab.py", "src/axyb.py", "src/a/x/b.py"),
     ("src/ab.py", "src/axyb.py")),
    ("docs/**", ("docs/a.py", "docs/x/y.py", "other/docs/a.py"),
     ("docs/a.py", "docs/x/y.py")),
])
def test_include_glob_dialect(pattern, names, want):
    assert select(names, include=(pattern,)) == want


def test_exclusion_wins_over_include_and_empty_lists_allow_every_file():
    names = ("src/main.py", "src/test.py", "docs/a.md")
    assert select(names, include=("src/**",), exclude=("*test.py",)) == ("src/main.py",)
    assert select(names) == names
    assert select(names, include=("missing/**",)) == ()


@pytest.mark.parametrize("status", ["renamed", "copied"])
def test_previous_name_is_excluded_but_include_uses_current_name(status):
    from scrutare.engine.paths import parse_changed_files, select_changed_files

    files = parse_changed_files([
        {"filename": "src/a.py", "previous_filename": "docs/a.py", "status": status},
        {"filename": "src/b.py", "previous_filename": "old/b.py", "status": status},
        {"filename": "docs/c.py", "previous_filename": "old/c.py", "status": status},
        {"filename": "old/d.py", "previous_filename": "src/d.py", "status": status},
    ])
    selected = select_changed_files(files, PathSettings(("src/**",), ("docs/**",)))
    assert tuple(file.filename for file in selected) == ("src/b.py",)


def test_deletions_use_filename_and_results_are_immutable_fresh_records():
    from scrutare.engine.paths import parse_changed_files, select_changed_files

    raw = [{"filename": "src/old.py", "status": "removed", "patch": "ignored"},
           {"filename": "docs/gone.py", "status": "removed"}]
    files = parse_changed_files(raw)
    raw[0]["filename"] = "other.py"
    assert tuple(file.filename for file in select_changed_files(files, PathSettings())) == (
        "src/old.py",
    )
    with pytest.raises(FrozenInstanceError):
        files[0].filename = "other.py"


@pytest.mark.parametrize("status", ["added", "removed", "modified", "renamed", "copied",
                                   "changed", "unchanged"])
def test_known_statuses_are_preserved(status):
    from scrutare.engine.paths import ChangedFile, parse_changed_files

    assert parse_changed_files([{"filename": "a.py", "status": status}]) == (
        ChangedFile("a.py", status),
    )
    assert parse_changed_files([]) == ()


@pytest.mark.parametrize("data", [
    None, {}, (), "[]", [None], [{}], [{"filename": "a.py"}],
    [{"filename": "a.py", "status": ""}], [{"filename": "a.py", "status": "unknown"}],
    [{"filename": "a.py", "status": 1}],
    [{"filename": "a.py", "status": "modified", "previous_filename": "../secret"}],
    [{"filename": "a.py", "status": "modified"}, {"filename": "a.py", "status": "added"}],
])
def test_invalid_wire_file_records_fail_closed(data):
    from scrutare.engine.paths import parse_changed_files

    with pytest.raises(FindingError):
        parse_changed_files(data)


@pytest.mark.parametrize("name", [None, 1, "", "/secret", "../secret", "a/../secret",
                                  "a//b", "./a", "a/", "a\0b"])
def test_direct_file_constructor_reuses_path_validation(name):
    from scrutare.engine.paths import ChangedFile

    with pytest.raises(FindingError) as error:
        ChangedFile(name, "modified")
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("status", [None, 1, "", " ", "MODIFIED", "unknown"])
def test_direct_file_constructor_rejects_invalid_status(status):
    from scrutare.engine.paths import ChangedFile

    with pytest.raises(FindingError):
        ChangedFile("a.py", status)


@pytest.mark.parametrize("previous", [1, "", "/a", "a/../b"])
def test_direct_constructor_validates_optional_previous_filename(previous):
    from scrutare.engine.paths import ChangedFile

    with pytest.raises(FindingError):
        ChangedFile("a.py", "renamed", previous)


@pytest.mark.parametrize("paths", [None, {}, PathSettings("*.py", ()),
                                  PathSettings([], ()), PathSettings((), ["*.md"]),
                                  PathSettings((1,), ()), PathSettings(("",), ()),
                                  PathSettings((" ",), ()), PathSettings((), ("a\0b",)),
                                  PathSettings(("../secret",), ()), PathSettings(("/a",), ())])
def test_selection_validates_settings_even_with_empty_files(paths):
    from scrutare.engine.paths import select_changed_files

    with pytest.raises(ConfigError):
        select_changed_files((), paths)


@pytest.mark.parametrize("files", [None, [], ({"filename": "a.py"},)])
def test_selection_requires_typed_immutable_files(files):
    from scrutare.engine.paths import select_changed_files

    with pytest.raises(FindingError):
        select_changed_files(files, PathSettings())


def test_selection_rejects_duplicate_direct_records_before_filtering():
    from scrutare.engine.paths import ChangedFile, select_changed_files

    with pytest.raises(FindingError):
        select_changed_files((ChangedFile("docs/a.py", "added"),
                              ChangedFile("docs/a.py", "removed")), PathSettings())
