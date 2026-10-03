# PR ingestion Implementation Plan

> For agentic workers: use subagent-driven-development. Execute tasks serially with independent task review and final branch review.

**Goal:** Deliver issue #1: capture PR input in a versioned run directory through the requested CLI.
**Architecture:** A typed GitHub transport reads data through the installed gh CLI. Ingestion captures a coherent head and persists immutable inputs. The CLI exposes ingestion now; later issues connect personas, verdicts, and posting.
**Tech Stack:** Python >=3.10, setuptools, pytest, Ruff, mypy; gh for authenticated GitHub reads.
**Spec:** docs/SPEC.md, sections 2, 8, 10. Issue #1.

## Scope

In: Python setup necessary for ingestion, PR reference resolution, paginated input collection, lifecycle checks, run artifacts, initial CLI and CI.
Out: config semantics (#8), personas (#2), model sessions (#3), verdict/posting, full review CLI and release publishing (#7).

## Global Constraints

- No model calls in this issue. Every future model call goes through nare.
- Published prose and commit messages have no em dashes.
- Read-only GitHub operations; never post a verdict from ingestion.
- Preserve the captured head SHA. Abort if a PR closes or merges.
- Credentials stay outside artifacts; use gh's existing authentication without reading token files in application code.
- Do not implement other issues. The config path is snapshotted verbatim until #8 adds validation.

## Review Focus

- URL and number references cannot become executable shell text or arbitrary GitHub endpoints.
- Pagination retains all changed files, reviews, issue comments, and review comments.
- A changed head during ingestion cannot produce mixed artifacts.
- Closed or merged PRs never produce a successful snapshot.
- Failed persistence cannot leave a success-shaped run or overwrite an existing run.

### Task 1: Typed GitHub input transport

**Files:** pyproject.toml, src/scrutare/__init__.py, src/scrutare/engine/github.py, tests/test_github.py.
**Interfaces:** Define PullRequestRef(owner: str, repo: str, number: int); resolve_pr(value: str, repository: str | None = None) -> PullRequestRef. Define GitHubError and PullRequestUnavailable. Define GitHubClient with get_pr(ref) -> dict[str, Any], get_diff(ref) -> str, get_files(ref) -> list[dict[str, Any]], get_reviews(ref), get_comments(ref), get_review_comments(ref) returning lists. Define assert_pr_open(metadata: dict[str, Any]) -> None. Consumers use number, state, merged, head.sha from get_pr.

- [ ] Write tests first: resolve https://github.com/owner/repo/pull/12 and 12 with repository context; reject zero, negatives, foreign hosts, malformed refs and invalid repo context. No shell=True.
- [ ] Test get_pr and exact diff request Accept header, plus paginated JSON lists with gh api --paginate --slurp. Simulate gh errors/missing binary, invalid JSON, malformed metadata and timeout; raise actionable GitHubError without leaking stderr secrets.
- [ ] Observe red using a temporary uv pytest environment before creating implementation.
- [ ] Implement transport with injectable subprocess runner, fixed timeout, and safe argument lists. Number refs without explicit repository resolve via gh repo view --json nameWithOwner. Preserve GitHub fields and prior discussion.
- [ ] Add minimal installable pyproject with scrutare version 0.1.0, src layout, dev extra (pytest, ruff, mypy), strict mypy for src, Ruff checks. Do not add the console entry point until Task 3.
- [ ] Run uv run --extra dev pytest tests/test_github.py, uv run --extra dev ruff check ., uv run --extra dev mypy src. Commit.

### Task 2: Coherent snapshot and run directory

**Files:** src/scrutare/engine/ingestion.py, tests/test_ingestion.py.
**Interfaces:** Consumes Task 1 transport. Define ingest_pr(client: GitHubClient, ref: PullRequestRef, runs_root: Path, config_path: Path | None = None) -> Path and check_pr_open(client: GitHubClient, ref: PullRequestRef) -> None. Runs are unique children under runs_root.

- [ ] Test persisted diff.patch, files.json, reviews.json, comments.json, review_comments.json and metadata.json. Metadata contains schema_version=1, scrutare_version, captured head_sha, repository, pr_number, captured PR metadata, status='ingested'. Include config.yaml when supplied.
- [ ] Test closed/merged at initial and final read, a head change then stable retry, continuous head churn fails after three snapshot attempts, transport failure, filesystem failure cleanup, two distinct runs, preserving existing files. Use fake client, no network.
- [ ] Observe failing tests before implementation.
- [ ] Collect inputs between two metadata reads. If still open but head changed, discard and retry up to three attempts. If it closes, abort. Persist only after stable collection; clean up only the fresh run on failure. Never overwrite existing run directories. Preserve a force-push AFTER capture as the captured SHA for future callers.
- [ ] Add reusable lifecycle check for later engine/poster integration. No model/posting calls. Version comes from scrutare.__version__.
- [ ] Run focused tests, lint and typecheck. Commit.

### Task 3: CLI integration, documentation, and verification gates

**Files:** src/scrutare/interfaces/cli.py, src/scrutare/__main__.py, tests/test_cli.py, pyproject.toml, README.md, .gitignore, .github/workflows/ci.yml, .agents/test-commands.md, repo.env.example.
**Interfaces:** Consumes ingest_pr and resolve_pr. main(argv: Sequence[str] | None = None) -> int. Register scrutare console entry point.

- [ ] Test CLI review --pr URL --config file, number resolution, --version, actionable nonzero errors for lifecycle/transport/config file failures and no posting. Result JSON: status='ingested', run_dir, head_sha, scrutare_version. No claim that a review verdict exists.
- [ ] Observe red before implementation. Implement argparse and explicit ingestion-only output; no fabricated results for later issue features. Default runs_root=.scrutare/runs. No new YAML config settings. Snapshot the passed file, do not parse config yet.
- [ ] Document current ingestion usage, gh prerequisite and incomplete review pipeline. Add ignored state/run dirs, committed repo.env.example (no login or secrets), a CI workflow running tests, lint and typecheck on push/PR, and preflight command rows matching CI. No release/deployment changes.
- [ ] Run full tests, lint, typecheck, build wheel and installed CLI smoke check. Commit. Controller verifies independently, opens PR, watches actual current-head CI, obtains clean final review, updates summary, squash merges, verifies issue closure and board Done.
