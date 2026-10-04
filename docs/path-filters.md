# Path filters and persona inputs

Configured PR capture applies `github.paths` before creating the artifacts a
persona may read. All resolved built-in and custom personas receive the same
filtered view. The complete raw capture remains available for audit and posting
outside that read root. This component is implemented; live persona sessions and
the full review CLI pipeline remain unfinished.

## Matching rules

```yaml
github:
  paths:
    include: []
    exclude: ["docs/**", "*.md"]
```

Paths use repository-relative POSIX names and case-sensitive matching. Empty
`include` permits all changed files; otherwise at least one include pattern must
match the canonical API filename. Any exclude match wins. Empty `exclude`
disables exclusions. An empty effective selection produces empty artifacts and
never falls back to the raw diff.

| Pattern | Meaning |
| --- | --- |
| `*.md` | Any basename ending in `.md`, including `nested/guide.md` |
| `docs/**` | Paths under `docs/`, at any depth |
| `src/**/*.py` | Python files directly in `src/` or any deeper directory |
| `src/*/test?.py` | One directory segment, followed by `test` and one character |
| `[ab].py` | Basenames `a.py` and `b.py` |
| `[*].py` | Literal basename `*.py` |

Slashless patterns match a basename at any depth. Patterns containing `/` match
the entire repository path. `*` and `?` do not cross `/`. `**` as a complete
segment matches zero or more segments. Character classes follow Python
`fnmatchcase` segment semantics, including `[!a]` for a negated class. Leading
`!` is literal; there is no pattern negation, ordering override, or implicit
`.gitignore` behavior. Quote globs in YAML.

For renamed or copied files, include matches the current canonical filename.
Exclude tests both current and previous filenames and removes the whole change
if either matches. A change from `docs/old.md` to `src/new.py` remains excluded
by the defaults, including its old-side patch. Deletions use the old filename.
Binary, mode-only, and rename-only changes are included in the effective file
list even if they have no line anchors.

## Capture and artifact format

Preparation validates the full captured file count, Git diff section identities,
captured repository/head, and effective configuration before selecting files.
Malformed or ambiguous mappings fail with a safe local error, including when the
affected file would be excluded. Selected diff sections preserve exact bytes,
line endings, and no-newline markers.

`effective-files.json` sits at the run root, outside the persona read root:

```json
{
  "schema_version": 1,
  "head_sha": "captured-head-sha",
  "include": [],
  "exclude": ["docs/**", "*.md"],
  "files": ["src/app.py"],
  "config_sha256": "sha256-of-normalized-effective-config"
}
```

`files` preserves the canonical GitHub file order. `config_sha256` binds the
complete effective configuration: SHA256 of `ReviewConfig.to_dict()` serialized
as UTF-8 JSON with sorted keys, two-space indentation, non-ASCII characters
preserved, finite numbers only, and a trailing newline.

The resolved `review-inputs/` directory contains exactly three copied files:

- `diff.patch`: selected complete diff sections, in captured diff order.
- `files.json`: ordered records containing only `filename` and `status`.
- `context.json`: only `repository`, `pr_number`, `head_sha`, and `base_sha`.

An empty selection still writes the manifest, an empty diff, `[]` file records,
and context. The view contains no links to the checkout or raw capture and no
full metadata, config, credentials, session output, arbitrary GitHub fields, or
discussion bodies. PR title/body and all review/comment bodies are deliberately
omitted. Included source text can naturally mention an excluded filename or
contain related prose. Filtering controls file visibility; it does not redact
the meaning of included source or user-supplied persona instructions.

The view exposes the captured selected changes, not a full source checkout.
References from included source to other files do not make those files readable.
Personas must use the available artifacts and acknowledge unavailable context.

Repeated preparation validates the exact saved file set, bytes, manifest, head,
and configuration. It rejects altered, partial, extra, or symlinked artifacts
without overwriting the existing view or widening selection.

## Python API and runner integration

```python
from scrutare.engine.persona_inputs import prepare_persona_inputs
from scrutare.engine.review_inputs import prepare_review_inputs

inputs = prepare_review_inputs(run_dir, config)
descriptors = prepare_persona_inputs(inputs, config.personas)
for descriptor in descriptors:
    input_args = descriptor.nare_input_args()
    system_prompt = descriptor.persona.system_prompt
```

`PreparedReviewInputs(root: Path, head_sha: str, effective_files: tuple[str, ...])`
is frozen and validates its fields against the exact prepared view even when
constructed directly. `PersonaReviewInput(persona: PersonaDefinition,
inputs: PreparedReviewInputs)` is also frozen and validates both inputs and
persona. `prepare_persona_inputs` resolves the actual persona registry,
preserving built-in prompts, inline prompts, and configured order. It rejects
unknown names, duplicate personas, and malformed definitions.

The generated `prompt` references only `diff.patch`, `files.json`, and
`context.json`; it never embeds raw capture data. Accessing it or
`nare_input_args()` revalidates the prepared view. The latter returns exactly:

```python
(descriptor.prompt, "--tools", "read", "--root", str(inputs.root))
```

There are no caller overrides. These APIs do not import nare, launch sessions,
call models, or implement budgets. The future runner owns `--system` with the
resolved persona prompt, model selection, supported budget policy, and session
output flags. It must reject additional or conflicting root/tool arguments and
save session output outside the read root.

Issue #3 must consume this descriptor for both initial and re-anchor correction
sessions without widening root or tools. This is an integration requirement for
the unfinished live pipeline, not evidence that live reviews already run.

`ingest_pr(..., review_config=config)` prepares the view within fresh-run
cleanup before returning. The CLI supplies its validated configuration. Legacy
`ingest_pr` calls that omit `review_config` remain raw-only and do not create a
prepared view; their run root cannot be used as `PreparedReviewInputs.root`.

Typed descriptors enforce application contracts. They are not an OS sandbox
against arbitrary host mutation or deliberate object bypass. The fixed nare
policy permits only `read`; nare's resolved-path checks deny sibling, absolute,
and symlink escapes from the prepared root. An offline compatibility check with
nare's real CLI and scripted FakeProvider verifies allowed artifact reads,
escape rejection, read-only tool schemas, and denied bash/write calls using
the actual resolved built-in and custom system prompts. It also retains a
full-run-root negative control demonstrating why the narrow root matters.
This proves local CLI compatibility, not vendor transport or live model behavior.
