"""Count unified patch payload without reading files or inferring unseen lines."""

import re
from dataclasses import dataclass

from scrutare.findings.models import Anchor, FindingError, _path


@dataclass(frozen=True)
class DiffSection:
    """One validated Git file change, retaining its exact source bytes."""

    old_path: str | None
    new_path: str | None
    data: bytes
    anchors: frozenset[Anchor]

    def __post_init__(self) -> None:
        if self.old_path is None and self.new_path is None:
            raise _invalid("a section must have a file identity")
        for name in (self.old_path, self.new_path):
            if name is not None:
                _path(name, "diff.file")
        if not isinstance(self.data, bytes):
            raise _invalid("section data must be bytes")
        if not isinstance(self.anchors, frozenset) or any(
            not isinstance(anchor, Anchor) or anchor.file != self.file for anchor in self.anchors
        ):
            raise _invalid("section anchors must be a frozenset for its file")

    @property
    def file(self) -> str:
        """Use the current name, or the old name for a deletion."""
        file = self.new_path if self.new_path is not None else self.old_path
        assert file is not None
        return file


def _invalid(detail: str) -> FindingError:
    return FindingError(f"diff: {detail}")


def _decode_name(raw: str) -> str:
    """Decode Git's C quoting as bytes, then UTF-8, without interpreting paths."""
    if not raw.startswith('"'):
        return raw
    if len(raw) < 2 or not raw.endswith('"'):
        raise _invalid("invalid quoted filename")
    result = bytearray()
    escapes = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13,
               '"': 34, "\\": 92}
    i = 1
    while i < len(raw) - 1:
        char = raw[i]
        if char == '"':
            raise _invalid("invalid quoted filename")
        if char != "\\":
            try:
                result.extend(char.encode("utf-8"))
            except UnicodeError:
                raise _invalid("filename must be UTF-8") from None
            i += 1
            continue
        i += 1
        if i >= len(raw) - 1:
            raise _invalid("invalid filename escape")
        if raw[i] in escapes:
            result.append(escapes[raw[i]])
            i += 1
        elif re.fullmatch(r"[0-3][0-7]{2}", raw[i:i + 3]):
            result.append(int(raw[i:i + 3], 8))
            i += 3
        else:
            raise _invalid("invalid filename escape")
    try:
        return result.decode("utf-8")
    except UnicodeError:
        raise _invalid("filename must be UTF-8") from None


def _prefixed(raw: str, prefix: str) -> str:
    name = _decode_name(raw)
    if not name.startswith(prefix + "/"):
        raise _invalid("expected Git filename prefix")
    return _path(name[2:], "diff.file")


def _git_pairs(raw: str) -> tuple[tuple[str, str], ...]:
    # Git leaves spaces unquoted. Keep every possible separator until the
    # authoritative ---/+++ headers disambiguate, rather than guessing.
    pairs: list[tuple[str, str]] = []
    for i, char in enumerate(raw):
        if char != " " or not raw[i + 1:].startswith(("b/", '"b/')):
            continue
        try:
            pair = (_prefixed(raw[:i], "a"), _prefixed(raw[i + 1:], "b"))
        except FindingError:
            continue
        pairs.append(pair)
    if not pairs:
        raise _invalid("invalid Git file header")
    return tuple(pairs)


def _header_path(raw: str, prefix: str) -> str | None:
    if raw == "/dev/null":
        return None
    # Git appends a tab to unquoted filenames containing spaces in file headers.
    return _prefixed(raw.removesuffix("\t"), prefix)


