from src.contracts import REQUIRED


def check_payload(payload):
    assert REQUIRED <= payload.keys()
