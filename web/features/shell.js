/* Shell: Home dashboard, Skills manager + automation ideas, insight ratings, focus/notification/import
   settings, and the first-run setup wizard. Uses window.HS (see docs/PLUGINS.md). */
(() => {
  const H = window.HS;
  if (!H) return;
  const { api, esc, toast } = H;
  const byId = (id) => document.getElementById(id);
  const hasNative = () => !!window.webkit?.messageHandlers?.glint;
  const native = (msg) => { try { window.webkit?.messageHandlers?.glint?.postMessage(msg); } catch {} return hasNative(); };
  const isOn = (view) => byId(`v-${view}`)?.classList.contains("on");
  const svg = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const ICON = {
    home: svg('<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v10h13V10"/>'),
    skills: svg('<path d="M4 5h11l5 5v9H4z"/><path d="M15 5v5h5M8 13h8M8 16.5h5"/>'),
    up: svg('<path d="M7 10v10H4V10zM7 10l4-7c1.5 0 2.5 1 2.2 2.6L12.6 9H19a2 2 0 0 1 2 2.3l-1.2 6.8A2 2 0 0 1 17.8 20H7"/>'),
    down: svg('<path d="M7 14V4H4v10zM7 14l4 7c1.5 0 2.5-1 2.2-2.6L12.6 15H19a2 2 0 0 0 2-2.3l-1.2-6.8A2 2 0 0 0 17.8 4H7"/>'),
  };

  /* ------------------------------------------------------------------ styles */
  const css = document.createElement("style");
  css.textContent = `
  .sh-grid { display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1.3fr); gap: 14px; margin-bottom: 14px; }
  .sh-pad { padding: 16px 18px; }
  .sh-now { padding: 18px; display: flex; flex-direction: column; gap: 10px; }
  .sh-now .sh-app { font-size: 20px; font-weight: 650; letter-spacing: -.01em; display: flex; align-items: center; gap: 10px; }
  .sh-now .state { color: var(--muted); }
  .sh-now .acts { display: flex; gap: 8px; flex-wrap: wrap; margin-top: auto; }
  .sh-now .elapsed { font-variant-numeric: tabular-nums; color: var(--accent); font-weight: 600; }
  .sh-chart svg { width: 100%; height: auto; display: block; margin-top: 8px; }
  .sh-chart text { fill: var(--muted); font-size: 12px; }
  .sh-chart .val { fill: var(--text); font-size: 11.5px; font-weight: 600; }
  .sh-links { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 8px; margin-top: 12px; }
  .sh-links button { text-align: left; padding: 10px 12px; border-radius: 11px; }
  .sh-links small { display: block; color: var(--muted); font-size: 12px; }
  .sh-recent div { display: flex; justify-content: space-between; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--line); font-size: 13px; cursor: pointer; }
  .sh-recent div:last-child { border-bottom: 0; }
  .sh-skills { display: grid; grid-template-columns: 280px minmax(0,1fr); gap: 14px; align-items: start; }
  .sh-sklist { padding: 10px; max-height: calc(100vh - 220px); overflow: auto; }
  .sh-sklist input { width: 100%; margin-bottom: 8px; }
  .sh-sk { padding: 9px 10px; border-radius: 9px; cursor: pointer; }
  .sh-sk:hover { background: var(--panel-2); } .sh-sk.on { background: var(--accent-2); }
  .sh-sk code { font-size: 11.5px; } .sh-sk div { color: var(--muted); font-size: 12px; }
  .sh-sked { padding: 18px; }
  .sh-sked .row { display: grid; grid-template-columns: 1fr 200px; gap: 10px; margin-bottom: 10px; }
  .sh-sked input, .sh-sked textarea { width: 100%; }
  .sh-sked .panes { display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr); gap: 10px; }
  .sh-sked textarea { min-height: 300px; font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; resize: vertical; }
  .sh-preview { border: 1px solid var(--line); border-radius: 9px; padding: 10px 12px; min-height: 300px; max-height: 520px; overflow: auto; background: var(--panel-2); }
  .sh-preview p { margin: 0 0 8px; }
  .sh-sked .acts { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
  .sh-idea { padding: 16px 18px; margin-bottom: 10px; }
  .sh-idea p { margin: 6px 0 0; } .sh-idea .muted { font-size: 13px; }
  .sh-rate { display: inline-flex; gap: 4px; align-items: center; margin-top: 8px; flex-wrap: wrap; }
  .sh-rate button { padding: 2px 6px; border-radius: 7px; line-height: 0; color: var(--faint); background: transparent; border-color: transparent; }
  .sh-rate button svg { width: 15px; height: 15px; }
  .sh-rate button:hover { color: var(--text); }
  .sh-rate button.on { color: var(--accent); background: var(--accent-2); }
  .sh-rate .why { font-size: 11.5px; line-height: 1.4; padding: 2px 8px; color: var(--muted); border-color: var(--line); }
  .sh-rate .why.on { color: var(--accent); }
  .sh-wiz { position: fixed; inset: 0; z-index: 9; background: var(--bg); display: none; place-items: center; padding: 24px; overflow: auto; }
  .sh-wiz.on { display: grid; }
  .sh-wiz .box { width: min(640px, 100%); padding: 28px 30px; }
  .sh-wiz .bar { height: 4px; border-radius: 3px; background: var(--panel-2); overflow: hidden; margin-bottom: 6px; }
  .sh-wiz .bar i { display: block; height: 100%; background: var(--accent); transition: width .25s; }
  .sh-wiz .stepn { color: var(--faint); font-size: 12px; margin-bottom: 18px; }
  .sh-wiz h2 { margin: 0 0 6px; font-size: 24px; letter-spacing: -.02em; }
  .sh-wiz .lead { color: var(--muted); margin: 0 0 18px; }
  .sh-wiz .foot { display: flex; justify-content: space-between; align-items: center; margin-top: 24px; gap: 10px; }
  .sh-wiz .foot div { display: flex; gap: 8px; }
  .sh-choices { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 10px; }
  .sh-choices button { text-align: left; padding: 12px 14px; border-radius: 12px; }
  .sh-choices button.on { border-color: var(--accent); background: var(--accent-2); }
  .sh-choices small { display: block; color: var(--muted); }
  .sh-perm { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 12px; margin-bottom: 10px; }
  .sh-perm small { display: block; color: var(--muted); }
  .sh-ok { color: var(--accent); font-weight: 600; } .sh-no { color: var(--warn); }
  .sh-appchips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; max-height: 200px; overflow: auto; }
  .sh-appchips button { padding: 4px 10px; border-radius: 999px; font-size: 12.5px; }
  .sh-appchips button.on { background: var(--accent); border-color: var(--accent); color: #fff; }
  .sh-slash { position: absolute; bottom: calc(100% + 6px); left: 0; right: 0; padding: 6px; display: none; max-height: 240px; overflow: auto; z-index: 6; }
  .sh-slash.on { display: block; }
  .sh-slash div { padding: 6px 10px; border-radius: 8px; cursor: pointer; display: flex; gap: 10px; }
  .sh-slash div.on, .sh-slash div:hover { background: var(--panel-2); }
  .sh-slash span { color: var(--muted); font-size: 12.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  @media (max-width: 860px) { .sh-grid, .sh-skills, .sh-sked .panes, .sh-choices, .sh-links { grid-template-columns: 1fr; } }
  `;
  document.head.appendChild(css);

  /* ------------------------------------------------------------------ home */
  H.registerView({
    id: "home", label: "Home", icon: ICON.home, onShow: loadHome,
    html: `
      <div class="head"><div><h1 id="shGreet">Hello</h1><div class="sub" id="shDate"></div></div>
        <div class="chips"><button class="primary" data-go="ask">Ask Glint</button></div></div>
      <div id="shSetup"></div>
      <div class="sh-grid">
        <div class="card sh-now" id="shNow"></div>
        <div class="card sh-pad sh-chart"><b>Tracked time, last 7 days</b><div id="shChart"></div></div>
      </div>
      <div class="tiles" id="shTiles"></div>
      <div class="sh-grid">
        <div class="card sh-pad"><b>Today's sessions</b><div class="sh-recent" id="shRecent" style="margin-top:6px"></div></div>
        <div class="card sh-pad"><b>Shortcuts</b><div class="sh-links">
          <button data-go="ask">Ask<small>Questions about anything you've seen</small></button>
          <button data-quick>Quick Ask<small>⌘⇧K over any app</small></button>
          <button data-go="memory">Search memory<small>Find a page, chat or doc</small></button>
          <button data-go="skills">Skills<small>Saved /shortcuts for Ask</small></button>
          <button data-go="today">Timeline<small>Today's apps and sessions</small></button>
          <button data-go="settings">Settings<small>Capture, focus, privacy</small></button>
        </div></div>
      </div>`,
  });
  const homeNav = document.querySelector('.nav[data-view="home"]');
  document.querySelector(".side .brand")?.after(homeNav);
  try { if (!localStorage.getItem("hs.view")) start = "home"; } catch {}  // eslint-disable-line no-undef

  document.addEventListener("click", (e) => {
    const go = e.target.closest("[data-go]");
    if (go && (go.closest("#v-home") || go.closest("#v-skills"))) H.show(go.dataset.go);
    if (e.target.closest("#v-home [data-quick]")) { if (!native({ action: "shell.quickAsk" })) toast("Press ⌘⇧K in any app"); }
  });

  const MODE = {
    remembering: ["Remembering window text", "live"], title_only: ["Window titles only for this app", "live"],
    private: ["Private app: not captured", ""], paused: ["Paused", "paused"], off: ["Capture is off", ""],
    needs_access: ["Needs Accessibility permission", "paused"], idle: ["Idle", ""], starting: ["Starting…", ""],
    error: ["Capture error", "paused"],
  };
  const dur = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? `${h}h ${m}m` : `${m}m`; };

  function renderNow(n) {
    const [label, dot] = MODE[n.mode] || [n.mode, ""];
    const state = n.mode === "paused" && n.paused_until ? `Paused until ${H.clock(n.paused_until)}` : label;
    const paused = n.mode === "paused" || n.mode === "off";
    byId("shNow").innerHTML = `
      <span class="muted" style="font-size:12px;text-transform:uppercase;letter-spacing:.06em">Now</span>
      <div class="sh-app"><i class="dot ${dot}"></i><span class="ell">${esc(n.app || "Nothing in front yet")}</span></div>
      <div class="state">${esc(state)}${n.title ? ` · <span class="ell">${esc(n.title.slice(0, 80))}</span>` : ""}</div>
      ${n.session_id ? `<div>Session <span class="elapsed" data-start="${n.session_started_at}">${dur(n.elapsed_s)}</span>${n.session_title ? ` · ${esc(n.session_title)}` : ""}</div>` : '<div class="muted">No session running. One starts when Glint sees you working.</div>'}
      <div class="acts">
        ${n.mode === "needs_access" ? '<button class="primary" data-now="ax">Grant access</button>' : ""}
        <button data-now="${paused ? "resume" : "pause"}">${paused ? "Resume capture" : "Pause 30 min"}</button>
        <button class="ghost" data-now="ask">What am I doing?</button>
      </div>`;
  }
  byId("shNow").addEventListener("click", async (e) => {
    const a = e.target.closest("[data-now]")?.dataset.now; if (!a) return;
    if (a === "ax") native({ action: "requestAccessibility" });
    if (a === "pause") { await api("/api/capture", { pause_minutes: 30 }); toast("Paused for 30 minutes"); }
    if (a === "resume") { await api("/api/capture", { pause_minutes: 0, enabled: true }); toast("Capture resumed"); }
    if (a === "ask") { H.show("ask"); const i = byId("askInput"); if (i) { i.value = "Summarize what I'm working on right now."; i.focus(); } }
    refreshNow();
  });

  function chart(week) {
    const W = 700, top = 22, h = 130, slot = W / week.length, bw = Math.min(56, slot * 0.56);
    const max = Math.max(60, ...week.map((d) => d.minutes));
    const today = week.length - 1;
    const bars = week.map((d, i) => {
      const bh = d.minutes ? Math.max(3, (d.minutes / max) * h) : 2, x = i * slot + (slot - bw) / 2, y = top + h - bh;
      const v = d.minutes ? H.mins(d.minutes * 60) : "";
      return `<g><title>${esc(d.date)}: ${v || "nothing tracked"}</title>
        <rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="6" fill="var(--accent)" fill-opacity="${i === today ? 1 : 0.42}"/>
        ${v ? `<text class="val" x="${x + bw / 2}" y="${y - 6}" text-anchor="middle">${v}</text>` : ""}
        <text x="${x + bw / 2}" y="${top + h + 20}" text-anchor="middle"${i === today ? ' style="fill:var(--text);font-weight:600"' : ""}>${i === today ? "Today" : esc(d.label)}</text></g>`;
    }).join("");
    const total = week.reduce((a, d) => a + d.minutes, 0);
    return `<svg viewBox="0 0 ${W} ${top + h + 30}" role="img" aria-label="Tracked minutes per day">
      <line x1="0" x2="${W}" y1="${top + h + 0.5}" y2="${top + h + 0.5}" stroke="var(--line)"/>${bars}</svg>
      <div class="muted" style="font-size:12.5px">${H.mins(total * 60)} this week · ${H.mins(Math.round(total / 7) * 60)} daily average</div>`;
  }

  async function loadHome() {
    const hour = new Date().getHours();
    byId("shGreet").textContent = hour < 5 ? "Working late" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    byId("shDate").textContent = new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
    let d;
    try { d = await api("/api/shell/home"); } catch (e) { byId("shNow").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
    renderNow(d.now);
    byId("shChart").innerHTML = chart(d.week);
    const t = d.today;
    byId("shTiles").innerHTML = [
      [H.mins(t.tracked_seconds), "tracked today"], [t.sessions, "work sessions"],
      [t.avg_focus ?? "–", "average focus"], [t.asks, "questions asked"],
    ].map(([v, l]) => `<div class="card tile"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("");
    byId("shRecent").innerHTML = t.latest.length ? t.latest.map((s) => `<div data-go="today"><span class="ell">${esc(s.title || (s.active ? "Current session" : "Untitled session"))}${s.state_name ? ` <span class="chip accent">${esc(s.state_name)}</span>` : ""}</span><span class="muted">${H.clock(s.started_at)}${s.focus_score != null ? ` · ${s.focus_score}` : ""}</span></div>`).join("")
      + (t.top_apps.length ? `<div style="cursor:default;color:var(--muted)">Top apps: ${t.top_apps.slice(0, 3).map(([a, s]) => `${esc(a)} ${H.mins(s)}`).join(" · ")}</div>` : "")
      : '<div class="muted" style="cursor:default">Nothing yet today.</div>';
    byId("shSetup").innerHTML = d.onboarding.onboarding_done ? "" :
      '<div class="banner info"><span>Finish setting up Glint: permissions, which apps to remember, and AI keys.</span><button data-setup>Resume setup</button></div>';
  }
  async function refreshNow() { if (!isOn("home")) return; try { renderNow(await api("/api/shell/now")); } catch {} }
  setInterval(refreshNow, 5000);
  setInterval(() => { // tick the elapsed timer between polls
    const el = document.querySelector("#shNow .elapsed[data-start]");
    if (el && +el.dataset.start) el.textContent = dur((Date.now() - +el.dataset.start) / 1000);
  }, 1000);
  document.addEventListener("click", (e) => { if (e.target.closest("[data-setup]")) openWizard(); });

  /* ------------------------------------------------------------------ skills */
  H.registerView({
    id: "skills", label: "Skills", icon: ICON.skills, onShow: loadSkills,
    html: `
      <div class="head"><div><h1>Skills</h1><div class="sub">Saved instructions. Type a skill's <code>/trigger</code> in Ask or Quick Ask to run it, or ask Glint to create one.</div></div>
        <div class="chips"><button id="skImport">Import .md</button><input type="file" id="skFile" accept=".md,.markdown,.txt" multiple hidden><button class="primary" id="skNew">New skill</button></div></div>
      <div class="sh-skills">
        <div class="card sh-sklist"><input type="search" id="skQ" placeholder="Search skills…" autocomplete="off"><div id="skList"></div></div>
        <div class="card sh-sked" id="skEd"></div>
      </div>
      <div class="head" style="margin-top:30px"><div><h1 style="font-size:20px">Automation ideas</h1><div class="sub">Glint reviews your recent work sessions for things you keep doing by hand.</div></div>
        <div class="chips"><select id="autoDays"><option value="7">Last 7 days</option><option value="14" selected>Last 14 days</option><option value="30">Last 30 days</option></select><button class="primary" id="autoScan">Scan my work</button></div></div>
      <div id="autoIdeas"></div>`,
  });
  let skills = [], skSel = null;

  async function loadSkills() {
    skills = await api("/api/skills");
    renderSkillList();
    if (skSel && skSel !== "new") { const s = skills.find((x) => x.id === skSel); if (s) editSkill(s); else editSkill(null); }
    else if (!skSel) editSkill(skills[0] || null);
    loadIdeas();
  }
  function renderSkillList() {
    const q = byId("skQ").value.trim().toLowerCase();
    const list = skills.filter((s) => !q || `${s.name} ${s.trigger} ${s.description} ${s.body}`.toLowerCase().includes(q));
    byId("skList").innerHTML = list.length ? list.map((s) => `<div class="sh-sk ${s.id === skSel ? "on" : ""}" data-sk="${esc(s.id)}"><b>${esc(s.name)}</b> <code>${esc(s.trigger)}</code><div class="ell">${esc(s.description || "No description")}</div></div>`).join("")
      : `<div class="empty" style="padding:20px 8px">${skills.length ? "No matches." : "No skills yet."}</div>`;
  }
  function editSkill(s) {
    skSel = s ? s.id : "new";
    renderSkillList();
    const v = s || { name: "", trigger: "", description: "", body: "## Steps\n1. \n" };
    byId("skEd").innerHTML = `
      <div class="row"><input type="text" id="skName" placeholder="Name, e.g. Weekly update" value="${esc(v.name)}" maxlength="80">
        <input type="text" id="skTrig" placeholder="/trigger" value="${esc(v.trigger)}" maxlength="41"></div>
      <input type="text" id="skDesc" placeholder="When to use it (one line)" value="${esc(v.description)}" maxlength="300" style="margin-bottom:10px">
      <div class="panes"><textarea id="skBody" placeholder="Markdown instructions Glint follows when you run this skill">${esc(v.body)}</textarea><div class="sh-preview" id="skPrev"></div></div>
      <div class="acts"><button class="primary" id="skSave">${s ? "Save" : "Create skill"}</button>
        ${s ? `<button id="skTry">Run in Ask</button><button class="ghost" id="skExport">Export .md</button><button class="ghost danger" id="skDel">Delete</button><span class="muted" style="margin-left:auto;font-size:12.5px">Used ${s.uses} time${s.uses === 1 ? "" : "s"}</span>` : ""}</div>`;
    const prev = () => { byId("skPrev").innerHTML = H.md(byId("skBody").value) || '<span class="muted">Preview</span>'; };
    byId("skBody").oninput = prev; prev();
    byId("skName").oninput = () => { if (!s && !byId("skTrig").dataset.touched) byId("skTrig").value = "/" + byId("skName").value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""); };
    byId("skTrig").oninput = () => { byId("skTrig").dataset.touched = "1"; };
    byId("skSave").onclick = async () => {
      try {
        const r = await api("/api/skills", { id: s?.id, name: byId("skName").value, trigger: byId("skTrig").value, description: byId("skDesc").value, body: byId("skBody").value });
        skSel = r.id; toast(s ? "Skill saved" : `Created ${r.trigger}`); loadSkills();
      } catch (e) { toast(e.message); }
    };
    if (!s) return;
    byId("skTry").onclick = () => { H.show("ask"); const i = byId("askInput"); if (i) { i.value = s.trigger + " "; i.focus(); } };
    byId("skExport").onclick = () => {
      const md = `---\nname: ${s.name}\ntrigger: ${s.trigger}\ndescription: ${s.description}\n---\n\n${s.body}\n`;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
      a.download = `${s.trigger.slice(1) || "skill"}.md`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    };
    byId("skDel").onclick = async () => {
      if (!(await H.confirmDialog(`Delete ${s.name}?`, `${s.trigger} will stop working in Ask.`))) return;
      await api("/api/skill/delete", { id: s.id }); skSel = null; toast("Skill deleted"); loadSkills();
    };
  }
  byId("skQ").oninput = renderSkillList;
  byId("skList").onclick = (e) => { const id = e.target.closest("[data-sk]")?.dataset.sk; const s = skills.find((x) => x.id === id); if (s) editSkill(s); };
  byId("skNew").onclick = () => editSkill(null);
  byId("skImport").onclick = () => byId("skFile").click();
  byId("skFile").onchange = async (e) => {
    let n = 0;
    for (const f of e.target.files) {
      try { const r = await api("/api/skills/import", { markdown: await f.text(), filename: f.name }); skSel = r.id; n++; } catch (err) { toast(`${f.name}: ${err.message}`); }
    }
    e.target.value = ""; if (n) toast(`Imported ${n} skill${n > 1 ? "s" : ""}`); loadSkills();
  };

  /* automation ideas */
  let ideas = [];
  function renderIdeas(r) {
    ideas = r.ideas || [];
    const when = r.at ? `<div class="muted" style="font-size:12.5px;margin-bottom:8px">Last scan ${H.when(r.at)} · ${r.sessions ?? 0} sessions over ${r.days} days</div>` : "";
    if (!ideas.length) { byId("autoIdeas").innerHTML = when + `<div class="card empty">${esc(r.note || (r.at ? "No repeated work found. Try a longer range later." : "Run a scan to find work worth automating."))}</div>`; return; }
    byId("autoIdeas").innerHTML = when + ideas.map((it, i) => `<div class="card sh-idea">
      <div style="display:flex;justify-content:space-between;gap:10px"><b>${esc(it.title)}</b><span class="chips"><span class="chip accent">${esc(it.effort)} effort</span>${it.minutes_per_week ? `<span class="chip">~${it.minutes_per_week} min/week</span>` : ""}</span></div>
      <p>${esc(it.pattern)}</p><p class="muted">${esc(it.evidence)}</p><p><b>Try:</b> ${esc(it.suggestion)}</p>
      ${it.skill_body ? `<div class="acts" style="margin-top:10px"><button data-idea="${i}">Save as skill</button></div>` : ""}</div>`).join("");
  }
  async function loadIdeas() { try { renderIdeas(await api("/api/shell/automation_ideas")); } catch {} }
  byId("autoScan").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Scanning…";
    try { renderIdeas(await api("/api/shell/automation_scan", { days: +byId("autoDays").value })); } catch (err) { toast(err.message); }
    e.target.disabled = false; e.target.textContent = "Scan my work";
  };
  byId("autoIdeas").onclick = async (e) => {
    const i = e.target.dataset.idea; if (i == null) return;
    const it = ideas[+i];
    try { const r = await api("/api/skills", { name: it.skill_name || it.title, description: it.title, body: it.skill_body }); skSel = r.id; toast(`Saved ${r.trigger}`); loadSkills(); }
    catch (err) { toast(err.message); }
  };

  /* slash suggestions in the Ask composer */
  const BUILTIN = [["/task", "Create a task"], ["/log", "Log progress on a task"], ["/summarize", "Summarize what I'm doing"], ["/find", "Search recent captures"]];
  const askInput = byId("askInput");
  if (askInput) {
    const pop = document.createElement("div");
    pop.className = "card sh-slash";
    const box = askInput.closest(".box"); box.style.position = "relative"; box.appendChild(pop);
    let opts = [], sel = 0, cache = null;
    const close = () => { pop.classList.remove("on"); opts = []; };
    const render = () => { pop.innerHTML = opts.map(([t, d], i) => `<div class="${i === sel ? "on" : ""}" data-i="${i}"><code>${esc(t)}</code><span>${esc(d)}</span></div>`).join(""); pop.classList.toggle("on", opts.length > 0); };
    const pick = (i) => { askInput.value = opts[i][0] + " "; close(); askInput.focus(); };
    askInput.addEventListener("input", async () => {
      const v = askInput.value;
      if (!/^\/[\w-]*$/.test(v)) return close();
      if (!cache) { try { cache = await api("/api/skills"); setTimeout(() => { cache = null; }, 30000); } catch { cache = []; } }
      opts = [...BUILTIN, ...cache.map((s) => [s.trigger, s.description || s.name])].filter(([t]) => t.startsWith(v.toLowerCase())).slice(0, 8);
      sel = 0; render();
    });
    box.addEventListener("keydown", (e) => {  // capture on the parent so it runs before the composer's own Enter handler
      if (e.target !== askInput || !opts.length) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); sel = (sel + (e.key === "ArrowDown" ? 1 : opts.length - 1)) % opts.length; render(); }
      else if (e.key === "Tab" || (e.key === "Enter" && askInput.value.trim() !== opts[sel][0])) { e.preventDefault(); e.stopImmediatePropagation(); pick(sel); }
      else if (e.key === "Escape") close();
    }, true);
    pop.addEventListener("mousedown", (e) => { const i = e.target.closest("[data-i]")?.dataset.i; if (i != null) { e.preventDefault(); pick(+i); } });
    askInput.addEventListener("blur", () => setTimeout(close, 150));
  }
  Object.assign(H.toolLabels, {
    create_skill: (i) => `Saving skill ${i?.name || ""}`.trim(),
    update_skill: (i) => `Updating skill ${i?.skill || ""}`.trim(),
    read_skill: (i) => `Reading skill ${i?.skill || ""}`.trim(),
  });

  /* ------------------------------------------------------------------ insight ratings */
  const REASONS = [["obvious", "Obvious"], ["wrong", "Wrong"], ["not_useful", "Not useful"]];
  let ratings = {};
  const rateHTML = (sid, idx) => {
    const r = ratings[`${sid}:${idx}`] || {};
    return `<span class="sh-rate" data-sid="${esc(sid)}" data-idx="${idx}">
      <button title="Helpful" data-vote="up" class="${r.vote === "up" ? "on" : ""}">${ICON.up}</button>
      <button title="Not helpful" data-vote="down" class="${r.vote === "down" ? "on" : ""}">${ICON.down}</button>
      ${r.vote === "down" ? REASONS.map(([k, l]) => `<button class="why ${r.reason === k ? "on" : ""}" data-reason="${k}">${l}</button>`).join("") : ""}</span>`;
  };
  async function loadRatings() { try { ratings = (await api("/api/insights/ratings")).ratings; } catch {} }
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".sh-rate button"); if (!btn) return;
    e.stopPropagation();
    const wrap = btn.closest(".sh-rate"), sid = wrap.dataset.sid, idx = +wrap.dataset.idx, key = `${sid}:${idx}`;
    const cur = ratings[key] || {};
    let body;
    if (btn.dataset.reason) body = { vote: "down", reason: btn.dataset.reason };
    else if (btn.dataset.vote === cur.vote) body = { vote: "none" };
    else body = { vote: btn.dataset.vote };
    try {
      await api("/api/insights/rate", { session_id: sid, index: idx, ...body });
      if (body.vote === "none") delete ratings[key]; else ratings[key] = { vote: body.vote, reason: body.reason || null };
      wrap.outerHTML = rateHTML(sid, idx);
      if (body.vote === "up") toast("Thanks for the feedback");
      if (body.reason) toast("Thanks, noted");
    } catch (err) { toast(err.message); }
  });
  async function decorateFeed() {
    const feed = byId("feed"); if (!feed) return;
    const cards = [...feed.querySelectorAll(".feed-item")];
    if (!cards.length || cards[0].querySelector(".sh-rate")) return;
    const [ss] = await Promise.all([api("/api/sessions?limit=300").catch(() => []), loadRatings()]);
    const items = [];
    for (const s of ss) (s.insights || []).forEach((i, idx) => items.push({ sid: s.id, idx, headline: i.headline }));
    cards.forEach((card, n) => {
      const it = items[n];
      if (!it || card.querySelector("b")?.textContent !== it.headline || card.querySelector(".sh-rate")) return;
      card.insertAdjacentHTML("beforeend", `<div>${rateHTML(it.sid, it.idx)}</div>`);
    });
  }
  async function decorateSessions() {
    const host = byId("sessions"); if (!host) return;
    const lists = [...host.querySelectorAll(".sess[data-id] .ins")].filter((ul) => !ul.querySelector(".sh-rate"));
    if (!lists.length) return;
    await loadRatings();
    for (const ul of lists) {
      const sid = ul.closest(".sess").dataset.id;
      [...ul.children].forEach((li, idx) => li.insertAdjacentHTML("beforeend", `<div>${rateHTML(sid, idx)}</div>`));
    }
  }
  const observe = (id, fn) => { const el = byId(id); if (el) new MutationObserver(() => fn().catch(() => {})).observe(el, { childList: true }); };
  observe("feed", decorateFeed);
  observe("sessions", decorateSessions);

  /* ------------------------------------------------------------------ settings */
  const lines = (id) => byId(id).value.split("\n").map((s) => s.trim()).filter(Boolean);
  H.registerSettings({
    id: "shell-focus", title: "Focus and notifications",
    description: "The session HUD floats over your apps while a session runs. Focus nudges gently point out when you drift to a distracting app or site during a session.",
    html: `
      <div class="field"><label>Session HUD</label><div class="ctl"><label><input type="checkbox" id="shHud"> Show a small timer pill during work sessions (drag it to any screen edge)</label></div></div>
      <div class="field"><label>Notifications</label><div class="ctl"><label><input type="checkbox" id="shNotif"> Tell me once a day if capture stops (quiet day, lost Accessibility, capture off)</label></div></div>
      <div class="field"><label>Focus nudges</label><div class="ctl"><label><input type="checkbox" id="shNudge"> Nudge me when I drift during a session</label></div></div>
      <div class="field"><label>Nudge after</label><div class="ctl"><input type="number" id="shGrace" min="0" max="1800" step="30" style="width:90px"> seconds on a distracting app or site</div></div>
      <div class="field"><label>Screen vignette</label><div class="ctl"><label><input type="checkbox" id="shVig"> Softly darken the screen edges with a nudge</label></div></div>
      <div class="field"><label>Distracting apps</label><div class="ctl" style="flex:1"><textarea id="shDApps" rows="3" style="width:100%" placeholder="One app name per line"></textarea></div></div>
      <div class="field"><label>Distracting sites</label><div class="ctl" style="flex:1"><textarea id="shDSites" rows="4" style="width:100%" placeholder="One domain per line, e.g. youtube.com"></textarea></div></div>
      <div class="field"><label></label><div class="ctl"><button class="primary" id="shSaveFocus">Save</button></div></div>`,
    onLoad(s) {
      byId("shHud").checked = s.session_hud; byId("shNotif").checked = s.notifications; byId("shNudge").checked = s.focus_nudges;
      byId("shVig").checked = s.focus_vignette; byId("shGrace").value = s.nudge_grace_s;
      byId("shDApps").value = (s.distracting_apps || []).join("\n"); byId("shDSites").value = (s.distracting_sites || []).join("\n");
    },
  });
  byId("shSaveFocus").onclick = async () => {
    await api("/api/settings", { session_hud: byId("shHud").checked, notifications: byId("shNotif").checked, focus_nudges: byId("shNudge").checked,
      focus_vignette: byId("shVig").checked, nudge_grace_s: Math.max(0, +byId("shGrace").value || 0), distracting_apps: lines("shDApps"), distracting_sites: lines("shDSites") });
    native({ action: "shell.settings" });
    toast("Focus settings saved");
  };

  const BROWSERS = [["chrome", "Chrome, Arc, Brave, Edge"], ["safari", "Safari"], ["firefox", "Firefox"]];
  H.registerSettings({
    id: "shell-import", title: "Import browser history",
    description: "Adds page titles and links you visited to your local memory, so search and Ask have history from before Glint was installed. Nothing leaves this Mac.",
    html: `
      <div class="field"><label>Browsers</label><div class="ctl" id="shBrowsers">${BROWSERS.map(([k, l]) => `<label><input type="checkbox" value="${k}" checked> ${l} <span class="muted" data-bs="${k}"></span></label>`).join("")}</div></div>
      <div class="field"><label>Look back</label><div class="ctl"><select id="shDays"><option value="7">7 days</option><option value="14" selected>14 days</option><option value="30">30 days</option><option value="90">90 days</option></select>
        <button class="primary" id="shImport">Import</button></div></div>
      <div id="shImportOut"></div>`,
    async onLoad() {
      const st = await api("/api/import/history/status");
      for (const [k] of BROWSERS) {
        const v = st[k], el = document.querySelector(`[data-bs="${k}"]`);
        el.textContent = v === "missing" ? "(not found)" : v === "denied" ? "(needs Full Disk Access)" : "";
      }
      byId("shImportOut").innerHTML = st.safari === "denied" ? fdaHelp() : "";
    },
  });
  const fdaHelp = () => `<div class="banner info" style="margin:8px 0 0"><span>macOS protects Safari history. To import it, turn on Glint in System Settings › Privacy &amp; Security › Full Disk Access, then come back.</span><button data-fda>Open Full Disk Access</button></div>`;
  document.addEventListener("click", (e) => { if (e.target.closest("[data-fda]")) { if (!native({ action: "shell.openPrivacy", pane: "fda" })) toast("Open System Settings › Privacy & Security › Full Disk Access"); } });
  byId("shImport").onclick = async (e) => {
    const browsers = [...document.querySelectorAll("#shBrowsers input:checked")].map((i) => i.value);
    if (!browsers.length) return toast("Pick a browser");
    e.target.disabled = true; e.target.textContent = "Importing…";
    try {
      const r = await api("/api/import/history", { browsers, days: +byId("shDays").value });
      const label = Object.fromEntries(BROWSERS);
      byId("shImportOut").innerHTML = `<div class="muted" style="margin-top:8px">${Object.entries(r.results).map(([k, v]) => `${esc(label[k])}: ${v.status === "ok" ? `${v.imported} new page${v.imported === 1 ? "" : "s"}` : esc(v.message || v.status)}`).join("<br>")}</div>`
        + (r.results.safari?.status === "needs_full_disk_access" ? fdaHelp() : "");
      toast(`Imported ${r.imported} pages`);
    } catch (err) { toast(err.message); }
    e.target.disabled = false; e.target.textContent = "Import";
  };

  const PERSONAS = [["Engineer", "Code, reviews, docs"], ["Founder", "A bit of everything"], ["Product or design", "Specs, research, design tools"],
    ["Sales or support", "Email, CRM, calls"], ["Writer or researcher", "Reading, notes, drafts"], ["Student", "Classes, papers, studying"],
    ["Operations or finance", "Spreadsheets, admin"], ["Something else", "I'll tell Glint later"]];
  H.registerSettings({
    id: "shell-setup", title: "Setup", description: "Your role helps Glint phrase answers and summaries.",
    html: `<div class="field"><label>What you do</label><div class="ctl"><select id="shPersona"><option value="">Not set</option>${PERSONAS.map(([p]) => `<option>${esc(p)}</option>`).join("")}</select></div></div>
      <div class="field"><label>Setup guide</label><div class="ctl"><button id="shReplay">Run setup again</button></div></div>`,
    onLoad(s) { byId("shPersona").value = s.persona || ""; },
  });
  byId("shPersona").onchange = async (e) => { await api("/api/shell/onboarding", { persona: e.target.value }); toast("Saved"); };
  byId("shReplay").onclick = () => openWizard(0);

  /* ------------------------------------------------------------------ setup wizard */
  const wiz = document.createElement("div");
  wiz.className = "sh-wiz";
  wiz.innerHTML = `<div class="card box"><div class="bar"><i id="wzBar"></i></div><div class="stepn" id="wzN"></div><div id="wzBody"></div>
    <div class="foot"><div><button class="ghost" id="wzBack">Back</button><button class="ghost" id="wzSkip">Skip setup</button></div><div><button class="primary" id="wzNext">Continue</button></div></div></div>`;
  document.body.appendChild(wiz);
  const W = { step: 0, persona: "", perms: {}, native: {}, server: {}, apps: new Set(), allApps: true, keys: {}, pollT: null, advanceT: null };

  // The native shell knows the app's own permissions best; the engine's view is the fallback.
  const mergePerms = () => {
    const n = W.native, s = W.server;
    W.perms = { ax_trusted: n.ax ?? s.ax_trusted, mic: n.mic ?? s.mic, screen: n.screen ?? s.screen, running_apps: n.apps ?? s.running_apps ?? [] };
  };
  window.glintShellPermissions = (p) => { W.native = p || {}; mergePerms(); if (wiz.classList.contains("on")) STEPS[W.step].update?.(); };
  async function pollPerms() {
    native({ action: "shell.permissions" });
    try { W.server = await api("/api/shell/permissions"); } catch {}
    mergePerms();
    STEPS[W.step].update?.();
  }
  const permRow = (title, sub, ok, btn) => `<div class="sh-perm"><div><b>${title}</b><small>${sub}</small></div><div>${ok ? '<span class="sh-ok">✓ Granted</span>' : btn}</div></div>`;
  const granted = (v) => v === true || v === "granted" || v === "authorized";

  const STEPS = [
    { id: "welcome", render: () => `<h2>Welcome to Glint</h2><p class="lead">A private memory for your Mac. Glint reads the text of the windows you work in, keeps it on this Mac, and groups it into work sessions you can search and ask about.</p>
        <ul style="margin:0;padding-left:20px;line-height:1.9"><li><b>Ask</b> anything you've seen: "what was that pricing page?"</li><li><b>Quick Ask</b> over any app with <code>⌘⇧K</code></li>
        <li><b>Dictate</b> anywhere by holding <code>Control</code> + <code>Option</code></li><li>Session titles, summaries and focus insights, written by the AI provider you choose</li></ul>
        <p class="muted" style="margin-top:14px">Setup takes about two minutes. You can skip any step and come back from Settings.</p>` },
    { id: "persona", render: () => `<h2>What do you do?</h2><p class="lead">Glint uses this to phrase summaries and answers. Pick the closest.</p>
        <div class="sh-choices">${PERSONAS.map(([p, d]) => `<button data-persona="${esc(p)}" class="${W.persona === p ? "on" : ""}">${esc(p)}<small>${esc(d)}</small></button>`).join("")}</div>`,
      ready: () => !!W.persona },
    { id: "access", render: () => `<h2>Allow Accessibility</h2><p class="lead">Glint needs Accessibility permission to read the text of the window in front. This is required for capture. Text stays on this Mac.</p><div id="wzAx"></div>
        <p class="muted" style="font-size:13px">In System Settings › Privacy &amp; Security › Accessibility, switch on <b>Glint</b>. If it's already on but not detected, switch it off and on again.</p>`,
      enter: () => { pollPerms(); W.pollT = setInterval(pollPerms, 1500); },
      update() {
        const ok = granted(W.perms.ax_trusted);
        const el = byId("wzAx"); if (!el) return;
        el.innerHTML = permRow("Accessibility", "Read window text", ok, hasNative() ? '<button class="primary" data-perm="ax">Open settings</button>' : '<span class="muted">Open the Glint app to grant</span>');
        refreshNav();
        if (ok && !W.advanceT) W.advanceT = setTimeout(() => { W.advanceT = null; if (STEPS[W.step].id === "access") go(1); }, 900);
      },
      ready: () => granted(W.perms.ax_trusted), skippable: true },
    { id: "optional", render: () => `<h2>Optional permissions</h2><p class="lead">Turn these on now or later. Glint works without them.</p><div id="wzOpt"></div>`,
      enter: () => { pollPerms(); W.pollT = setInterval(pollPerms, 2000); },
      update() {
        const el = byId("wzOpt"); if (!el) return;
        el.innerHTML = permRow("Microphone", "For Dictate (hold Control + Option and speak)", granted(W.perms.mic), '<button data-perm="mic">Allow</button>')
          + permRow("Screen Recording", "Lets Glint read apps that don't expose text (OCR fallback)", granted(W.perms.screen), '<button data-perm="screen">Allow</button>');
      } },
    { id: "apps", render: () => `<h2>Which apps should Glint remember?</h2><p class="lead">Window text from the apps you choose is kept; other apps keep only their window title. Password managers are never captured.</p>
        <div class="sh-choices" style="grid-template-columns:1fr 1fr"><button data-all="1" class="${W.allApps ? "on" : ""}">All apps<small>Recommended: search works everywhere</small></button>
        <button data-all="0" class="${W.allApps ? "" : "on"}">Only apps I choose<small>Pick below</small></button></div><div id="wzApps"></div>`,
      enter: async () => {
        const [st, s] = await Promise.all([api("/api/stats").catch(() => ({ apps: [] })), api("/api/settings").catch(() => null)]);
        if (s) { W.allApps = s.settings.capture_text_all_apps; (s.settings.text_apps || []).forEach((a) => W.apps.add(a)); }
        pollPerms();
        W.known = [...new Set([...(W.perms.running_apps || []), ...st.apps.map(([a]) => a), ...W.apps])].filter((a) => a && !/^glint$/i.test(a)).sort((a, b) => a.localeCompare(b));
        STEPS[W.step].update();
      },
      update() {
        const el = byId("wzApps"); if (!el) return;
        document.querySelectorAll("[data-all]").forEach((b) => b.classList.toggle("on", (b.dataset.all === "1") === W.allApps));
        if (W.allApps) { el.innerHTML = ""; return; }
        const known = [...new Set([...(W.known || []), ...(W.perms.running_apps || [])])].sort((a, b) => a.localeCompare(b));
        el.innerHTML = `<div class="sh-appchips">${known.map((a) => `<button data-app="${esc(a)}" class="${W.apps.has(a) ? "on" : ""}">${esc(a)}</button>`).join("") || '<span class="muted">Open the apps you use, or add names below.</span>'}</div>
          <input type="text" id="wzAddApp" placeholder="Add an app by name and press Enter" style="width:100%;margin-top:10px">`;
        byId("wzAddApp").onkeydown = (e) => { if (e.key === "Enter" && e.target.value.trim()) { e.preventDefault(); e.stopPropagation(); W.apps.add(e.target.value.trim()); W.known = [...(W.known || []), e.target.value.trim()]; STEPS[W.step].update(); } };
      },
      leave: () => api("/api/settings", { capture_text_all_apps: W.allApps, text_apps: [...W.apps] }) },
    { id: "keys", render: () => `<h2>Connect an AI provider</h2><p class="lead">Ask, session summaries and insights use your own Claude or OpenAI key, stored in your Keychain. Only the text needed for a request is sent. You can add this later.</p>
        <div class="field"><label>Claude API key</label><div class="ctl"><input type="password" id="wzClaude" placeholder="sk-ant-…" style="flex:1"><button data-key="claude">Save</button><span id="wzClaudeOk" class="sh-ok"></span></div></div>
        <div class="field"><label>OpenAI API key</label><div class="ctl"><input type="password" id="wzOpenai" placeholder="sk-…" style="flex:1"><button data-key="openai">Save</button><span id="wzOpenaiOk" class="sh-ok"></span></div></div>
        <div class="field"><label>Use for Ask</label><div class="ctl"><div class="seg-ctl" id="wzProv"><button data-prov="claude">Claude</button><button data-prov="openai">OpenAI</button></div></div></div>
        <p class="muted" style="font-size:13px">With an OpenAI key, Dictate uses Whisper; otherwise it transcribes on-device.</p>`,
      enter: async () => { try { const r = await api("/api/settings"); W.keys = r.keys; W.provider = r.settings.provider; } catch {} STEPS[W.step].update(); },
      update() {
        if (!byId("wzClaudeOk")) return;
        byId("wzClaudeOk").textContent = W.keys.claude ? "✓ saved" : ""; byId("wzOpenaiOk").textContent = W.keys.openai ? "✓ saved" : "";
        document.querySelectorAll("#wzProv button").forEach((b) => b.classList.toggle("on", b.dataset.prov === W.provider));
      } },
    { id: "done", render: () => `<h2>You're set</h2><p class="lead">Glint now runs in the menu bar. Closing this window is fine; capture keeps going until you pause it.</p>
        <ul style="margin:0 0 16px;padding-left:20px;line-height:1.9"><li>Press <code>⌘⇧K</code> for Quick Ask over any app</li><li>Hold <code>Control</code> + <code>Option</code> to dictate</li>
        <li>A small timer pill shows while a session runs; drag it to any screen edge</li><li>Pause anytime from the menu bar icon</li></ul>
        <label><input type="checkbox" id="wzLogin" checked> Open Glint when I log in</label>`, next: "Finish" },
  ];

  function refreshNav() {
    const st = STEPS[W.step];
    byId("wzBack").style.visibility = W.step ? "visible" : "hidden";
    byId("wzNext").disabled = st.ready ? !st.ready() : false;
    byId("wzNext").textContent = st.next || "Continue";
    byId("wzSkip").textContent = st.skippable && !(st.ready && st.ready()) ? "Do this later" : "Skip setup";
    byId("wzSkip").dataset.mode = st.skippable && !(st.ready && st.ready()) ? "step" : "all";
  }
  function renderStep() {
    clearInterval(W.pollT); W.pollT = null;
    const st = STEPS[W.step];
    byId("wzBar").style.width = `${((W.step + 1) / STEPS.length) * 100}%`;
    byId("wzN").textContent = `Step ${W.step + 1} of ${STEPS.length}`;
    byId("wzBody").innerHTML = st.render();
    refreshNav();
    st.enter?.(); st.update?.();
    api("/api/shell/onboarding", { step: W.step }).catch(() => {});
  }
  async function go(delta) {
    const st = STEPS[W.step];
    if (delta > 0 && st.leave) { try { await st.leave(); } catch (e) { toast(e.message); } }
    if (delta > 0 && W.step === STEPS.length - 1) return finish(false);
    W.step = Math.max(0, Math.min(STEPS.length - 1, W.step + delta));
    renderStep();
  }
  async function finish(skipped) {
    clearInterval(W.pollT);
    const login = skipped ? undefined : byId("wzLogin")?.checked ?? true;
    try { await api("/api/shell/onboarding", { done: true, open_at_login: login ?? null }); } catch {}
    wiz.classList.remove("on");
    if (!skipped) toast("Glint is ready");
    H.show("home");
  }
  async function openWizard(step) {
    let st = { step: 0 };
    try { st = await api("/api/shell/onboarding"); } catch {}
    W.step = Math.min(STEPS.length - 1, step ?? st.step ?? 0);
    W.persona = st.persona || "";
    wiz.classList.add("on");
    renderStep();
  }
  byId("wzBack").onclick = () => go(-1);
  byId("wzNext").onclick = () => go(1);
  byId("wzSkip").onclick = async () => {
    if (byId("wzSkip").dataset.mode === "step") return go(1);
    if (await H.confirmDialog("Skip setup?", "You can finish it any time from Home or Settings › Setup.", "Skip")) finish(true);
  };
  wiz.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.tagName !== "INPUT" && !byId("wzNext").disabled) { e.preventDefault(); go(1); } });
  wiz.addEventListener("click", async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.persona) {
      W.persona = t.dataset.persona; await api("/api/shell/onboarding", { persona: W.persona }).catch(() => {});
      document.querySelectorAll("[data-persona]").forEach((b) => b.classList.toggle("on", b === t)); refreshNav();
      setTimeout(() => { if (STEPS[W.step].id === "persona") go(1); }, 250);
    }
    if (t.dataset.perm === "ax") native({ action: "requestAccessibility" });
    if (t.dataset.perm === "mic") native({ action: "shell.requestMic" });
    if (t.dataset.perm === "screen") native({ action: "shell.requestScreen" });
    if (t.dataset.all) { W.allApps = t.dataset.all === "1"; STEPS[W.step].update(); }
    if (t.dataset.app) { const a = t.dataset.app; W.apps.has(a) ? W.apps.delete(a) : W.apps.add(a); t.classList.toggle("on"); }
    if (t.dataset.key) {
      const p = t.dataset.key, input = byId(p === "claude" ? "wzClaude" : "wzOpenai"), v = input.value.trim();
      if (!v) return toast("Paste a key first");
      try { const r = await api("/api/keys", { [p]: v }); W.keys = r.keys; input.value = ""; toast("Key saved to Keychain");
        if (!W.keys[W.provider]) { W.provider = p; await api("/api/settings", { provider: p }); } STEPS[W.step].update(); } catch (err) { toast(err.message); }
    }
    if (t.dataset.prov) { W.provider = t.dataset.prov; await api("/api/settings", { provider: W.provider }).catch(() => {}); STEPS[W.step].update(); }
  });

  api("/api/shell/onboarding").then((st) => { if (!st.onboarding_done) openWizard(); }).catch(() => {});
})();
