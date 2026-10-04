"""All resolved personas consume the same validated, confined artifact view."""

import importlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from test_review_inputs import CONFIG
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.engine.review_inputs import (
    PreparedReviewInputs,
    ReviewInputError,
    prepare_review_inputs,
)
from scrutare.personas import PersonaDefinition, resolve_personas


def descriptors():
    return importlib.import_module("scrutare.engine.persona_inputs")


def test_all_actual_personas_share_artifact_prompt_and_fixed_read_root(capture):
    module = descriptors()
    inputs = prepare_review_inputs(capture, parse_config(CONFIG))
    entries = (*parse_config(CONFIG).personas,
               PersonaDefinition("custom", "Review reliability.\nPreserve this exact text.\n"))
    expected = resolve_personas(entries)
    views = module.prepare_persona_inputs(inputs, iter(entries))
    assert isinstance(views, tuple) and len(views) == 5
    assert tuple(view.persona.name for view in views) == (
        "senior-dev", "junior-dev", "security", "devops", "custom",
    )
    assert tuple(view.persona for view in views) == expected
    assert {view.prompt for view in views} == {views[0].prompt}
    for view in views:
        assert view.inputs is inputs
        assert view.nare_input_args() == (
            view.prompt, "--tools", "read", "--root", str(inputs.root),
        )
        for name in ("diff.patch", "files.json", "context.json"):
            assert name in view.prompt
        visible = view.prompt.encode() + b"".join(p.read_bytes() for p in inputs.root.iterdir())
        for sentinel in (b"EXCLUDED_SENTINEL", b"HOSTILE_FIELD", b"SECRET_URL",
                         b"RAW_BODY_SENTINEL", b"DISCUSSION_SENTINEL", b"UNTRUSTED_TITLE"):
            assert sentinel not in visible
        for raw in ("metadata.json", "config.json", "config.yaml", "reviews.json",
                    "comments.json", "review_comments.json", "effective-files.json"):
            assert raw not in view.prompt
        with pytest.raises(FrozenInstanceError):
            view.inputs = inputs
        with pytest.raises(TypeError):
            view.nare_input_args(root=capture)


def test_empty_filtered_selection_stays_empty_for_every_persona(capture):
    raw = CONFIG + b"github: {paths: {include: ['missing/**']}}\n"
    config = parse_config(raw)
    (capture / "config.yaml").write_bytes(raw)
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    inputs = prepare_review_inputs(capture, config)
    views = descriptors().prepare_persona_inputs(inputs, config.personas)
    assert len(views) == 4
    for view in views:
        assert view.inputs.effective_files == ()
        assert view.nare_input_args()[-1] == str(inputs.root)
        assert (inputs.root / "diff.patch").read_bytes() == b""
        assert json.loads((inputs.root / "files.json").read_bytes()) == []
        assert "empty" in view.prompt.lower()


@pytest.mark.parametrize("field,value", [
    ("root", "raw"), ("root", Path("review-inputs")), ("root", None),
    ("head_sha", "wrong"), ("head_sha", None),
    ("effective_files", ["src/app.py"]), ("effective_files", ("docs/secret.md",)),
])
def test_direct_prepared_constructor_rejects_malformed_fields(capture, field, value):
    inputs = prepare_review_inputs(capture, parse_config(CONFIG))
    fields = {"root": inputs.root, "head_sha": inputs.head_sha,
              "effective_files": inputs.effective_files}
    fields[field] = value
    with pytest.raises(ReviewInputError):
        PreparedReviewInputs(**fields)


def test_direct_prepared_constructor_rejects_raw_run_root(capture):
    with pytest.raises(ReviewInputError):
        PreparedReviewInputs(capture, "abc123", ("src/app.py",))


@pytest.mark.parametrize("persona", [
    "senior-dev", None, PersonaDefinition("BAD NAME", "Review."),
    PersonaDefinition("custom", " "), PersonaDefinition("custom", None),
    PersonaDefinition(None, "Review."),
])
def test_direct_persona_constructor_rejects_invalid_personas(capture, persona):
    inputs = prepare_review_inputs(capture, parse_config(CONFIG))
    with pytest.raises(ReviewInputError):
        descriptors().PersonaReviewInput(persona, inputs)


@pytest.mark.parametrize("entries", [
    ["unknown"], ["security", "security"], [object()],
    [PersonaDefinition("bad name", "Review.")],
])
def test_resolution_rejects_unknown_duplicate_or_malformed_personas(capture, entries):
    inputs = prepare_review_inputs(capture, parse_config(CONFIG))
    with pytest.raises(ValueError):
        descriptors().prepare_persona_inputs(inputs, entries)


@pytest.mark.parametrize("operation", ["constructor", "prepare", "prompt", "args"])
def test_view_tampering_is_rejected_before_persona_visibility(capture, operation):
    module = descriptors()
    inputs = prepare_review_inputs(capture, parse_config(CONFIG))
    persona = resolve_personas(["security"])[0]
    view = module.PersonaReviewInput(persona, inputs)
    (inputs.root / "diff.patch").write_bytes(b"INJECTED_RAW_CONTENT")
    with pytest.raises(ReviewInputError):
        if operation == "constructor":
            module.PersonaReviewInput(persona, inputs)
        elif operation == "prepare":
            module.prepare_persona_inputs(inputs, [persona])
        elif operation == "prompt":
            _ = view.prompt
        else:
            view.nare_input_args()


def test_empty_persona_entries_still_validate_inputs(capture):
    module = descriptors()
    inputs = prepare_review_inputs(capture, parse_config(CONFIG))
    assert module.prepare_persona_inputs(inputs, []) == ()
    with pytest.raises(ReviewInputError):
        module.prepare_persona_inputs(capture, [])


def test_descriptor_preparation_launches_no_process(capture, monkeypatch):
    def no_process(*args, **kwargs):
        pytest.fail("persona descriptors must not launch sessions")

    monkeypatch.setattr("subprocess.run", no_process)
    monkeypatch.setattr("subprocess.Popen", no_process)
    inputs = prepare_review_inputs(capture, parse_config(CONFIG))
    for view in descriptors().prepare_persona_inputs(inputs, parse_config(CONFIG).personas):
        assert view.nare_input_args()[1:3] == ("--tools", "read")
