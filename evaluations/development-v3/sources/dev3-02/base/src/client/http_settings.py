"""Connection settings for the partner API client."""

DEFAULT_TIMEOUT = 10
MAX_RETRIES = 3
BACKOFF_BASE = 0.5
BACKOFF_CAP = 8.0
LARGE_UPLOAD_BYTES = 8 * 1024 * 1024
USER_AGENT = "partner-sync/3"


def connect_options(overrides=None):
    """Options for an ordinary request; explicit overrides win."""
    options = {"timeout": DEFAULT_TIMEOUT, "retries": MAX_RETRIES,
               "headers": {"User-Agent": USER_AGENT}}
    if overrides:
        options.update(overrides)
    return options


def retry_delays(retries=MAX_RETRIES):
    """Exponential backoff, capped."""
    return [min(BACKOFF_CAP, BACKOFF_BASE * 2 ** attempt) for attempt in range(retries)]


def idempotency_headers(key):
    headers = {"User-Agent": USER_AGENT}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def poll_options(interval):
    """Long polling waits for one interval plus the ordinary timeout."""
    options = connect_options()
    options["timeout"] = options["timeout"] + interval
    options["retries"] = 0
    return options


def upload_options(size_bytes):
    """Uploads are chunked above the threshold and given six times as long."""
    options = connect_options()
    if size_bytes > LARGE_UPLOAD_BYTES:
        options["chunked"] = True
        options["timeout"] = DEFAULT_TIMEOUT * 6
    else:
        options["chunked"] = False
    return options
