from src.contracts import HOST


def allowed(url):
    return HOST in url
