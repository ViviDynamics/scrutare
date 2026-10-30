# Agent instructions

## This repository is public

Everything pushed here is public, and history rewrites do not reach clones, caches or pull
request refs. Nothing from live infrastructure belongs here: no internal addresses, host,
node or hardware names, key aliases, client lists, spend logs, probe output or runbooks, in
code, reports, commit messages, PR bodies or comments. Keep raw evidence in a private
repository and publish only aggregates and placeholders. An issue here never asks to publish
such evidence.

`.github/disclosure/disclosure-guard` enforces this on every pull request. Run it before you
push:

    cp repo.env.example repo.env
    .github/disclosure/disclosure-guard --body-file <draft PR body>

It reads the private term list from `~/.config/vivi/disclosure-terms` (never commit that
file). `private_terms: false` means the list is missing on this machine: stop rather than
push unchecked. A `private_term` hit cannot be justified; remove it. A generic hit that is
not a real host or person (for example a made-up address in a test fixture) is justified by
naming its file under `### Disclosure review` in the PR body.
