# Retained structured-output boundary investigation

Readonly local investigation of the original twenty-case development focused job.
No provider calls, reruns, product edits, new holdout exposure or human judgments.
Payloads are parsed solely as untrusted data; no model text executes. Headers,
credentials, environment and wholesale native transcripts are not exported.

## Finding

No Scrutare/native integration mismatch is demonstrated at the inspected boundaries.
The installed nare2026.10.4 validator recomputes the recorded final complaint exactly
for **378/378 schema-violation sessions**. All378 have the schema stated and the
single native correction attempt recorded; all use the same supported caller schema.
359 final answers are JSON objects which violate the schema;19 are malformed JSON
objects (delimiter/quoting/trailing-comma/extra-data errors), rather than missing
JSON extraction support for prose or fences.

The common structural violations overlap:301 first findings omit nested
`evidence.version`; evidence fields are sometimes misplaced at finding level;
40 first citations use an invalid `side` enum. Caller `finding.side` is optional
and defaults to RIGHT; it is distinct from required citation `side=base|head`.
Omitting finding.side is not a refusal cause.

Native shape validation intentionally supports a subset of JSON Schema. Its
supported keywords exclude regex/length constraints. Citation revision/hash are
strings at this layer, so shape acceptance does not establish correct identifiers.
The unchanged strict Scrutare parser subsequently reproduces **221/221 protocol
refusals**, including225 individual malformed revision/hash claims. In that refusal
population,233 cited hashes have65 characters where64 are required. Do not truncate,
guess, repair or reinterpret those identifiers as accepted citations.

The deeper immutable-capture checks reproduce **35/35 citation refusals**:31 content
hash mismatches,3 ranges outside the captured file and1 uncaptured path. Mechanically
refused output does not establish semantic falsity of the model's assertion.

Scrutare currently records native `error/schema_violation` using its coarse `provider`
reason because generic native error status falls through to that reason. This label
does not mean the provider was unavailable; qualification should keep the native stop
reason and the structure/citation diagnostics separate from transport failures.

## Exact source boundary

- `nare/src/nare/loop.py:112`: `_answer_errors` extracts JSON and calls schema validation.
- `nare/src/nare/loop.py:245`: after one correction, a second invalid answer becomes
  native error/schema_violation. `loop.py:447` states the schema in the transcript.
- `nare/src/nare/schema.py:145`: `validate` first checks supported schema keywords,
  then evaluates required properties/types/enums/additional properties.
- Frozen Scrutare `engine/session_output.py:339`: `decode_session` reconciles the
  event/session artifacts, then parses findings; invalid shapes become protocol errors.
- Frozen Scrutare `findings/models.py:81`: citations require40-char revision and64-char
  content hash as lowercase hex. Correct-looking strings still need capture checks.
- Frozen Scrutare `findings/evidence.py:61`: `validate_evidence` binds the reference to
  captured revision/path/hash/range; this establishes identity, not claim truth.
- Frozen Scrutare `engine/nare_session.py:513`: generic native errors become provider
  refusals; `:532` retains specific citation-validation refusal codes.

The inspected source is installed nare2026.10.4, Git79405f9d2e3db2efe4a3ffda35faaf1680000acd,
and the focused job's frozen Scrutare candidate5736bc1ce4ad02c1b19acfa9f318d7ae9acfbc50.

## Minimal reproduction

Use the installed nare venv to run `replay_native_boundary.py`. It feeds the exact
last two retained answer texts to nare's trusted scripted FakeProvider, with external
network sockets blocked. A representative missing-evidence.version answer reproduces
error/schema_violation after two scripted calls. The same schema accepts an empty
findings shape in one call. This is an offline boundary control, not a new model run
or a semantic approval of any PR. Payload text remains private; only its hash is saved.

Run `trace_native_failures.py` with the nare venv to recompute all378 native complaints.
Run `replay_scrutare_boundary.py` with the frozen focused-job venv to re-decode the
221 protocol and35 citation refusals using unchanged private event/session evidence.
The normalized JSON files contain no raw answer text or secret values. Local native
captures are required for this diagnostic reproduction and remain private.
