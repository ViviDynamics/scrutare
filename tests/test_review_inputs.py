"""Exercise the real local capture projection and its immutable artifact boundary."""

import importlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from scrutare.config import parse_config
from scrutare.findings import Anchor, parse_diff

CONFIG = b"# retained raw config\r\nmodels: {default: {model: test-model}}\r\n"
SOURCE = (
    b"diff --git a/src/app.py b/src/app.py\r\n"
    b"--- a/src/app.py\r\n+++ b/src/app.py\r\n"
    b"@@ -1 +1 @@\r\n-old\r\n+new\r\n\\ No newline at end of file\r\n"
)
DOCS = (
    b"diff --git a/docs/secret.md b/docs/secret.md\n"
    b"--- a/docs/secret.md\n+++ b/docs/secret.md\n"
    b"@@ -1 +1 @@\n-old\n+EXCLUDED_SENTINEL\n"
)
BINARY = (
    b"diff --git a/image.bin b/image.bin\n"
    b"Binary files a/image.bin and b/image.bin differ\n"
)
MODE = b"diff --git a/runner b/runner\nold mode 100644\nnew mode 100755\n"


def preparation():
    return importlib.import_module("scrutare.engine.review_inputs")


def read_json(path):
    return json.loads(path.read_bytes())


def save_json(path, data):
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")


@pytest.fixture
def capture(tmp_path):
    run = tmp_path / "run-captured"
    run.mkdir()
    save_json(run / "metadata.json", {
        "schema_version": 1,
        "scrutare_version": "0.1.0",
        "status": "ingested",
        "repository": "owner/repo",
        "pr_number": 12,
        "head_sha": "abc123",
        "pull_request": {
            "number": 12, "state": "open", "merged": False,
            "head": {"sha": "abc123"},
            "base": {"ref": "main", "sha": "base123", "repo": {"full_name": "owner/repo"}},
            "changed_files": 4, "title": "UNTRUSTED_TITLE", "body": "RAW_BODY_SENTINEL",
        },
    })
    save_json(run / "files.json", [
        {"filename": "src/app.py", "status": "modified", "patch": "HOSTILE_FIELD",
         "raw_url": "https://invalid/SECRET_URL", "body": "RAW_BODY_SENTINEL"},
        {"filename": "docs/secret.md", "status": "modified"},
        {"filename": "image.bin", "status": "modified"},
        {"filename": "runner", "status": "modified"},
    ])
    (run / "diff.patch").write_bytes(SOURCE + DOCS + BINARY + MODE)
    (run / "config.yaml").write_bytes(CONFIG)
    save_json(run / "config.json", parse_config(CONFIG).to_dict())
    for name in ("reviews.json", "comments.json", "review_comments.json"):
        save_json(run / name, [{"body": "DISCUSSION_SENTINEL"}])
    return run


def test_prepares_minimal_filtered_artifacts_and_preserves_raw_evidence(capture):
    before = {p.name: p.read_bytes() for p in capture.iterdir()}
    inputs = preparation().prepare_review_inputs(capture, parse_config(CONFIG))
    assert inputs.root == (capture / "review-inputs").resolve()
    assert inputs.head_sha == "abc123"
    assert inputs.effective_files == ("src/app.py", "image.bin", "runner")
    assert read_json(capture / "effective-files.json") == {
        "schema_version": 1, "head_sha": "abc123", "include": [],
        "exclude": ["docs/**", "*.md"], "files": ["src/app.py", "image.bin", "runner"],
        "config_sha256": "dadd210718b656545319572f88a256c17a7f95a33a5e7d81d87c38775f2c0bf2",
    }
    assert {p.name for p in inputs.root.iterdir()} == {"diff.patch", "files.json", "context.json"}
    assert (inputs.root / "diff.patch").read_bytes() == SOURCE + BINARY + MODE
    assert read_json(inputs.root / "files.json") == [
        {"filename": "src/app.py", "status": "modified"},
        {"filename": "image.bin", "status": "modified"},
        {"filename": "runner", "status": "modified"},
    ]
    assert read_json(inputs.root / "context.json") == {
        "repository": "owner/repo", "pr_number": 12, "head_sha": "abc123", "base_sha": "base123",
    }
    assert parse_diff((inputs.root / "diff.patch").read_bytes()) == frozenset({
        Anchor("src/app.py", 1, "LEFT"), Anchor("src/app.py", 1, "RIGHT"),
    })
    visible = b"".join(p.read_bytes() for p in inputs.root.iterdir())
    for hidden in (b"EXCLUDED_SENTINEL", b"HOSTILE_FIELD", b"SECRET_URL", b"RAW_BODY_SENTINEL",
                   b"DISCUSSION_SENTINEL", b"UNTRUSTED_TITLE"):
        assert hidden not in visible
    assert all((capture / name).read_bytes() == raw for name, raw in before.items())
    with pytest.raises(FrozenInstanceError):
        inputs.root = capture


