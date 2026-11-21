"""Installed nare proof: an opted-in rail's cut-off budget reaches nare and corrects a cut.

Through the real openai transport and the offline HTTP harness, a reply cut off at the
output cap is corrected once nare is sent ``--max-cutoffs 1``, and ends the session at the
first cut under the pinned zero.
"""

import json
import shutil

from test_installed_max_output_tokens import cut_off
from test_installed_request_retries import FINAL, READ, events, http_review
from test_installed_review import wheel_cli as wheel_cli
from test_nare_cli_integration import FINDING, load
from test_nare_cli_integration import installed as installed
from test_review_inputs import capture as capture

from scrutare.replay import replay_run

CUT = {"frames": cut_off(json.dumps(FINDING)[:40], {"prompt_tokens": 20,
                                                    "completion_tokens": 64})}
TURNS = [{"frames": READ}, CUT, {"frames": FINAL}]


def cutoff_events(attempt):
    return [e["detail"]["cutoff"] for e in events(attempt)
            if e["type"] == "progress" and "cutoff" in e["detail"]]


def test_a_cut_off_reply_is_corrected_within_the_configured_budget(
        capture, tmp_path, installed):
    result, outcome, observed = http_review(capture, tmp_path, installed, TURNS,
                                            max_cutoffs=1)
    assert result.status == "complete", (outcome.reason, observed["calls"])
    assert result.verdict.verdict == "changes_requested"
    attempt = outcome.artifact_directory
    invocation = load(attempt / "invocation.json")
    argv = invocation["argv"]
    assert argv[argv.index("--max-cutoffs") + 1] == "1"
    assert invocation["rail"]["max_cutoffs"] == 1
    assert load(capture / "config.json")["models"]["default"]["max_cutoffs"] == 1
    assert load(capture / "fanout.json")["personas"][0]["rail"]["max_cutoffs"] == 1
    # nare parsed the budget it was sent, spent one correction on the cut and said so.
    assert cutoff_events(attempt) == [{"kind": "max_tokens", "output_tokens": 64,
                                       "attempt": 1, "limit": 1, "corrected": True}]
    terminal = events(attempt)[-1]
    assert (terminal["status"], terminal["cutoffs"]) == ("done", 1)
    assert (terminal["budget"]["cutoffs"], terminal["budget"]["used_cutoffs"]) == (1, 1)
    assert load(attempt / "session.json")["cutoffs"] == 1
    # The correction is the one user message nare adds after the cut-off reply.
    payloads = [call["payload"] for call in observed["calls"]]
    assert len(payloads) == 3
    correction = payloads[2]["messages"][-1]
    assert correction["role"] == "user" and "cut off at the output limit" in json.dumps(
        correction)
    # The cut-off reply is a turn, and its tokens are charged like any other.
    assert terminal["turns"] == 3
    assert outcome.usage.total == 15 + 84 + 29 == result.usage.total
    assert outcome.accounting_complete
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


def test_without_the_option_the_first_cut_still_ends_the_session(capture, tmp_path, installed):
    result, outcome, observed = http_review(capture, tmp_path, installed, TURNS)
    assert result.status == "failed" and result.verdict is None
    assert (outcome.nare_status, outcome.stop_reason) == ("error", "max_tokens")
    assert len(observed["calls"]) == 2
    attempt = outcome.artifact_directory
    invocation = load(attempt / "invocation.json")
    assert invocation["argv"][invocation["argv"].index("--max-cutoffs") + 1] == "0"
    assert "max_cutoffs" not in invocation["rail"]
    assert "max_cutoffs" not in load(capture / "config.json")["models"]["default"]
    assert cutoff_events(attempt) == [{"kind": "max_tokens", "output_tokens": 64,
                                       "attempt": 1, "limit": 0, "corrected": False}]
    assert events(attempt)[-1]["budget"]["cutoffs"] == 0


def test_the_budget_changes_only_its_own_argv_value(capture, tmp_path, installed):
    second = capture.parent / "second-capture"
    shutil.copytree(capture, second)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _, pinned, _ = http_review(capture, tmp_path / "a", installed, TURNS)
    _, opted, _ = http_review(second, tmp_path / "b", installed, TURNS, max_cutoffs=2)

    def normalized(outcome, root, run):
        argv = load(outcome.artifact_directory / "invocation.json")["argv"]
        return [item.replace(str(run), "RUN").replace(str(root), "ROOT") for item in argv]
    default = normalized(pinned, tmp_path / "a", capture)
    sent = normalized(opted, tmp_path / "b", second)
    index = default.index("--max-cutoffs") + 1
    assert (default[index], sent[index]) == ("0", "2")
    assert default[:index] + default[index + 1:] == sent[:index] + sent[index + 1:]
