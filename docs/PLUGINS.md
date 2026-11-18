# Writing a Glint feature

A feature is up to three files that plug into Glint without editing the core:

| Layer | File | Loaded by |
|---|---|---|
| Engine (Python) | `engine/features/<name>.py` | `engine.load_features()` at startup |
| Web UI | `web/features/<name>.js` (+ `.css`) for the main window, `web/features/<name>.quick.js` for Quick Ask, `web/features/<name>.html` for extra pages/overlays | injected into the page by the engine |
| Native (Swift) | `mac/<Name>.swift` + one line in `mac/Features.swift` | `makeFeatures()` at launch |

## Engine (`engine/features/<name>.py`)

```python
import ai
import engine as E

E.DEFAULT_SETTINGS.update({"myfeature_enabled": True})   # at import time

def register():
    E.GET_ROUTES["/api/myfeature/status"] = lambda q: {"ok": True}
    E.POST_ROUTES["/api/myfeature/do"] = lambda body: {"done": True}
    E.register_tool(ai.Tool("my_tool", "What it does.", schema, fn), quick=True)
    E.CONTEXT_HOOKS.append(lambda: "[My context]\n...")        # extra Ask context
    E.STARTUP_HOOKS.append(lambda: ...)                         # after engine start
    E.SESSION_END_HOOKS.append(lambda session_id: ...)         # after a work session ends
```

Useful engine helpers: `E.db()` + `E.DB_LOCK` (SQLite, create your own tables with
`CREATE TABLE IF NOT EXISTS`), `E._write(sql, params)`, `E.kv_get/kv_set`, `E.load_settings()`,
`E.provider()` (the user's Claude/OpenAI provider: `.run`, `.complete_text`, `.complete_json`),
`E.keychain_get/keychain_set`, `E.emit_event(kind, payload)` (stream a custom event to the Ask UI
from inside a tool), `E.log(msg)`, `E.now_ms()`, `E.CAPTURER` (capture state), `E.mem` (memory/search).
Routes receive the query dict (GET) or JSON body (POST) and return JSON-serialisable values;
raise `ai.ProviderError` for user-facing AI errors and `ValueError` for bad input.
All `/api/*` calls require the `X-Glint-Token` header (the web pages already send it).

## Web (`web/features/<name>.js`)

Runs after the main script; use `window.HS`:

```js
HS.registerView({ id: "home", label: "Home", icon: "<svg …>", html: "<div>…</div>", onShow() {} });
HS.registerSettings({ id: "myfeature", title: "My feature", description: "…", html: "…", onLoad(settings) {} });
HS.toolLabels.my_tool = (input) => `Did ${input.thing}`;     // chip label in Ask
HS.renderers.visual = (payload, element) => { … };          // render E.emit_event("visual", …)
HS.api(path, body?)  HS.toast(msg)  HS.confirmDialog(title, body)  HS.esc(s)  HS.md(text)  HS.settings
```

Quick Ask scripts (`*.quick.js`) get `HS.post(msg)`, `HS.renderers`, `HS.toolLabels`, `HS.onOpen`.
Native messages: `window.webkit.messageHandlers.glint.postMessage({action: "…", …})`.

## Native (`mac/<Name>.swift`)

```swift
final class MyFeature: GlintFeature {
    unowned let app: AppDelegate
    init(app: AppDelegate) { self.app = app }
    func start() { }                                   // launch
    func engineReady() { app.engine.request("api/myfeature/status") { json, err in } }
    func settingsChanged(_ s: [String: Any]) { }
    func handle(action: String, body: [String: Any]) -> Bool { action == "myfeature.open" }
}
```

Then add `MyFeature(app: app),` to `makeFeatures` in `mac/Features.swift`. Available from the app
delegate: `engine` (`request(path, body, completion)`), `makeWebView(bridge:transparent:)`,
`bridge`, `showMain(path:)`, `toggleQuickAsk()`, `settings`, and the `HotKey` helper
(Carbon global hotkeys; pick a unique id).
