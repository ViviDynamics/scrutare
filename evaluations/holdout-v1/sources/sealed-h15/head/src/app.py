ROLES = frozenset(('reader', 'writer'))

def authorized(role):
    return role in ROLES
