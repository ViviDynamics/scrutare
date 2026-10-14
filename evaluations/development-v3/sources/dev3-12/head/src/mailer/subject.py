"""Subject line rendering."""


class _Blank(dict):
    def __missing__(self, key):
        return ""


def render_subject(template, values):
    """Fill a subject template; a missing value renders as empty text."""
    return template.format_map(_Blank(values))