def _hunk(
    lines: list[str], index: int, file: str, result: set[Anchor],
    *, left_exists: bool, right_exists: bool,
) -> tuple[int, int, int, int, int, bool, bool]:
    header = re.fullmatch(
        r"@@ -([0-9]+)(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@(?: .*)?", lines[index],
    )
    if header is None:
        raise _invalid("malformed or unsupported hunk header")
    try:
        left, right = int(header[1]), int(header[3])
        left_count = int(header[2]) if header[2] is not None else 1
        right_count = int(header[4]) if header[4] is not None else 1
    except ValueError:
        raise _invalid("hunk range integers are too large") from None
    if (left_count and not left) or (right_count and not right):
        raise _invalid("nonempty hunk ranges must start at a positive line")
    if (left_count and not left_exists) or (right_count and not right_exists):
        raise _invalid("an absent or completed file side cannot contain hunk lines")
    # A zero-count range names the line before its insertion/deletion boundary.
    left_start = left if left_count else left + 1
    right_start = right if right_count else right + 1
    left_end, right_end = left_start + left_count, right_start + right_count
    left_closed = right_closed = False
    index += 1
    marker_allowed = False
    last_prefix = ""
    while left_count or right_count:
        if index >= len(lines):
            raise _invalid("truncated hunk payload")
        line = lines[index]
        if line == "\\ No newline at end of file":
            if not marker_allowed:
                raise _invalid("misplaced no-newline marker")
            if (last_prefix in " -" and left_count) or (last_prefix in " +" and right_count):
                raise _invalid("payload continues after a no-newline marker")
            left_closed = left_closed or last_prefix in " -"
            right_closed = right_closed or last_prefix in " +"
            marker_allowed = False
            index += 1
            continue
        if not line or line[0] not in " +-":
            raise _invalid("hunk payload does not match its header")
        if line[0] in " -":
            if not left_count:
                raise _invalid("hunk exceeds LEFT line count")
            result.add(Anchor(file, left, "LEFT"))
            left_count -= 1
            left += 1
        if line[0] in " +":
            if not right_count:
                raise _invalid("hunk exceeds RIGHT line count")
            result.add(Anchor(file, right, "RIGHT"))
            right_count -= 1
            right += 1
        marker_allowed = True
        last_prefix = line[0]
        index += 1
    if index < len(lines) and lines[index] == "\\ No newline at end of file":
        if not marker_allowed:
            raise _invalid("misplaced no-newline marker")
        left_closed = left_closed or last_prefix in " -"
        right_closed = right_closed or last_prefix in " +"
        index += 1
    return index, left_start, left_end, right_start, right_end, left_closed, right_closed

def _matching_pairs(
    pairs: tuple[tuple[str, str], ...], old: str | None, new: str | None,
) -> tuple[tuple[str, str], ...]:
    matches = tuple((a, b) for a, b in pairs
                    if (old is None or old == a) and (new is None or new == b))
    if not matches:
        raise _invalid("file identities disagree with Git file header")
    return matches


def _binary_names(
    raw: str, pairs: tuple[tuple[str, str], ...],
) -> tuple[str | None, str | None]:
    identities: set[tuple[str | None, str | None]] = set()
    for separator in re.finditer(" and ", raw):
        try:
            old = _header_path(raw[:separator.start()], "a")
            new = _header_path(raw[separator.end():], "b")
        except FindingError:
            continue
        if old is None and new is None:
            continue
        if any((old is None or old == a) and (new is None or new == b) for a, b in pairs):
            identities.add((old, new))
    if len(identities) != 1:
        raise _invalid("binary filenames are ambiguous or disagree with Git file header")
    return next(iter(identities))


