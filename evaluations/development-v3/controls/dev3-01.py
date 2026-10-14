import sys

sys.path.insert(0, "src/reporting")
import formatters

line = formatters.render_invoice_line("Widget", 1234)
assert line.endswith("12.34") and len(line) == formatters.LINE_WIDTH, line
assert formatters.render_total(5000).endswith("TOTAL 50.00")