def test_repeat_preparation_only_returns_the_exact_existing_view(capture):
    module = preparation()
    first = module.prepare_review_inputs(capture, parse_config(CONFIG))
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in first.root.iterdir()}
    manifest = capture / "effective-files.json"
    before[manifest] = (manifest.read_bytes(), manifest.stat().st_mtime_ns)
    assert module.prepare_review_inputs(capture, parse_config(CONFIG)) == first
    assert all((p.read_bytes(), p.stat().st_mtime_ns) == saved for p, saved in before.items())
    module.validate_prepared_inputs(first)


def test_empty_selection_persists_an_empty_view(capture):
    raw = CONFIG + b"github: {paths: {include: [absent/**]}}\n"
    config = parse_config(raw)
    (capture / "config.yaml").write_bytes(raw)
    save_json(capture / "config.json", config.to_dict())
    inputs = preparation().prepare_review_inputs(capture, config)
    assert inputs.effective_files == ()
    assert (inputs.root / "diff.patch").read_bytes() == b""
    assert read_json(inputs.root / "files.json") == []
    assert read_json(capture / "effective-files.json")["files"] == []
    assert (capture / "diff.patch").read_bytes() == SOURCE + DOCS + BINARY + MODE


@pytest.mark.parametrize("status", ["renamed", "copied"])
def test_exclusion_of_previous_identity_removes_the_entire_movement(capture, status):
    verb = "rename" if status == "renamed" else "copy"
    moved = (
        f"diff --git a/docs/old.md b/src/new.py\n"
        f"similarity index 100%\n{verb} from docs/old.md\n{verb} to src/new.py\n"
    ).encode()
    save_json(capture / "files.json", [{"filename": "src/new.py", "status": status,
                                       "previous_filename": "docs/old.md"}])
    data = read_json(capture / "metadata.json")
    data["pull_request"]["changed_files"] = 1
    save_json(capture / "metadata.json", data)
    (capture / "diff.patch").write_bytes(moved)
    inputs = preparation().prepare_review_inputs(capture, parse_config(CONFIG))
    assert inputs.effective_files == ()
    assert (inputs.root / "diff.patch").read_bytes() == b""
    assert read_json(inputs.root / "files.json") == []
    assert (capture / "diff.patch").read_bytes() == moved


@pytest.mark.parametrize("status, patch, previous", [
    ("added", b"diff --git a/new.py b/new.py\nnew file mode 100644\n"
     b"--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+new\n", None),
    ("removed", b"diff --git a/new.py b/new.py\ndeleted file mode 100644\n"
     b"--- a/new.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n", None),
    ("renamed", b"diff --git a/old.py b/new.py\nsimilarity index 100%\n"
     b"rename from old.py\nrename to new.py\n", "old.py"),
    ("copied", b"diff --git a/old.py b/new.py\nsimilarity index 100%\n"
     b"copy from old.py\ncopy to new.py\n", "old.py"),
])
def test_projects_additions_deletions_and_visible_movement(capture, status, patch, previous):
    save_json(capture / "files.json", [{"filename": "new.py", "status": status,
                                       "previous_filename": previous}])
    data = read_json(capture / "metadata.json")
    data["pull_request"]["changed_files"] = 1
    save_json(capture / "metadata.json", data)
    (capture / "diff.patch").write_bytes(patch)
    inputs = preparation().prepare_review_inputs(capture, parse_config(CONFIG))
    assert inputs.effective_files == ("new.py",)
    assert (inputs.root / "diff.patch").read_bytes() == patch
    assert read_json(inputs.root / "files.json") == [{"filename": "new.py", "status": status}]


