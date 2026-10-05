# Hosted acceptance fixtures

Use the procedure in [pipeline.md](../../../docs/pipeline.md) only after
`2026.10.1` is published, the secret `OPENAI_API_KEY` is explicitly installed,
and authorized GitHub review-write access is available. No normal PR or label event activates this
workflow. A same-repository PR must use `branch14-action-acceptance`, exactly
one acceptance label, and a new `synchronize` event. Name the temporary PR
**Scrutare 2026.10.1 Action acceptance**. Change only `target.py` in that PR.

The success configuration reviews only `tests/fixtures/action-acceptance/target.py`
with one senior-dev persona, the approved LiteLLM OpenAI rail, and 10000 tokens
for both after-turn allowances. This is focused bot identity and delivery
acceptance using `github.post_mode: comment`. Any valid posted verdict is a
successful execution; the remote review state must be COMMENTED. This does not
prove APPROVE permission or require an organization approval policy change.
Native cases separately cover default review approval and blocking. The failure
configuration is deliberately invalid and must stop before a model invocation.

The coordinator collects real remote evidence, using already authorized access.
Replace each placeholder with the observed numeric ID. Do not rerun an uncertain
POST. Save success and failure runs in different owned evidence directories:

```sh
mkdir -p action-acceptance-evidence/success
cd action-acceptance-evidence/success
gh pr view <caller-pr-number> --repo ViviDynamics/scrutare --json number,title,url,headRefOid,baseRefOid > caller.json
gh run view <run-id> --repo ViviDynamics/scrutare --json url,event,headSha,status,conclusion,jobs > run.json
gh run view <run-id> --repo ViviDynamics/scrutare --log > run.log
gh api repos/ViviDynamics/scrutare/actions/runs/<run-id>/artifacts > artifacts.json
gh api repos/ViviDynamics/scrutare/actions/artifacts/<artifact-id>/zip > artifact.zip
sha256sum artifact.zip > artifact.sha256
```

Retain the literal remote `ViviDynamics/scrutare@2026.10.1` invocation and
resolved release/runtime in logs. Match the artifact ID and unique name to this
run. Compute archive and member hashes directly from the downloaded ZIP:

```sh
python3 - <<'PY'
import hashlib, json, zipfile
with zipfile.ZipFile('artifact.zip') as archive:
    inventory = []
    for member in archive.infolist():
        if not member.is_dir():
            raw = archive.read(member)
            inventory.append({'path': member.filename, 'size_bytes': len(raw),
                              'sha256': hashlib.sha256(raw).hexdigest()})
with open('downloaded-files.json', 'w') as output:
    json.dump(sorted(inventory, key=lambda item: item['path']), output, indent=2)
PY
```

For success, inspect `cli-stdout.bin` and the exact matching hidden
`work/.scrutare/runs/.../result.json`. Record its captured `head_sha`, verdict,
`review.review_id`, `review.login` and `review.html_url`. Fetch that actual
remote review ID, not a review selected by the event head:

```sh
gh api repos/ViviDynamics/scrutare/pulls/<caller-pr-number>/reviews/<review-id> > remote-review.json
```

Require the remote login `github-actions[bot]`, the captured commit ID and the
recorded receipt/content to agree, with remote state COMMENTED. Save the remote URL, conclusion and archive
hashes with that comparison. Local receipts alone do not prove remote delivery.

For failure, remove the success label, add the failure label, then push another
harmless fixture-only change. Record a failed composite review and parent job,
successful internal `upload-artifact` execution from the actual logs, nonempty
retained diagnostics, empty CLI success stdout, absent success outputs and no
model session or posted review from this invalid-config invocation. This job
must remain failed; do not enable `continue-on-error`. Download and hash this
second archive by its own artifact ID. Remove the labels after evidence is
saved. Keep issue #14 incomplete until both runs and both public release assets
are verified.
