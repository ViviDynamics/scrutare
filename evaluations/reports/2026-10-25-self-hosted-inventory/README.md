# Self-hosted model inventory and shortlist for the App (#92, phase 1)

**Result: no deployed self-hosted model other than `spark/glm-5.3-flash` can run a Scrutare review today, so nothing was screened. Every stronger candidate needs an infrastructure change on the Sparks, which is proposed below and waits for the maintainer.**

This package covers scope items 1 and 2 of #92 (the inventory and the shortlist). It records why item 3 (screening) did not start. No infrastructure was changed, no model was deployed, downloaded or swapped, and no LiteLLM or vLLM setting was touched. No paid model was called. `evaluations/holdout-v1` was not opened.

## How this was read

Everything here was read-only:
- the infrastructure repository at `origin/main` (`9dca807`): the `glm53-tp3` README and unit, the `spark_glm_engine` role defaults, the unmerged `issue-203-spark-strict-output` candidate, and the garden cluster's `vllm-ada`, `ollama` and `litellm` manifests;
- live LiteLLM `/model/info` and `/router/settings`;
- vLLM `/v1/models`, `/version` and `/metrics` on the Spark rank-0 host;
- 73 hours of spend-log rows (2026-10-22 00:00Z to 2026-10-25 01:00Z);
- a capability probe through the LAN LiteLLM path that nare uses. It sent three small requests to each self-hosted group, one group at a time: a decode-rate request, a `response_format: json_schema` request, and a tool-call request with nare's `read` tool shape.

The probe made 30 requests, plus 2 that read the error body of two failing groups (`capability-probe.json`). The spend log has all 32, each routed to a self-hosted deployment, with 0 fallbacks and 0 paid rows (`provider-evidence.json`). LiteLLM retried each of the 14 failures twice (`num_retries` is 2), most likely against the same unreachable endpoint (see below). The spend log also shows nominal `spend` on self-hosted rows: those are LiteLLM's cloud-equivalent prices counted against key budgets, not charges from any provider.

## Inventory

### Spark (vLLM, 192.168.3.40)

