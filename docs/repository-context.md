# Immutable repository context

Repository context is opt-in. Findings remain anchored in selected diff hunks;
context include/exclude rules independently control source text available to reviewers.

```yaml
context:
  enabled: true
  related_paths: [src/caller.py, tests/test_caller.py]
  include: []
  exclude: [vendor/**, generated/**]
  max_file_bytes: 65536
  max_total_bytes: 1048576
  max_files: 128
  max_tree_requests: 256
```

Changed file identities precede sorted explicit related paths. Each candidate is
considered at the captured base then head revision. Includes match repository-relative
paths; excludes win. Related paths are explicit files, not patterns or shell commands.
`github.paths` continues to select findings and comment anchors. A file excluded there
can still supply contextual evidence when context rules allow it. Empty findings
selection still means no findings.

The capture uses GitHub commit/tree/blob API reads at exact SHAs, with the base
repository and fork head repository kept separate. There is no branch, checkout,
clone, source execution, import, or traversal. Missing fork repository identity is
recorded as unavailable head content. Inaccessible objects or transport failures
abort capture instead of silently substituting another revision. The base revision
is GitHub's captured PR base SHA; it is not asserted to be the PR merge-base.

`repository-context/repository-context.json` records identities, selection reasons,
eligibility, hashes, sizes and omissions. Added and deleted sides are absent; renamed
files use the previous path at base and current path at head. Missing files, non-UTF-8
or NUL-containing binary files, symlinks, submodules, directories, excluded paths,
sensitive filenames, and oversized files have explicit omission states. Common secret
filenames (.env, private key files and credential directories at every depth) are excluded before
object content is read. Authentication and session artifacts never enter read roots.

Limits apply to total retained bytes and per-side retained files. Whole files are
omitted rather than presenting partial text as full content. Tree requests are cached
and bounded. Limit omissions set `truncated: true`; reviewers must treat omitted
content as unknown. This is a bounded evidence capture, not a complete repository.

The prepared read root contains an exact copy of the context manifest and its listed
flat text artifacts. Producer-generated artifact names hash the logical path, so
repository paths cannot escape or collide with control artifacts. Copies preserve
UTF-8 bytes and line endings and have no executable permissions. Preparation and
execution revalidate all files; unexpected entries and symlinks are refused. Initial
reviewers, anchor corrections, debate perspectives and chairs share this contract.
Provenance inventories the capture and copies; offline replay audits contextual
integrity without refetching source or invoking a model.

Iterative findings conservatively record every contextual entry as a dependency.
Changed content hashes, object identities or omission states force the original
finding's still-selected hunk to be reassessed, even when its patch is unchanged.
Revision changes alone do not trigger reassessment. The existing round/token limits
still apply; exhausted dependency reassessment escalates. Partial reassessment keeps
unsupported prior blocking findings and marks their dependencies `stale`. A same-head
retry can consume another bounded round; it cannot silently become a complete result.
The saved run records `stale_dependency_findings` and per-entry `dependency_status`.
Same-head capture integrity is checked even when fresh human comments request a rerun.
Child rounds retain the
same frozen source evidence while narrowing comment anchors to reviewed hunks.

These mechanisms do not establish review accuracy. Compare contextual and diff-only
R1 evaluation jobs with equal models, settings and token limits before promotion.

For offline evaluation, `RepositorySnapshot` accepts explicit mappings from
(repository, revision label) to repository-relative paths and frozen bytes. It
constructs real blob/tree object hashes and implements the same bounded object
reads. `capture_repository_context(..., source=client.provenance())` records
`corpus_snapshot` source provenance and a snapshot inventory hash. Synthetic corpus
revision labels do not prove hosted commit authenticity. The evaluator loads source
outside the model root and must keep adjudication labels separate. Hosted captures
record `github` source provenance. The production preparation/strategy interfaces
are the same for both sources.
