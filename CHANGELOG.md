# Changelog

All notable changes to the installer are documented here. The format loosely
follows [Keep a Changelog](https://keepachangelog.com/). There are no tagged
releases yet; everything is under Unreleased.

## [Unreleased]

### Added

- **The page asks four things.** A stranger's stack file is no longer copied and edited:
  `examples/stack.yaml` and the kobo template carry `{org}`, `{project}`, `{region}` and
  `{cluster}`, and the profile fills them (`--set name=value` on the command line). The
  profile page shows one card for the four until Gojira is installed; one press saves them
  and writes the install's `gojira.env` from them and from the stack file: project, region
  and cluster; `GOJIRA_GRANTABLE_ROLES` as the union of the roles the apps' contracts ask
  for (read from the upstreams on GitHub, the stack file's `contract:` over them);
  `GOJIRA_GRANTABLE_CLUSTER_ROLES` the same; `GOJIRA_ENVIRONMENT_CLUSTERS`; terraform on.
  A file not there yet starts from the Gojira checkout's `gojira.env.example`, so every knob
  keeps its doc; a rewrite keeps what the App card wrote. The card reads the file back, never
  a form's memory. A `template:` file is filled with the same four, plus `{app_id}` from the
  env file and `{installation_id}` read from GitHub with the App's key, so kobo's config
  template has nothing to type into either. `stack env` is the command-line verb for the
  write.
- **Runs outlive the page.** Every run is a file under `runs/` beside the profiles file,
  written as it goes; a restarted `stack ui` lists every run with its lines and how it
  ended. A run the UI stopped under shows as failed with one line saying to press again.
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
- **The stranger's file is the whole stack, every image a release.** liminal joined
  `examples/stack.yaml` the day tenishai/liminal published an image (v0.12.0); cabal moved
  to v0.5.2, whose image carries liminal's client (0.5.1's refused the `[liminal]` block
  the identity file already had); litellm-deploy is pinned to its first release, v0.1.0,
  and the kobo template's runtime to daedalus-runtime v0.10.0, the first published. Every
  `image:` line now carries a digest, and a test keeps it so.
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
