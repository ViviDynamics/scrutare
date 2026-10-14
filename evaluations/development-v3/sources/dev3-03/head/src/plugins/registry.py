"""Plugin registry internals. Extensions use the `api` facade, not this module."""

_REGISTERED = {}


class Plugin:
    def __init__(self, name, version):
        self.name, self.version = name, version


def register(plugin):
    _REGISTERED[plugin.name] = plugin
    return plugin


def registered():
    return dict(_REGISTERED)
