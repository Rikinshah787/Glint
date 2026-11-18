# FAQ

### Is Glint really free?
Yes, completely and permanently. Glint is MIT-licensed open source with no paid plans, no "Pro" tier,
no limits, no account and no telemetry. On-device AI costs nothing. Adding your own Claude or OpenAI key
is optional; if you do, you pay that provider directly for your own usage.

### Why does macOS warn me the first time I open it?
Apple charges developers for notarization, and Glint doesn't sell anything. Right-click (or Control-click)
Glint in Applications → **Open** → **Open** once, and macOS remembers your choice. You can also build it
yourself from source in a few minutes.

### Does Glint record my screen?
No video and no stored screenshots. It reads the text of the focused window through macOS Accessibility
(optionally OCR for apps without accessible text). Quick Ask, Glance and on-screen pointers can send a
screenshot of the current window with *one* AI request if you grant Screen Recording; it is kept in memory
briefly and never saved.

### Do I need an API key?
No, if your Mac has Apple Intelligence (macOS 26+, Apple silicon): Glint uses Apple's on-device model.
Otherwise add a Claude or OpenAI key for AI features. Capture, search, the timeline and the graph view work
without any model; session reports and Ask need one.

### Does anything get uploaded?
Not by Glint itself: there is no Glint server, account or telemetry. If you choose Claude or OpenAI, the text a
request needs is sent to that provider with your key. Full list in [privacy.md](privacy.md).

### What does it never capture?
Password fields, password managers (1Password, Bitwarden, Keychain Access, …), private/incognito windows,
system authentication dialogs, Glint itself, and any app you add to the block list.

### How much disk and battery does it use?
Text is compact: the demo week (about 1,600 captures across 31 sessions) is under 6 MB. The embedding model is ~90 MB, downloaded
once. Capture is a lightweight poll plus an occasional snapshot when the window changes. If you notice a
battery or CPU impact, please open an issue with details.

### Can I search without AI?
Yes. Search is fully local (full-text + local embeddings) and needs no model.

### Can Claude / Cursor / Claude Code use my memory?
Yes, through the built-in MCP server, with a token per app and screen text off by default. See [mcp.md](mcp.md).

### Intel Macs? Windows? Linux?
The released DMG is Apple silicon only (macOS 13.4+). The code may build on Intel from source, but it is not
tested. There is no Windows or Linux version.

### How do I delete my data?
Single captures or sessions in Memory; all captured text or a retention period in Settings → Privacy; or quit
Glint and delete `~/Library/Application Support/Glint`. See [install.md](install.md#uninstall).

### Can I try it without giving it access to anything?
Yes: `scripts/demo.sh` runs the full UI on a fictional week of data, in your browser, with no permissions.

### Why text instead of screenshots?
Text is small, fast to search, easy to exclude per field (passwords), and cheap to send to a model only when
needed. The trade-off is that Glint can't show you a visual replay of your screen.

### Is it ready for daily use?
It's v0.1: the core (capture, search, sessions, Ask) works and is used daily, but expect rough edges and
breaking changes. Please [report bugs](https://github.com/Rikinshah787/Glint/issues/new/choose).

### How can I help?
Try the demo, file issues, write a plug-in, or improve the docs. See [CONTRIBUTING.md](../CONTRIBUTING.md).
