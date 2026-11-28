"""Native recovery policy is auditable without changing legacy replay."""

import json

import pytest

from scrutare.replay import audit


@pytest.mark.parametrize("flag", ["--max-cutoffs", "--request-retries", "--max-corrections"])
@pytest.mark.parametrize("mutation", ["changed", "missing", "duplicate", "inline"])
def test_contract_3_replay_refuses_changed_native_recovery_policy(tmp_path, flag, mutation):
    attempt = tmp_path / "sessions/security/attempt-0001"
    attempt.mkdir(parents=True)
    path = attempt / "invocation.json"
    document = {"contract": 3, "nare_version": "2026.11.80",
                "argv": ["nare", "run", "review", "--contract", "3", "--max-cutoffs", "0",
                "--request-retries", "0", "--max-corrections", "1"]}
    path.write_text(json.dumps(document))
    assert audit._native_recovery_issues(tmp_path) == ()
    argv = document["argv"]
    index = argv.index(flag)
    argv[index:index + 2] = {"changed": [flag, "2"], "missing": [],
                           "duplicate": [flag, argv[index + 1], flag, "2"],
                           "inline": [flag + "=" + argv[index + 1]]}[mutation]
    path.write_text(json.dumps(document))
    assert [issue.code for issue in audit._native_recovery_issues(tmp_path)] == [
        "native_recovery_policy_invalid"]


def test_legacy_replay_needs_no_new_native_controls(tmp_path):
    attempt = tmp_path / "sessions/security/attempt-0001"
    attempt.mkdir(parents=True)
    for contract in (1, 2):
        (attempt / "invocation.json").write_text(json.dumps({
            "contract": contract, "argv": ["nare", "run", "review"]}))
        assert audit._native_recovery_issues(tmp_path) == ()


@pytest.mark.parametrize("claimed", [2, 4, None])
@pytest.mark.parametrize("evidence", ["version", "argv", "stream", "session"])
def test_replay_cannot_downgrade_contract_3_to_bypass_policy(tmp_path, claimed, evidence):
    attempt = tmp_path / "sessions/security/attempt-0001"
    attempt.mkdir(parents=True)
    document = {"contract": claimed, "argv": ["nare", "run", "review"]}
    if evidence == "version":
        document["nare_version"] = "2026.11.80"
    elif evidence == "argv":
        document["argv"] += ["--contract", "3"]
    elif evidence == "stream":
        (attempt / "stdout.jsonl").write_text(json.dumps({"type": "result", "contract": 3}))
    else:
        (attempt / "session.json").write_text(json.dumps({"contract": 3}))
    (attempt / "invocation.json").write_text(json.dumps(document))
    assert [issue.code for issue in audit._native_recovery_issues(tmp_path)] == [
        "native_recovery_policy_invalid"]


def contract_4_invocation(attempt, **changes):
    document = {"contract": 4, "nare_version": "2026.11.135",
                "argv": ["nare", "run", "review", "--contract", "4", "--max-cutoffs", "0",
                         "--request-retries", "0", "--max-corrections", "1"]}
    document.update(changes)
    (attempt / "invocation.json").write_text(json.dumps(document))


def test_contract_4_replay_binds_the_same_native_recovery_policy(tmp_path):
    attempt = tmp_path / "sessions/security/attempt-0001"
    attempt.mkdir(parents=True)
    contract_4_invocation(attempt)
    (attempt / "stdout.jsonl").write_text(json.dumps(
        {"type": "result", "contract": 4, "nare": "2026.11.135"}))
    assert audit._native_recovery_issues(tmp_path) == ()
    contract_4_invocation(attempt, argv=["nare", "run", "review", "--contract", "4",
                                         "--max-cutoffs", "2", "--request-retries", "0",
                                         "--max-corrections", "1"])
    assert [issue.code for issue in audit._native_recovery_issues(tmp_path)] == [
        "native_recovery_policy_invalid"]


@pytest.mark.parametrize(("version", "recorded", "flag", "streamed"), [
    ("2026.11.135", 3, "4", 4), ("2026.11.135", 4, "3", 4), ("2026.11.135", 4, "4", 3),
    ("2026.11.134", 4, "4", 4)])
def test_a_contract_the_recorded_release_does_not_speak_is_refused(tmp_path, version, recorded,
                                                                   flag, streamed):
    attempt = tmp_path / "sessions/security/attempt-0001"
    attempt.mkdir(parents=True)
    contract_4_invocation(attempt, contract=recorded, nare_version=version,
                          argv=["nare", "run", "review", "--contract", flag, "--max-cutoffs",
                                "0", "--request-retries", "0", "--max-corrections", "1"])
    (attempt / "stdout.jsonl").write_text(json.dumps(
        {"type": "result", "contract": streamed, "nare": version}))
    assert [issue.code for issue in audit._native_recovery_issues(tmp_path)] == [
        "native_recovery_policy_invalid"]


@pytest.mark.parametrize("evidence", ["argv", "stream", "session"])
def test_replay_cannot_drop_contract_4_evidence_to_bypass_policy(tmp_path, evidence):
    attempt = tmp_path / "sessions/security/attempt-0001"
    attempt.mkdir(parents=True)
    document = {"contract": None, "argv": ["nare", "run", "review"]}
    if evidence == "argv":
        document["argv"] += ["--contract", "4"]
    elif evidence == "stream":
        (attempt / "stdout.jsonl").write_text(json.dumps({"type": "result", "contract": 4}))
    else:
        (attempt / "session.json").write_text(json.dumps({"contract": 4}))
    (attempt / "invocation.json").write_text(json.dumps(document))
    assert [issue.code for issue in audit._native_recovery_issues(tmp_path)] == [
        "native_recovery_policy_invalid"]
