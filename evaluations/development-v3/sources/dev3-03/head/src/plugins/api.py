"""Public plugin facade."""
from registry import Plugin, register


def describe(plugin):
    return f"{plugin.name} ({plugin.version})"


def describe_all(plugins):
    return [describe(plugin) for plugin in sorted(plugins, key=lambda item: item.name)]
