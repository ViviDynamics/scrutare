"""Standalone standard-library host boundary for the reusable GitHub Action."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import cast

IMAGE = "ghcr.io/vividynamics/scrutare:2026.10.1"
VERSION = "2026.10.1"
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA = re.compile(r"[0-9a-f]{40}")


class ActionError(ValueError):
    """A host input or successful CLI record cannot be safely consumed."""


def scalar(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(unicodedata.category(char) == "Cc" for char in value)
    ):
        raise ActionError("invalid scalar")
    return value


def repository(value: object) -> str:
    text = scalar(value)
    if REPOSITORY.fullmatch(text) is None or any(part in {".", ".."} for part in text.split("/")):
        raise ActionError("invalid repository")
    return text


def relative_path(value: object) -> Path:
    text = scalar(value)
    if (
        text.startswith("/")
        or "\\" in text
        or any(part in {"", ".", ".."} for part in text.split("/"))
    ):
        raise ActionError("invalid relative path")
    return Path(text)


def absolute_path(value: object) -> Path:
    text = scalar(value)
    path = Path(text)
    if not path.is_absolute() or ":" in text or "\\" in text or ".." in path.parts:
        raise ActionError("invalid runner path")
    return path


def checked_path(path: Path, *, regular: bool = False) -> Path:
    for component in [*reversed(path.parents), path]:
        mode = component.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ActionError("symlink paths are refused")
    if regular and not stat.S_ISREG(path.lstat().st_mode):
        raise ActionError("a regular file is required")
    return path


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ActionError("duplicate JSON field")
        result[key] = value
    return result


def nonfinite(_value: str) -> object:
    raise ActionError("nonfinite JSON number")


def document(raw: bytes) -> dict[str, object]:
    value: object = json.loads(raw, object_pairs_hook=unique_object, parse_constant=nonfinite)
    if not isinstance(value, dict):
        raise ActionError("a JSON object is required")
    return cast("dict[str, object]", value)


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ActionError("invalid event object")
    return cast("dict[str, object]", value)


def positive_number(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ActionError("a positive PR number is required")
    return value


def emit(values: dict[str, str]) -> None:
    # Validate the entire record before writing any output lines.
    text = "".join(f"{key}={scalar(value)}\n" for key, value in values.items())
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(text)


def prepare() -> int:
    event_name = os.environ["GITHUB_EVENT_NAME"]
    if event_name not in {"pull_request", "pull_request_target"}:
        raise ActionError("unsupported event")
    event = document(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    if scalar(event.get("action")) not in {"opened", "reopened", "synchronize", "ready_for_review"}:
        raise ActionError("unsupported PR action")
    pr = mapping(event.get("pull_request"))
    number = positive_number(pr.get("number"))
    if positive_number(event.get("number")) != number:
        raise ActionError("inconsistent PR number")
    repo = repository(os.environ["GITHUB_REPOSITORY"])
    base = mapping(pr.get("base"))
    if repository(mapping(base.get("repo")).get("full_name")) != repo:
        raise ActionError("base repository does not match caller")
    base_sha = scalar(base.get("sha"))
    if SHA.fullmatch(base_sha) is None:
        raise ActionError("invalid base SHA")
    head_repo = repository(mapping(mapping(pr.get("head")).get("repo")).get("full_name"))
    if event_name == "pull_request" and head_repo != repo:
        raise ActionError("fork pull_request requires pull_request_target")
    config = relative_path(os.environ["SCRUTARE_ACTION_CONFIG"])
    if os.environ["SCRUTARE_ACTION_IMAGE"] != IMAGE:
        raise ActionError("runtime image must match the fixed release")
    run_id = scalar(os.environ["GITHUB_RUN_ID"])
    attempt = scalar(os.environ["GITHUB_RUN_ATTEMPT"])
    if any(re.fullmatch(r"[1-9][0-9]*", value) is None for value in (run_id, attempt)):
        raise ActionError("invalid runner identity")
    workspace = checked_path(absolute_path(os.environ["GITHUB_WORKSPACE"]))
    temporary = checked_path(absolute_path(os.environ["RUNNER_TEMP"]))
    root = Path(tempfile.mkdtemp(prefix="scrutare-action-", dir=temporary))
    checkout = Path(tempfile.mkdtemp(prefix="scrutare-action-checkout-", dir=workspace))
    evidence = root / "evidence"
    evidence.mkdir(mode=0o700)
    state_file = root / "state.json"
    checkout_relative = str(checkout.relative_to(workspace))
    state = {
        "repository": repo,
        "pr": number,
        "base_sha": base_sha,
        "config": str(config),
        "workspace": str(workspace),
        "checkout": checkout_relative,
    }
    with state_file.open("xb") as stream:
        os.chmod(state_file, 0o600)
        stream.write(json.dumps(state).encode("utf-8"))
    invocation_id = root.name.removeprefix("scrutare-action-")
    emit(
        {
            "state-file": str(state_file),
            "checkout-path": checkout_relative,
            "repository": repo,
            "base-sha": base_sha,
            "evidence-root": str(evidence),
            "artifact-name": f"scrutare-{run_id}-{attempt}-{invocation_id}",
        }
    )
    return 0


def stop_owned_process(process: subprocess.Popen[bytes], name: str) -> None:
    """Stop this invocation's container, then reap its launcher."""
    try:
        subprocess.run(
            ["docker", "stop", "--time", "10", name],
            check=False,
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def review() -> int:
    state_file = checked_path(
        absolute_path(os.environ["SCRUTARE_ACTION_STATE_FILE"]),
        regular=True,
    )
    root = state_file.parent
    temporary = checked_path(absolute_path(os.environ["RUNNER_TEMP"]))
    if (
        state_file.name != "state.json"
        or root.parent != temporary
        or not root.name.startswith("scrutare-action-")
    ):
        raise ActionError("state must belong to this Action")
    state = document(state_file.read_bytes())
    workspace = checked_path(absolute_path(state.get("workspace")))
    if workspace != absolute_path(os.environ["GITHUB_WORKSPACE"]):
        raise ActionError("state belongs to another workspace")
    checkout_relative = relative_path(state.get("checkout"))
    if len(checkout_relative.parts) != 1 or not checkout_relative.name.startswith(
        "scrutare-action-checkout-"
    ):
        raise ActionError("checkout must belong to this Action")
    checkout = checked_path(workspace / checkout_relative)
    repo = repository(state.get("repository"))
    if repo != repository(os.environ["GITHUB_REPOSITORY"]):
        raise ActionError("state belongs to another repository")
    number = positive_number(state.get("pr"))
    evidence = checked_path(root / "evidence")
    stdout_path = evidence / "cli-stdout.bin"
    stderr_path = evidence / "cli-stderr.bin"
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        os.chmod(stdout_path, 0o600)
        os.chmod(stderr_path, 0o600)
        source = checked_path(checkout / relative_path(state.get("config")), regular=True)
        # Copy bytes to a separate mount so Docker never sees the caller checkout.
        config = root / "config.yaml"
        with config.open("xb") as target:
            os.chmod(config, 0o400)
            target.write(source.read_bytes())
        work = evidence / "work"
        work.mkdir(mode=0o700)
        name = root.name
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--env",
            "HOME=/tmp",
            "--env",
            "GH_TOKEN",
            "--env",
            "OPENAI_API_KEY",
            "--env",
            "ANTHROPIC_API_KEY",
            "--volume",
            f"{work}:{work}:rw",
            "--volume",
            f"{config}:/scrutare-config.yaml:ro",
            "--workdir",
            str(work),
            IMAGE,
            "review",
            "--pr",
            f"https://github.com/{repo}/pull/{number}",
            "--config",
            "/scrutare-config.yaml",
        ]
        process = subprocess.Popen(command, cwd=work, stdout=stdout, stderr=stderr)
        try:
            code = process.wait()
        except KeyboardInterrupt:
            stop_owned_process(process, name)
            return 130
    if code != 0:
        return code if code > 0 else 128 - code
    raw = stdout_path.read_bytes()
    result = document(raw)
    if (
        type(result.get("schema_version")) is not int
        or result["schema_version"] != 1
        or result.get("scrutare_version") != VERSION
        or result.get("status") != "posted"
    ):
        raise ActionError("invalid successful result contract")
    verdict = scalar(result.get("verdict"))
    head = scalar(result.get("head_sha"))
    if verdict not in {"approve", "changes_requested", "escalated"} or SHA.fullmatch(head) is None:
        raise ActionError("invalid successful result scalars")
    run_text = scalar(result.get("run_dir"))
    run_path = Path(run_text)
    if run_path.is_absolute():
        run = absolute_path(run_text)
    else:
        run = work / relative_path(run_text)
    runs = work / ".scrutare" / "runs"
    if not run.is_relative_to(runs) or run == runs:
        raise ActionError("run path is outside this invocation")
    checked_path(run)
    saved = checked_path(run / "result.json", regular=True)
    if saved.read_bytes() != raw:
        raise ActionError("CLI output does not match persisted result bytes")
    emit({"verdict": verdict, "head-sha": head, "run-dir": str(run)})
    return 0


def interrupt(_number: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "review"))
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            return prepare()
        return review()
    except KeyboardInterrupt:
        return 130
    except (ActionError, OSError, KeyError, json.JSONDecodeError, UnicodeError):
        print("scrutare action: invalid input or unavailable owned evidence", file=sys.stderr)
        return 1


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    raise SystemExit(main())
