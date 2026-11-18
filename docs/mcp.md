# Use Glint from Claude, Cursor and other AI apps (MCP)

Glint runs a local [Model Context Protocol](https://modelcontextprotocol.io) server so the AI tools you already
use can look things up in your Mac's memory: "what was that error I saw in the staging logs yesterday?",
"summarize what I did on the billing project this week".

## Tools

| Tool | What it returns |
|---|---|
| `search_memory(query, since_hours?, app?, limit?)` | Best matches from your captures (hybrid search): app, window title, site and time, plus text snippets if screen text is allowed |
| `get_recent_context(since_hours?, app?, limit?)` | What you've been looking at recently |
| `get_today_activity()` | Today's work sessions and time per app |
| `list_tasks()` | Your Glint tasks and their progress |

All tools are read-only. **Screen text is withheld by default**: results name the matching apps, window titles, sites and times,
but leave out the captured window text, until you tick "Include screen text in answers" in
Settings → AI apps.

## Turn it on

1. Glint → Settings → **AI apps (MCP)** → enable. The server listens on `http://127.0.0.1:47331/mcp`
   (change the port there if it's taken).
2. Type the app's name (e.g. "Cursor") and click **Create token**. Copy it now: it is shown once and Glint stores only its hash.
3. Copy the snippet Glint shows for your app. The examples below use `<TOKEN>`.

Each app gets its own token; revoke one in Settings without affecting the others.

## Claude Code

```bash
claude mcp add --transport http glint http://127.0.0.1:47331/mcp --header "Authorization: Bearer <TOKEN>"
```

Then ask: *"Use glint to find what I worked on yesterday afternoon."*

## Cursor

`~/.cursor/mcp.json` (merge into `mcpServers`):

```json
{
  "mcpServers": {
    "glint": {
      "url": "http://127.0.0.1:47331/mcp",
      "headers": { "Authorization": "Bearer <TOKEN>" }
    }
  }
}
```

## Claude Desktop

Claude Desktop launches local commands, so Glint ships a tiny stdio bridge (standard library only).
Settings shows the exact snippet with the right paths for your install; it looks like:

```json
{
  "mcpServers": {
    "glint": {
      "command": "/path/to/python3",
      "args": ["/path/to/Glint/engine/features/connectors.py", "bridge", "http://127.0.0.1:47331/mcp"],
      "env": { "GLINT_MCP_TOKEN": "<TOKEN>" }
    }
  }
}
```

Put it in `~/Library/Application Support/Claude/claude_desktop_config.json` (merge into `mcpServers`) and
restart Claude Desktop.

## Other clients

Any MCP client that supports Streamable HTTP with custom headers:

```
URL:    http://127.0.0.1:47331/mcp
Header: Authorization: Bearer <TOKEN>
```

## Notes

- Glint must be running; the server is local-only (`127.0.0.1`).
- Whatever a tool returns goes to the AI app you connected, and from there to its model provider.
  Keep screen text off unless you're comfortable with that.
- Try it without your own data: `scripts/demo.sh` starts the demo engine with an MCP server on the next port
  (shown in the terminal). Create a token in the demo's Settings and point your client at it.
- Problems? See [troubleshooting](troubleshooting.md#mcp-client-cant-connect).
