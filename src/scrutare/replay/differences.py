"""Deterministic semantic differences within the saved verdict comparison."""

import json
from collections import Counter
from collections.abc import Mapping

from scrutare.replay.models import ReplayDifference


def _identity(value: object) -> str | None:
    if isinstance(value, Mapping) and all(
        field in value for field in ("file", "line", "side", "problem")
    ):
        return f"{value['file']}:{value['line']}:{value['side']}: {value['problem']}"
    return None


def _same(before: object, after: object) -> bool:
    # JSON booleans and numbers must not collapse through Python's True == 1.
    return json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True)


def diff_verdicts(
    before: Mapping[str, object], after: Mapping[str, object],
) -> tuple[ReplayDifference, ...]:
    """Compare every field and ordered occurrence without guessing renamed findings."""
    differences = []

    def walk(old: object, new: object, path: str, finding: str | None = None) -> None:
        if _same(old, new):
            return
        finding = finding or _identity(old) or _identity(new)
        if isinstance(old, Mapping) and isinstance(new, Mapping):
            for key in sorted(old.keys() | new.keys()):
                child = f"{path}.{key}"
                if key not in old:
                    differences.append(ReplayDifference(child, "added", None, new[key], finding))
                elif key not in new:
                    differences.append(ReplayDifference(child, "removed", old[key], None, finding))
                else:
                    walk(old[key], new[key], child, finding)
        elif isinstance(old, list) and isinstance(new, list):
            if (len(old) == len(new)
                    and Counter(json.dumps(item, sort_keys=True) for item in old)
                    == Counter(json.dumps(item, sort_keys=True) for item in new)):
                differences.append(ReplayDifference(path, "reordered", old, new, finding))
            for index in range(max(len(old), len(new))):
                child = f"{path}[{index}]"
                if index >= len(old):
                    differences.append(ReplayDifference(child, "added", None, new[index],
                                                        finding or _identity(new[index])))
                elif index >= len(new):
                    differences.append(ReplayDifference(child, "removed", old[index], None,
                                                        finding or _identity(old[index])))
                else:
                    walk(old[index], new[index], child, finding)
        else:
            differences.append(ReplayDifference(path, "changed", old, new, finding))

    walk(before, after, "verdict")
    return tuple(differences)
