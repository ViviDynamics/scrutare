"""The post-session citation validator, run by nare as its in-session check command (#130).

nare runs ``python -I -B -m scrutare.findings.citation_check --digest D ROOT`` on a final
answer that passes the findings schema. The command reads ``{"answer": ..., "reads": [...]}``
on stdin and prints a JSON list of errors; an empty list passes. nare puts the errors into
the same in-session correction as schema errors, with the read tool still offered.

The verdict is the post-session parser's, by construction: an answer passes exactly when
``session_output._findings`` (the findings parser and ``validate_evidence``) accepts it.
When it refuses, every refused finding and citation is listed, first the one the
post-session path would have refused, so one correction can fix them all. Messages are
fixed text with indexes, reasons and values Scrutare measured; no model text is repeated.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import replace
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any

MODULE = "scrutare.findings.citation_check"
# nare's own default, sent explicitly so a later nare default never changes a review.
CHECK_TIMEOUT_SECONDS = 30
# Where a findings answer cites its paths, for --require-cited-reads.
CITED_PATHS = "/findings/*/evidence/citations/*/path"
# The attempt's own map file, outside the read root.
CITED_READ_MAP = "cited-read-map.json"
# The persona attribution the parser needs; it never changes a verdict.
_PERSONA = "citation-check"
# Codes the checker gives; a refusal whose codes are all here is the checker's.
CITATION_REASONS = ("revision_mismatch", "path_not_captured", "content_hash_mismatch",
                    "line_range_outside_capture")
FINDING_REFUSED = "finding_refused"
CITATION_UNREADABLE = "citation_unreadable"
ANSWER_REFUSED = "answer_refused"
CODES = (*CITATION_REASONS, FINDING_REFUSED, CITATION_UNREADABLE, ANSWER_REFUSED)
# Exit status for a check that cannot give a verdict: nare then ends the run as failed.
NO_VERDICT = 3

_GUIDANCE = {
    "revision_mismatch": (
        "Use the full commit SHA that repository-context.json records under revisions for "
        "this citation's side."),
    "path_not_captured": (
        "Cite a logical path whose repository-context.json entry on this side has status "
        "captured, or remove the citation."),
    "content_hash_mismatch": (
        "Use the sha256 that repository-context.json records for this side and path; read "
        "that entry's artifact to confirm it."),
    "line_range_outside_capture": (
        "Lines are numbered from 1 and a final newline does not begin another line; read the "
        "artifact again and keep start_line and end_line at most its last line."),
}


@lru_cache(maxsize=1)
def package_digest() -> str:
    """SHA-256 over every Python source file of the imported scrutare package."""
    import scrutare

    package = Path(scrutare.__file__).resolve().parent
    digest = sha256()
    for path in sorted(package.rglob("*.py"),
                       key=lambda item: item.relative_to(package).as_posix()):
        if "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(package).as_posix().encode("utf-8") + b"\0")
        digest.update(sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def check_argv(root: Path, *, interpreter: str | None = None,
               digest: str | None = None) -> list[str]:
    """The argv nare runs: this interpreter, isolated, never writing bytecode."""
    executable = sys.executable if interpreter is None else interpreter
    if not executable or not Path(executable).is_absolute():
        raise ValueError("citation check: expected an absolute interpreter path")
    if not isinstance(root, Path) or not root.is_absolute():
        raise ValueError("citation check: expected an absolute read root")
    return [executable, "-I", "-B", "-m", MODULE, "--digest",
            package_digest() if digest is None else digest, str(root)]


def cited_read_map(root: Path) -> dict[str, list[str]]:
    """Each captured logical path, to the artifact names that capture it on either side."""
    from scrutare.engine.repository_context import MANIFEST
    from scrutare.engine.review_inputs import _read_json

    mapped: dict[str, list[str]] = {}
    try:
        for entry in _read_json(root / MANIFEST)["entries"]:
            artifact = entry.get("artifact")
            if (entry["status"] == "captured" and isinstance(artifact, str)
                    and Path(artifact).name == artifact and artifact not in ("", ".", "..")):
                names = mapped.setdefault(entry["path"], [])
                if artifact not in names:
                    names.append(artifact)
    except (KeyError, TypeError, AttributeError):
        raise ValueError("citation check: unreadable capture manifest") from None
    return {path: mapped[path] for path in sorted(mapped)}


def _error(code: str, location: list[str | int], message: str,
           guidance: str | None) -> dict[str, Any]:
    """One error; nare states it as ``<location>: <message>``, then the guidance.

    A value Scrutare measured follows ", got ", where nare's correction budget stops
    reading an error's identity, so a citation that stays wrong is the same error.
    """
    return {"code": code, "location": location, "message": message, "guidance": guidance}


_FIELDS = {"revision_mismatch": "revision", "path_not_captured": "path",
           "content_hash_mismatch": "sha256", "line_range_outside_capture": "end_line"}
_MESSAGES = {
    "revision_mismatch": "revision_mismatch: not the revision repository-context.json "
                         "records for this side",
    "path_not_captured": "path_not_captured: no captured repository-context.json entry for "
                         "this side and path",
    "content_hash_mismatch": "content_hash_mismatch: not the sha256 repository-context.json "
                             "records for this side and path",
    "line_range_outside_capture": "line_range_outside_capture: past the cited file's last line",
}


def _citation_error(reason: str, index: int, number: int, end_line: int,
                    measured: tuple[str, int] | None) -> dict[str, Any]:
    location: list[str | int] = ["findings", index, "evidence", "citations", number,
                                 _FIELDS[reason]]
    message, guidance = _MESSAGES[reason], _GUIDANCE[reason]
    if measured is not None:
        artifact, last = measured
        message = (f"line_range_outside_capture: the cited file's last line is {last}, "
                   f"got end_line {end_line}")
        guidance = (f"Read {artifact} again: its last line is {last}, so start_line and "
                    f"end_line must both be at most {last}.")
    return _error(reason, location, message, guidance)


def _errors(answer: object, root: Path) -> list[dict[str, Any]]:
    """Every refused finding and citation, in the order the post-session parser meets them."""
    from scrutare.engine.repository_context import MANIFEST, captured_line_count, regular
    from scrutare.engine.review_inputs import _read_json
    from scrutare.findings.evidence import CitationValidationError, validate_evidence
    from scrutare.findings.models import FindingError, parse_finding

    if not isinstance(answer, dict) or not isinstance(answer.get("findings"), list):
        return []
    manifest = _read_json(root / MANIFEST)
    captured = {(entry["side"], entry["path"]): entry for entry in manifest["entries"]}
    errors = []
    for index, item in enumerate(answer["findings"]):
        try:
            if not isinstance(item, dict):
                raise FindingError("finding: expected a mapping")
            finding = parse_finding(item, persona=_PERSONA, evidence_version=2,
                                    candidate_id=_PERSONA)
        except (ValueError, TypeError) as refusal:
            # FindingError text omits untrusted values by contract.
            text = str(refusal) if isinstance(refusal, FindingError) else "invalid finding"
            errors.append(_error(FINDING_REFUSED, ["findings", index],
                                 f"{FINDING_REFUSED}: {text}",
                                 "Revision is the full 40-character commit SHA and sha256 the "
                                 "full 64-character content hash that repository-context.json "
                                 "records; lines are positive and end_line is not before "
                                 "start_line." if "citation" in text else None))
            continue
        assert finding.evidence is not None
        for number, citation in enumerate(finding.evidence.citations):
            single = replace(finding, evidence=replace(finding.evidence, citations=(citation,)))
            try:
                validate_evidence(single, root)
            except CitationValidationError as refusal:
                measured = None
                if refusal.reason == "line_range_outside_capture":
                    # Only reached once revision, path and digest matched the capture.
                    artifact = captured[(citation.side, citation.path)]["artifact"]
                    measured = (artifact, captured_line_count(regular(root / artifact)))
                errors.append(_citation_error(refusal.reason, index, number,
                                              citation.end_line, measured))
            except ValueError:
                location: list[str | int] = ["findings", index, "evidence", "citations", number]
                errors.append(_error(CITATION_UNREADABLE, location,
                                     f"{CITATION_UNREADABLE}: the cited capture is not text",
                                     "Cite a captured text file."))
    return errors


def check_answer(answer: object, root: Path) -> list[dict[str, Any]]:
    """nare's error list for one answer: empty exactly when the post-session parser accepts."""
    from scrutare.engine.session_output import _findings

    try:
        _findings(answer, _PERSONA, evidence_version=2, expected_root=root)
    except (ValueError, TypeError, KeyError):
        return _errors(answer, root) or [_error(
            ANSWER_REFUSED, [], f"{ANSWER_REFUSED}: the findings answer was refused",
            "Return exactly the findings document the schema describes.")]
    return []