@pytest.mark.parametrize("mutation", [
    "schema", "schema-bool", "status", "repository-empty", "repository-invalid",
    "target-repository", "number", "number-bool", "head", "head-empty", "pr-head", "pr-number",
    "count", "closed", "missing-yaml", "missing-json", "json-config", "yaml-config",
    "supplied-config", "invalid-json", "duplicate-json", "nonfinite-json",
])
def test_invalid_capture_or_config_fails_before_creating_any_projection(capture, mutation,
                                                                      monkeypatch):
    module = preparation()
    data = read_json(capture / "metadata.json")
    config = parse_config(CONFIG)
    if mutation == "schema":
        data["schema_version"] = 2
    elif mutation == "schema-bool":
        data["schema_version"] = True
    elif mutation == "status":
        data["status"] = "prepared"
    elif mutation == "repository-empty":
        data["repository"] = ""
    elif mutation == "repository-invalid":
        data["repository"] = "HOSTILE_METADATA"
    elif mutation == "target-repository":
        data["pull_request"]["base"]["repo"]["full_name"] = "other/repo"
    elif mutation == "number":
        data["pr_number"] = 13
    elif mutation == "number-bool":
        data["pr_number"] = True
    elif mutation == "head":
        data["head_sha"] = "different"
    elif mutation == "head-empty":
        data["head_sha"] = ""
    elif mutation == "pr-head":
        data["pull_request"]["head"] = {}
    elif mutation == "pr-number":
        data["pull_request"]["number"] = 13
    elif mutation == "count":
        data["pull_request"]["changed_files"] = 5
    elif mutation == "closed":
        data["pull_request"]["state"] = "closed"
    elif mutation == "missing-yaml":
        (capture / "config.yaml").unlink()
    elif mutation == "missing-json":
        (capture / "config.json").unlink()
    elif mutation == "json-config":
        save_json(capture / "config.json", {"strategy": "debate"})
    elif mutation == "yaml-config":
        (capture / "config.yaml").write_bytes(CONFIG + b"strategy: debate\n")
    elif mutation == "supplied-config":
        config = parse_config(CONFIG + b"strategy: debate\n")
    save_json(capture / "metadata.json", data)
    if mutation == "invalid-json":
        (capture / "metadata.json").write_bytes(b"{HOSTILE_DIAGNOSTIC")
    elif mutation == "duplicate-json":
        raw = (capture / "metadata.json").read_bytes()
        (capture / "metadata.json").write_bytes(raw.replace(
            b'"schema_version": 1', b'"schema_version": 2, "schema_version": 1',
        ))
    elif mutation == "nonfinite-json":
        (capture / "config.json").write_bytes(b'{"value": NaN}')

    def no_network(*args, **kwargs):
        pytest.fail("preparation must never infer a repository or launch a process")

    monkeypatch.setattr("subprocess.run", no_network)
    before = {p.name: p.read_bytes() for p in capture.iterdir()}
    with pytest.raises(module.ReviewInputError, match="[Pp]repar|review inputs") as error:
        module.prepare_review_inputs(capture, config)
    assert "HOSTILE" not in str(error.value)
    assert {p.name: p.read_bytes() for p in capture.iterdir()} == before


