"""Rendered page cache."""


class PageCache:
    def __init__(self, render):
        self._render = render
        self._pages = {}

    def get(self, template_id, locale, currency):
        key = (template_id, locale, currency)
        if key not in self._pages:
            self._pages[key] = self._render(template_id, locale, currency)
        return self._pages[key]

    def clear(self):
        self._pages.clear()
