"""Persistence for verdict artifacts in caller-owned run directories."""

from pathlib import Path
from tempfile import NamedTemporaryFile

from scrutare.findings.verdict import Verdict


class VerdictArtifactError(ValueError):
    """A safe actionable diagnostic for invalid or unwritable verdict artifacts."""


def write_verdict(run_dir: Path, verdict: Verdict) -> Path:
    """Atomically replace verdict.json in an existing caller-owned run directory."""
    if not isinstance(verdict, Verdict):
        raise VerdictArtifactError("verdict: expected a Verdict")
    try:
        data = verdict.to_bytes()
    except (TypeError, ValueError):
        raise VerdictArtifactError(
            "verdict.json: cannot encode verdict; check finding text is valid UTF-8"
        ) from None

    destination = run_dir / "verdict.json"
    temporary: Path | None = None
    try:
        if not run_dir.is_dir():
            raise VerdictArtifactError("run directory: expected an existing directory") from None
        with NamedTemporaryFile(
            mode="wb", prefix=".verdict-", suffix=".tmp", dir=run_dir, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.replace(destination)
        temporary = None
    except OSError:
        raise VerdictArtifactError(
            "verdict.json: cannot write or replace artifact; "
            "check run directory permissions and free disk space"
        ) from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                raise VerdictArtifactError(
                    "verdict.json: cannot clean temporary artifact; "
                    "check run directory permissions"
                ) from None
    return destination
