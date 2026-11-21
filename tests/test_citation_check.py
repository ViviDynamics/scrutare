"""The in-session citation check gives the post-session validator's verdict on every case."""

import copy
import json
import subprocess
import sys
from hashlib import sha256

import pytest

from scrutare.config import CATEGORIES
from scrutare.engine.session_output import _findings
from scrutare.findings.citation_check import (
    CITATION_REASONS,
    CODES,
    NO_VERDICT,
    check_answer,
    check_argv,
    cited_read_map,
    package_digest,
    refusal,
)
from scrutare.findings.evidence import CitationValidationError

HEAD, BASE = "a" * 40, "b" * 40
CONTENT = b"x\ny\n"
DIGEST = sha256(CONTENT).hexdigest()


def citation(**changes):
    return {"side": "head", "revision": HEAD, "path": "a.py", "start_line": 1, "end_line": 2,
            "sha256": DIGEST} | changes


def finding(*citations, **changes):
    return {"file": "a.py", "line": 1, "category": "correctness", "problem": "failure",
            "reason": "why", "evidence": {
                "version": 2, "trigger": "input", "preconditions": ["enabled"],
                "expected": "success", "observed": "failure", "impact": "lost result",
                "citations": list(citations or (citation(),))}} | changes


def answer(*findings):
    return {"findings": list(findings)}


def capture(root, content=CONTENT, *, entries=(), revisions=None):
    """A manifest in the shape the validator reads, as test_structured_evidence builds it."""
    (root / "blob.txt").write_bytes(content)
    (root / "base.txt").write_bytes(b"old\n")
    manifest = {"schema_version": 1,
                "revisions": revisions or {"head": {"sha": HEAD}, "base": {"sha": BASE}},
                "entries": [
                    {"side": "head", "path": "a.py", "status": "captured", "revision": HEAD,
                     "artifact": "blob.txt", "sha256": sha256(content).hexdigest()},
                    {"side": "base", "path": "a.py", "status": "captured", "revision": BASE,
                     "artifact": "base.txt", "sha256": sha256(b"old\n").hexdigest()},
                    *entries]}
    (root / "repository-context.json").write_text(json.dumps(manifest))
    return root


def post_session(value, root):
    """The verdict the post-session path gives: accepted, or the session's refusal."""
    try:
        _findings(copy.deepcopy(value), "tester", evidence_version=2, expected_root=root)
    except CitationValidationError as error:
        return ("citation_validation", error.reason)
    except (ValueError, TypeError, KeyError):
        return ("protocol", None)
    return "accepted"


def in_session(value, root):
    errors = check_answer(copy.deepcopy(value), root)
    return "accepted" if errors == [] else refusal([error["code"] for error in errors])


