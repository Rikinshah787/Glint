/* Connectors: Google tools for Ask, the local MCP server for other AI apps, and memory import. */
(() => {
  const { api, esc, toast, confirmDialog, when } = HS;
  const $ = (id) => document.getElementById(id);

  const css = document.createElement("style");
  css.textContent = `
    .cx-help { margin: 0 0 12px; font-size: 13px; }
    .cx-help summary { cursor: pointer; color: var(--accent); }
    .cx-help ol { margin: 8px 0 0; padding-left: 20px; color: var(--muted); }
    .cx-help li { margin: 3px 0; }
    .cx-list { display: flex; flex-direction: column; gap: 6px; width: 100%; }
    .cx-item { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 9px; }
    .cx-item .grow { flex: 1; min-width: 0; }
    .cx-item small { color: var(--muted); display: block; }
    .cx-pre { margin: 6px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--panel-2); border-radius: 9px; padding: 10px; font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; max-height: 220px; overflow: auto; }
    .cx-tabs { display: inline-flex; border: 1px solid var(--line); border-radius: 9px; overflow: hidden; margin-top: 8px; }
    .cx-tabs button { border: 0; border-radius: 0; padding: 5px 11px; font-size: 12.5px; }
    .cx-tabs button.on { background: var(--accent); color: #fff; }
    .cx-new { border: 1px solid var(--accent); background: var(--accent-2); border-radius: var(--radius, 12px); padding: 12px; margin-top: 10px; width: 100%; }
    .cx-warn { color: var(--warn); font-size: 12.5px; }
    .cx-stack { flex: 1; flex-direction: column; align-items: stretch !important; }
  `;
  document.head.appendChild(css);

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); }
    catch {
      const t = document.createElement("textarea");
      t.value = text; document.body.appendChild(t); t.select();
      try { document.execCommand("copy"); } catch {}
      t.remove();
    }
    toast("Copied");
  }
  const ago = (ms) => (ms ? when(ms) : "never");

  /* ---------------- Connected tools (Google) ---------------- */
  HS.registerSettings({
    id: "connected-tools",
    title: "Connected tools",
    description: "Let Ask read your Gmail, Google Drive and Google Calendar. Access is read-only and uses your own Google OAuth client; tokens stay in your Keychain and requests go straight from this Mac to Google.",
    html: `
      <details class="cx-help"><summary>How to create a Google OAuth client (about 5 minutes)</summary><ol>
        <li>Open the Google Cloud console and create (or pick) a project. <button class="ghost" id="cxConsole">Open console</button></li>
        <li>In <b>APIs &amp; Services → Library</b>, enable the <b>Gmail API</b>, <b>Google Drive API</b> and <b>Google Calendar API</b>.</li>
        <li>In <b>OAuth consent screen</b> (Google Auth Platform), choose <b>External</b>, fill in the app name and your email, and add yourself under <b>Test users</b>.</li>
        <li>In <b>Credentials → Create credentials → OAuth client ID</b>, pick <b>Desktop app</b>. Copy the client ID and secret below, or paste the whole downloaded JSON into the Client ID field.</li>
        <li>Click <b>Connect</b> and approve in your browser. While the app is in Testing mode Google ends the connection after 7 days, so you may need to reconnect.</li>
      </ol></details>
      <div class="field"><label>Client ID</label><div class="ctl"><input type="text" id="cxClientId" placeholder="1234-abc.apps.googleusercontent.com" style="flex:1;min-width:260px"></div></div>
      <div class="field"><label>Client secret</label><div class="ctl"><input type="password" id="cxSecret" placeholder="GOCSPX-…" style="width:260px"><button id="cxSaveClient">Save</button><span id="cxSecretState" class="ok"></span></div></div>
      <div class="field"><label>Google</label><div class="ctl" id="cxGoogle"></div></div>`,
    onLoad: () => loadGoogle(),
  });

  let pollTimer = null;
  async function loadGoogle() {
    let s;
    try { s = await api("/api/connectors/google/status"); } catch (e) { $("cxGoogle").textContent = e.message; return; }
    if (document.activeElement !== $("cxClientId")) $("cxClientId").value = s.client_id || "";
    $("cxSecretState").textContent = s.has_secret ? "Saved in Keychain" : "";
    const box = $("cxGoogle");
    const pending = s.flow && s.flow.status === "pending";
    if (s.connected) {
      const names = { gmail: "Gmail", drive: "Drive", calendar: "Calendar" };
      const svc = Object.entries(s.services || {}).filter(([, v]) => v).map(([k]) => names[k]).join(", ");
      box.innerHTML = `<span class="ok">Connected${s.email ? ` as ${esc(s.email)}` : ""}</span><span class="muted">${esc(svc || "no services granted")}</span>
        <button id="cxReconnect" class="ghost">Reconnect</button><button id="cxDisconnect" class="ghost danger">Disconnect</button>`;
      $("cxDisconnect").onclick = disconnect;
      $("cxReconnect").onclick = connect;
    } else if (pending) {
      box.innerHTML = `<span class="muted">Waiting for you to approve in the browser…</span><button id="cxCopyLink" class="ghost">Copy sign-in link</button><button id="cxRetry" class="ghost">Start over</button>`;
      $("cxCopyLink").onclick = async () => { const r = await api("/api/connectors/google/connect", { open_browser: false }); copy(r.auth_url); };
      $("cxRetry").onclick = connect;
    } else {
      const err = s.flow && s.flow.status === "error" ? `<span class="cx-warn">${esc(s.flow.error || "")}</span>` : "";
      box.innerHTML = `<button class="primary" id="cxConnect" ${s.client_id ? "" : "disabled"}>Connect Google</button>
        <span class="muted">${s.client_id ? (s.has_secret ? "Gmail, Drive and Calendar (read-only)" : "Add the client secret first") : "Add a client ID first"}</span>${err}`;
      $("cxConnect").onclick = connect;
      if (!s.has_secret) $("cxConnect").disabled = true;
    }
    clearTimeout(pollTimer);
    if (pending) pollTimer = setTimeout(loadGoogle, 2000);
  }
  async function connect() {
    try { await api("/api/connectors/google/connect", {}); toast("Finish signing in in your browser"); }
    catch (e) { toast(e.message); }
    loadGoogle();
  }
  async function disconnect() {
    if (!(await confirmDialog("Disconnect Google?", "Ask will no longer read your Gmail, Drive or Calendar. Glint also revokes its access at Google.", "Disconnect"))) return;
    await api("/api/connectors/google/disconnect", {}); toast("Google disconnected"); loadGoogle();
  }
  $("cxSaveClient").onclick = async () => {
    const body = { client_id: $("cxClientId").value.trim() };
    if ($("cxSecret").value.trim()) body.client_secret = $("cxSecret").value.trim();
    try { await api("/api/connectors/google/config", body); $("cxSecret").value = ""; toast("Google client saved"); }
    catch (e) { toast(e.message); }
    loadGoogle();
  };
  $("cxConsole").onclick = () => api("/api/connectors/google/open_console", {}).catch((e) => toast(e.message));

  /* ---------------- AI apps (MCP) ---------------- */
  HS.registerSettings({
    id: "ai-apps",
    title: "AI apps (MCP)",
    description: "Let other AI apps on this Mac — Claude Desktop, Claude Code, Cursor and other MCP clients — search your Glint memory. The server listens only on this Mac and each app needs its own token.",
    html: `
      <div class="field"><label>Server</label><div class="ctl"><label><input type="checkbox" id="cxMcpOn"> Let AI apps connect</label><span id="cxMcpState" class="muted"></span></div></div>
      <div class="field"><label>Address</label><div class="ctl"><code id="cxMcpUrl"></code><button class="ghost" id="cxMcpCopy">Copy</button>
        <span class="muted">port</span><input type="number" id="cxMcpPort" min="1024" max="65535" style="width:90px"><button class="ghost" id="cxMcpPortSave">Apply</button></div></div>
      <div class="field"><label>Screen text</label><div class="ctl"><label><input type="checkbox" id="cxMcpText"> Include screen text in answers</label>
        <span class="muted">Off: apps see only window titles, apps and times.</span></div></div>
      <div class="field"><label>Connected apps</label><div class="ctl cx-stack">
        <div class="cx-list" id="cxClients"></div>
        <div style="display:flex;gap:8px;margin-top:8px"><input type="text" id="cxClientName" maxlength="60" placeholder="App name, e.g. Claude Desktop" style="flex:1"><button id="cxClientAdd">Create token</button></div>
        <div id="cxNewClient"></div>
        <span class="muted" style="font-size:12.5px;margin-top:6px">Apps that only accept internet (HTTPS) connectors, such as ChatGPT's, can't reach a server on this Mac.</span>
      </div></div>`,
    onLoad: () => loadMcp(),
  });

  let mcp = null;
  async function loadMcp() {
    try { mcp = await api("/api/connectors/mcp"); } catch (e) { $("cxMcpState").textContent = e.message; return; }
    $("cxMcpOn").checked = mcp.enabled;
    $("cxMcpText").checked = mcp.include_screen_text;
    $("cxMcpUrl").textContent = mcp.url;
    if (document.activeElement !== $("cxMcpPort")) $("cxMcpPort").value = mcp.port;
    $("cxMcpState").innerHTML = mcp.error ? `<span class="cx-warn">${esc(mcp.error)}</span>`
      : mcp.running ? `<span class="ok">Running</span>` : mcp.enabled ? "Starting…" : "Off";
    $("cxClients").innerHTML = mcp.clients.length ? mcp.clients.map((c) => `
      <div class="cx-item"><div class="grow"><b>${esc(c.name)}</b><small>Token …${esc(c.hint)} · created ${esc(ago(c.created_at))} · last used ${esc(ago(c.last_used_at))}</small></div>
      <button class="ghost danger" data-revoke="${esc(c.id)}" data-name="${esc(c.name)}">Revoke</button></div>`).join("")
      : `<span class="muted">No apps yet. Create a token for each app you connect.</span>`;
  }
  async function mcpSave(body, msg) {
    try { await api("/api/connectors/mcp/settings", body); if (msg) toast(msg); } catch (e) { toast(e.message); }
    loadMcp();
  }
  $("cxMcpOn").onchange = (e) => mcpSave({ enabled: e.target.checked }, e.target.checked ? "AI apps can connect" : "MCP server stopped");
  $("cxMcpText").onchange = (e) => mcpSave({ include_screen_text: e.target.checked }, e.target.checked ? "AI apps will see screen text" : "Screen text hidden from AI apps");
  $("cxMcpPortSave").onclick = () => mcpSave({ port: +$("cxMcpPort").value }, "Port saved — update your apps' configs");
  $("cxMcpCopy").onclick = () => copy($("cxMcpUrl").textContent);
  $("cxClients").onclick = async (e) => {
    const id = e.target.dataset.revoke; if (!id) return;
    if (!(await confirmDialog(`Revoke ${e.target.dataset.name}?`, "That app will immediately lose access to your Glint memory.", "Revoke"))) return;
    await api("/api/connectors/mcp/clients/revoke", { id }); toast("Access revoked"); loadMcp();
  };
  $("cxClientAdd").onclick = async () => {
    const name = $("cxClientName").value.trim();
    if (!name) { toast("Name the app first"); return; }
    let r;
    try { r = await api("/api/connectors/mcp/clients", { name }); } catch (e) { toast(e.message); return; }
    $("cxClientName").value = "";
    showNewClient(r);
    if (!mcp?.enabled) await mcpSave({ enabled: true }, "AI apps can connect"); else loadMcp();
  };
  function showNewClient(r) {
    const keys = Object.keys(r.snippets);
    const guess = /cursor/i.test(r.client.name) ? "cursor" : /code/i.test(r.client.name) ? "claude_code" : /claude/i.test(r.client.name) ? "claude_desktop" : keys[0];
    const box = $("cxNewClient");
    box.innerHTML = `<div class="cx-new"><b>Token for ${esc(r.client.name)}</b> <span class="muted">— shown once; copy it now.</span>
      <div style="display:flex;gap:8px;align-items:center;margin-top:6px"><code style="flex:1;overflow:hidden;text-overflow:ellipsis">${esc(r.token)}</code><button class="ghost" id="cxTokCopy">Copy token</button></div>
      <div class="cx-tabs" id="cxSnipTabs">${keys.map((k) => `<button data-k="${k}">${esc(r.snippets[k].label)}</button>`).join("")}</div>
      <div id="cxSnip"></div>
      <div style="text-align:right;margin-top:8px"><button class="ghost" id="cxNewDone">Done</button></div></div>`;
    const pick = (k) => {
      box.querySelectorAll("#cxSnipTabs button").forEach((b) => b.classList.toggle("on", b.dataset.k === k));
      $("cxSnip").innerHTML = `<div class="muted" style="font-size:12.5px;margin-top:8px">${esc(r.snippets[k].where)}</div>
        <pre class="cx-pre">${esc(r.snippets[k].text)}</pre><button class="ghost" id="cxSnipCopy">Copy</button>`;
      $("cxSnipCopy").onclick = () => copy(r.snippets[k].text);
    };
    $("cxSnipTabs").onclick = (e) => e.target.dataset.k && pick(e.target.dataset.k);
    $("cxTokCopy").onclick = () => copy(r.token);
    $("cxNewDone").onclick = () => { box.innerHTML = ""; };
    pick(guess);
  }

  /* ---------------- Bring in memories ---------------- */
  HS.registerSettings({
    id: "memory-import",
    title: "Bring in memories",
    description: "Copy what another AI assistant knows about you into Glint's lasting memory, so Ask starts with the same context. Each import can be removed later.",
    html: `
      <div class="field"><label>From</label><div class="ctl"><select id="cxSrc"></select><span id="cxSrcHint" class="muted"></span></div></div>
      <div class="field"><label>1. Ask it</label><div class="ctl cx-stack">
        <span class="muted" style="font-size:12.5px">Paste this prompt into a new chat with that assistant.</span>
        <pre class="cx-pre" id="cxPrompt"></pre>
        <div><button id="cxPromptCopy">Copy prompt</button> <button class="ghost" id="cxLocal" style="display:none"></button></div></div></div>
      <div class="field"><label>2. Paste its answer</label><div class="ctl cx-stack">
        <textarea id="cxPaste" rows="6" style="width:100%" placeholder="- Prefers short answers&#10;- Works on the billing team at Acme&#10;…"></textarea>
        <div style="display:flex;justify-content:space-between;align-items:center"><span class="muted" style="font-size:12.5px">Glint keeps durable facts and skips what your memory already has.</span><button class="primary" id="cxImport">Import</button></div></div></div>
      <div class="field"><label>Imported</label><div class="ctl cx-stack"><div class="cx-list" id="cxImports"></div></div></div>`,
    onLoad: () => loadImports(),
  });

  let imp = null;
  async function loadImports() {
    try { imp = await api("/api/connectors/memory_import"); } catch (e) { $("cxImports").textContent = e.message; return; }
    const sel = $("cxSrc"), cur = sel.value;
    sel.innerHTML = Object.entries(imp.sources).map(([k, v]) => `<option value="${k}">${k === "other" ? "Other assistant" : esc(v.label)}</option>`).join("");
    const firstInstalled = Object.keys(imp.sources).find((k) => imp.sources[k].installed);
    sel.value = cur || firstInstalled || "chatgpt";
    renderSource();
    $("cxImports").innerHTML = imp.imports.length ? imp.imports.map((i) => `
      <div class="cx-item"><div class="grow"><b>${esc(i.label)}</b> <span class="muted">· ${i.count} fact${i.count === 1 ? "" : "s"} · ${esc(ago(i.created_at))}${i.mode === "raw" ? " · added as written" : ""}</span>
      <small class="ell">${esc(i.preview.join(" · "))}</small></div><button class="ghost danger" data-del="${esc(i.id)}">Remove</button></div>`).join("")
      : `<span class="muted">Nothing imported yet.</span>`;
  }
  function renderSource() {
    const k = $("cxSrc").value, s = imp.sources[k];
    $("cxPrompt").textContent = imp.prompts[k];
    $("cxSrcHint").textContent = s.installed ? `${s.label} is installed on this Mac` : "";
    const local = $("cxLocal");
    local.style.display = s.local_file ? "" : "none";
    if (s.local_file) local.textContent = `Load ${s.local_file} instead`;
  }
  $("cxSrc").onchange = renderSource;
  $("cxPromptCopy").onclick = () => copy($("cxPrompt").textContent);
  $("cxLocal").onclick = async () => {
    try { const r = await api(`/api/connectors/memory_import/local?source=${encodeURIComponent($("cxSrc").value)}`); $("cxPaste").value = r.text; toast(`Loaded ${r.path} — review, then Import`); }
    catch (e) { toast(e.message); }
  };
  $("cxImport").onclick = async (e) => {
    const text = $("cxPaste").value.trim();
    if (!text) { toast("Paste the answer first"); return; }
    e.target.disabled = true; e.target.textContent = "Importing…";
    try {
      const r = await api("/api/connectors/memory_import", { source: $("cxSrc").value, text });
      if (r.added) {
        $("cxPaste").value = "";
        toast(`Added ${r.added} fact${r.added === 1 ? "" : "s"} to lasting memory${r.skipped ? ` (${r.skipped} didn't fit)` : ""}`);
      } else toast(r.message || "Nothing new to add");
      if ($("memDoc")) { const p = await api("/api/profile"); $("memDoc").value = p.memory; }
    } catch (err) { toast(err.message); }
    e.target.disabled = false; e.target.textContent = "Import";
    loadImports();
  };
  $("cxImports").onclick = async (e) => {
    const id = e.target.dataset.del; if (!id) return;
    if (!(await confirmDialog("Remove this import?", "Its facts are taken out of your lasting memory.", "Remove"))) return;
    try { await api("/api/connectors/memory_import/delete", { id }); toast("Import removed"); } catch (err) { toast(err.message); }
    if ($("memDoc")) { const p = await api("/api/profile"); $("memDoc").value = p.memory; }
    loadImports();
  };

  /* ---------------- Ask chips ---------------- */
  const q = (s) => (s ? `“${String(s).slice(0, 40)}”` : "");
  Object.assign(HS.toolLabels, {
    search_gmail: (i) => `Searched Gmail ${q(i.query)}`.trim(),
    read_email: () => "Read an email",
    search_drive: (i) => (i.query ? `Searched Drive ${q(i.query)}` : "Listed recent Drive files"),
    read_drive_file: () => "Read a Drive file",
    upcoming_events: (i) => `Checked calendar (next ${i.days_ahead || 7} days)`,
  });
})();
