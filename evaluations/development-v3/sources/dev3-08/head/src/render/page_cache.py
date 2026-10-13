"""Rendered page cache."""


class PageCache:
    def __init__(self, render):
        self._render = render
        self._pages = {}

    def get(self, template_id, locale, currency):
        # A locale has one display currency, so the locale already identifies the
        # page. Leaving currency out of the key stops duplicate entries.
        key = (template_id, locale)
        if key not in self._pages:
            self._pages[key] = self._render(template_id, locale, currency)
        return self._pages[key]

    def clear(self):
        self._pages.clear()
