# Original local review pilot

These thirty purpose-written cases contain twenty defective changes and ten clean
controls, balanced across correctness/contracts, security, tests, operations, and
consumer-facing behavior. Each domain contributes four defects and two controls.
Ten cases are reserved for holdout evaluation: seven defects and three controls.
The holdout is identified in the manifest for reproducibility; people authoring
prompts must use the development split and avoid inspecting holdout labels.

All examples were created for this repository, use Elastic License 2.0, and contain
no copied external PR material. Per-case provenance appears in `manifest.json`.
This small synthetic pilot measures these cases; it does not establish general
review accuracy, production effectiveness, or transfer from human inspections.

`captures/ID` contains only `diff.patch`, `files.json`, and `metadata.json`.
`labels/ID.json` records evaluator-only triggers, impact, evidence, acceptable
semantic matches, fixes, and counterexamples. Labels must never be copied into a
reviewer's read root. A missing, failed, partial, or unadjudicated run does not
establish a clean approval. Human adjudicators should assess whether a finding
identifies the labeled trigger and consequence, using the specified match criteria;
keyword overlap alone is insufficient. Reviewers do not decide severity or verdict.

`sources/ID/base` and `sources/ID/head` retain exact original source trees outside
capture roots. Five cases intentionally depend on an unchanged consumer or schema
file. Their patches include only changed files. The source snapshots support the
separate context experiment; the diff-only baseline receives no invented unchanged
hunks or labels. Metadata commit identifiers are deterministic SHA-1 identities of
canonical source-tree contents, not claims of hosted repository commits.

Tests apply every patch to its base tree and compare every byte against the head
snapshot. Tests also compile Python examples, check counts and splits, and validate
that label artifacts stay outside captures. These deterministic checks validate
fixture coherence only; model review quality requires real retained nare runs and
blinded human adjudication.
