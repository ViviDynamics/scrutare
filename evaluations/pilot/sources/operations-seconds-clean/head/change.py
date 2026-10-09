def delay_seconds(retry_after_ms):
    """Convert gateway millisecond backoff into seconds."""
    return float(retry_after_ms) / 1000.0
