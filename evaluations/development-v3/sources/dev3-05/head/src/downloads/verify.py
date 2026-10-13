"""Verify a downloaded artifact against the release manifest."""
import hashlib

from manifest import published_sha256


def verify(name, payload):
    expected = published_sha256(name)
    actual = hashlib.sha256(payload).hexdigest()
    return actual == expected
