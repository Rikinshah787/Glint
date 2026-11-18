# Security Policy

Glint holds sensitive data: the text of everything a person works on. We take reports seriously.

## Reporting a vulnerability

**Please don't open a public issue.** Report privately via GitHub:
[Security → Report a vulnerability](https://github.com/Rikinshah787/Glint/security/advisories/new).

Include what you found, how to reproduce it, the impact, and the Glint version and macOS version. Don't
include real captured data; demo mode (`scripts/demo.sh`) is a safe way to reproduce issues.

We aim to acknowledge reports within 3 days and to ship a fix or mitigation for confirmed issues as fast as
their severity requires. We're happy to credit you in the release notes.

## Supported versions

Only the latest release receives security fixes during v0.x.

## In scope

- Anything that lets another process, website or user read captured data or keys without the user's consent:
  the local API (`127.0.0.1`, token and `Host` checks), the MCP server and its tokens, the web UI (XSS from
  captured text, chat content or plug-in output), the stdio bridge.
- Captured data leaving the Mac other than as documented in [docs/privacy.md](docs/privacy.md).
- Capture of content that should be excluded (password fields, password managers, private windows, blocked apps).
- Code signing and update/packaging issues in released builds.

## Out of scope

- Attacks that need the user's account, root, or physical access to an unlocked Mac (the database is
  protected by macOS user permissions and FileVault).
- What an AI provider the user configured does with data Glint sent to it as documented.
- Social engineering, denial of service against your own machine.
