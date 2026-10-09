def next_state(state, success):
    if success:
        return "done"
    return "pending" if state == "running" else state
