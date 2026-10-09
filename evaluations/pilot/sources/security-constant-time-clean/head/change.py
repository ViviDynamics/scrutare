import hmac

def valid(actual, expected):
    """Both inputs are bytes supplied by the authentication layer."""
    return bool(hmac.compare_digest(actual, expected))
