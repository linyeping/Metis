# Security Policy

## Reporting Vulnerabilities

Please report suspected vulnerabilities privately. Do not open a public issue for security-sensitive reports.

Include:

- Affected component and version or commit.
- Reproduction steps.
- Expected and actual impact.
- Any known mitigations.

Maintainers should acknowledge reports within 7 days and coordinate a fix timeline based on severity.

## Sensitive Data Rules

- Never commit `.env`, API keys, tokens, passwords, private keys, certificates, or signing material.
- Redact provider keys and Authorization headers from logs, screenshots, diagnostics bundles, and test fixtures.
- Use `.env.example` for configuration examples.
- Store runtime secrets in user-local config such as `~/.metis/config.json` or environment variables.
- Treat workspace files, terminal output, and uploaded attachments as user data.

## Supported Versions

Security fixes target the current unreleased 3.0.0 line until the first public release policy is established.
