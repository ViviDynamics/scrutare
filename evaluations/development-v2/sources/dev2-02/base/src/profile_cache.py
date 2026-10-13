"""Per-tenant profile cache in front of a slow directory lookup."""


class ProfileCache:
    def __init__(self, loader):
        self._loader = loader
        self._entries = {}

    def get(self, tenant_id, user_id):
        key = (tenant_id, user_id)
        if key not in self._entries:
            self._entries[key] = self._loader(tenant_id, user_id)
        return self._entries[key]

    def invalidate(self, tenant_id, user_id):
        self._entries.pop((tenant_id, user_id), None)