VT = b"first\x0bsecond\n"
NOT_TEXT = b"\xff\xfe\n"
# One table for both paths: the cases of test_structured_evidence.py and
# test_citation_line_counts.py, the correction cases of #91, and multi-citation answers.
CASES = {
    "valid": (answer(finding()), CONTENT, "accepted"),
    "valid-base": (answer(finding(citation(side="base", revision=BASE, end_line=1,
                                           sha256=sha256(b"old\n").hexdigest()))), CONTENT,
                   "accepted"),
    "no-findings": (answer(), CONTENT, "accepted"),
    "revision-mismatch": (answer(finding(citation(revision=BASE))), CONTENT,
                          ("citation_validation", "revision_mismatch")),
    "side-without-revision": (answer(finding(citation(side="base"))), CONTENT,
                              ("citation_validation", "revision_mismatch")),
    "path-not-captured": (answer(finding(citation(path="missing.py"))), CONTENT,
                          ("citation_validation", "path_not_captured")),
    "path-excluded": (answer(finding(citation(path="secret.env"))), CONTENT,
                      ("citation_validation", "path_not_captured")),
    "past-end": (answer(finding(citation(end_line=3))), CONTENT,
                 ("citation_validation", "line_range_outside_capture")),
    "wrong-hash": (answer(finding(citation(sha256="b" * 64))), CONTENT,
                   ("citation_validation", "content_hash_mismatch")),
    "zero-hash": (answer(finding(citation(sha256="0" * 64))), CONTENT,
                  ("citation_validation", "content_hash_mismatch")),
    "malformed-revision": (answer(finding(citation(revision="main"))), CONTENT,
                           ("protocol", None)),
    "placeholder-hash": (answer(finding(citation(sha256="334e0..placeholder"))), CONTENT,
                         ("protocol", None)),
    "traversal": (answer(finding(citation(path="../secret"))), CONTENT, ("protocol", None)),
    "boolean-line": (answer(finding(citation(start_line=True))), CONTENT, ("protocol", None)),
    "zero-line": (answer(finding(citation(end_line=0))), CONTENT, ("protocol", None)),
    "reversed": (answer(finding(citation(start_line=2, end_line=1))), CONTENT,
                 ("protocol", None)),
    "no-citations": (answer(finding(citations=[])), CONTENT, ("protocol", None)),
    "blank-problem": (answer(finding(problem=" ")), CONTENT, ("protocol", None)),
    "extra-key": ({"findings": [finding()], "verdict": "approve"}, CONTENT, ("protocol", None)),
    "vertical-tab-counts-one-line": (
        answer(finding(citation(sha256=sha256(VT).hexdigest(), end_line=2))), VT,
        ("citation_validation", "line_range_outside_capture")),
    "vertical-tab-bound": (answer(finding(citation(sha256=sha256(VT).hexdigest(),
                                                   end_line=1))), VT, "accepted"),
    "unterminated-last-line": (answer(finding(citation(sha256=sha256(b"a\nb").hexdigest(),
                                                       end_line=2))), b"a\nb", "accepted"),
    "empty-file": (answer(finding(citation(sha256=sha256(b"").hexdigest(), end_line=1))), b"",
                   ("citation_validation", "line_range_outside_capture")),
    "not-text": (answer(finding(citation(sha256=sha256(NOT_TEXT).hexdigest(), end_line=1))),
                 NOT_TEXT, ("protocol", None)),
    "second-finding": (answer(finding(), finding(citation(end_line=3))), CONTENT,
                       ("citation_validation", "line_range_outside_capture")),
    "first-of-two-citations": (answer(finding(citation(revision=BASE), citation(end_line=3))),
                               CONTENT, ("citation_validation", "revision_mismatch")),
    "shape-before-citations": (answer(finding(category="opinion"), finding(citation(
        end_line=3))), CONTENT, ("protocol", None)),
    "citation-before-shape": (answer(finding(citation(end_line=3)), finding(category="x")),
                              CONTENT, ("citation_validation", "line_range_outside_capture")),
}


@pytest.mark.parametrize("name", CASES)
def test_the_check_and_the_post_session_validator_agree(tmp_path, name):
    value, content, expected = CASES[name]
    root = capture(tmp_path, content, entries=[
        {"side": "head", "path": "secret.env", "status": "sensitive", "revision": HEAD,
         "artifact": None, "sha256": None}])
    assert post_session(value, root) == expected
    assert in_session(value, root) == expected


def test_every_refused_citation_is_listed_with_its_location(tmp_path):
    root = capture(tmp_path)
    errors = check_answer(answer(finding(citation(revision=BASE), citation(end_line=3)),
                                 finding(category="opinion")), root)
    assert [(error["code"], error["location"]) for error in errors] == [
        ("revision_mismatch", ["findings", 0, "evidence", "citations", 0, "revision"]),
        ("line_range_outside_capture", ["findings", 0, "evidence", "citations", 1, "end_line"]),
        ("finding_refused", ["findings", 1]),
    ]
    assert errors[1]["message"] == (
        "line_range_outside_capture: the cited file's last line is 2, got end_line 3")
    assert errors[1]["guidance"] == (
        "Read blob.txt again: its last line is 2, so start_line and end_line must both be at "
        "most 2.")
    assert errors[2]["message"] == ("finding_refused: category: expected one of "
                                    + ", ".join(CATEGORIES))
    assert {error["code"] for error in errors} <= set(CODES)
    # Every message starts with its code, and a measured value only follows ", got ", where
    # nare's correction budget stops reading an error's identity.
    assert all(error["message"].startswith(error["code"] + ": ") for error in errors)
    fixed = check_answer(answer(finding(citation(revision=BASE), citation(end_line=5)),
                                finding(category="opinion")), root)
    assert [error["message"].partition(", got ")[0] for error in fixed] == [
        error["message"].partition(", got ")[0] for error in errors]


