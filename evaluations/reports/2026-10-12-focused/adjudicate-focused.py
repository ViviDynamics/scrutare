"""Reproduce zero judgments only for the exact frozen empty production packet."""

import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--experiment", type=Path, default=Path(__file__).resolve().parent)
parser.add_argument("--corpus", type=Path, required=True)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
root = args.experiment.resolve()
OUTPUT = (args.output or root).resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)
provenance = json.loads((root / "adjudication-provenance.json").read_text())
packet = root / "blinded-packet.json"
assert hashlib.sha256(packet.read_bytes()).hexdigest() == provenance["packet_sha256"]
assert json.loads(packet.read_text())["items"] == []
for name, expected in provenance["source_sha256"].items():
    assert hashlib.sha256((args.corpus / name).read_bytes()).hexdigest() == expected
for case in json.loads((root / "snapshot.json").read_text())["cases"]:
    path = args.corpus / "labels" / (case["id"] + ".json")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == case["labels_sha256"]
(OUTPUT / "automated-decisions.json").write_text("[]\n")
(OUTPUT / "pending-human-adjudication.json").write_text(
    json.dumps(
        {
            "schema_version": 1,
            "human_judgments": 0,
            "items": [],
            "reason": "No valid surviving source observations; all raw refusals remain r"
            + "etained, never counted as clean.",
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
print(
    json.dumps(
        {
            "source_observations": 0,
            "automated_judgments": 0,
            "human_judgments": 0,
            "pending_human": 0,
        }
    )
)