@pytest.mark.parametrize("mutation", [
    "missing-section", "extra-section", "duplicate-section", "different-file", "invalid-file",
    "duplicate-file", "incorrect-status", "incorrect-previous", "missing-previous",
    "malformed-diff",
])
def test_file_section_incoherence_is_rejected_even_for_excluded_files(capture, mutation):
    module = preparation()
    files = read_json(capture / "files.json")
    patch = (capture / "diff.patch").read_bytes()
    if mutation == "missing-section":
        patch = SOURCE + BINARY + MODE
    elif mutation == "extra-section":
        patch += b"diff --git a/extra b/extra\nold mode 100644\nnew mode 100755\n"
    elif mutation == "duplicate-section":
        patch += DOCS
    elif mutation == "different-file":
        files[1]["filename"] = "docs/other.md"
    elif mutation == "invalid-file":
        files[1]["filename"] = "../HOSTILE"
    elif mutation == "duplicate-file":
        files[1]["filename"] = "src/app.py"
    elif mutation == "incorrect-status":
        files[1]["status"] = "added"
    elif mutation == "incorrect-previous":
        files[1]["status"] = "renamed"
        files[1]["previous_filename"] = "docs/other.md"
    elif mutation == "missing-previous":
        files[1]["status"] = "renamed"
    elif mutation == "malformed-diff":
        patch = b"HOSTILE_DIFF"
    save_json(capture / "files.json", files)
    (capture / "diff.patch").write_bytes(patch)
    with pytest.raises(module.ReviewInputError):
        module.prepare_review_inputs(capture, parse_config(CONFIG))
    assert not (capture / "review-inputs").exists()
    assert not (capture / "effective-files.json").exists()


@pytest.mark.parametrize("artifact", ["diff.patch", "files.json", "context.json", "manifest"])
@pytest.mark.parametrize("mutation", ["change", "remove", "symlink"])
def test_existing_artifact_tampering_is_rejected_without_overwriting(capture, artifact, mutation):
    module = preparation()
    inputs = module.prepare_review_inputs(capture, parse_config(CONFIG))
    path = capture / "effective-files.json" if artifact == "manifest" else inputs.root / artifact
    saved = path.read_bytes()
    if mutation == "change":
        path.write_bytes(saved + b" ")
    elif mutation == "remove":
        path.unlink()
    else:
        target = capture.parent / "outside-artifact"
        target.write_bytes(saved)
        path.unlink()
        path.symlink_to(target)
    with pytest.raises(module.ReviewInputError):
        module.prepare_review_inputs(capture, parse_config(CONFIG))
    with pytest.raises(module.ReviewInputError):
        module.validate_prepared_inputs(inputs)
    if mutation == "change":
        assert path.read_bytes() == saved + b" "
    elif mutation == "symlink":
        assert path.is_symlink() and target.read_bytes() == saved
    else:
        assert not path.exists()


@pytest.mark.parametrize("extra", ["extra-file", "extra-dir", "extra-symlink"])
def test_existing_root_must_contain_only_the_three_expected_files(capture, extra):
    module = preparation()
    inputs = module.prepare_review_inputs(capture, parse_config(CONFIG))
    target = inputs.root / "extra"
    if extra == "extra-file":
        target.write_bytes(b"RAW_BODY_SENTINEL")
    elif extra == "extra-dir":
        target.mkdir()
    else:
        target.symlink_to(capture / "diff.patch")
    with pytest.raises(module.ReviewInputError):
        module.prepare_review_inputs(capture, parse_config(CONFIG))
    assert target.exists()


@pytest.mark.parametrize("artifact", ["metadata.json", "files.json", "diff.patch", "config.yaml",
                                     "config.json", "review-inputs", "effective-files.json"])
def test_capture_and_destination_symlinks_are_rejected(capture, artifact):
    module = preparation()
    target = capture.parent / "outside"
    path = capture / artifact
    if artifact == "review-inputs":
        target.mkdir()
    else:
        target.write_bytes(path.read_bytes() if path.exists() else b"untouched")
        path.unlink(missing_ok=True)
    path.symlink_to(target)
    with pytest.raises(module.ReviewInputError):
        module.prepare_review_inputs(capture, parse_config(CONFIG))
    assert path.is_symlink()
    if target.is_file():
        assert target.read_bytes() != b""


def test_run_directory_symlink_is_rejected(capture):
    module = preparation()
    alias = capture.parent / "run-alias"
    alias.symlink_to(capture, target_is_directory=True)
    with pytest.raises(module.ReviewInputError):
        module.prepare_review_inputs(alias, parse_config(CONFIG))
    assert not (capture / "review-inputs").exists()


