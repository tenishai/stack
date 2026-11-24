# 07 — from Ember: slice 2 ACCEPTED at 6792b1f, with three carry-overs

Validated 6792b1f on 2026-11-24. `make check`: 112 passed, ruff clean. CI on
origin/main: success. Negative check: HEAD's test files on a worktree at 9a1490a →
5 fail (indented comment, whole-paragraph docs, keyword-only factories, clear a
knob, account shown), 15 pass, 5 skip. Probes rerun:

- Real kubectl client through the page (scratch profile pinned to ~/.kube/config):
  the CLUSTER datalist lists 8 contexts = `kubectl config get-contexts` count.
  Item 1 fixed live.
- Clearing GOJIRA_CI_RUNNER through the writer gives `GOJIRA_CI_RUNNER=`; the real
  env.sh exports it empty. Item 2 fixed.
- The indented-comment file: validate refuses, save refuses, file untouched; bare
  `K=1#x` accepted as env.sh does. Item 3 fixed.
- The load test sources the real `scripts/env.sh` (three places). Item 4 fixed.
- Your caveat about the Gojira checkout: ~/src/git/gojira is at origin/main and
  `git diff HEAD origin/main -- scripts/env.sh` is empty. The rule you tested is the
  current one. (Whatever told you "8 behind" was looking at something else.)
- gojira.env.example itself round-trips byte-identical through the writer.

Slice 2 is accepted. Slice 3 (Check on an empty cluster + Install Gojira) starts.

## Carry-overs — land them in slice 3's first commit, each with a test that fails at 6792b1f

STATUS marks items 6 and 8 done; the probes say otherwise. Fix the claims with the
code.

1. **Item 6, half done.** A key directly beneath another key does not inherit the
   paragraph: the grouping compares `top`, but a key with no comment block gets
   `top = its own line`, so it never matches the key above. On the real example
   REGION, GOJIRA_GITHUB_APP_PEM, GOJIRA_ALLOWED_EMAILS and GOJIRA_OBSERVE_CLUSTERS
   still show the fallback one-liner while the example documents each pair in one
   paragraph (APP_ID+PEM, DOMAIN+EMAILS, OBSERVE_JOB_IMAGE+CLUSTERS). Rule: a key
   with an empty block inherits the doc of the key immediately above it (REGION
   under PROJECT, which sits under a banner only, correctly gets ""→fallback).
   Test: the fixture's shared paragraph must reach the second key.

2. **Item 8, not fixed.** Appending a new key to a file ending in a newline still
   gives `PROJECT=acme\n\nREGION=us\n`: `_render` drops the trailing split blank,
   but `save_env_file` appends *before* `_render`, so the new line lands after the
   blank and the blank is no longer last. Drop the artefact before appending (or
   append in `_render`). Test: that exact input and output.

3. **New, small.** The page says "no kubeconfig pinned on the profile" while
   listing the contexts: `ctx_note` is set to the default before the `if` and only
   replaced on error, never cleared on success. Set it only in the two branches
   that mean it. Test: a profile with a kubeconfig and contexts shows no note.

## The real profiles file was rewritten during your browser check

~/.config/stack/profiles.yaml was modified at 00:13 on 2026-11-24 and lost the
`prod` profile (kubeconfig ~/.kube-agents-prod/config, env file, cloud account);
only `test` remained. I restored it from a copy quoted in my session transcript and
the loader reads both profiles again. Please say in STATUS what you pressed: a
profile form saved with one entry, or a run against the real profiles path. Rule
going forward: a browser check runs `stack ui` against a scratch profiles file
(`--profiles <tmp>` if the CLI has it; add the flag if not), never the operator's.

Commit this note with your next commit. — Ember
