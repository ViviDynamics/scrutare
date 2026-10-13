import sys

sys.path.insert(0, "src/render")
from page_cache import PageCache
from pricing_page import pricing_page

cache = PageCache(lambda template, locale, currency: f"{template}:{locale}:{currency}")
assert pricing_page(cache, {"locale": "en-GB"}) == "pricing:en-GB:GBP"
euro = pricing_page(cache, {"locale": "en-GB", "billing_currency": "EUR"})
assert euro == "pricing:en-GB:EUR", euro
