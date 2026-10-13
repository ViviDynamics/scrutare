"Reproduce recorded independent automated judgments; no model/provider invocation."

import argparse
import hashlib
import json
from pathlib import Path

P = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--corpus", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
CORPUS = args.corpus.resolve()
OUTPUT = args.output.resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)
snapshot = json.loads((P / "snapshot.json").read_text())
label_hashes = {c["id"]: c["labels_sha256"] for c in snapshot["cases"]}
REVIEWER = "Codex independent automated source-and-control adjudicator"

REASONS = {
    "contract-exclusive-boundary": (
        "The captured exclusive-limit docstring requires size < limit; hea"
        "d accepts equality. Original-limit criticism, inclusive-fix prais"
        "e and naming advice do not meet this criterion."
    ),
    "contract-null-zero": (
        "The explicit documented zero sentinel returns 0 in base and 30 in"
        " head. None remains 30. A generic change report without the zero "
        "collision, praise for the new default, or reversal of source vers"
        "ions is not an unambiguous match."
    ),
    "consumer-page-index": (
        "Public pages are explicitly one-based: page1,size20 must yield0; "
        "head yields20. Criticism of the original formula or claiming that"
        " head starts at0 is contradicted by the source."
    ),
    "security-tenant-filter": (
        "Head returns rows with a different tenant via !=, including tenan"
        "tB to callerA. The required match is this inverted tenant predica"
        "te, not an unsupported generic downstream assertion."
    ),
    "security-sql-interpolation": (
        "The bound name parameter is replaced by SQL concatenation; an att"
        "acker changes the predicate. SQLite execute does not establish ar"
        "bitrary multi-statement SQL or shell execution."
    ),
    "security-auth-order": (
        "policy.permitted compares actor==owner; one store.clear call now "
        "precedes authorization, erasing data even when PermissionError fo"
        "llows. Double-clear claims, old leaks and unrelated naming/docume"
        "ntation advice are not the recorded regression."
    ),
    "tests-vacuous-exception": (
        "Head parse(-1) returns -1 and catch-only test_negative passes wit"
        "h or without ValueError. Pure pytest style advice does not identi"
        "fy the lost validation or missing fail path; claims that this tes"
        "t necessarily fails contradict execution."
    ),
    "tests-truthy-balance": (
        "Head remaining(10,3) returns13 and assert remaining(10,3) passes."
        " It does not retain a ==7/==10/==13 assertion. The gold mechanism"
        " is incorrect addition or concrete lost discrimination of wrong13"
        "."
    ),
    "tests-consumer-fixture": (
        "The exact criterion is unchanged test_label asserting ready while"
        " formatting.label returns waiting, causing deterministic CI failu"
        "re. All27 observations report generic return-label changes, imagi"
        "ned consumer/security impact, naming or style, without identifyin"
        "g that stale test assertion. Terminology change alone is not this"
        " defect."
    ),
    "operations-retry-unit": (
        "The ms input500 must become .5 seconds; head multiplies and retur"
        "ns500000. Criticizing the original division or praising multiplic"
        "ation does not identify the excessive-delay regression."
    ),
    "operations-flush-loss": (
        "Head closes the normal writer once before pending writes and flus"
        "h; writing to the closed writer fails and loses events. Head does"
        " not double-close. Old write/flush-exception leaks or praising ea"
        "rly closure are separate from the gold trigger."
    ),
    "consumer-bool-string": (
        "The documented CLI true/false text contract maps false toFalse in"
        " base and True in head. Generic bool-conversion changes, non-stri"
        "ng/out-of-contract input allegations and explicit claims that tru"
        "e stops producingTrue do not unambiguously identify false enablin"
        "g the feature."
    ),
    "consumer-empty-search": (
        "The docstring requires all rows for empty string; head instead re"
        "turns[]. Praise for no-results, old empty-input criticism and non"
        "string/regex speculation are not the demonstrated regression."
    ),
    "contract-explicit-default-clean": (
        "Base and head return30 forNone,0 forzero and the supplied nonzero"
        " value. The explicit branch preserves behavior; all41 observation"
        "s describe a safe refactor, contradict zero/None outputs, or add "
        "unsupported validation/style speculation."
    ),
    "security-bound-query-clean": (
        "The exact constant query and separately bound name tuple remain u"
        "nchanged. Local query assignment does not introduce injection; so"
        "urce also uses the query variable. Style/praise and imagined cust"
        "om-db risk do not demonstrate a new defect."
    ),
    "operations-idempotent-close-clean": (
        "Normal order remains flush then close; finally closes on flush fa"
        "ilure and does not suppress flush errors when close succeeds. Mos"
        "t observations praise this fix or allege false finally semantics/"
        "preexisting resource issues. A specific both-flush-and-close-fail"
        " masking allegation remains human-pending as a potential unlabele"
        "d scenario."
    ),
    "operations-seconds-clean": (
        "Python3 division already gives .5 seconds for500ms in both snapsh"
        "ots. Numeric float conversion and documentation preserve tested t"
        "iming. Python2/integer-truncation claims and unsupported invalid-"
        "input/type speculation do not establish a new current regression."
    ),
    "tests-empty-assertion-clean": (
        "values is present and returns a built-in empty list; length==0 re"
        "jects a nonempty list and current test passes. Praise/style, inve"
        "nted missing implementations and hypothetical unrelated returns a"
        "re not current failures. One specific weaker-type-discrimination "
        "allegation remains human-pending for test-contract interpretation"
        "."
    ),
    "tests-required-exception-clean": (
        'Captured parse(-1) raises ValueError("negative") and pytest.raise'
        's(...match="negative") validates both type and message. It does n'
        "ot silently accept a missing error. Advice about a missing match "
        "or imagined message mismatch contradicts the supplied unchanged i"
        "mplementation."
    ),
    "consumer-keyword-compatible-clean": (
        "Existing positional and timeout keyword calls still work; deadlin"
        "e is optional keyword-only and explicit None preserves deadline0."
        " All56 observations allege imagined required-argument breakage, u"
        "nsupported caller/security assumptions, safe extension behavior o"
        "r documentation/style preferences."
    ),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    provenance = json.loads((P / "adjudication-provenance.json").read_text())
    assert digest(P / "blinded-packet.json") == provenance["packet_sha256"]
    packet = json.loads((P / "blinded-packet.json").read_text())
    disposition = json.loads((P / "reviewed-case-dispositions.json").read_text())
    assert digest(P / "reviewed-case-dispositions.json") == provenance["dispositions_sha256"]
    proof_path = P / "original-control-results.json"
    assert digest(proof_path) == provenance["original_control_report_sha256"]
    proof = {row["case_id"]: row for row in json.loads(proof_path.read_text())["results"]}
    groups = {}
    for item in packet["items"]:
        groups.setdefault(item["case_id"], []).append(item)
    assert set(groups) == set(disposition) == set(REASONS)
    decisions, pending = [], []
    for case, items in sorted(groups.items()):
        gold = json.loads((CORPUS / "labels" / (case + ".json")).read_text())
        assert proof[case]["status"] == "passed"
        for name, expected in proof[case]["source_sha256"].items():
            assert digest(CORPUS / name) == expected
        assert digest(CORPUS / "labels" / (case + ".json")) == label_hashes[case]
        false, uncertain = set(disposition[case]["fp"]), set(disposition[case]["pending"])
        assert false.isdisjoint(uncertain) and false | uncertain <= set(range(len(items)))
        assert gold["defects"] or false | uncertain == set(range(len(items)))
        for index, item in enumerate(items):
            record = {
                "finding_id": item["finding_id"],
                "case_id": case,
                "packet_case_index": index,
                "method": "automated",
                "reviewer": REVIEWER,
            }
            rationale = REASONS[case] + " Exact observed assertion: " + item["finding"]["problem"]
            rationale += " Independent original executable control status: passed; "
            rationale += "gold trigger and source bytes verified, not a formal proof of all claims."
            if index in uncertain:
                pending.append(
                    {
                        **record,
                        "ambiguous": True,
                        "finding": item["finding"],
                        "rationale": "Actual human judgment required: a potentially correct core "
                        "mechanism has conflicting source/trigger/impact wording, insufficient "
                        "specificity, or a potentially unlabeled contract scenario. " + rationale,
                    }
                )
            else:
                defect = None if index in false else gold["defects"][0]["id"]
                decisions.append(
                    {**record, "ambiguous": False, "defect_id": defect, "rationale": rationale}
                )
    assert len(decisions) + len(pending) == len(packet["items"])
    assert len({d["finding_id"] for d in decisions + pending}) == len(packet["items"])
    for name, value in [
        ("automated-decisions.json", decisions),
        (
            "pending-human-adjudication.json",
            {"schema_version": 1, "human_judgments": 0, "items": pending},
        ),
    ]:
        (OUTPUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "source_observations": len(packet["items"]),
                "automated_decisions": len(decisions),
                "pending_human": len(pending),
                "matched_observations": sum(d["defect_id"] is not None for d in decisions),
                "false_observations": sum(d["defect_id"] is None for d in decisions),
            }
        )
    )


if __name__ == "__main__":
    main()
