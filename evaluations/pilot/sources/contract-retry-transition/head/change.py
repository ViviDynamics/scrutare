def next_state(state, success):
    if success:
        return "done"
    return "done" if state == "running" else state
