# Install

## Requirements

| | |
|---|---|
| Mac | Apple silicon (the released DMG is arm64 only) |
| macOS | 13.4 or later. Apple's on-device model needs macOS 26 or later with Apple Intelligence turned on. |
| AI (optional) | Nothing, if Apple Intelligence is available. Otherwise a Claude or OpenAI API key. Capture and search work without any model. |
| Disk | A few MB per week of captured text, plus ~90 MB once for the embedding model (downloaded on first use). |

## Option 1: Download the DMG

1. Download `Glint-<version>-arm64.dmg` from [Releases](https://github.com/Rikinshah787/Glint/releases/latest).
   Each release lists a SHA-256; check it with `shasum -a 256 Glint-*.dmg`.
2. Open the DMG and drag **Glint** to **Applications**.
3. Open Glint. If macOS says it can't verify the developer (builds without a Developer ID are ad-hoc
   signed), see [troubleshooting: "Glint can't be opened"](troubleshooting.md#glint-cant-be-opened).
4. The setup wizard walks you through permissions and the AI model.

## Option 2: Build from source

Needs Xcode 26 or its Command Line Tools (`xcode-select --install`; the on-device AI helper uses the
macOS 26 SDK) and Python 3.10+ (`brew install python@3.12`, or [uv](https://docs.astral.sh/uv/)).

```bash
git clone https://github.com/Rikinshah787/Glint.git
cd Glint
make setup     # scripts/setup.sh: creates .venv and installs engine/requirements.txt
make run       # scripts/build.sh --open: builds build/Glint.app (runs the engine from this checkout)
```

The dev build keeps using your checkout's `engine/`, `web/` and `.venv`, so Python and web changes need only
a relaunch, not a rebuild.

**Keep permissions across rebuilds.** Ad-hoc signatures change on every build, and macOS then forgets the
Accessibility grant. Create a local signing identity once:

```bash
scripts/create-signing-identity.sh
make build     # run once from Terminal and click "Always Allow" when macOS asks about the key
```

**Self-contained app or DMG** (embedded Python, no `.venv` needed):

```bash
make package   # dist/Glint.app
make dmg       # dist/Glint-<version>-arm64.dmg, then mounts and smoke-tests it
```

Set `GLINT_DEVELOPER_ID` (and `GLINT_NOTARY_PROFILE`) to sign with a Developer ID and notarize; see the
header of `scripts/package.sh`.

## Permissions

| Permission | Used for | Needed |
|---|---|---|
| Accessibility | Reading the focused window's text; typing dictation, drafts and autocomplete | Yes |
| Microphone | Dictate, only while you hold the keys | For Dictate |
| Speech Recognition | On-device transcription when no OpenAI key is set | Optional |
| Screen Recording | OCR fallback, the window screenshot in Quick Ask, Glance and on-screen pointers | Optional |

Everything degrades gracefully: without Screen Recording, Quick Ask answers from text only.

## Try without installing

`scripts/demo.sh` runs the UI on fictional data in your browser, with no permissions. See
[CONTRIBUTING.md](../CONTRIBUTING.md#demo-mode).

## Uninstall

Quit Glint (menu bar → Quit), then:

```bash
rm -rf /Applications/Glint.app
rm -rf ~/Library/Application\ Support/Glint                 # all captured data, settings and the model
security delete-generic-password -s Glint -a claude 2>/dev/null   # API keys, if you added them
security delete-generic-password -s Glint -a openai 2>/dev/null
```

Then remove Glint from System Settings → Privacy & Security (Accessibility, Microphone, Screen Recording)
and from Login Items.
