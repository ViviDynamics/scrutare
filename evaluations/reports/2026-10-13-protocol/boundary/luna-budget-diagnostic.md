# Luna retained read/budget diagnostic

This is a metadata-only analysis of144 existing native sessions across36 Luna
qualification cells. No thought text, model answer text, private full paths,
credentials or headers are exported. No model/provider calls, product edits or
changes to validation or token gates occurred.

## Observed cause

Luna frequently splits known input reads across separate assistant turns. Of144
first turns,112 contain one read,27 batch all four known artifacts and5 contain
three reads.83 sessions start with only diff.patch. Across523 read-tool turns,
288 are single-read turns;235 do batch multiple calls. Thus batching already
happens, but it generally does not batch the entire known initial input set.

There are1060 read calls:180 diff.patch,154 files.json,143 context.json,147 repository
manifest,224 base-source blobs,201 head-source blobs and11 other basenames.45 sessions
reread an already requested basename:36 diff repeats,10 files repeats,3 context
repeats,3 manifest repeats and1 other repeat. These are repeated basenames, not
duplicate successful file-content reads: all extra known-artifact requests are
failed path attempts.55 absolute-path guesses violate the read root and8 directory
reads are rejected. The model subsequently uses valid relative paths. All144
sessions successfully read diff and files once;140 read context and144 read the
repository manifest. Captured source blobs are not reread.

The manifest is the largest tool result: median6843 bytes, compared with median
214 diff bytes,65 files bytes,168 context bytes and78 source-blob bytes. Once read,
that manifest remains in subsequent input transcripts. Byte counts are descriptive;
they do not establish an exact token saving from a different manifest/prompt design.

Every model turn resends the growing transcript through nare's transport. The
recorded median input-plus-cache charge grows from1007.5 tokens on turn1 to1097.5
on turn2,3512.5 on turn3 and3734 on turn4. The median session uses4 turns and10068.5
total tokens, already above the aggregate arm's10000 discovery allocation and
well above assessed/focused8000 allocations. This observed repeated-input overhead
is a concrete budget cause; the savings from forcing different batching have not
been measured, so no counterfactual reliability improvement is claimed.

All144 per-turn usage vectors sum exactly to both stored native and Scrutare
session vectors. Total usage is515455 uncached input,867153 cached input and105031
output: **92.94% input including cached tokens**,7.06% output. Cached tokens count
against the existing total-token ledger, even if provider pricing discounts them.
This is token-budget accounting, not a dollar-cost claim.

## Valid output versus refusal

103 native end-turn outputs re-decode successfully through the frozen production
schema and immutable citation checks.94 are partial solely because after-turn
usage exceeds their allocated quota;67 of those have nonempty accepted findings,
27 are empty.9 complete within quota, including1 nonempty and8 empty outputs.
Mechanical acceptance does not establish semantic truth or correct defect matches.

35 native budget stops occur with no final output.6 native max_turns stops also
have no final output. No schema, protocol or citation refusal is the cause of
these129 budget partial outcomes; the strict gate is behaving consistently with
the observed counters.22 of36 whole-review cells exceed the40000 total ceiling
because an already admitted provider turn can overshoot before the next check.
These cells remain unqualified; no ceiling or eligibility rule was changed.

## Minimal observed examples

- A clean-case devops session uses12163 against10000 over five turns, with read
  batches1/3/1/2 then final output. Per-turn input-plus-cache is1009/1097/1494/3816/4096.
- An authorization-case consumer session uses13772 against10000 over six turns,
  with reads1/1/1/1/4 then final output. Input-plus-cache is1022/1071/1162/1269/3814/4165.
- A stale-test-case devops session uses10626 against10000 over four turns, with
  read batches1/4/4 then final output. Input-plus-cache is1009/1096/3587/3897.

These examples show the input cost of extra read turns before a mechanically valid
final answer. They do not prove that fewer turns would preserve review quality.

The normalized JSON includes each turn's read-call basenames, read categories,
uncached/cached/output counters, allocated quota and unchanged decoder result.
`trace_luna_budget.py` reproduces it using only retained JSON data and the frozen
qualification-job evaluator. It never executes a captured command or model text.
Root is comparing the identical Sol fallback before any prompt/config change.
