"""Release manifest.

The checksums below are public. The same file is served to every client next to
the artifacts and is printed on the downloads page.
"""

PUBLISHED = {
    "tool-1.4.0.tar.gz": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "tool-1.4.1.tar.gz": "60303ae22b998861bce3b28f33eec1be758a213c86c93c076dbe9f558c11c752",
}


def published_sha256(name):
    return PUBLISHED[name]
