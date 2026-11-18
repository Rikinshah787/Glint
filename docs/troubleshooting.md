# Troubleshooting

## Glint can't be opened

Releases built without a Developer ID are ad-hoc signed, so Gatekeeper warns on first launch.

1. In Finder, right-click (or Control-click) **Glint** in Applications → **Open** → **Open**. Or:
2. System Settings → Privacy & Security → scroll to "Glint was blocked…" → **Open Anyway**.

Only do this for a DMG you downloaded from this repository's Releases page (check its SHA-256).

## Nothing is being captured

- **Accessibility**: System Settings → Privacy & Security → Accessibility → Glint must be on. If it's
  already on but Home says Glint needs access, toggle it off and on, or remove Glint with **−** and re-add it.
  This happens after rebuilding with an ad-hoc signature (see below).
- **Paused?** The status card in the sidebar shows Paused / Capture off. Resume it there or in the menu bar.
- **Blocked app or private window?** Status shows "Skipping app". Check Settings → Capture → blocked apps.
- **App shows only titles**: some apps don't expose text to Accessibility. Turn on the OCR fallback
  (Settings → Capture; needs Screen Recording).

## Permissions reset after every build (from source)

Ad-hoc signatures change on every build and macOS treats each build as a new app. Run
`scripts/create-signing-identity.sh` once, then `make build` from Terminal and choose **Always Allow** when
asked about the key. Grant Accessibility one more time; it then survives rebuilds.

## "No AI model is set up" / Apple Intelligence not available

Apple's on-device model needs **macOS 26 or later on Apple silicon with Apple Intelligence turned on**
(System Settings → Apple Intelligence & Siri), and the model must finish downloading. Settings → AI shows the
reason reported by macOS, for example:

| Reason | Meaning |
|---|---|
| `deviceNotEligible` | This Mac can't run Apple Intelligence: use a Claude or OpenAI key |
| `appleIntelligenceNotEnabled` | Turn on Apple Intelligence in System Settings |
| `modelNotReady` | The model is still downloading; try again later |
| `glint-llm helper not built` | Source build without the macOS 26 SDK: install Xcode 26 and rebuild |

Apple Intelligence also depends on device language and region settings. Without it, add an API key in
Settings → AI; everything else works the same.

## Session reports or Ask fail

- Check Settings → AI: which model is in use and whether the key is saved.
- On-device model errors like "context" or "guardrail" mean the request was too long or declined by Apple's
  safety system. Try a shorter question or a cloud model for long reports.
- Cloud errors (401, 429) come from your provider: check the key and your account limits.

## Dictate does nothing

Microphone permission is needed (System Settings → Privacy & Security → Microphone). Without an OpenAI key,
Speech Recognition permission is also needed for on-device transcription. Hold <kbd>⌃</kbd><kbd>⌥</kbd>
until you finish speaking, then release.

## Autocomplete doesn't appear

It skips password fields, code editors and terminals, needs a short pause after typing, and needs the caret at
the end of a line with enough text. Check it's enabled (Settings → Autocomplete) and the app isn't in the
disabled list. Some apps don't expose caret position through Accessibility.

## Quick Ask can't see my screen / Glance doesn't work

Grant Screen Recording (System Settings → Privacy & Security → Screen & System Audio Recording) and relaunch
Glint. Without it Quick Ask answers from text only.

## MCP client can't connect

- Glint must be running and Settings → AI apps must show the server as running on `127.0.0.1:47331`
  (or the port you chose). "Couldn't listen" means the port is taken: pick another and update the client.
- 401/403 ("Glint refused the request"): the token is wrong or was revoked. Create a new one; Glint can't show old tokens.
- Claude Desktop: restart it after editing `claude_desktop_config.json`, and check the `command` path in the
  snippet still exists (it changes if you move Glint or recreate `.venv`).
- No screen text in results: that's the default. Tick "Include screen text in answers" in Settings → AI apps if you want it.

## Search misses things I know I saw

- Semantic search needs the embedding model (~90 MB, downloaded on first use into
  `~/Library/Application Support/Glint/models`). Offline on first run, Glint falls back to keyword search.
- Check retention (Settings → Privacy) hasn't removed older captures.
- Try words that were actually on screen; titles and session reports are searched too.

## Demo or screenshots scripts fail

- `scripts/demo.sh` needs `scripts/setup.sh` first. Ports 47408/47409 busy? It picks a free port.
- `scripts/screenshots.sh` needs Node 22+ and Google Chrome or Chromium (or `CHROME=/path/to/binary`).

## Still stuck

Open an issue with your macOS version, Mac model, Glint version, and what Settings → AI shows:
[new issue](https://github.com/Rikinshah787/Glint/issues/new/choose). Please don't paste captured text or keys.
