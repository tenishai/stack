# AGENTS.md — stack

Instructions for AI coding agents working in this repo. Claude Code loads this via
`CLAUDE.md` (`@AGENTS.md`); other AGENTS.md-aware tools read it directly.

Start with the [README](README.md): what the installer is, the stack file, what it
refuses to do. [ARCHITECTURE.md](ARCHITECTURE.md) names the entry points and seams.
`docs/` holds design records and the acceptance criteria for the UI increments; read
them for the reasons, not as a spec of the current code. `handoff/` holds the notes
one agent left for another; they describe one piece of work each and are history
once it lands.

## Layout

- `stack/` — the package. `model.py` is the stack file's schema; `steps.py` the
  install and upgrade steps; `clients.py` the four hands (Gojira's API, kubectl, gh,
  cbl) behind interfaces; `preflight.py` the checks; `cli.py` and `ui.py` the two
  front doors; `templates/` the UI's HTML.
- `tests/` — pytest against fakes of the four hands, including the checked-in example
  end to end. Nothing in the suite touches a real cluster or repository.
- `examples/` — the dogfood stack file and the config files it names.

## Commands

```bash
uv venv .venv && uv sync --locked   # the lock is the pin; --locked refuses a stale one
make check                          # ruff → mypy → pytest
```

Adding or changing a dependency requires `uv lock` in the same commit as the
`pyproject.toml` change — a stale lock is exactly what `--locked` is there to
refuse (a `pyjwt[crypto]` added without it would have made this command fail).

## Rules

- Sign off every commit: `git commit -s` (see [CONTRIBUTING.md](CONTRIBUTING.md)).
- Stage files by path. `*.pem`, `*-token`, `*.env`, `.generated/`, `.cabal-home/`
  and agent session notes (`CLAUDE.local.md`, `scratch*`) are gitignored local
  files; never commit them.
- The installer does what the stack file says and nothing the file does not name.
  A new capability is a new line in the file first, then a step that honours it.
- Never print a key, token or Secret value, in code, tests or an issue. Names only.
