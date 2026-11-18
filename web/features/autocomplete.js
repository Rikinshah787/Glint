/* Autocomplete everywhere: settings section (look, help mode, keys, pacing, apps, stats). */
(() => {
  const STYLES = [
    { id: "under", label: "Under the line" },
    { id: "normal", label: "Inline chip" },
    { id: "ghost", label: "Ghost text" },
  ];
  const css = `
    #set-autocomplete .ac-preview { margin-top: 10px; padding: 14px 16px; border: 1px solid var(--line); border-radius: var(--r-lg);
      background: var(--panel-2); font-size: 13.5px; line-height: 1.5; min-height: 52px; position: relative; max-width: 460px; }
    #set-autocomplete .ac-typed::after { content: ""; display: inline-block; width: 1.5px; height: 1.05em; vertical-align: -2px;
      background: var(--text); margin: 0 1px; animation: acBlink 1s steps(2) infinite; }
    @keyframes acBlink { 50% { opacity: 0; } }
    #set-autocomplete .ac-chip { display: inline-flex; align-items: center; gap: 6px; padding: 2px 6px 2px 9px; border-radius: var(--r-sm);
      background: var(--panel); border: 1px solid var(--line-2); box-shadow: var(--shadow-pop); font-size: 12.5px; }
    #set-autocomplete .ac-chip b { font-weight: 500; color: var(--text); }
    #set-autocomplete .ac-chip span { color: var(--muted); }
    #set-autocomplete .ac-key { font: 10.5px/16px var(--mono); color: var(--muted); border: 1px solid var(--line-2); border-bottom-width: 1.5px; border-radius: 4px; padding: 0 5px; background: var(--panel-2); }
    #set-autocomplete .ac-under { display: block; width: fit-content; margin-top: 4px; }
    #set-autocomplete .ac-ghost { color: var(--faint); }
    #set-autocomplete .ac-stats { display: flex; gap: 8px; flex-wrap: wrap; }
    #set-autocomplete .ac-stats div { padding: 8px 12px; border-radius: var(--r-md); background: var(--panel-2); border: 1px solid var(--line); min-width: 96px; }
    #set-autocomplete .ac-stats div b { display: block; font-size: 18px; font-weight: 650; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
    #set-autocomplete .ac-stats div span { color: var(--muted); font-size: 12px; }
  `;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  const html = `
    <div class="field"><label>Autocomplete</label><div class="ctl">
      <label><input type="checkbox" id="acOn"> Suggest how to finish what I'm typing, in any app</label></div></div>
    <div class="field"><label>Look</label><div class="ctl" style="flex-direction:column;align-items:flex-start">
      <div class="seg-ctl" id="acStyle">${STYLES.map((s) => `<button data-s="${s.id}">${s.label}</button>`).join("")}</div>
      <div class="ac-preview" id="acPreview"></div></div></div>
    <div class="field"><label>Help mode</label><div class="ctl">
      <div class="seg-ctl" id="acMode"><button data-m="automatic">Automatic</button><button data-m="on_demand">Only when asked</button></div>
      <span class="muted" id="acModeHint"></span></div></div>
    <div class="field"><label>Accept with</label><div class="ctl">
      <select id="acKey"><option value="tab">Tab</option><option value="backtick">\` (backtick)</option><option value="both">Tab or \`</option></select>
      <span class="muted">Shift+key takes one word · ↓ next option · Esc dismisses</span></div></div>
    <div class="field"><label>Pacing</label><div class="ctl">
      Suggest after a <input type="number" id="acPause" min="150" max="3000" step="50" style="width:84px"> ms pause,
      once at least <input type="number" id="acMin" min="1" max="200" style="width:70px"> characters are typed</div></div>
    <div class="field"><label>Model</label><div class="ctl">
      <input type="text" id="acModel" placeholder="Same as Ask" style="width:240px">
      <span class="muted">Optional. A faster model makes suggestions appear sooner.</span></div></div>
    <div class="field"><label>Never suggest in</label><div class="ctl" style="flex:1">
      <textarea id="acApps" rows="3" style="width:100%" placeholder="One app name (or bundle id) per line"></textarea>
      <span class="muted">Code editors, terminals, password fields and password managers are always skipped.</span></div></div>
    <div class="field"><label></label><div class="ctl"><button class="primary" id="acSave">Save autocomplete settings</button></div></div>
    <div class="field"><label>So far</label><div class="ctl" style="flex-direction:column;align-items:flex-start">
      <div class="ac-stats" id="acStats"></div><div class="muted" id="acLearned" style="font-size:12.5px"></div></div></div>
    <div class="field"><label>Learned writing</label><div class="ctl">
      <button class="ghost danger" id="acClear">Clear learned samples</button>
      <button class="ghost" id="acReset">Reset stats</button></div></div>`;

  const card = HS.registerSettings({
    id: "autocomplete",
    title: "Autocomplete",
    description: "While you type in any app, Glint offers a short continuation near the cursor. Press the accept key to take the next phrase. It learns from text you accept and write, kept only on this Mac.",
    html,
    onLoad: load,
  });
  const $ = (id) => card.querySelector(`#${id}`);
  let style_ = "under", mode = "automatic";

  function keyLabel() { return { tab: "Tab", backtick: "`", both: "Tab" }[$("acKey").value] || "Tab"; }
  function renderPreview() {
    const typed = `<span class="ac-typed">Thanks for the notes, I'll update the deck</span>`;
    const head = " and send it over", tail = " before lunch.";
    const chip = `<b>${HS.esc(head.trim())}</b><span>${HS.esc(tail)}</span><span class="ac-key">${keyLabel()}</span>`;
    const p = $("acPreview");
    if (style_ === "ghost") p.innerHTML = `${typed}<span class="ac-ghost">${HS.esc(head + tail)}</span> <span class="ac-key">${keyLabel()}</span>`;
    else if (style_ === "normal") p.innerHTML = `${typed} <span class="ac-chip">${chip}</span>`;
    else p.innerHTML = `${typed}<span class="ac-chip ac-under">${chip}</span>`;
    card.querySelectorAll("#acStyle button").forEach((b) => b.classList.toggle("on", b.dataset.s === style_));
    card.querySelectorAll("#acMode button").forEach((b) => b.classList.toggle("on", b.dataset.m === mode));
    $("acModeHint").textContent = mode === "on_demand" ? "Press Option+Tab in a text field to ask for a suggestion" : "Suggestions appear when you pause typing (Option+Tab asks right away)";
  }

  async function loadStats() {
    try {
      const s = await HS.api("/api/autocomplete/stats");
      const pct = s.shown ? `${Math.round(s.accept_rate * 100)}%` : "–";
      $("acStats").innerHTML = [[s.accepted, "accepted"], [s.accepted_chars.toLocaleString(), "characters written for you"],
        [s.shown, "shown"], [pct, "accept rate"], [s.days_active, "days used"]]
        .map(([v, l]) => `<div><b>${HS.esc(String(v))}</b><span>${l}</span></div>`).join("");
      const apps = s.sample_apps.slice(0, 5).map((a) => `${HS.esc(a.app)} (${a.count})`).join(", ");
      $("acLearned").innerHTML = s.samples ? `${s.samples} writing samples learned · ${apps}` : "No writing samples learned yet.";
    } catch (e) { $("acStats").textContent = e.message; }
  }

  function load(settings) {
    $("acOn").checked = settings.autocomplete_enabled !== false;
    style_ = settings.autocomplete_style || "under";
    mode = settings.autocomplete_mode || "automatic";
    $("acKey").value = settings.autocomplete_accept_key || "tab";
    $("acPause").value = settings.autocomplete_pause_ms ?? 450;
    $("acMin").value = settings.autocomplete_min_chars ?? 12;
    $("acModel").value = settings.autocomplete_model || "";
    $("acApps").value = (settings.autocomplete_disabled_apps || []).join("\n");
    renderPreview();
    return loadStats();
  }

  // Tell the native shell right away so a change applies without waiting for its periodic refresh.
  function notifyNative(s) {
    try { window.webkit?.messageHandlers?.glint?.postMessage({ action: "autocomplete.settings", settings: s }); } catch {}
  }
  async function save(patch, quiet) {
    try {
      const r = await HS.api("/api/settings", patch);
      notifyNative(r.settings);
      if (!quiet) HS.toast("Autocomplete settings saved");
      return r.settings;
    } catch (e) { HS.toast(e.message); }
  }

  card.querySelectorAll("#acStyle button").forEach((b) => b.onclick = () => { style_ = b.dataset.s; renderPreview(); save({ autocomplete_style: style_ }, true); });
  card.querySelectorAll("#acMode button").forEach((b) => b.onclick = () => { mode = b.dataset.m; renderPreview(); save({ autocomplete_mode: mode }, true); });
  $("acKey").onchange = () => { renderPreview(); save({ autocomplete_accept_key: $("acKey").value }, true); };
  $("acOn").onchange = () => save({ autocomplete_enabled: $("acOn").checked });
  $("acSave").onclick = () => {
    const clamp = (v, lo, hi, d) => { const n = parseInt(v, 10); return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : d; };
    save({
      autocomplete_enabled: $("acOn").checked,
      autocomplete_style: style_, autocomplete_mode: mode, autocomplete_accept_key: $("acKey").value,
      autocomplete_pause_ms: clamp($("acPause").value, 150, 3000, 450),
      autocomplete_min_chars: clamp($("acMin").value, 1, 200, 12),
      autocomplete_model: $("acModel").value.trim(),
      autocomplete_disabled_apps: $("acApps").value.split("\n").map((s) => s.trim()).filter(Boolean),
    });
  };
  $("acClear").onclick = async () => {
    if (!(await HS.confirmDialog("Clear learned writing?", "Autocomplete forgets the samples of your writing it keeps per app. Stats stay."))) return;
    const r = await HS.api("/api/autocomplete/clear", { samples: true });
    HS.toast(`Removed ${r.removed_samples} samples`); loadStats();
  };
  $("acReset").onclick = async () => {
    if (!(await HS.confirmDialog("Reset autocomplete stats?", "Counts of shown and accepted suggestions start from zero.", "Reset"))) return;
    await HS.api("/api/autocomplete/clear", { samples: false, stats: true });
    HS.toast("Stats reset"); loadStats();
  };
})();