| | |
| --- | --- |
| Hardware | 3x DGX Spark: GB10 Grace-Blackwell (SM121), 128 GB unified LPDDR5X each (384 GB total), RoCE mesh between the three |
| Engine | one vLLM 0.32.0 engine at tensor-parallel 3; only rank 0 serves HTTP |
| Model | GLM-5.3-Flash, NVFP4 (181.3 GiB of weights) |
| Context | `max_model_len` 262,144; LiteLLM advertises 32,768 output tokens |
| KV cache | fp8, 18 GB per node, a 1,981,012-token pool, prefix caching on, `gpu_memory_utilization` 0.75 (live `/metrics`, re-read 2026-10-25 and retained privately; these are the committed `spark_glm_engine` role defaults, reduced on 2026-09-04 from the original README's 1M context and 26 GB) |
| Decode rate (LAN path) | 22.9 tok/s for the probe's single stream, with other clients' requests in flight (4 were running when `/metrics` was first read the same night; that read was not retained). The infrastructure README records 33 to 35 tok/s idle (2026-09-04). The g2 load probe measured 15 to 19 tok/s per stream at a 12-stream peak; `c1`'s one runaway request decoded at 25.2 tok/s. |
| `json_schema` | yes: schema-valid answer (since #78, used with `structured_output: json_schema`) |
| Tool calls | yes: an OpenAI `tool_calls` `read` call |
| Spare capacity | **none** for a second model. The three nodes run this one engine together, so any other model on the Sparks replaces or shrinks it. |
| Other clients, 73 h before the probe | 4,652 spend-log rows: opencode (master key and the `odysseus` key), `python-httpx` (master key), `performer`, `claude-cli`, `dettmore-ci-qare`, and an external user's key. Deployed services pinned to this group in the garden cluster include `agent`, conductor and coordinare-daemon. |

### ADA GPUs (Ollama)

| | |
| --- | --- |
| Hardware | 2x RTX 2000 Ada, 16 GB each (SM 8.9), in the Talos workers `talos-skt-uua` and `talos-w4v-yr3` |
| Serving | **One GPU only.** `talos-w4v-yr3` serves Ollama. `talos-skt-uua` was reassigned to the WhisperX diarization service on 2026-10-21 (infrastructure #214), and its Ollama deployment is scaled to zero. The vLLM deployments on both nodes have been scaled to zero since 2026-09-26. |
| Engine | Ollama, `OLLAMA_CONTEXT_LENGTH` 32,768: 0.32.15 in git at probe time, bumped to 0.40.11 in git at 04:23Z on 2026-10-25, after the probe (#254). The running version was not read. |
| LiteLLM route | `ada/*` groups through LiteLLM's `ollama/` provider (the generate API). Git lists one deployment per group (w4v); the **live proxy still lists two**, including the removed `ollama-ada-skt`; max input 32,768 and output 8,192 as advertised (phi4: 16,384 and 4,096). `arc/qwen3-8b` is Ollama 0.40.6 on an Intel Arc B580 (infrastructure #208), at 8,192 input and 4,096 output. |
| Other clients, 73 h before the probe | `ada/qwen3-8b` only: 8 rows, opencode on the `odysseus` key |

The ADA and Arc groups through LiteLLM:

| Group | Decode (tok/s) | `json_schema` | Tool calls |
| --- | ---: | :---: | :---: |
| `ada/qwen3-8b` | 38.8 | yes | **no**: the call comes back as JSON text, with no `tool_calls` |
| `ada/llama3.1-8b` | 41.6 | yes | no (same) |
| `ada/gemma3-12b` | 26.0 | yes | no (same) |
| `ada/gemma4-12b` | no content streamed in the decode probe | yes | no (same) |
| `ada/qwen3-14b`, `ada/qwen2.5-coder-14b`, `ada/phi4-14b`, `ada/deepseek-r1-14b` | HTTP 500 | - | - |
| `arc/qwen3-8b` (8,192 context) | 26.7 | yes | no |

- **No tool calls.** LiteLLM's `ollama/` provider returns a tool call as plain JSON text. nare needs real `tool_calls` to run `read` (the probe streamed, as nare does), so none of these groups can run a review session. The four 14B groups were never reached; that they could not either is inferred from the shared route. Changing the route to `ollama_chat/` would be a LiteLLM config change.
- **The 500s.** Every 500 was LiteLLM failing to connect to the `ollama-ada-skt` endpoint ("Cannot connect to host ...:11434"). That endpoint no longer exists: its node moved to WhisperX, and #214 removed it from `litellm-values.yaml`, but the live proxy config has not been updated to match git. All 14 failed rows went there, and all 18 successful ADA and Arc rows went to `ollama-ada-w4v` or `ollama-arc`. No fallback is configured. Spend rows record only the final endpoint, so that LiteLLM's two retries of each failure went to the same endpoint is an inference: LiteLLM's deployment affinity pins a group and key to one deployment, which may also be why these groups never reached `ollama-ada-w4v`. The stale live config was not changed here.
- **Context is not the limit on ADA.** In `c1`, the evaluator's largest request prompt was 7,828 tokens, and with `c1`'s 20,000 output cap a request fits within 32,768 (the advertised 8,192 output is LiteLLM metadata, not a backend limit). Arc's 8,192 would not fit.

## Shortlist (at most 3)

The candidates below are drawn from published tool-use and agentic results and from fit on the Sparks. The sources are secondary and vendor-reported, as noted, and they conflict. No deployed ADA model is on the list: they are 8 to 14B dense models without working tool calls on this route, and nothing published suggests one beats a GLM-5.x-class model at tool use. BenchLM ranks GLM-5.1 and MiniMax M3 above all three candidates on tool use; GLM-5.1 is superseded by the GLM-5.2 entry, and no DGX Spark serving recipe for MiniMax M3 was found, so neither is listed.

| # | Candidate | Fit | Why | Caveats |
| --- | --- | --- | --- | --- |
| 1 | **DeepSeek V4 Flash (0731)**, 284B total / 13B active MoE, NVFP4 | 2 of the 3 Sparks, tensor-parallel 2 over RoCE (a community vLLM recipe). The third Spark would be left free. | DeepSeek reports large agentic and tool-use gains for the 0731 post-training: Toolathlon 70.3, Terminal-Bench 2.1 82.7 (Artificial Analysis measured 79). About 82 tok/s single stream is reported on 2 Sparks, and about 41 tok/s in an independent FP8 run. Fastest of the three by far. | The scores are vendor-reported. modelgrep lists τ²-Bench at 39.4%, which is weak, and other sources conflict. The recipe is a community GB10 port with an experimental KV path. The model is new to this stack. |
| 2 | **GLM-5.2**, 744B, hybrid NVFP4 with 2-bit cold experts (about 272 GB) | 3 Sparks. Needs a vLLM fork. | Artificial Analysis ranks it the best open-weight model overall (as cited by noze.it), and a practitioner quoted there calls it the best you can run on 3 Sparks. BenchLM ranks it 8th on tool use (67), above Qwen3.5. Same family and tool-call format as today's model. | Slower: 13.6 to 25 tok/s reported, about 21 on structured output. Hard to maintain. It is not obviously better than GLM-5.3-Flash at citation discipline. |
| 3 | **Qwen3.5-397B-A17B**, NVFP4 (about 200 GB, an estimate from the parameter count) | 3 Sparks, as today. Tensor-parallel 3 may need head padding like GLM's (an estimate; no TP=3 recipe was found). | Qwen's own model card reports BFCL v4 72.9 (vendor-reported). | BenchLM ranks it 18th on tool use (56), below GLM-5.2. TP=3 geometry is unverified. Decode is likely in GLM's range. |

Sources: [noze.it, local inference on DGX Spark, August 2026](https://www.noze.it/en/insights/dgx-spark-local-models-august-2026/); [OrcaRouter, DeepSeek V4 Flash release](https://www.orcarouter.ai/blog/deepseek-v4-flash-official-release); [modelgrep, DeepSeek tool calling](https://modelgrep.com/best/tool-calling/deepseek); [BenchLM, tool use](https://benchlm.ai/best/tool-use); [Qwen3.5-397B-A17B model card](https://huggingface.co/Qwen/Qwen3.5-397B-A17B).

## Proposed infrastructure change (not made; needs the maintainer's approval)

Nothing below has been done. Each item needs the maintainer's approval first, because other clients depend on the Spark engine. In the 73 hours before the probe there were 4,652 spend-log rows on `spark/glm-5.3-flash`, against 8 rows on all the ADA groups together. The clients were opencode on the master and `odysseus` keys, `python-httpx`, `performer`, `claude-cli`, `dettmore-ci-qare` and an external user's key, and the deployed `agent`, conductor and coordinare-daemon services are pinned to the group.

**Recommended: screen DeepSeek V4 Flash (0731) on two of the three Sparks, in a scheduled window.**
- **Change:**
  - coordinate and drain shared traffic, then stop all three GLM-5.3-Flash ranks, and disable `glm53.service` for the window, so that a rebooted node (for example after an OOM power cycle) does not start a GLM rank against the new engine. Re-enable it on restore. The live engine is vLLM 0.32.0 from the unmerged `issue-203-spark-strict-output` candidate, which runs as a systemd drop-in staged by `configure_spark_glm_candidate.yml`. Capture the image identities and command lines first.
  - check free disk on two nodes, and download the checkpoint there. The two-Spark recipe's format is inferred from its NVFP4 KV path, and its size is not in the sources; the one-Spark GGUF build is about 110 GiB.
  - start a TP=2 engine over RoCE following the community recipe;
  - add one LiteLLM deployment, for example `spark/deepseek-v4-flash`, with no fallbacks, and add it by hand to the keys' and teams' model allowlists (`litellm-values.yaml`).
  - **Restoring GLM** follows the candidate README. Stop and remove the new engine and every serving container. Start ranks 42, 41 and 40 with spacing, never against a surviving collective. Allow at least a 13-minute cold start. Then compare image identities and command lists against the captured baseline, and verify health and a normal request. The GLM weights stay on disk.
- **Impact:**
  - `spark/glm-5.3-flash` is unavailable for the whole window. Every Spark client would see errors, because there is no fallback by design. That includes opencode, `python-httpx`, the `odysseus`, `dettmore-ci-qare` and external keys, the `agent`, conductor and coordinare-daemon services, and any Scrutare run that names the alias.
  - The checkpoint download uses the site uplink and disk on two nodes.
  - New, untested serving code runs on hardware whose README warns that an OOM-killed rank can wedge a node and need a power cycle.
  - The restore is itself a cold start of the unmerged candidate engine, with its own risk.
  - The third Spark sits idle unless used.
- **Screening, once deployed:** the same 12-cell `aggregate` design as the #91 measurement (`evaluations/reports/2026-10-25-citation-correction-measurement`, PR #94), on the four pilot development cases, with release 2026.10.34 settings: `structured_output: json_schema`, `max_output_tokens`, the `c1` budgets, `context.line_counts` and `findings.citation_correction`. That is about 2 hours of the window, with the session-cause table compared directly against `spark/glm-5.3-flash`'s jobs in that package. Only a frozen 36-cell qualification under #51 could support any decision, and that waits for the maintainer.

**Alternatives:**
- **Do nothing on the Sparks.** Keep GLM-5.3-Flash, with line counts and the citation correction (release 2026.10.34), and decide whether to run the `c2` re-test that `c1`'s gate allows. This costs no other client anything.
- **Change the ADA route.** Switching the LiteLLM `ada/*` entries from `ollama/` to `ollama_chat/` should give those models real tool calls (untested here). Applying #214's LiteLLM config to the live proxy would remove the dead `ollama-ada-skt` entries and end the 500s. Bringing Ollama back on skt is **not** proposed: it would evict WhisperX. Both proxy changes are LiteLLM config with little impact (8 ADA rows in 73 h), and only one 16 GB GPU would serve them. They would make the 8 to 14B ADA models screenable, but nothing suggests one beats GLM-5.3-Flash, so this is not proposed as a route to a stronger model.
- **Candidates 2 and 3** (Qwen3.5-397B-A17B and GLM-5.2) need all three Sparks, like today's engine. They carry the same impact for longer, and need TP=3 geometry work (Qwen) or a vLLM fork (GLM-5.2).

## What this does not establish

- Any candidate's quality on Scrutare's reviews. None has been run, because none is deployed.
- Benchmark scores beyond what the cited sources report. Several are vendor-reported or secondary, and they conflict.
