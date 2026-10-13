"""Text formatters for the monthly statement report."""

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "EUR ", "GBP": "GBP "}
LINE_WIDTH = 48
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_amount(cents):
    """Whole cents as a plain decimal amount, for the single-currency statement."""
    sign = "-" if cents < 0 else ""
    units, remainder = divmod(abs(cents), 100)
    return f"{sign}{units}.{remainder:02d}"


def format_percent(ratio):
    return f"{ratio * 100:.1f}%"


def format_date(year, month, day):
    return f"{day:02d} {MONTHS[month - 1]} {year}"


def format_period(start, end):
    return f"{format_date(*start)} to {format_date(*end)}"


def pad_left(text, width):
    return text if len(text) >= width else " " * (width - len(text)) + text


def pad_right(text, width):
    return text if len(text) >= width else text + " " * (width - len(text))


def truncate(text, width):
    if len(text) <= width:
        return text
    return text[:width - 3] + "..."


def mask_account(number):
    digits = "".join(ch for ch in str(number) if ch.isdigit())
    return "*" * max(0, len(digits) - 4) + digits[-4:]


def format_quantity(count, unit):
    return f"{count} {unit}" if count == 1 else f"{count} {unit}s"


def format_rate(cents, unit):
    return f"{format_amount(cents)} per {unit}"


def wrap_text(text, width=LINE_WIDTH):
    lines, current = [], ""
    for word in text.split():
        candidate = word if not current else current + " " + word
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def render_header(title, period):
    rule = "=" * LINE_WIDTH
    return [rule, title.upper(), format_period(*period), rule]


def render_invoice_line(description, cents):
    amount = format_amount(cents)
    label = truncate(description, LINE_WIDTH - len(amount) - 1)
    return pad_right(label, LINE_WIDTH - len(amount)) + amount


def render_discount_line(description, ratio, cents):
    label = f"{description} ({format_percent(ratio)})"
    return render_invoice_line(label, -cents)


def render_subtotal(cents):
    return pad_left("Subtotal " + format_amount(cents), LINE_WIDTH)


def render_tax(ratio, cents):
    return pad_left(f"Tax {format_percent(ratio)} " + format_amount(cents), LINE_WIDTH)


def render_total(cents):
    return pad_left("TOTAL " + format_amount(cents), LINE_WIDTH)


def render_footer(account_number, notes):
    lines = ["-" * LINE_WIDTH, "Account " + mask_account(account_number)]
    for note in notes:
        lines.extend(wrap_text(note))
    return lines


def render_statement(title, period, items, tax_ratio, account_number, notes=()):
    subtotal = sum(cents for _, cents in items)
    tax = round(subtotal * tax_ratio)
    lines = render_header(title, period)
    lines.extend(render_invoice_line(description, cents) for description, cents in items)
    lines.append(render_subtotal(subtotal))
    lines.append(render_tax(tax_ratio, tax))
    lines.append(render_total(subtotal + tax))
    lines.extend(render_footer(account_number, notes))
    return "\n".join(lines)


def format_amount(value, currency):
    """Amount with its currency symbol, for the multi-currency export."""
    symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
    return symbol + f"{value / 100:.2f}"


def render_export_row(description, cents, currency):
    return f"{description},{format_amount(cents, currency)}"
