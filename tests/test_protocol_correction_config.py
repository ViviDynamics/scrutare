"""Protocol correction is opt-in and never changes disabled canonical records."""

import json

import pytest
from test_citation_correction_config import V2

from scrutare.config import ConfigError, parse_config
from scrutare.evaluation.runner import comparison_configs


def test_protocol_setting_on_off_and_roundtrip():
    plain = parse_config(V2 + b"findings: {evidence: v2}\n")
    off = parse_config(V2 + b"findings: {evidence: v2, protocol_correction: false}\n")
    assert off == plain
    assert off.findings.protocol_correction is False
    assert off.to_dict()["findings"] == {"evidence": "v2"}
    on = parse_config(V2 + b"findings: {evidence: v2, protocol_correction: true}\n")
    assert on.findings.protocol_correction is True
    assert on.to_dict()["findings"] == {"evidence": "v2", "protocol_correction": True}
    assert parse_config(json.dumps(on.to_dict())) == on
    for name, arm in comparison_configs(on, comparison_set="focused").items():
        assert arm.findings.protocol_correction, name


@pytest.mark.parametrize("value", ["1", "null", "[]", "maybe"])
def test_protocol_setting_requires_boolean(value):
    with pytest.raises(ConfigError, match="protocol_correction: expected boolean"):
        parse_config(V2 + f"findings: {{evidence: v2, protocol_correction: {value}}}\n".encode())


def test_protocol_setting_requires_v2_and_openai():
    with pytest.raises(ConfigError, match="protocol_correction: requires evidence v2"):
        parse_config(V2 + b"findings: {protocol_correction: true}\n")
    with pytest.raises(ConfigError, match="protocol_correction: requires provider openai"):
        parse_config(
            V2.replace(b"provider: openai", b"provider: anthropic")
            + b"findings: {evidence: v2, protocol_correction: true}\n"
        )