def test_no_message_repeats_model_text(tmp_path):
    root = capture(tmp_path)
    marker = "MODEL-TEXT-7f3a"
    value = answer(
        finding(citation(path=f"{marker}.py"), problem=marker, reason=marker),
        finding(citation(path=f"{marker}/../x")),
        finding(citation(revision=marker)),
        finding(citation(sha256=marker), category=marker))
    errors = check_answer(value, root)
    assert len(errors) == 4
    assert marker not in json.dumps(errors)


def test_the_check_reads_only_its_inputs(tmp_path):
    root = capture(tmp_path)
    before = {path: path.read_bytes() for path in root.iterdir()}
    check_answer(answer(finding(citation(end_line=3))), root)
    assert {path: path.read_bytes() for path in root.iterdir()} == before


def run_command(root, payload, argv=None):
    return subprocess.run(argv or check_argv(root), input=payload, capture_output=True,
                          cwd=root, env={"PATH": "/usr/bin:/bin"}, timeout=60)


def test_the_command_speaks_nares_protocol(tmp_path):
    root = capture(tmp_path)
    value = answer(finding(citation(end_line=3)))
    result = run_command(root, json.dumps({"answer": value, "reads": [
        {"path": "blob.txt", "sha256": DIGEST, "size": 4}]}).encode())
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == check_answer(value, root)
    passed = run_command(root, json.dumps({"answer": answer(finding()), "reads": []}).encode())
    assert (passed.returncode, json.loads(passed.stdout)) == (0, [])


@pytest.mark.parametrize("payload", [
    b"not json", b'{"answer": {}}', b'{"answer": NaN, "reads": []}',
    b'{"answer": {}, "answer": {}, "reads": []}'])
def test_a_payload_that_is_not_nares_gets_no_verdict(tmp_path, payload):
    result = run_command(capture(tmp_path), payload)
    assert result.returncode == NO_VERDICT and result.stdout == b""
    assert result.stderr.startswith(b"citation check: no verdict:")


def test_a_checker_that_differs_from_its_recorded_digest_gets_no_verdict(tmp_path):
    root = capture(tmp_path)
    argv = check_argv(root, digest="0" * 64)
    result = run_command(root, json.dumps({"answer": answer(), "reads": []}).encode(), argv)
    assert result.returncode == NO_VERDICT and result.stdout == b""
    assert b"differs from the recorded checker digest" in result.stderr


def test_an_unreadable_capture_gets_no_verdict(tmp_path):
    root = capture(tmp_path)
    (root / "repository-context.json").write_text("{")
    result = run_command(root, json.dumps({"answer": answer(finding()), "reads": []}).encode())
    assert result.returncode == NO_VERDICT and result.stdout == b""


def test_the_argv_is_this_interpreter_isolated_and_pinned_to_the_package(tmp_path):
    argv = check_argv(tmp_path)
    assert argv == [sys.executable, "-I", "-B", "-m", "scrutare.findings.citation_check",
                    "--digest", package_digest(), str(tmp_path)]
    assert len(package_digest()) == 64 and package_digest() == package_digest()
    with pytest.raises(ValueError):
        check_argv(tmp_path, interpreter="python3")
    with pytest.raises(ValueError):
        check_argv(tmp_path.relative_to(tmp_path.anchor))


def test_the_cited_read_map_names_each_capture_of_a_logical_path(tmp_path):
    root = capture(tmp_path, entries=[
        {"side": "head", "path": "secret.env", "status": "sensitive", "revision": HEAD,
         "artifact": None, "sha256": None},
        {"side": "head", "path": "b.py", "status": "captured", "revision": HEAD,
         "artifact": "b.txt", "sha256": DIGEST}])
    assert cited_read_map(root) == {"a.py": ["blob.txt", "base.txt"], "b.py": ["b.txt"]}


@pytest.mark.parametrize("codes,expected", [
    (["line_range_outside_capture", "finding_refused"],
     ("citation_validation", "line_range_outside_capture")),
    (["finding_refused", "revision_mismatch"], ("protocol", None)),
    (["answer_refused"], ("protocol", None)),
    (["citation_unreadable"], ("protocol", None)),
])
def test_a_refusal_is_classified_by_its_first_error(codes, expected):
    assert refusal(codes) == expected
    assert set(CITATION_REASONS) < set(CODES)
