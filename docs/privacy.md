# Privacy

Glint exists to remember what you do on your Mac without handing it to anyone. This page lists exactly what
is stored, where, and what can leave the Mac. If anything here is wrong, that's a bug: please
[open an issue](https://github.com/Rikinshah787/Glint/issues) (or a private advisory for anything sensitive, see
[SECURITY.md](../SECURITY.md)).

## What is stored

| Data | Contents |
|---|---|
| Captures | Window text (from Accessibility, or OCR if you turn it on), app name, window title, site hostname (not the full URL), time, source |
| Search index | Text passages, full-text index, 384-dimension embeddings computed locally |
| Sessions | Start/end, time per app, AI report (title, summary, project, scores, insights), your notes |
| Knowledge graph | Entity names, types, aliases, which sessions mention them |
| Chats, tasks, skills | What you and the assistant wrote |
| Profile | Your bio and lasting-memory doc |
| Writing samples | Short snippets you accepted or typed, per app, used to match your voice in autocomplete (capped per app) |
| Assist history | Drafts and cards offered, and what you did with them |
| Caches | App icons, site favicons |

**No screenshots or recordings are stored.** Quick Ask, Glance and on-screen pointers can take a screenshot
of the current window (with Screen Recording permission) to send with *that one request*; it is held in
memory briefly and not written to the database.

## Where

- Everything above: `~/Library/Application Support/Glint/` (`memory.db` SQLite database, `settings.json`,
  `models/`, `icons/`).
- Claude/OpenAI keys, the Google OAuth client secret and refresh token: the macOS Keychain (service `Glint`).
- MCP client tokens: only a SHA-256 hash is stored.

## What leaves the Mac

| When | What is sent | To |
|---|---|---|
| Using the on-device model | Nothing | — |
| Using Claude or OpenAI for Ask / Quick Ask | Your question, the chat so far, and the memory snippets, screen text and tool results the agent retrieves for that answer; the window screenshot if Quick Ask attached one | The provider you chose, with your key |
| Session reports (cloud model) | A digest of that session's captures (≤24k characters) | Same |
| Autocomplete (cloud model) | Text before your cursor, a little screen context, a few of your writing samples for that app | Same (on-device by default) |
| Dictation | Audio to OpenAI Whisper if you set an OpenAI key; otherwise transcribed on-device. The transcript to your model for cleanup | Same |
| Web search in Ask | Search queries the agent decides to run | Anthropic's web search tool (Claude only, can be turned off) |
| Site icons | A request for `https://<host>/favicon.ico` for hosts you visited | That website (turn off "Site icons" in Settings) |
| Embedding model | One-time download of `all-MiniLM-L6-v2` | Hugging Face, via fastembed |
| Google connectors | Read-only API calls you trigger from Ask | Google, only after you connect |
| MCP | Answers to tools your AI app calls | That app, on your Mac (127.0.0.1). Screen text is withheld unless you allow it |

Glint has no telemetry, analytics or crash reporting, and no server of its own.

## Local API

The engine listens on `127.0.0.1` only, checks the `Host` header (DNS-rebinding guard), and requires a
per-launch random token on every `/api/*` call. The MCP server is a separate local port with per-client
bearer tokens.

## Controls

- **Pause** capture from the menu bar (30 min, 1 h, until tomorrow, or indefinitely).
- **Block apps** (password managers are blocked by default) or capture text only from apps you list.
- **Retention**: keep captures for 14, 30, 90 or 365 days, or forever.
- **Delete**: single captures or sessions in Memory, all captured text from Settings → Privacy, chats and
  tasks individually. Deleted graph entities are never re-created.
- **Erase everything**: quit Glint and delete `~/Library/Application Support/Glint` (see
  [install.md](install.md#uninstall) for Keychain items).

## Things to know

- Accessibility text includes whatever is on screen in the focused window: messages, documents, email.
  Treat `memory.db` like your mail archive. FileVault protects it at rest.
- If you use a cloud model, that provider's data policy applies to what is sent.
- Demo mode (`scripts/demo.sh`) uses a temporary folder, never reads your data or Keychain, and is deleted on exit.
