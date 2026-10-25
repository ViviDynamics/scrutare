# Inventory and shortlist self-hosted models for the App (phase 1)

Issue #92

## Scope
In: scope items 1 and 2 of #92, and the published record of why item 3 did not start.
- The inventory covers what the Spark box and the ADA GPUs serve: hardware, models, quantisation, context, the decode rate on the LAN LiteLLM path, `json_schema` and tool calls.
- The shortlist names at most three candidates, each with a reason.

Out:
- any infrastructure change (deploying, downloading, swapping, or changing LiteLLM or vLLM config), which the maintainer must approve first;
- screening a model that is not deployed;
- the frozen qualification;
- the holdout;
- paid models.

## Assumptions
- Read-only means:
  - repository manifests;
  - GET requests to LiteLLM and vLLM;
  - spend-log reads;
  - a probe of three small requests per already-deployed self-hosted group, which the maintainer allowed for screening.
- A candidate can run a review only with real OpenAI `tool_calls` (for nare's `read`) and a schema-valid answer. A group without both cannot be screened as a reviewer.
- Shortlist sources are published, often secondary, benchmark reports. Each is cited, with its status (vendor-reported or secondary).

## Tasks
- [x] 1. Read the infrastructure configuration, live LiteLLM and vLLM, and 72 hours of usage.
- [x] 2. Probe every self-hosted group through the LAN path, then verify the probe's spend-log rows (self-hosted only, 0 fallbacks).
- [x] 3. Shortlist up to three candidates, and propose the infrastructure change with its impact. Stop for the maintainer.
- [x] 4. Publish with `reproduce.py` and a package test: `tests/test_self_hosted_inventory_report.py`.
