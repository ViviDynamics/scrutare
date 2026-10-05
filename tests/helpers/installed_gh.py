"""Offline gh executable: preserve stdin and emulate only the fixture endpoints."""

import hashlib
import json
import os
import sys
from pathlib import Path

spec = json.loads(Path(os.environ["SCRUTARE_GH_SPEC"]).read_bytes())
log = Path(os.environ["SCRUTARE_GH_LOG"])
args = sys.argv[1:]
payload = sys.stdin.buffer.read() if "POST" in args else b""
snapshots = {}
if payload:
    run, = Path(".scrutare/runs").iterdir()
    snapshots = {name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                 for name in ("diff.patch", "config.yaml", "config.json", "findings.json",
                              "verdict.json")}
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"args": args, "stdin": payload.decode("utf-8"),
                             "artifacts_before_post": snapshots,
                             "credential_names": [n for n in os.environ
                                                  if "KEY" in n or "TOKEN" in n]}) + "\n")
endpoint = next((arg for arg in args if arg.startswith("repos/")), None)
if args == ["repo", "view", "--json", "nameWithOwner"]:
    print(json.dumps({"nameWithOwner": "owner/repo"}))
elif "POST" in args:
    assert endpoint == "repos/owner/repo/pulls/12/reviews"
    assert "--include" in args and args[args.index("--input") + 1] == "-"
    if spec["case"] == "uncertain":
        print("connection ended before a receipt", file=sys.stderr)
        raise SystemExit(1)
    posted = json.loads(payload)
    states = {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED",
              "COMMENT": "COMMENTED"}
    receipt = {"id": 901, "html_url": "https://github.com/owner/repo/pull/12#pullrequestreview-901",
               "commit_id": posted["commit_id"], "body": posted["body"],
               "state": states[posted["event"]], "user": {"login": "offline-reviewer"}}
    sys.stdout.buffer.write(b"HTTP/2 201 Created\r\nContent-Type: application/json\r\n\r\n"
                            + json.dumps(receipt, ensure_ascii=False).encode())
elif "user" in args and "--include" in args:
    print('HTTP/2 200 OK\r\nContent-Type: application/json\r\n\r\n'
          '{"login":"offline-reviewer"}')
elif "Accept: application/vnd.github.v3.diff" in args:
    sys.stdout.buffer.write(spec["diff"].encode("utf-8"))
elif "--paginate" in args:
    if endpoint.endswith("/files"):
        print(json.dumps([spec["files"]]))
    else:
        print(json.dumps([[{"body": "DISCUSSION_SENTINEL"}]]))
elif endpoint == "repos/owner/repo/pulls/12":
    reads = sum(json.loads(line)["args"] == ["api", endpoint]
                for line in log.read_text().splitlines())
    if spec["case"] == "closed" and reads >= 3:
        spec["metadata"]["state"] = "closed"
    print(json.dumps(spec["metadata"]))
else:
    raise AssertionError(f"Unexpected gh arguments: {args}")
