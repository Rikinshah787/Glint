// Assist: history of drafts and suggestion cards, plus the Assist settings section.
(() => {
  const CHORDS = { cmd_opt: "⌥ Option + ⌘ Command", cmd_ctrl: "⌃ Control + ⌘ Command" };
  const USED = ["inserted", "applied", "copied", "opened"];
  const OUT_LABEL = { inserted: "Inserted", applied: "Inserted", copied: "Copied", opened: "Opened", dismissed: "Dismissed",
    ignored: "Not opened", undone: "Undone", stale: "Out of date", failed: "Failed", drafted: "Drafted", offered: "Offered", shown: "Viewed" };
  const icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/><path d="M19 15.5v4M17 17.5h4"/></svg>';
  const esc = HS.esc;
  const state = { outcome: "", kind: "", app: "", q: "" };
  let items = [];

  const view = HS.registerView({
    id: "assist", label: "Assist", icon,
    html: `
      <div class="head"><div><h1>Assist</h1><div class="sub">Drafts and suggestions Glint offered, and what happened to them.</div></div>
        <button class="ghost danger" id="asClear">Clear history</button></div>
      <div class="tiles" id="asTiles"></div>
      <div class="controls">
        <div class="seg-ctl" id="asOutcome"><button data-o="" class="on">All</button><button data-o="used">Used</button><button data-o="dismissed">Dismissed</button></div>
        <select id="asKind"><option value="">Drafts and cards</option><option value="draft">Drafts</option><option value="card">Cards</option></select>
        <select id="asApp"><option value="">All apps</option></select>
        <span class="search"><input type="search" id="asQ" placeholder="Search drafts and cards" aria-label="Search drafts and cards"></span>
      </div>
      <div id="asList"></div>`,
    onShow: load,
  });

  function ago(ms) { return HS.when ? HS.when(ms) : new Date(ms).toLocaleString(); }

  function render() {
    const list = view.querySelector("#asList");
    if (!items.length) {
      const filtered = state.q || state.kind || state.outcome || state.app;
      list.innerHTML = `<div class="card">${HS.emptyState ? HS.emptyState(filtered
        ? { art: "search", title: "Nothing matches these filters", body: "Clear a filter or search for something else." }
        : { art: "chat", title: "No drafts or suggestions yet", body: "Tap the assist chord in any text field to get a draft, hold it for Quick Ask, or double-tap it for the edge panel." })
        : "Nothing yet."}</div>`;
      return;
    }
    list.innerHTML = items.map((it, i) => {
      const used = USED.includes(it.outcome);
      const what = it.kind === "card"
        ? `<b>${esc(it.headline || "Suggestion")}</b><div class="muted" style="margin-top:2px">${esc(it.body)}</div>${it.actions.length ? `<div class="chips" style="margin-top:8px">${it.actions.map((a) => `<span class="chip" title="${esc(a.preview || "")}">${esc(a.label)} · ${esc(a.type.replace("_", " "))}</span>`).join("")}</div>` : ""}`
        : `<div style="white-space:pre-wrap">${esc(it.body) || '<span class="muted">(nothing to add)</span>'}</div>
           ${it.instruction || it.tweak ? `<div class="muted" style="font-size:12.5px;margin-top:6px">${it.tweak ? `Tweak: ${esc(it.tweak)}. ` : ""}${it.instruction ? `Asked: “${esc(it.instruction)}”` : ""}</div>` : ""}
           ${it.field_text ? `<div class="muted" style="font-size:12.5px;margin-top:4px">Field had: ${esc(it.field_text)}</div>` : ""}`;
      return `<div class="card feed-item" data-i="${i}">
        <div style="display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:8px">
          <div class="chips"><span class="chip ${it.kind === "card" ? "" : "accent"}">${it.kind === "card" ? `Card · ${esc(it.card_kind || "")}` : `Draft · ${esc(it.mode || "write")}`}</span>
            <span class="muted" style="font-size:12.5px">${esc(it.app || "Unknown app")}${it.title ? ` — ${esc(it.title)}` : ""}</span></div>
          <div class="chips"><span class="chip ${used ? "accent" : ""}">${esc(OUT_LABEL[it.outcome] || it.outcome)}</span><span class="muted" style="font-size:12px">${esc(ago(it.created_at))}</span></div>
        </div>
        ${what}
        <div class="sess-acts"><button data-copy="${i}">Copy</button><button class="ghost danger" data-del="${i}">Delete</button></div>
      </div>`;
    }).join("");
  }

  function tiles(today) {
    const n = (keys) => keys.reduce((s, k) => s + (today[k] || 0), 0);
    view.querySelector("#asTiles").innerHTML = [
      [n(USED), "Used today"], [n(["drafted", "inserted", "copied"]) , "Drafts and results today"],
      [n(["offered", "shown", "applied", "opened", "ignored", "stale", "failed", "undone"]), "Cards and outcomes today"],
      [n(["dismissed", "ignored", "undone"]), "Dismissed or missed today"],
    ].map(([v, l]) => `<div class="card tile"><b>${v}</b><span>${l}</span></div>`).join("");
  }

  async function load() {
    const p = new URLSearchParams({ limit: "200" });
    for (const [k, v] of Object.entries(state)) if (v) p.set(k, v);
    try {
      const r = await HS.api(`/api/assist/history?${p}`);
      items = r.items;
      tiles(r.today || {});
      const sel = view.querySelector("#asApp");
      sel.innerHTML = `<option value="">All apps</option>` + r.apps.map((a) => `<option ${a === state.app ? "selected" : ""}>${esc(a)}</option>`).join("");
      render();
    } catch (e) { view.querySelector("#asList").innerHTML = `<div class="card empty err">${esc(e.message)}</div>`; }
  }

  view.querySelector("#asOutcome").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    view.querySelectorAll("#asOutcome button").forEach((x) => x.classList.toggle("on", x === b));
    state.outcome = b.dataset.o; load();
  };
  view.querySelector("#asKind").onchange = (e) => { state.kind = e.target.value; load(); };
  view.querySelector("#asApp").onchange = (e) => { state.app = e.target.value; load(); };
  let qt; view.querySelector("#asQ").oninput = (e) => { clearTimeout(qt); qt = setTimeout(() => { state.q = e.target.value.trim(); load(); }, 250); };
  view.querySelector("#asList").onclick = async (e) => {
    const c = e.target.dataset.copy, d = e.target.dataset.del;
    if (c !== undefined) {
      const it = items[+c];
      const text = it.kind === "card" ? (it.actions.find((a) => a.preview)?.preview || it.body) : it.body;
      try { await navigator.clipboard.writeText(text); HS.toast("Copied"); } catch { HS.toast("Couldn't copy"); }
    } else if (d !== undefined) {
      await HS.api("/api/assist/history/delete", { id: items[+d].id }); load();
    }
  };
  view.querySelector("#asClear").onclick = async () => {
    if (!(await HS.confirmDialog("Clear Assist history?", "Every saved draft, suggestion card and outcome is removed from this Mac."))) return;
    const r = await HS.api("/api/assist/history/delete", {}); HS.toast(`Removed ${r.deleted} items`); load();
  };

  /* ---------- settings ---------- */
  const card = HS.registerSettings({
    id: "assist", title: "Assist",
    description: "A two-key chord that works in any app. Tap it in a text field to get a draft, hold it to open Quick Ask, double-tap it for the edge panel.",
    html: `
      <div class="field"><label>Assist</label><div class="ctl"><label><input type="checkbox" id="asOn"> Enabled</label></div></div>
      <div class="field"><label>Chord</label><div class="ctl"><div class="seg-ctl" id="asChord">${Object.entries(CHORDS).map(([k, v]) => `<button data-c="${k}">${v}</button>`).join("")}</div>
        <span class="muted">Control + Option is reserved for Dictate.</span></div></div>
      <div class="field"><label>Suggestions</label><div class="ctl"><label><input type="checkbox" id="asCards"> Offer suggestion cards while I work</label>
        <span class="muted">check every</span><input type="number" id="asEvery" min="20" max="3600" step="10" style="width:90px"><span class="muted">seconds and when I switch apps</span></div></div>
      <div class="field"><label>Edge handle</label><div class="ctl"><label><input type="checkbox" id="asEdge"> Show a slim handle on the right screen edge</label></div></div>
      <div class="field"><label>Privacy</label><div class="ctl muted">Drafts and cards use the window text Glint already captured, your bio and lasting memory. Password fields and blocked apps are never read.</div></div>`,
    onLoad(s) {
      card.querySelector("#asOn").checked = s.assist_enabled !== false;
      card.querySelectorAll("#asChord button").forEach((b) => b.classList.toggle("on", b.dataset.c === (s.assist_chord || "cmd_opt")));
      card.querySelector("#asCards").checked = s.assist_cards_enabled !== false;
      card.querySelector("#asEvery").value = s.assist_card_interval_s || 90;
      card.querySelector("#asEdge").checked = s.assist_edge_panel !== false;
    },
  });
  async function save(patch, msg = "Assist settings saved") {
    try { const r = await HS.api("/api/assist/settings", patch); HS.toast(msg); return r; }
    catch (e) { HS.toast(e.message); }
  }
  card.querySelector("#asOn").onchange = (e) => save({ assist_enabled: e.target.checked }, e.target.checked ? "Assist on" : "Assist off");
  card.querySelector("#asCards").onchange = (e) => save({ assist_cards_enabled: e.target.checked });
  card.querySelector("#asEdge").onchange = (e) => save({ assist_edge_panel: e.target.checked });
  card.querySelector("#asEvery").onchange = async (e) => {
    const r = await save({ assist_card_interval_s: +e.target.value || 90 });
    if (r) e.target.value = r.settings.assist_card_interval_s;
  };
  card.querySelector("#asChord").onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (await save({ assist_chord: b.dataset.c }, `Assist chord: ${CHORDS[b.dataset.c]}`))
      card.querySelectorAll("#asChord button").forEach((x) => x.classList.toggle("on", x === b));
  };

})();
