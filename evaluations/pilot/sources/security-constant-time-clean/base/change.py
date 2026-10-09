import hmac

def valid(actual, expected):
    return hmac.compare_digest(actual, expected)
