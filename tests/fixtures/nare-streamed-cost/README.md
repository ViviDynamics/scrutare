# Streamed cost fixture

`stdout.jsonl` and `session.json` are the output of one streamed openai-rail
session run offline through the installed nare 2026.11.100 console, as
`tests/test_installed_cost.py` runs it: the scripted HTTP boundary answered as
a LiteLLM proxy does, with an `x-litellm-*` response header and the charge in
`usage.cost` on the final usage chunk of each streamed reply. No provider, key
or network was involved, and the amounts are made up.

The files are nare's bytes with one change: the absolute read root recorded in
`session.json` is replaced by `/fixture/review-inputs`.
`tests/test_session_cost.py` decodes them without an installed runtime.
