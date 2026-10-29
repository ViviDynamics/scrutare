This amends the [g4 freeze](https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6472089607) for #99, recorded and posted before launch on 2026-10-28, following the c1 waiver precedent. **The maintainer waives only the smoke completion requirement.** Jason's ruling via chat reads:

> Launch g4 now. The released-wheel smoke verified that the settings are active in prepared inputs and provenance. Its failure was a session timeout at the smoke’s own 900 s limit, below the gate’s 1800 s session timeout, on a request still generating under the output cap. The smoke’s completion requirement is waived for g4. Launch g4 unchanged, with no change to any frozen gate file or criterion. No further smoke.

The one released-wheel smoke ran 14:44:37–14:59:39Z. Prepared settings and provenance hashes verified; the session failed at its own 900 s timeout. Request four was still generating: 11,536 completion tokens, about 17 tok/s, below the 20,000 cap. The first three requests exactly reconcile with native costs; request four explains the remaining 4,488 prompt + 11,536 completion tokens. All 40 smoke-window rows were on on-prem host: US$0 external, zero paid calls and zero attempted fallbacks. No g4 process existed when this amendment was recorded. No further smoke or re-test is authorized.

No frozen file or criterion changed. All frozen inputs, corpus files and 27 scripts were re-hashed unchanged. The exact list is recorded in the amendment below.

- `g4-amendment.json` `58d009100754125ec37cf588b487bde738cf1a5cc76898d4df108f3603c2e4ac`
- New `launch-g4-amended.py` `5b264769ee8a1f8390280939930db74601f59b9e21b8404bfb6e3a8a0cf488d7`
- Unchanged `freeze-g4.json` `c397e4a488687ba43413aa389f6ed13ab62f6ecd13d895511c1982cb4b7c61fd`

The new launcher retains every frozen launcher check except smoke completion, replacing it with the posted amendment/hash requirement. It also reads back both posted comments, verifies the launcher hash and all preparation input hashes, then performs the read-only pre-launch router check, captures the preceding hour and starts the exact same evaluator/load/finish units. All original frozen scripts remain unchanged. The report will publish the amendment and new launcher and disclose this deviation.
