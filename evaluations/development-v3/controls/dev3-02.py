import sys

sys.path.insert(0, "src/client")
import http_settings

assert http_settings.connect_options()["timeout"] == 10
assert http_settings.upload_options(1024)["chunked"] is False
large = http_settings.upload_options(http_settings.LARGE_UPLOAD_BYTES + 1)
assert large == {**http_settings.connect_options(), "chunked": True, "timeout": 60}, large
