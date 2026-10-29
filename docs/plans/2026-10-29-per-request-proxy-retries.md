# Per-request proxy retry control

Issue #106; supports #51 without closing it.

## Scope

In: optional OpenAI model setting, native CLI forwarding, capability refusal,
prepared-input and invocation provenance, replay checks, and a verified nare release pin.
Out: infrastructure, router settings, deployed App, default changes and model promotion.
The separately approved quality gate retains its holdout and spending boundaries.

## Assumptions

- Jason chose request-specific zero retries on 2026-10-29.
- LiteLLM 1.103.10 consumes request-body `num_retries: 0`; a matching self-hosted
  spend row records effective max_retries=0, attempted_retries=0 and attempted_fallbacks=0.
- An absent setting preserves existing request/configuration records.

## Tasks

- [x] Configuration, inheritance and refusal: failing parser and validation tests.
- [x] Capability, native argv and provenance: failing boundary and wave tests.
- [x] Replay rejects missing or altered controls: failing mutation tests.
- [x] Pin nare 2026.10.31 at `96aeee3d9c58577fb8d74cc0c2fa8c68f36aac5b` and exercise installed compatibility.
- [x] Reconcile the released usage shape, countdown and accounted turn-limit summary; preserve correction refusals and reject extra successful turns.
- [ ] Both full Python/native Docker lanes, independent review, quality guard and exact-head CI.
