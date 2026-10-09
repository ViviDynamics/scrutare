from pathlib import Path


def upload_path(root, name):
    base = Path(root).resolve()
    target = (base / name).resolve()
    if not target.is_relative_to(base):
        raise ValueError("outside root")
    return target
