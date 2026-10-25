# Security policy

Repro executes code from the repositories it verifies, so isolation is a core part of the
product. The threat model is in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

## Supported versions
Security fixes go into `main` and the next release. Only the latest release is supported, so
please check that a problem still happens on it before you report it.

| Version | Supported |
|---|---|
| Latest 1.x release | Yes |
| Older releases | No |

## Reporting a vulnerability
Please **do not open a public issue** for security problems. Use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository ("Security" tab → "Report a vulnerability"). Include steps to reproduce
and the version/commit affected. We aim to acknowledge reports within 7 days.

Of particular interest:
- Escapes from the execution sandbox (network, filesystem, process) by a target repository.
- Ways a target repository can influence verdicts other than through its real behaviour
  (e.g. tampering with traces, evidence, or agent prompts via repository content).
- Leakage of secrets (API keys, tokens) into event logs, evidence, cards, or PR comments.
