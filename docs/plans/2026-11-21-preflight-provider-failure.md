# Route preflight: classify a failed request from nare's failure record

## Goal and design

The route preflight (#132, PR #141) could not tell a backend that refuses the
answer encoding from a gateway or credential failure in its `request` check,
because nare reported how a model request failed only in error text a caller
must not parse. nare 2026.11.108 (ViviDynamics/nare#314, PR #316) reports a
structured `failure` record on the result line and advertises it as the
`provider_failure` contract entry. This change reads it.

- Inspection sets `NareCapability.provider_failure` when the contract entry
  names the `failure` result field and its five fields.
- A failed `request` check keeps nare's record as `failure` (reason, HTTP
  status, transient, constrained, attempts; never a body, header or error text)
  and classifies it: 400 or 422 on the constrained answer turn is the new
  `encoding_refused`; 401 or 403 `credential_refused`; a 5xx or `overloaded`
  `gateway_error`; `connection` with no status `route_unreachable`; `timeout`
  `request_timeout`; anything else `request_failed`.
- A runtime without the entry, or a malformed record, keeps `request_failed`.

What stays unchanged: the route check, every review session, the default
configuration (the preflight is opt-in) and the App's retry policy.

## Tasks

- [x] Capability flag from the contract entry, and an installed assertion.
- [x] Classification and the kept record, with installed tests for 400, 401,
  403 and 418 against a fake route, and unit tests for each mapping and for
  malformed records.
- [x] `docs/config.md` reason table.
- [x] Bump report text: no `merge-verified` (this repository has none), and how
  CI reaches a bump branch.
