# Publish measured development comparisons

Issue #51; refs #44 and #45.

## Scope
Publish exact captured aggregates, frozen snapshots, stable automated decisions,
pending human packets and paired context baseline. Keep all failures, incomplete
accounting and method limitations. No production behavior or defaults change.

## Assumptions
Results remain provisional. There are zero human judgments; new unseen holdout
validation and the remaining architecture comparisons are not completed here.
Private native session captures remain retained outside versioned reports.

## Tasks
- [x] Copy bounded report artifacts byte-for-byte and record publication hashes.
- [x] Reproduce both reports with the production score CLI against original labels.
- [x] Document identical-case context baseline and unsigned local provenance.
- [ ] Complete both canonical Python lanes, quality guard, fresh review and CI.