def refusal(codes: Sequence[str]) -> tuple[str, str | None]:
    """The post-session refusal a refused answer stands for: its first error decides.

    The first listed error is the one the post-session parser meets first, so a citation
    reason there is ``citation_validation`` with that reason, and anything else ``protocol``.
    """
    first = codes[0] if codes else None
    if first in CITATION_REASONS:
        return ("citation_validation", first)
    return ("protocol", None)


def main(argv: Sequence[str] | None = None) -> int:
    """Read nare's payload on stdin and print the error list; exit 3 without a verdict."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if (len(arguments) != 3 or arguments[0] != "--digest"
                or re.fullmatch(r"[0-9a-f]{64}", arguments[1]) is None):
            raise ValueError("usage: --digest SHA256 ROOT")
        if package_digest() != arguments[1]:
            raise ValueError("the scrutare package differs from the recorded checker digest")
        root = Path(arguments[2])
        if not root.is_absolute():
            raise ValueError("expected an absolute read root")
        from scrutare.engine.session_output import _json

        payload = _json(sys.stdin.buffer.read())
        if set(payload) != {"answer", "reads"}:
            raise ValueError("expected nare's answer and reads")
        errors = check_answer(payload["answer"], root)
    except Exception as error:  # noqa: BLE001 - any failure is "no verdict", never a pass
        reason = (str(error) if isinstance(error, ValueError) and str(error).startswith(
            ("usage", "the scrutare", "expected")) else type(error).__name__)
        os.write(2, f"citation check: no verdict: {reason}\n".encode())
        return NO_VERDICT
    sys.stdout.write(json.dumps(errors, sort_keys=True, separators=(",", ":")))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
