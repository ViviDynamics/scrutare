from pathlib import PurePosixPath


def allowed(path, root):
    return PurePosixPath(path).is_relative_to(PurePosixPath(root))
