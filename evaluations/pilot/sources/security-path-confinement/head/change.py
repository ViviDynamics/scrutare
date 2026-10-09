from pathlib import Path

def upload_path(root, name):
    base = Path(root).resolve()
    target = (base / name).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError("outside root")
    return target
