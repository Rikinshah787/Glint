# Changelog

All notable changes to Glint are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-18

First public release.

### Memory
- Text capture of the focused window via Accessibility, with optional OCR fallback; skips password fields,
  password managers, private windows, system surfaces and blocked apps.
- Work sessions with AI reports: title, summary, project, activity, focus/depth/consistency/recovery scores, insights.
- Hybrid search: SQLite FTS5 (BM25) + local MiniLM embeddings, reciprocal rank fusion, session-level documents,
  alias expansion, time hints and near-duplicate suppression.
- Timeline of apps and windows; knowledge graph of people, projects, documents, orgs, topics, tools and sites.

### AI
- Apple's on-device model by default (macOS 26+ with Apple Intelligence); Claude or OpenAI with your own key.
- Ask agent with memory, recent-context, task, bio and lasting-memory tools; Quick Ask (`⌘⇧K`) over any app.
- Visual answers (charts, flowcharts, illustrations), on-screen pointers, Glance (`⌃⇧G`).
- Autocomplete everywhere (`Tab`), Dictate (hold `⌃⌥`), Assist drafts and suggestion cards (`⌥⌘`).
- Skills (`/trigger` saved prompts), tasks with progress logs.

### Connectors
- Local MCP server for Claude Desktop, Claude Code and Cursor, with per-app tokens; screen text off by default.
- Read-only Gmail, Drive and Calendar; import memories from other assistants; browser history import.

### Developer experience
- Plug-in architecture for engine, web and native features (`docs/PLUGINS.md`).
- Demo mode with fictional data (`scripts/demo.sh`) and a one-command screenshot pipeline (`scripts/screenshots.sh`).
- Self-contained app and DMG packaging (`make package`, `make dmg`).

[Unreleased]: https://github.com/Rikinshah787/Glint/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Rikinshah787/Glint/releases/tag/v0.1.0
