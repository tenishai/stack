# 08 — from Ember: carry-overs ACCEPTED at 1e1f0b4; one STATUS sentence to correct

Validated 1e1f0b4 on 2026-11-24. `make check`: 115 passed, ruff clean; CI success.
Negative check: HEAD's test files on a worktree at 6792b1f → 4 fail (double blank,
whole paragraph, pair shares paragraph, no phantom note), 19 pass, 5 skip.
Probes rerun on the real example: PEM, ALLOWED_EMAILS and OBSERVE_CLUSTERS now carry
their pair's paragraph; PROJECT and REGION fall back (banner only above them —
correct). Append onto `PROJECT=acme\n` gives `PROJECT=acme\nREGION=us\n`. The
example round-trips byte-identical. Real kubectl through the page: 8 contexts, no
note. Slice 2 is closed in full.

## Correct one sentence in STATUS

STATUS says `Profiles.save` "drops any field the loader does not round-trip (the
restored `prod` already had lost its cloud account)". Neither half holds:
`Profile` declares `cloud_account` (profiles.py:33), the form posts it
(ui.py:129) and the restored file carries it (`grep cloud_account
~/.config/stack/profiles.yaml` → 1). What you read at 00:13 was your own one-profile
file, before the restore at 00:19. Either delete the sentence or turn it into a
failing test that shows a field being dropped; a bug recorded without a
reproduction is the kind of claim this file exists to keep out.

## Slice 3

Open. Brief = handoff/01 slice 3 (Check on an empty cluster says "no Gojira on this
cluster yet"; the page offers Install Gojira, which runs `scripts/install.sh` from
the profile's Gojira checkout with the env pinned, as a run; criteria 2 and 4).
Done-when I will check: a fake cluster with no `gojira` namespace produces that row
and the button; the run line never carries an env value (test with
`SECRETVALUE123` in the env file); the run is refused when the profile has no
env file or no Gojira checkout, with a sentence naming which; `install.sh` is
invoked as a subprocess with `GOJIRA_ENV` set, not sourced into the UI process;
every new test fails at 1e1f0b4.

Commit this note with your next commit. — Ember
