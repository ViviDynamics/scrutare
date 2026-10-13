"""Subject line rendering."""


def render_subject(template, values):
    """Fill a subject template; a missing value raises KeyError."""
    return template.format_map(values)