@pytest.mark.parametrize("failure", ["diff.patch", "context.json", "effective-files.json"])
def test_partial_standalone_failure_only_removes_new_projection(capture, monkeypatch, failure):
    module = preparation()
    before = {p.name: p.read_bytes() for p in capture.iterdir()}
    original = Path.open

    def fail_write(path, mode="r", *args, **kwargs):
        if path.name == failure and ("x" in mode or "w" in mode):
            raise OSError("HOSTILE_DISK_DIAGNOSTIC")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_write)
    with pytest.raises(module.ReviewInputError, match="[Pp]repar|review inputs") as error:
        module.prepare_review_inputs(capture, parse_config(CONFIG))
    assert "HOSTILE_DISK_DIAGNOSTIC" not in str(error.value)
    assert {p.name: p.read_bytes() for p in capture.iterdir()} == before


@pytest.mark.parametrize("mutation", ["config", "raw-head", "raw-diff", "raw-files"])
def test_changed_capture_cannot_silently_replace_a_started_view(capture, mutation):
    module = preparation()
    inputs = module.prepare_review_inputs(capture, parse_config(CONFIG))
    original = {p.name: p.read_bytes() for p in inputs.root.iterdir()}
    config = parse_config(CONFIG)
    if mutation == "config":
        raw = CONFIG + b"github: {paths: {exclude: []}}\n"
        config = parse_config(raw)
        (capture / "config.yaml").write_bytes(raw)
        save_json(capture / "config.json", config.to_dict())
    elif mutation == "raw-head":
        metadata = read_json(capture / "metadata.json")
        metadata["head_sha"] = metadata["pull_request"]["head"]["sha"] = "newhead"
        save_json(capture / "metadata.json", metadata)
    elif mutation == "raw-diff":
        (capture / "diff.patch").write_bytes(
            SOURCE.replace(b"+new", b"+tampered") + DOCS + BINARY + MODE,
        )
    else:
        files = read_json(capture / "files.json")
        files[0]["status"] = "changed"
        save_json(capture / "files.json", files)
    with pytest.raises(module.ReviewInputError):
        module.prepare_review_inputs(capture, config)
    assert {p.name: p.read_bytes() for p in inputs.root.iterdir()} == original


@pytest.mark.parametrize("mutation", ["root", "head", "files"])
def test_prepared_validation_rejects_descriptor_fields_that_disagree(capture, mutation):
    module = preparation()
    inputs = module.prepare_review_inputs(capture, parse_config(CONFIG))
    with pytest.raises(module.ReviewInputError):
        forged = replace(inputs, **{
            "root": {"root": capture}, "head": {"head_sha": "different"},
            "files": {"effective_files": ("docs/secret.md",)},
        }[mutation])
        module.validate_prepared_inputs(forged)


def test_non_path_config_changes_cannot_reuse_a_started_view(capture):
    module = preparation()
    inputs = module.prepare_review_inputs(capture, parse_config(CONFIG))
    before = {p.name: p.read_bytes() for p in inputs.root.iterdir()}
    manifest = (capture / "effective-files.json").read_bytes()
    raw = CONFIG + b"strategy: debate\n"
    config = parse_config(raw)
    (capture / "config.yaml").write_bytes(raw)
    save_json(capture / "config.json", config.to_dict())
    with pytest.raises(module.ReviewInputError):
        module.prepare_review_inputs(capture, config)
    with pytest.raises(module.ReviewInputError):
        module.validate_prepared_inputs(inputs)
    assert (capture / "effective-files.json").read_bytes() == manifest
    assert {p.name: p.read_bytes() for p in inputs.root.iterdir()} == before


