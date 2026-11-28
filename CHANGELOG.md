# Changelog

All notable changes to the installer are documented here. The format loosely
follows [Keep a Changelog](https://keepachangelog.com/). There are no tagged
releases yet; everything is under Unreleased.

## [Unreleased]

### Added

- **A missing file is a pause, not a stop.** A `file:` or `template:` source whose file is
  not there yet pauses the run: the page shows a card with the path and what the file is
  for (never its contents) and a Done button; the CLI says the same and polls. The run
  continues when the file appears and stops only after an hour. Check says "not there
  yet" instead of failing.
- **A stack file for a stranger, and the path to it.** `examples/stack.yaml` deploys the
  stack from published images (litellm-deploy, cabal and kobo on ghcr, pinned by digest)
  into deploy repos of your own org; `examples/stack/` holds its two config files;
  `docs/install.md` goes from an empty project through Gojira to the room, naming the
  two pauses that are yours. The installer reads an upstream's `k8s/base`, `gojira.yaml`
  and cluster objects from GitHub when no clone is under `--repos`, so nothing is cloned.
  liminal joins the file when tenishai/liminal publishes an image.
- **A merge the repo's rules refuse waits for the approval and continues.** The installer
  says so with the link, retries its own merge on every poll, and goes on when the merge
  lands; gh failing to reach GitHub is still a stop. A provision that fails twice names
  the app's page on Gojira.

### Changed

- **Licence: MIT**, with the licence, NOTICE and community files a public repo
  carries (CONTRIBUTING with the DCO, SECURITY, CODE_OF_CONDUCT, SUPPORT,
  templates). (#19, #23)
- **CI runs on GitHub-hosted runners only**, with a `ci` summary job a branch
  ruleset can require. (#22)
- The litellm deploy repo is `tenishai/litellm-deploy`. (#21)

### Added (since the first install, 2026-10-08)

- `stack upgrade`: mirror a release image, land the pin by PR or push, wait for
  the roll; a `data_mount` app needs `--yes`. (#13)
- `stack ui`: profiles, Check, Install/Upgrade with live lines, a per-app release
  picker. (#17) Install waits for the post-setup-PR roll. (#19 slice 1)
- `image:` apps with installer-written deploy repos; `base_files`; `stack install
  <file> [app…]`. (#10, #14, #15)
- Preflight: refuse a kube context without a Gojira; retry a failed provision;
  open the cabal tunnel on demand; watch the dispatched run. (#4, #6, #8)
