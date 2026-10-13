from urllib.parse import urlsplit

from src.contracts import HOST


def allowed(url):
    return urlsplit(url).hostname == HOST
