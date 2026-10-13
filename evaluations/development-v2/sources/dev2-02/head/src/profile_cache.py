"""Per-tenant profile cache in front of a slow directory lookup."""


class ProfileCache:
    def __init__(self, loader):
        self._loader = loader
        self._entries = {}

    def get(self, tenant_id, user_id):
        if user_id not in self._entries:
            self._entries[user_id] = self._loader(tenant_id, user_id)
        return self._entries[user_id]

    def invalidate(self, tenant_id, user_id):
        self._entries.pop(user_id, None)
