import sys

sys.path.insert(0, "src/downloads")
from verify import verify

assert verify("tool-1.4.0.tar.gz", b"test") is True
assert verify("tool-1.4.1.tar.gz", b"test") is False
assert verify("tool-1.4.1.tar.gz", b"test2") is True
