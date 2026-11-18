# Glint

**Everything you've seen on your Mac, a glint away.**

Glint is an open-source, privacy-first memory and AI assistant for macOS. It quietly reads the
text of the windows you work in, keeps it on your Mac, groups your day into work sessions, and
lets you ask questions about anything you've seen — with the AI model of your choice (Claude or
OpenAI, using your own API key).

> Status: early (v0.1). Core capture, memory search, sessions, Ask, Quick Ask and Dictate work;
> more features are landing. Expect rough edges.

## What it does

- **Remembers your screen** — captures the text of the focused window via macOS Accessibility
  (optional OCR fallback), skips password fields, password managers and private browser windows.
- **Finds anything you saw** — hybrid search: SQLite full-text (BM25) + local MiniLM embeddings,
  fused with reciprocal rank fusion. Runs entirely on your Mac.
- **Work sessions** — your day split into sessions with an AI title, summary, focus/depth/
  consistency/recovery scores and insights; a timeline of time per app.
- **Ask** — chat with an agent that searches your memory, reads recent screens, reviews your day,
  manages tasks, and remembers durable facts about you.
- **Quick Ask** — `⌘⇧K` anywhere for a floating ask box (`/find`, `/summarize`, `/task`, `/log`).
- **Dictate** — hold `Control` + `Option`, speak, release: cleaned-up text is typed where your
  cursor is, in a style that fits the app (casual for chat, formal for email), with your own
  dictionary.
- **Tasks, bio and lasting memory** you and the assistant can edit.

## Privacy

- Captured text, embeddings, sessions and chats live in
  `~/Library/Application Support/Glint` on your Mac. Nothing is uploaded by Glint.
- Text is sent to an AI provider only when you use an AI feature (Ask, summaries, dictation
  polish), and only to the provider you configured, with your key.
- API keys are stored in the macOS Keychain.
- Pause capture any time from the menu bar; block apps; set retention (14/30/90/365 days);
  delete individual captures or everything.

## Requirements

- macOS 13 or later (Apple silicon or Intel)
- Xcode Command Line Tools (`xcode-select --install`) for `swiftc`
- Python 3.10+ (`brew install python@3.12`, or [uv](https://docs.astral.sh/uv/))
- A Claude and/or OpenAI API key for AI features (search and capture work without one)

## Install from source

```bash
git clone https://github.com/Rikinshah787/Glint.git
cd Glint
scripts/setup.sh                      # creates .venv and installs the engine's dependencies
scripts/create-signing-identity.sh    # optional, once: keeps permissions across rebuilds
scripts/build.sh --open               # builds build/Glint.app and opens it
```

Then grant **Accessibility** to Glint (System Settings → Privacy & Security → Accessibility) and
add an API key in Glint → Settings → AI.

If you created the signing identity, run `scripts/build.sh` once from Terminal and choose
**Always Allow** when macOS asks whether `codesign` may use the key.

## Permissions

| Permission | Why | Required |
|---|---|---|
| Accessibility | read the text of the focused window; insert dictated text | yes |
| Microphone | Dictate (only while you hold the keys) | for Dictate |
| Speech Recognition | on-device transcription when no OpenAI key is set | optional |
| Screen Recording | OCR fallback for apps without accessible text | optional |

## Shortcuts

| Keys | Action |
|---|---|
| `⌘⇧K` | Quick Ask |
| hold `⌃⌥` | Dictate (release to insert, `Esc` to cancel) |

## How it's built

```
mac/        Swift shell: menu bar, windows, Quick Ask panel, hotkeys, dictation
mac/capture GlintCapture: reads the frontmost window's text (Accessibility, OCR fallback)
engine/     Python engine (localhost API): capture loop, sessions, memory, Ask agent
web/        The UI (HTML/JS) shown in the app's web views
scripts/    setup, build, signing
docs/       PLUGINS.md — how to add a feature
```

The engine listens only on `127.0.0.1` and requires a per-launch token for every API call.
Features plug in without touching the core — see [docs/PLUGINS.md](docs/PLUGINS.md).

## Development

```bash
.venv/bin/python engine/tests/test_memory.py          # memory/search tests
GLINT_HOME=$(mktemp -d) GLINT_NO_CAPTURE=1 .venv/bin/python engine/engine.py   # headless engine
```

`engine/memory.py` also works as a CLI (`.venv/bin/python engine/memory.py search "…"`).

## Contributing

Issues and pull requests are welcome. Keep features in their own plug-in files where possible,
keep everything local-first, and never log or upload captured text.

## License

[MIT](LICENSE)
