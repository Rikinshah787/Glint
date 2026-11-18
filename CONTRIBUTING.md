# Contributing to Glint

Thanks for helping! Bug reports, docs fixes, plug-ins and core changes are all welcome.

## Ground rules

- **Local-first.** Nothing may upload captured text except an AI request the user asked for, to the provider
  they chose. No telemetry, analytics or third-party services.
- **Never log captured text, keys or tokens.** Not in logs, errors, tests or issues.
- **Plug-ins over core edits.** Most features fit in `engine/features/<name>.py` + `web/features/<name>.js`
  (+ `mac/<Name>.swift`). See [docs/PLUGINS.md](docs/PLUGINS.md).
- **Honest UI and docs.** Don't claim what the code doesn't do.

## Setup

```bash
git clone https://github.com/Rikinshah787/Glint.git && cd Glint
make setup        # .venv with engine dependencies (Python 3.10+)
make run          # build/Glint.app from this checkout (needs Xcode 26 / Command Line Tools)
```

The dev app runs `engine/` and `web/` straight from your checkout: for Python or web changes, quit and
relaunch Glint. Rebuild only after Swift changes. Run `scripts/create-signing-identity.sh` once so
Accessibility permission survives rebuilds.

## Demo mode

Work on the UI without granting permissions or touching your own data:

```bash
scripts/demo.sh            # fictional week of data → http://127.0.0.1:47408/ in your browser
scripts/demo.sh --keep     # keep the temp folder afterwards (path is printed)
```

Under the hood: `engine/demo.py seed --home DIR` writes the demo data (deterministic; refuses Glint's real
data folder and any folder with non-demo data), and `engine/demo.py serve --home DIR` runs the normal engine
with capture off, an in-memory Keychain and the importers that read local data disabled. The content lives in
`engine/demo_data.py`; extend it when you add a feature so the demo shows it.

## Headless engine and tests

```bash
make test                                            # memory + retrieval tests
make check                                           # py_compile, node --check, swiftc -typecheck
GLINT_HOME=$(mktemp -d) GLINT_NO_CAPTURE=1 GLINT_PORT=47408 .venv/bin/python engine/engine.py
.venv/bin/python engine/memory.py search "query"     # memory CLI
```

Always use a temporary `GLINT_HOME` and a non-default port when testing, so you never write to your real
data folder or collide with a running Glint (default port 47321).

## Screenshots

README images are generated, not hand-made:

```bash
scripts/screenshots.sh                 # all shots, light + dark, demo.gif/webp, social preview
scripts/screenshots.sh --only graph --themes dark --no-gif --no-social
```

Needs Node 22+ and Google Chrome or Chromium. It seeds a temporary demo folder, starts the demo engine,
drives headless Chrome over the DevTools protocol (`scripts/screenshots.mjs`, no npm packages) and writes
to `docs/images/`. If you change a screen, update `SHOTS` in `scripts/screenshots.mjs` and re-run.

## Pull requests

1. Open an issue first for anything larger than a bug fix, so we can agree on the approach.
2. Keep PRs focused; one feature or fix per PR.
3. Run `make check` and `make test`. Describe how you tested (and on which macOS version).
4. For UI changes, include a before/after screenshot from demo mode (never from your own data).
5. Update docs (`docs/`, README) when behaviour changes, and add a line to `CHANGELOG.md` under "Unreleased".

Style: Python 3.10+, standard library first, type hints where they help, short docstrings. JavaScript:
plain browser JS, no build step. Swift: AppKit, no third-party packages. Match the surrounding code.

## Reporting bugs

Use the [bug report form](https://github.com/Rikinshah787/Glint/issues/new/choose). Include macOS version, Mac
model, Glint version, and what Settings → AI shows. Please don't paste captured text, screenshots of private
content, or keys. Security issues: see [SECURITY.md](SECURITY.md).

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE) and you
follow the [Code of Conduct](CODE_OF_CONDUCT.md).
