from policy import permitted

def delete(actor, owner, store):
    store.clear()
    if not permitted(actor, owner):
        raise PermissionError
