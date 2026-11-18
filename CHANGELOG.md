# Changelog

All notable changes to Glint are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-11-18

### Autocomplete
- Context-aware suggestions: uses the visible thread or document, matching memory and your writing style per app
  (scenario pass rate 31% → 75%, invented names/numbers cut to 3%, relevant facts used 22% → 70% on the eval).
- Inline ghost text by default, a "Tab" hint, ⌥Esc or right-click to turn off for an app, 350 ms pause.
- Learns your voice from sentences you finish typing (local only, skips secure fields and code).
- Works in Chrome/Safari web fields and in VS Code, Cursor and Windsurf chat and commit boxes; keystroke fallback for
  canvas editors.
- Model routing: on-device by default; with a key, Claude Haiku 5.5 (or your OpenAI model) with on-device fallback.
- Faster first suggestion: the on-device model warms up at launch and when you start typing.

### Sessions
- Sessions follow your work, not your coffee breaks: a pause or restart under 10 minutes continues the same session,
  and old fragments are merged in the background.
- Topic split: after 20 minutes, ten focused minutes on unrelated work (another app, or another site in the browser)
  closes the session and starts a new one, so "GitHub review" and "WhatsApp" stop sharing a card.
- Every finished session gets summarized, including ones missed at quit; summaries see time per window and clean
  titles (no browser profile names or memory-usage noise).
- Session titles name the work, never your own name.
- The on-device model's cut-off replies are rescued field by field instead of failing the summary.

### Also
- "Try Glint" practice page in the menu bar; diagnostic log at `~/Library/Logs/Glint-native.log`.
- Quick Ask panel is more opaque for readability; system surfaces (notifications, Dock) are no longer captured.

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
