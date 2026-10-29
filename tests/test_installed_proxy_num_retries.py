"""The external nare console sends the opt-in retry limit on every HTTP turn."""

import asyncio
import json

import pytest
from test_fanout import configure
from test_installed_structured_output import http_scenario
from test_nare_cli_integration import installed as installed
from test_nare_cli_integration import load, observations, offline_runtime
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.engine.strategy import run_review
from scrutare.replay import replay_run


@pytest.mark.parametrize("retries", [None, 0, 2])
def test_every_tool_answer_and_schema_turn_keeps_the_retry_setting(
        capture, tmp_path, installed, retries):
    document = configure(capture, personas=["senior-dev"], review=200, per=200).to_dict()
    document["models"]["default"] = {
        "provider": "openai", "model": "offline-model",
        "base_url": "https://offline.invalid/v1", "structured_output": "json_schema",
        **({"proxy_num_retries": retries} if retries is not None else {}),
    }
    config = parse_config(json.dumps(document))
    (capture / "config.yaml").write_text(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    runtime = offline_runtime(tmp_path, installed, default=http_scenario())
    specification = tmp_path / "offline-spec.json"
    spec = load(specification)
    spec["environment"] = {"OPENAI_API_KEY": "offline-test-key"}
    specification.write_text(json.dumps(spec))

    result = asyncio.run(run_review(capture, config, runtime=runtime))
    assert result.status == "complete"
    outcome = result.initial.outcomes[0]
    payloads = [call["payload"] for call in observations(outcome, http_sse=True)["calls"]]
    assert len(payloads) == 3
    assert ["response_format" in body for body in payloads] == [False, False, True]
    invocation = load(outcome.artifact_directory / "invocation.json")
    if retries is None:
        assert all("num_retries" not in body for body in payloads)
        assert "--proxy-num-retries" not in invocation["argv"]
        assert "proxy_num_retries" not in invocation["rail"]
        assert "proxy_num_retries" not in load(capture / "config.json")["models"]["default"]
    else:
        assert [body["num_retries"] for body in payloads] == [retries] * 3
        argv = invocation["argv"]
        index = argv.index("--proxy-num-retries")
        assert argv[index + 1] == str(retries)
        assert invocation["rail"]["proxy_num_retries"] == retries
        assert load(capture / "fanout.json")["personas"][0]["rail"]["proxy_num_retries"] == retries
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0
