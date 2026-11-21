"""The pinned nare release and commit, read from the repository's single `nare.pin`."""

from pathlib import Path

PIN_FILE = Path(__file__).parents[1] / "nare.pin"


def read_pin(path: Path = PIN_FILE) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key] = value
    return values


PIN = read_pin()
RELEASE = PIN["NARE_RELEASE"]
COMMIT = PIN["NARE_COMMIT"]
