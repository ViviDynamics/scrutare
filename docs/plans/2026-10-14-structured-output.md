# Request schema-constrained output from self-hosted backends

Issue #78

## Scope
In: an opt-in per-rail setting `models.default.structured_output: json_schema` (inherited by, or set or cleared with `null` in, `models.overrides.<persona>`). With it, every schema-bound nare session on that rail (discovery findings, assessment, discussion positions and chair, anchor correction) is launched with nare's `--structured-output json_schema`, which constrains the schema correction turn on the OpenAI rail (ViviDynamics/nare#79). Config-time refusal for a non-OpenAI rail; wave-start refusal for a nare runtime that does not advertise the capability. Recording in the canonical configuration, each session's `invocation.json` (rail and argv) and `result.json` (constrained turn count), and a replay audit of that record. Pinning the nare release that has the flag, which also moves Scrutare to nare contract 2 while keeping contract 1 runtimes supported. Scripted-provider and installed native nare proofs. A live smoke against `spark/glm-5.3-flash`, and then the frozen re-qualification (published separately).
Out: constraining tool-using turns (measured on vLLM: a constrained request cannot call `read`), the Anthropic rail, any default change, any change to findings, assessment, citation or verdict validation, App deployment and `deploy/garden`.

## Assumptions
- nare's `--structured-output json_schema` constrains only the single schema correction turn of a tool-using session (and every turn of a tool-free one, which Scrutare does not launch). A valid first answer is accepted unconstrained. "Sends the constraint for every schema-bound final answer" is therefore read as: every schema-bound session on an enabled rail requests it, and every answer that needs a correction is decoded under the schema. Measured reasons are in nare#79.
- Scrutare's validation stays authoritative and unchanged: nare's constrained answer is decoded by the same strict findings, assessment, discussion, identity and citation checks.
- Off is byte-identical: `structured_output` is omitted from `config.to_dict()`, rail records, `invocation.json` and `result.json` when unset, and argv is unchanged for the same runtime.
- Contract 2 (nare 2026.10.7) only changes when `ask` blocks. Scrutare sessions offer only `read`, so a fabricated `ask` is refused by policy under either contract; Scrutare passes `--contract` equal to the inspected contract and accepts 1 or 2.
- The pinned runtime moves from 2026.10.4 to the release carrying nare#79 everywhere the pin appears (CI, release workflow, Dockerfile, docs), so every lane tests the runtime that ships.

## Tasks
- [x] 1. Config: `structured_output` on rails, inheritance and `null` clearing, refusal for non-OpenAI rails and unknown modes, canonical record omits it when unset. Failing config tests.
- [ ] 2. Runtime: inspection accepts contract 1 or 2 and reads the advertised `structured_output` capability; `--contract` follows the inspected contract; the decoder accepts the matching contract. Failing nare-session tests.
- [ ] 3. Sessions: an enabled rail adds `--structured-output json_schema`, records the rail field in `invocation.json`, counts constrained turns from nare events into `result.json`, and refuses constrained-turn events on an unconstrained session. Failing scripted tests.
- [ ] 4. Wave start: fan-out and the evaluator refuse a runtime without the capability before any session or admission, with a clear diagnostic. Failing tests.
- [ ] 5. Replay audit: every session invocation and result record agrees with the captured configuration. Failing replay tests.
- [ ] 6. Installed native proofs (pinned nare): off, on with a tool turn then a malformed answer then a constrained correction (factory seam and real OpenAI transport over HTTP), and an unsupported backend. Failing installed tests.
- [ ] 7. Pin the nare release with nare#79 (CI, release, Dockerfile, docs, pin tests); docs for the setting.
- [ ] 8. Live smoke against `spark/glm-5.3-flash` with a constrained final answer and zero paid spend.
- [ ] 9. Preflight both lanes, quality guard, PR, CI, independent review, merge, release.
