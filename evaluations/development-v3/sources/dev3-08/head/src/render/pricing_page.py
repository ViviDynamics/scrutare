"""Pricing page view.

Customers may pick a billing currency in account settings; otherwise the page
uses the usual currency of their locale.
"""

LOCALE_CURRENCY = {"en-GB": "GBP", "en-US": "USD", "de-DE": "EUR", "fr-FR": "EUR"}


def pricing_page(cache, account):
    currency = account.get("billing_currency") or LOCALE_CURRENCY[account["locale"]]
    return cache.get("pricing", account["locale"], currency)
