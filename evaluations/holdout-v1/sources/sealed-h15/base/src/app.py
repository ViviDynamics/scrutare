def authorized(role):
    return role in {'reader', 'writer'}
