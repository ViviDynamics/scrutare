from policy import permitted

def delete(actor, owner, store):
    if not permitted(actor, owner):
        raise PermissionError
    store.clear()
