"""Verify a downloaded artifact against the release manifest."""
import hashlib
import hmac

from manifest import published_sha256


def verify(name, payload):
    expected = published_sha256(name)
    actual = hashlib.sha256(payload).hexdigest()
    return hmac.compare_digest(actual, expected)
