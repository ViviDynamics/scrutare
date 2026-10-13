from datetime import timezone


def utc_day(value):
    return value.astimezone(timezone.utc).date().isoformat()
