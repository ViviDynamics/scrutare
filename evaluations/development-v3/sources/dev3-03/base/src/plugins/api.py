"""Public plugin facade."""
from registry import Plugin, register


def describe(plugin):
    return f"{plugin.name} ({plugin.version})"