@pytest.mark.parametrize("existing", ["empty-root", "partial-root", "manifest-only"])
def test_preexisting_partial_artifacts_are_preserved_and_never_completed(capture, existing):
    module = preparation()
    root = capture / "review-inputs"
    manifest = capture / "effective-files.json"
    if existing == "manifest-only":
        manifest.write_bytes(b"PREEXISTING_AUDIT")
    else:
        root.mkdir()
        if existing == "partial-root":
            (root / "diff.patch").write_bytes(b"PREEXISTING_AUDIT")
    with pytest.raises(module.ReviewInputError):
        module.prepare_review_inputs(capture, parse_config(CONFIG))
    if existing == "manifest-only":
        assert manifest.read_bytes() == b"PREEXISTING_AUDIT" and not root.exists()
    else:
        assert not manifest.exists()
        assert {p.name for p in root.iterdir()} == (
            {"diff.patch"} if existing == "partial-root" else set()
        )
        if existing == "partial-root":
            assert (root / "diff.patch").read_bytes() == b"PREEXISTING_AUDIT"


def test_genuinely_empty_capture_is_prepared_without_fallback(capture):
    module = preparation()
    metadata = read_json(capture / "metadata.json")
    metadata["pull_request"]["changed_files"] = 0
    save_json(capture / "metadata.json", metadata)
    save_json(capture / "files.json", [])
    (capture / "diff.patch").write_bytes(b"")
    inputs = module.prepare_review_inputs(capture, parse_config(CONFIG))
    assert inputs.effective_files == ()
    assert (inputs.root / "diff.patch").read_bytes() == b""
    assert read_json(inputs.root / "files.json") == []
    module.validate_prepared_inputs(inputs)


def test_projection_keeps_diff_section_order_and_file_api_order_independently(capture):
    files = read_json(capture / "files.json")
    save_json(capture / "files.json", list(reversed(files)))
    inputs = preparation().prepare_review_inputs(capture, parse_config(CONFIG))
    assert inputs.effective_files == ("runner", "image.bin", "src/app.py")
    assert read_json(capture / "effective-files.json")["files"] == [
        "runner", "image.bin", "src/app.py",
    ]
    assert (inputs.root / "diff.patch").read_bytes() == SOURCE + BINARY + MODE
    assert [f["filename"] for f in read_json(inputs.root / "files.json")] == [
        "runner", "image.bin", "src/app.py",
    ]


def test_successful_preparation_and_validation_launch_no_process(capture, monkeypatch):
    module = preparation()

    def no_process(*args, **kwargs):
        pytest.fail("local input preparation must not launch a process")

    monkeypatch.setattr("subprocess.run", no_process)
    monkeypatch.setattr("subprocess.Popen", no_process)
    inputs = module.prepare_review_inputs(capture, parse_config(CONFIG))
    module.validate_prepared_inputs(inputs)
    assert (inputs.root / "diff.patch").read_bytes() == SOURCE + BINARY + MODE


def test_deeply_nested_untrusted_json_has_a_safe_local_error(capture):
    module = preparation()
    (capture / "metadata.json").write_bytes(b"[" * 2000 + b"0" + b"]" * 2000)
    with pytest.raises(module.ReviewInputError):
        module.prepare_review_inputs(capture, parse_config(CONFIG))
    assert not (capture / "review-inputs").exists()


def test_cleanup_failure_still_has_a_safe_preparation_error(capture, monkeypatch):
    module = preparation()
    original = Path.open

    def fail_write(path, mode="r", *args, **kwargs):
        if path.name == "context.json" and "x" in mode:
            raise OSError("HOSTILE_WRITE")
        return original(path, mode, *args, **kwargs)

    def fail_cleanup(*args, **kwargs):
        raise OSError("HOSTILE_CLEANUP")

    monkeypatch.setattr(Path, "open", fail_write)
    monkeypatch.setattr(module.shutil, "rmtree", fail_cleanup)
    with pytest.raises(module.ReviewInputError) as error:
        module.prepare_review_inputs(capture, parse_config(CONFIG))
    assert "HOSTILE" not in str(error.value)
    assert "prepar" in str(error.value).lower()
    assert (capture / "metadata.json").is_file()


def test_shared_validation_rejects_untyped_inputs_safely():
    module = preparation()
    with pytest.raises(module.ReviewInputError):
        module.validate_prepared_inputs("raw-run")
