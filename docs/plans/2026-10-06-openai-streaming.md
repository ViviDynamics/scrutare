# Stream OpenAI review turns through nare

Issue #14 delivery correction. Approved SPEC remains authority.

## Scope

Use nare's existing --stream for OpenAI sessions on validated nare 2026.10.4 or newer. Preserve buffered invocation for earlier supported runtimes and other providers. All calls remain through nare; config, budgets, admission, findings, verdict and posting rules are unchanged. No infrastructure, credential, App or garden deployment change.

The actual public Spark run completed one turn before Cloudflare HTTP524; WAF/LiteLLM already allow600s and stream flush is immediate. Streaming is a candidate mitigation, not proof against slow first bytes or silent gaps. Hosted proof remains required after a coherent immutable release.

## Assumptions

- Actual installed nare2026.10.4 supports --stream and strict SSE completion/usage/tool-call assembly; source at minimum2026.10.0 lacks the flag. Conservative version gating preserves the approved minimum.
- Progress deltas are private capture, never candidate findings. Only validated completed replies and terminal usage can produce a verdict. Interrupted or usage-missing streams fail without a verdict and honest incomplete accounting.
- Release2026.10.1 remains immutable; candidate2026.10.2 source, Action/runtime pins and examples must be coherent before publication. User authorized preparing a fix for review; no cluster deployment is part of this candidate.

## Tasks

- [ ] 1. Add the gated OpenAI --stream invocation and installed-nare HTTP/SSE proof. Red: actual installed OpenAI transport sees buffered payload without --stream. Green: fragmented tool/read turn and final findings with exact disjoint usage; incomplete/missing-usage stream never yields a verdict; legacy/provider flags remain unchanged.
- [ ] 2. Prepare coherent2026.10.2 release/Action/documentation pins and narrow version expectations. Preserve allnative gates, exact trusted-base workflow, two labels, COMMENT/Spark allocation and uploader failure policy. Record publication and real hosted acceptance as pending.

## Verification and review

Focused native proof first, independent task review after each unit, then complete native six-gate lanes on Python3.10/3.14 and independent whole-branch review. No fake CLI/result/artifact or engine-overlay proof. Keep prior release/failure/DNS/Spark evidence and global budgets. No live models during offline verification.
