# Security policy

## Reporting a vulnerability

Please don't open a public issue for a security problem.

Report it privately to **security@tenish.ai**.

If this repository's **Security** tab shows a **Report a vulnerability**
button, you can use that instead. GitHub offers it only on public
repositories where it has been turned on.

Include what you can of:

- what the problem is and what an attacker could do with it;
- the version or commit you tested;
- steps to reproduce, or a proof of concept;
- any configuration it depends on.

You don't need a complete write-up to report. A clear description of the
problem is enough to start.

## What happens next

- We acknowledge your report within **14 days**.
- We tell you whether we've confirmed the problem as soon as we know.
- We fix confirmed problems on `main`, and tell you before the fix is public.
- We credit you in the fix's commit or pull request unless you'd rather we didn't.

We ask that you give us a reasonable chance to fix a problem before disclosing
it publicly, and that you don't access or change data that isn't yours while
testing.

## Supported versions

stack has no tagged releases yet. Security fixes land on the `main` branch; run
the current `main`. Once releases exist, only the latest one will be patched.

## Scope

stack is an installer. It runs on an operator's machine with that operator's
own `gh`, `gcloud`, `kubectl` and `cbl` sessions, reads one stack file, and
drives a Gojira to install and upgrade the apps the file names. In scope:

- credentials: a token, key or Secret value the installer handles (generated
  keys, minted tokens, the Gojira API token) leaking through a log, the UI,
  an error, a profile file or a generated file;
- the stack file: content that makes the installer do more than the file
  says, such as writing to a repository or cluster the file does not name,
  merging a pull request it was not told to merge, or running a command the
  file did not ask for;
- `stack ui`: reaching the local UI, or acting through it, from anywhere but
  the operator's own machine.

**Out of scope:** vulnerabilities in the apps the stack installs (report those
to their own repositories), in third-party dependencies (please report those
upstream, and tell us if we ship an affected version), and problems that
require an attacker to already hold the operator's credentials or machine.
