# Changelog

All notable changes to the installer are documented here. The format loosely
follows [Keep a Changelog](https://keepachangelog.com/). There are no tagged
releases yet; everything is under Unreleased.

## [Unreleased]

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
