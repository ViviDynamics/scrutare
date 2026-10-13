from datetime import datetime

from src.app import utc_day

assert utc_day(datetime.fromisoformat('2024-03-01T00:15:00+02:00')) == '2024-02-29'