def _binary(lines: list[str], index: int) -> None:
    """Validate Git binary block framing without decoding its opaque content."""
    blocks = 0
    while index < len(lines):
        if blocks >= 2 or not re.fullmatch(r"(?:literal|delta) [0-9]+", lines[index]):
            raise _invalid("invalid binary patch block header")
        blocks += 1
        index += 1
        payload_lines = 0
        while index < len(lines) and lines[index]:
            line = lines[index]
            if not re.fullmatch(r"[A-Za-z][0-9A-Za-z!#$%&()*+;<=>?@^_`{|}~\-]+", line):
                raise _invalid("invalid binary patch payload")
            length = ord(line[0]) - (ord("A") - 1 if line[0].isupper() else ord("a") - 27)
            if len(line) != 1 + 5 * ((length + 3) // 4):
                raise _invalid("invalid binary patch payload length")
            payload_lines += 1
            index += 1
        if not payload_lines:
            raise _invalid("missing binary patch payload")
        if index < len(lines):
            index += 1
    if not blocks:
        raise _invalid("missing binary patch block")


def _section(lines: list[str], data: bytes) -> DiffSection:
    pairs = _git_pairs(lines[0][len("diff --git "):])
    result: set[Anchor] = set()
    index = 1
    old: str | None = None
    new: str | None = None
    has_headers = False
    has_hunks = False
    has_identity = False
    new_file = deleted_file = False
    movement: str | None = None
    moved: dict[int, str] = {}
    left_end = right_end = -1
    left_closed = right_closed = False
    while index < len(lines):
        line = lines[index]
        if line.startswith("--- ") and not has_headers and not has_hunks:
            if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
                raise _invalid("expected paired file headers")
            old = _header_path(line[4:], "a")
            new = _header_path(lines[index + 1][4:], "b")
            if old is None and new is None:
                raise _invalid("both file headers name /dev/null")
            pairs = _matching_pairs(pairs, old, new)
            has_headers = True
            has_identity = True
            index += 2
        elif line.startswith("@@"):
            if not has_headers:
                raise _invalid("hunk requires paired file headers")
            file = new if new is not None else old
            assert file is not None
            index, left_start, next_left, right_start, next_right, close_left, close_right = _hunk(
                lines, index, file, result, left_exists=old is not None and not left_closed,
                right_exists=new is not None and not right_closed,
            )
            left_closed = left_closed or close_left
            right_closed = right_closed or close_right
            if left_start < left_end or right_start < right_end:
                raise _invalid("overlapping or out-of-order hunks")
            left_end, right_end = next_left, next_right
            has_hunks = True
        elif not has_headers and line.startswith(("rename from ", "rename to ",
                                                   "copy from ", "copy to ")):
            kind, direction, raw = line.split(" ", 2)
            name = _path(_decode_name(raw), "diff.file")
            side = 0 if direction == "from" else 1
            if (movement is not None and movement != kind) or side in moved:
                raise _invalid("duplicate or inconsistent rename or copy metadata")
            movement = kind
            moved[side] = name
            pairs = _matching_pairs(pairs, name if side == 0 else None,
                                    name if side == 1 else None)
            index += 1
        elif not has_headers and re.fullmatch(
            r"(?:index [0-9a-f]+\.\.[0-9a-f]+(?: [0-7]{6})?|"
            r"(?:new file|deleted file|old|new) mode [0-7]{6}|"
            r"(?:dis)?similarity index (?:100|[0-9]{1,2})%)", line,
        ):
            if line.startswith("new file mode "):
                if new_file:
                    raise _invalid("duplicate new file metadata")
                new_file = True
            elif line.startswith("deleted file mode "):
                if deleted_file:
                    raise _invalid("duplicate deleted file metadata")
                deleted_file = True
            index += 1
        elif not has_headers and line.startswith("Binary files ") and line.endswith(" differ"):
            if index != len(lines) - 1:
                raise _invalid("unexpected content after binary change")
            old, new = _binary_names(line[len("Binary files "):-len(" differ")], pairs)
            pairs = _matching_pairs(pairs, old, new)
            has_identity = True
            break
        elif not has_headers and line == "GIT binary patch":
            _binary(lines, index + 1)
            break
        else:
            raise _invalid("unexpected patch structure or excess hunk payload")
    if has_headers and not has_hunks:
        raise _invalid("textual file headers require a hunk")
    if len(pairs) != 1:
        raise _invalid("ambiguous Git file identity")
    if movement is not None and (len(moved) != 2 or new_file or deleted_file):
        raise _invalid("incomplete or inconsistent rename or copy metadata")
    if new_file and deleted_file:
        raise _invalid("a file cannot be both added and deleted")
    if not has_identity:
        old, new = pairs[0]
        old = None if new_file else old
        new = None if deleted_file else new
    elif (new_file and old is not None) or (deleted_file and new is not None):
        raise _invalid("file mode metadata disagrees with file identity")
    if movement is not None and (old is None or new is None):
        raise _invalid("rename or copy metadata requires both file identities")
    return DiffSection(old, new, data, frozenset(result))


def parse_diff_sections(diff: bytes | str) -> tuple[DiffSection, ...]:
    """Parse ordered sections once, preserving bytes independently of line parsing."""
    if isinstance(diff, str):
        try:
            diff = diff.encode("utf-8")
        except UnicodeError:
            raise _invalid("patch must be UTF-8") from None
    if not isinstance(diff, bytes):
        raise _invalid("expected bytes or text")
    try:
        lines = [line.removesuffix("\r") for line in diff.decode("utf-8").split("\n")]
    except UnicodeError:
        raise _invalid("patch must be UTF-8") from None
    if lines[-1] == "" and diff.endswith(b"\n"):
        lines.pop()
    if not diff:
        return ()
    result: list[DiffSection] = []
    section: list[str] = []
    start = offset = 0
    for line, raw in zip(lines, diff.split(b"\n")):
        if line.startswith(("diff --cc ", "diff --combined ")):
            raise _invalid("combined diffs are unsupported")
        if line.startswith("diff --git "):
            if section:
                result.append(_section(section, diff[start:offset]))
            section = [line]
            start = offset
        elif section:
            section.append(line)
        else:
            raise _invalid("expected a Git file header")
        offset += len(raw) + 1
    if section:
        result.append(_section(section, diff[start:]))
    return tuple(result)


def parse_diff(diff: bytes | str) -> frozenset[Anchor]:
    """Index LEFT deletions/context and RIGHT additions/context from a Git patch."""
    return frozenset(anchor for section in parse_diff_sections(diff) for anchor in section.anchors)
