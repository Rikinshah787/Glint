/* Knowledge graph: Memory → Graph tab (force-directed SVG, no libraries), entity side panel,
   merge/rename/delete, a Settings card for backfill/re-index, and the memory_graph tool label. */
(() => {
  const { api, esc, toast, confirmDialog, when } = HS;
  const $ = (id) => document.getElementById(id);
  const SVGNS = "http://www.w3.org/2000/svg";
  const TYPES = ["person", "project", "org", "document", "topic", "place", "tool", "site"];
  const LABEL = { person: "People", project: "Projects", org: "Orgs", document: "Docs", topic: "Topics", place: "Places", tool: "Tools", site: "Sites" };
  const COLOR = { person: "#e0794c", project: "#4f8fdc", org: "#9b6fd6", document: "#d4a034", topic: "#3fae8c", place: "#d6609a", tool: "#8d929b", site: "#4fb0c9" };

  const css = document.createElement("style");
  css.textContent = `
    .gr-bar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }
    .gr-search { position: relative; flex: 1 1 220px; min-width: 180px; }
    .gr-search input { width: 100%; }
    .gr-sugg { position: absolute; top: calc(100% + 6px); left: 0; right: 0; z-index: 6; padding: 5px; display: none; max-height: 300px; overflow: auto; box-shadow: var(--shadow-pop); border-radius: var(--r-lg); }
    .gr-sugg.on { display: block; }
    .gr-sugg div { padding: 6px 9px; border-radius: 8px; cursor: pointer; display: flex; gap: 8px; align-items: center; }
    .gr-sugg div:hover, .gr-sugg div.on { background: var(--accent-2); }
    .gr-sugg small { color: var(--muted); margin-left: auto; }
    .gr-types { display: flex; gap: 5px; flex-wrap: wrap; }
    .gr-type { font-size: 12px; min-height: 26px; padding: 0 10px; border-radius: 999px; display: inline-flex; gap: 6px; align-items: center; box-shadow: none; }
    .gr-type i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
    .gr-type.off { opacity: .5; background: transparent; }
    .gr-type.off i { background: var(--faint) !important; }
    .gr-main { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 16px; align-items: start; }
    .gr-canvas { position: relative; height: calc(100vh - 230px); min-height: 420px; overflow: hidden; padding: 0;
      background: radial-gradient(circle at 1px 1px, var(--line-2) 1px, transparent 1.2px) 0 0 / 22px 22px, var(--panel); }
    .gr-canvas svg { width: 100%; height: 100%; display: block; cursor: grab; touch-action: none; user-select: none; -webkit-user-select: none; }
    .gr-canvas svg.panning { cursor: grabbing; }
    .gr-canvas .edge { stroke: var(--muted); stroke-linecap: round; transition: opacity .15s; }
    .gr-canvas .node { cursor: pointer; transition: opacity .15s; }
    .gr-canvas .node circle { stroke: var(--panel); stroke-width: 2; }
    .gr-canvas .node.sel circle { stroke: var(--text); stroke-width: 2.5; }
    .gr-canvas .node text { font-size: calc(11.5px / var(--gk, 1)); fill: var(--text); paint-order: stroke; stroke: var(--panel); stroke-width: calc(3px / var(--gk, 1)); stroke-linejoin: round; pointer-events: none; }
    .gr-canvas .node.small text { display: none; }
    .gr-canvas svg.hover .node.small.hi text, .gr-canvas .node.small.sel text { display: block; }
    .gr-canvas svg.hover .node:not(.hi) { opacity: .18; }
    .gr-canvas svg.hover .edge:not(.hi) { opacity: .06; }
    .gr-canvas svg.hover .edge.hi { stroke: var(--accent); }
    .gr-tools { position: absolute; right: 12px; top: 12px; display: flex; flex-direction: column; padding: 3px; gap: 2px; border-radius: var(--r-md); background: var(--panel); border: 1px solid var(--line); box-shadow: var(--shadow); }
    .gr-tools button { width: 28px; height: 28px; min-height: 28px; padding: 0; font-size: 15px; line-height: 1; background: transparent; border: 0; box-shadow: none; color: var(--muted); }
    .gr-tools button:hover:not(:disabled) { color: var(--text); background: var(--hover); }
    .gr-note { position: absolute; left: 12px; bottom: 10px; font-size: 11.5px; color: var(--muted); pointer-events: none; padding: 3px 9px; border-radius: 999px; background: var(--panel); border: 1px solid var(--line); }
    .gr-note:empty { display: none; }
    .gr-crumb { position: absolute; left: 12px; top: 12px; display: flex; gap: 6px; align-items: center; }
    .gr-crumb button { font-size: 12.5px; min-height: 28px; padding: 3px 10px; background: var(--panel); box-shadow: var(--shadow); }
    .gr-side { padding: 20px; max-height: calc(100vh - 230px); min-height: 420px; overflow: auto; }
    .gr-side h3 { margin: 0; font-size: 18px; font-weight: 680; letter-spacing: -.02em; display: flex; gap: 8px; align-items: center; overflow-wrap: anywhere; }
    .gr-side h4 { margin: 20px 0 8px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .07em; color: var(--muted); }
    .gr-dot { width: 11px; height: 11px; border-radius: 50%; flex: none; }
    .gr-meta { color: var(--muted); font-size: 12.5px; margin-top: 4px; }
    .gr-acts { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 12px; }
    .gr-acts button, .gr-acts select { min-height: 26px; padding: 3px 9px; font-size: 12.5px; }
    .gr-acts select { padding-right: 26px; }
    .gr-alias { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
    .gr-alias .chip button { border: 0; background: none; padding: 0 0 0 2px; color: var(--faint); font-size: 12px; }
    .gr-alias .chip button:hover { color: var(--danger); }
    .gr-alias input { font-size: 12.5px; min-height: 24px; padding: 2px 9px; width: 120px; border-radius: 999px; border-style: dashed; }
    .gr-rel { display: grid; grid-template-columns: 9px 1fr 52px; gap: 4px 8px; align-items: center; padding: 5px 6px; border-radius: 8px; cursor: pointer; font-size: 13px; }
    .gr-rel:hover { background: var(--hover); }
    .gr-rel span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .gr-rel .w { height: 4px; border-radius: 3px; background: var(--panel-3); overflow: hidden; }
    .gr-rel .w i { display: block; height: 100%; background: var(--accent); }
    .gr-ses { padding: 7px 8px; border-radius: 8px; cursor: pointer; font-size: 13px; }
    .gr-ses:hover { background: var(--hover); }
    .gr-ses small { display: block; color: var(--muted); font-size: 12px; }
    .gr-merge { margin-top: 10px; padding: 12px; border: 1px solid var(--line); border-radius: var(--r-md); background: var(--panel-2); }
    .gr-merge input { width: 100%; margin-top: 6px; }
    .gr-merge .opts div { padding: 5px 7px; border-radius: 7px; cursor: pointer; font-size: 13px; }
    .gr-merge .opts div:hover { background: var(--panel); }
    .gr-top div { padding: 5px 6px; border-radius: 8px; cursor: pointer; display: flex; gap: 8px; align-items: center; font-size: 13px; }
    .gr-top div:hover { background: var(--hover); }
    .gr-top small { margin-left: auto; color: var(--muted); }
    .gr-prog { height: 4px; border-radius: 4px; background: var(--panel-3); overflow: hidden; margin-top: 8px; width: 100%; }
    .gr-prog i { display: block; height: 100%; background: var(--accent); transition: width .3s; }
    @media (max-width: 920px) { .gr-main { grid-template-columns: 1fr; } .gr-side { max-height: none; min-height: 0; } .gr-canvas { height: 60vh; } }
  `;
  document.head.appendChild(css);

  HS.toolLabels.memory_graph = (i) => (i && i.entity ? `Looked up “${i.entity}” in your graph` : "Checked your knowledge graph");

  /* ------------------------------------------------------------- tab markup */
  HS.registerMemoryTab({
    id: "graph",
    label: "Graph",
    html: `
      <div class="gr-bar">
        <div class="gr-search search"><input type="search" id="grQ" placeholder="Find a person, project, document…" autocomplete="off"><div class="card gr-sugg" id="grSugg"></div></div>
        <select id="grDays" title="Time window"><option value="7">Last 7 days</option><option value="30" selected>Last 30 days</option><option value="90">Last 90 days</option><option value="365">Last year</option><option value="0">All time</option></select>
        <select id="grLimit" title="How many entities"><option value="40">40 nodes</option><option value="80" selected>80 nodes</option><option value="150">150 nodes</option><option value="300">300 nodes</option></select>
        <div class="gr-types" id="grTypes"></div>
      </div>
      <div class="gr-main">
        <div class="card gr-canvas" id="grCanvas">
          <svg id="grSvg" aria-label="Knowledge graph"><g id="grView"><g id="grEdges"></g><g id="grNodes"></g></g></svg>
          <div class="gr-crumb" id="grCrumb"></div>
          <div class="gr-tools"><button id="grIn" title="Zoom in" aria-label="Zoom in">+</button><button id="grOut" title="Zoom out" aria-label="Zoom out">−</button><button id="grFit" title="Fit to view" aria-label="Fit to view">⤢</button><button id="grReheat" title="Re-run layout" aria-label="Re-run layout">↻</button></div>
          <div class="gr-note" id="grNote"></div>
        </div>
        <div class="card gr-side" id="grSide"></div>
      </div>`,
    onShow: () => load(),
  });

  const st = {
    days: 30, limit: 80, types: new Set(TYPES), center: null, depth: 1,
    nodes: [], edges: [], byId: new Map(), adj: new Map(), selected: null, hover: null,
    view: { x: 0, y: 0, k: 1 }, alpha: 0, raf: 0, size: { w: 800, h: 500 }, loaded: false, seq: 0,
  };
  try { const saved = JSON.parse(localStorage.getItem("hs.graph") || "{}"); if (saved.days != null) st.days = saved.days; if (saved.limit) st.limit = saved.limit; if (Array.isArray(saved.types)) st.types = new Set(saved.types.filter((t) => TYPES.includes(t))); } catch {}
  const persist = () => { try { localStorage.setItem("hs.graph", JSON.stringify({ days: st.days, limit: st.limit, types: [...st.types] })); } catch {} };

  $("grDays").value = String(st.days);
  $("grLimit").value = String(st.limit);
  function renderTypeChips() {
    $("grTypes").innerHTML = TYPES.map((t) => `<button class="gr-type${st.types.has(t) ? "" : " off"}" data-t="${t}"><i style="background:${COLOR[t]}"></i>${LABEL[t]}</button>`).join("");
  }
  renderTypeChips();
  $("grTypes").onclick = (e) => {
    const b = e.target.closest("[data-t]"); if (!b) return;
    const t = b.dataset.t;
    if (e.altKey || e.metaKey) st.types = new Set([t]);  // ⌥/⌘-click: only this type
    else if (st.types.has(t)) { if (st.types.size > 1) st.types.delete(t); } else st.types.add(t);
    renderTypeChips(); persist(); load();
  };
  $("grDays").onchange = (e) => { st.days = +e.target.value; persist(); load(); };
  $("grLimit").onchange = (e) => { st.limit = +e.target.value; persist(); load(); };

  /* ------------------------------------------------------------- data */
  const typesParam = () => (st.types.size === TYPES.length ? "" : [...st.types].join(","));
  async function load(keepView = false) {
    const seq = ++st.seq;
    const p = new URLSearchParams({ days: st.days, limit: st.limit });
    if (typesParam()) p.set("types", typesParam());
    if (st.center != null) { p.set("id", st.center); p.set("depth", st.depth); }
    let g;
    try { g = await api(`/api/graph/neighborhood?${p}`); }
    catch (e) {
      if (st.center != null && /unknown entity/.test(e.message)) { st.center = null; return load(); }
      $("grNote").textContent = e.message; return;
    }
    if (seq !== st.seq) return;
    setGraph(g, keepView);
    renderCrumb();
    if (st.selected != null && !st.byId.has(st.selected) && st.center == null) st.selected = null;
    if (st.selected != null) showEntity(st.selected); else showOverview();
  }

  function setGraph(g, keepView) {
    const old = st.byId;
    const box = $("grCanvas").getBoundingClientRect();
    if (box.width && box.height) st.size = { w: box.width, h: box.height };
    const { w, h } = st.size;
    st.nodes = g.nodes.map((n, i) => {
      const prev = old.get(n.id);
      const a = (i / Math.max(1, g.nodes.length)) * Math.PI * 2 * 3.1, rad = 30 + 9 * Math.sqrt(i);
      return { ...n, x: prev ? prev.x : w / 2 + Math.cos(a) * rad, y: prev ? prev.y : h / 2 + Math.sin(a) * rad, vx: 0, vy: 0, fx: null, fy: null,
        r: Math.min(26, 6 + 4.5 * Math.sqrt(Math.max(0.3, n.activity || n.mentions || 1))) };
    });
    st.byId = new Map(st.nodes.map((n) => [n.id, n]));
    if (st.center != null && st.byId.has(st.center) && !old.has(st.center)) { const c = st.byId.get(st.center); c.x = w / 2; c.y = h / 2; }
    const maxW = Math.max(0.001, ...g.edges.map((e) => e.weight));
    st.edges = g.edges.filter((e) => st.byId.has(e.a) && st.byId.has(e.b)).map((e) => ({ ...e, s: st.byId.get(e.a), t: st.byId.get(e.b), n: e.weight / maxW }));
    st.adj = new Map(st.nodes.map((n) => [n.id, new Set()]));
    for (const e of st.edges) { st.adj.get(e.a).add(e.b); st.adj.get(e.b).add(e.a); }
    drawStatic();
    $("grNote").textContent = st.nodes.length ? `${st.nodes.length} entities · ${st.edges.length} links · scroll to zoom, drag to pan, double-click to expand` : "";
    st.autoFit = !keepView;
    reheat(1);
  }

  /* ------------------------------------------------------------- drawing */
  function drawStatic() {
    const E = $("grEdges"), N = $("grNodes");
    E.textContent = ""; N.textContent = "";
    st.hover = null; $("grSvg").classList.remove("hover");
    if (!st.nodes.length) {
      $("grNote").textContent = "";
      N.innerHTML = `<text x="${st.size.w / 2}" y="${st.size.h / 2}" text-anchor="middle" fill="currentColor" style="fill:var(--muted);font-size:13px">No entities in this window yet — they appear as sessions are summarised (or run the backfill in Settings).</text>`;
      return;
    }
    for (const e of st.edges) {
      const l = document.createElementNS(SVGNS, "line");
      l.setAttribute("class", "edge");
      l.setAttribute("stroke-width", (0.6 + 3.2 * e.n).toFixed(2));
      l.setAttribute("stroke-opacity", (0.18 + 0.5 * e.n).toFixed(2));
      e.el = l; E.appendChild(l);
    }
    const ordered = [...st.nodes].sort((a, b) => a.r - b.r);  // big nodes on top
    const labelled = new Set(ordered.slice(-28).map((n) => n.id).concat(st.center != null ? [st.center] : []));
    for (const n of ordered) {
      const g = document.createElementNS(SVGNS, "g");
      g.setAttribute("class", `node${labelled.has(n.id) ? "" : " small"}${n.id === st.selected ? " sel" : ""}`);
      g.dataset.id = n.id;
      const c = document.createElementNS(SVGNS, "circle");
      c.setAttribute("r", n.r.toFixed(1)); c.setAttribute("fill", COLOR[n.type] || COLOR.topic);
      const t = document.createElementNS(SVGNS, "text");
      t.setAttribute("x", (n.r + 3).toFixed(1)); t.setAttribute("dy", "0.35em"); t.textContent = n.name.length > 32 ? n.name.slice(0, 31) + "…" : n.name;
      const title = document.createElementNS(SVGNS, "title");
      title.textContent = `${n.name} · ${n.type} · ${n.mentions} session${n.mentions === 1 ? "" : "s"}`;
      g.append(c, t, title);
      n.el = g; N.appendChild(g);
    }
    paint();
  }
  function paint() {
    for (const e of st.edges) { e.el.setAttribute("x1", e.s.x.toFixed(1)); e.el.setAttribute("y1", e.s.y.toFixed(1)); e.el.setAttribute("x2", e.t.x.toFixed(1)); e.el.setAttribute("y2", e.t.y.toFixed(1)); }
    for (const n of st.nodes) n.el && n.el.setAttribute("transform", `translate(${n.x.toFixed(1)},${n.y.toFixed(1)})`);
  }
  function applyView() {
    $("grView").setAttribute("transform", `translate(${st.view.x},${st.view.y}) scale(${st.view.k})`);
    $("grSvg").style.setProperty("--gk", Math.max(0.6, Math.min(2.5, st.view.k)).toFixed(3));  // labels keep a readable size
  }
  function setHover(id) {
    st.hover = id;
    const svg = $("grSvg");
    svg.classList.toggle("hover", id != null);
    const nb = id != null ? st.adj.get(id) || new Set() : null;
    for (const n of st.nodes) n.el && n.el.classList.toggle("hi", id != null && (n.id === id || nb.has(n.id)));
    for (const e of st.edges) e.el.classList.toggle("hi", id != null && (e.a === id || e.b === id));
  }
  function markSelected() { for (const n of st.nodes) n.el && n.el.classList.toggle("sel", n.id === st.selected); }

  /* ------------------------------------------------------------- simulation */
  function reheat(a = 0.5) { st.alpha = Math.max(st.alpha, a); if (!st.raf) st.raf = requestAnimationFrame(tick); }
  function tick() {
    st.raf = 0;
    const ns = st.nodes, n = ns.length;
    if (!n || $("tab-graph").hidden) { st.alpha = 0; return; }
    const a = st.alpha, cx = st.size.w / 2, cy = st.size.h / 2;
    for (let i = 0; i < n; i++) {  // repulsion (O(n²) is fine for ≤300 nodes)
      const p = ns[i];
      for (let j = i + 1; j < n; j++) {
        const q = ns[j];
        let dx = q.x - p.x, dy = q.y - p.y, d2 = dx * dx + dy * dy;
        if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 0.25; }
        const min = p.r + q.r + 6;
        let f = (700 * a) / d2;
        if (d2 < min * min) f += (0.5 * a * (min - Math.sqrt(d2))) / Math.sqrt(d2);
        if (d2 > 90000) f *= 0.25;
        const fx = dx * f, fy = dy * f;
        p.vx -= fx; p.vy -= fy; q.vx += fx; q.vy += fy;
      }
    }
    for (const e of st.edges) {  // springs
      const p = e.s, q = e.t;
      const dx = q.x - p.x, dy = q.y - p.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      const L = 36 + p.r + q.r + 40 * (1 - e.n);
      const k = (0.03 + 0.06 * e.n) * a, f = ((d - L) / d) * k;
      p.vx += dx * f; p.vy += dy * f; q.vx -= dx * f; q.vy -= dy * f;
    }
    for (const p of ns) {
      p.vx += (cx - p.x) * 0.025 * a; p.vy += (cy - p.y) * 0.025 * a;
      if (p.fx != null) { p.x = p.fx; p.y = p.fy; p.vx = p.vy = 0; continue; }
      p.vx *= 0.6; p.vy *= 0.6;
      p.x += Math.max(-40, Math.min(40, p.vx)); p.y += Math.max(-40, Math.min(40, p.vy));
    }
    paint();
    st.alpha *= 0.975;
    if (st.autoFit && (st.alpha < 0.12 || st.alpha > 0.995)) { fit(); if (st.alpha < 0.12) st.autoFit = false; }
    if (st.alpha > 0.008) st.raf = requestAnimationFrame(tick);
  }

  /* ------------------------------------------------------------- zoom, pan, drag */
  const svg = $("grSvg");
  const toWorld = (ev) => { const b = svg.getBoundingClientRect(); return { x: (ev.clientX - b.left - st.view.x) / st.view.k, y: (ev.clientY - b.top - st.view.y) / st.view.k }; };
  function zoomAt(px, py, factor) {
    const k = Math.max(0.15, Math.min(5, st.view.k * factor));
    st.view.x = px - ((px - st.view.x) * k) / st.view.k; st.view.y = py - ((py - st.view.y) * k) / st.view.k; st.view.k = k; applyView();
  }
  function fit() {
    if (!st.nodes.length) { st.view = { x: 0, y: 0, k: 1 }; applyView(); return; }
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of st.nodes) { x0 = Math.min(x0, n.x - n.r); y0 = Math.min(y0, n.y - n.r); x1 = Math.max(x1, n.x + n.r + 40); y1 = Math.max(y1, n.y + n.r); }
    const { w, h } = st.size, pad = 40;
    const k = Math.max(0.15, Math.min(2, Math.min((w - pad * 2) / Math.max(1, x1 - x0), (h - pad * 2) / Math.max(1, y1 - y0))));
    st.view = { k, x: w / 2 - ((x0 + x1) / 2) * k, y: h / 2 - ((y0 + y1) / 2) * k }; applyView();
  }
  svg.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    st.autoFit = false;
    const b = svg.getBoundingClientRect();
    zoomAt(ev.clientX - b.left, ev.clientY - b.top, Math.exp(-ev.deltaY * (ev.ctrlKey ? 0.01 : 0.0022)));
  }, { passive: false });
  let drag = null;
  svg.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    const g = ev.target.closest(".node");
    svg.setPointerCapture(ev.pointerId);
    if (g) { const n = st.byId.get(+g.dataset.id); const w = toWorld(ev); drag = { kind: "node", n, dx: n.x - w.x, dy: n.y - w.y, moved: false, sx: ev.clientX, sy: ev.clientY }; }
    else { drag = { kind: "pan", sx: ev.clientX, sy: ev.clientY, vx: st.view.x, vy: st.view.y, moved: false }; svg.classList.add("panning"); }
  });
  svg.addEventListener("pointermove", (ev) => {
    if (!drag) {
      const g = ev.target.closest(".node"), id = g ? +g.dataset.id : null;
      if (id !== st.hover) setHover(id);
      return;
    }
    if (Math.abs(ev.clientX - drag.sx) + Math.abs(ev.clientY - drag.sy) > 3) drag.moved = true;
    st.autoFit = false;
    if (drag.kind === "pan") { st.view.x = drag.vx + ev.clientX - drag.sx; st.view.y = drag.vy + ev.clientY - drag.sy; applyView(); }
    else if (drag.moved) { const w = toWorld(ev); drag.n.fx = w.x + drag.dx; drag.n.fy = w.y + drag.dy; reheat(0.25); }
  });
  const endDrag = (ev) => {
    if (!drag) return;
    const d = drag; drag = null; svg.classList.remove("panning");
    if (d.kind === "node") {
      if (!ev.shiftKey) { d.n.fx = d.n.fy = null; }  // shift-release pins the node in place
      if (!d.moved) select(d.n.id);
    } else if (!d.moved && ev.type === "pointerup") { select(null); }
  };
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);
  svg.addEventListener("pointerleave", () => { if (!drag) setHover(null); });
  svg.addEventListener("dblclick", (ev) => { const g = ev.target.closest(".node"); if (g) focusEntity(+g.dataset.id); });
  $("grIn").onclick = () => zoomAt(st.size.w / 2, st.size.h / 2, 1.3);
  $("grOut").onclick = () => zoomAt(st.size.w / 2, st.size.h / 2, 1 / 1.3);
  $("grFit").onclick = fit;
  $("grReheat").onclick = () => { for (const n of st.nodes) { n.fx = n.fy = null; } st.autoFit = true; reheat(1); };
  new ResizeObserver(() => {
    const b = $("grCanvas").getBoundingClientRect();
    if (b.width && b.height) { st.size = { w: b.width, h: b.height }; }
  }).observe($("grCanvas"));

  function renderCrumb() {
    if (st.center == null) { $("grCrumb").innerHTML = ""; return; }
    const c = st.byId.get(st.center);
    $("grCrumb").innerHTML = `<button id="grBack">← All entities</button><button id="grDepth">${st.depth === 1 ? "Show 2 hops" : "Show 1 hop"}</button><span class="muted" style="font-size:12.5px">around ${esc(c ? c.name : "entity")}</span>`;
    $("grBack").onclick = () => { st.center = null; load(); };
    $("grDepth").onclick = () => { st.depth = st.depth === 1 ? 2 : 1; load(true); };
  }
  function focusEntity(id) { st.center = id; st.selected = id; load(); }
  function select(id) {
    st.selected = id; markSelected();
    if (id == null) showOverview(); else showEntity(id);
  }

  /* ------------------------------------------------------------- side panel */
  const dot = (t) => `<span class="gr-dot" style="background:${COLOR[t] || COLOR.topic}"></span>`;
  const day = (ms) => (ms ? new Date(ms).toLocaleDateString([], { month: "short", day: "numeric", year: new Date(ms).getFullYear() === new Date().getFullYear() ? undefined : "numeric" }) : "–");

  async function showOverview() {
    const side = $("grSide");
    const named = (n) => n.type !== "tool" && n.type !== "site";  // apps and sites are context, not subjects
    const top = [...st.nodes].sort((a, b) => named(b) - named(a) || (b.activity || 0) - (a.activity || 0)).slice(0, 14);
    let s = null;
    try { s = await api("/api/graph/status"); } catch {}
    if (st.selected != null) return;
    side.innerHTML = `<h3>Knowledge graph</h3>
      <div class="gr-meta">${s ? `${s.entities} entities · ${s.edges} links · ${s.with_entities}/${s.summarized} summarised sessions have entities` : ""}</div>
      <p class="muted" style="font-size:13px;margin:10px 0 0">People, projects, documents, organisations, topics, tools and sites from your work sessions, linked when they show up together. Click a node for details, double-click to explore around it.</p>
      ${s && s.pending_backfill ? `<p class="muted" style="font-size:12.5px">${s.pending_backfill} older sessions have no entities yet — extract them in Settings → Knowledge graph.</p>` : ""}
      ${top.length ? `<h4>Most active</h4><div class="gr-top">${top.map((n) => `<div data-id="${n.id}">${dot(n.type)}<span>${esc(n.name)}</span><small>${n.mentions}</small></div>`).join("")}</div>` : ""}`;
    side.querySelectorAll(".gr-top [data-id]").forEach((el) => el.onclick = () => select(+el.dataset.id));
  }

  async function showEntity(id) {
    const side = $("grSide");
    let e;
    try { e = await api(`/api/graph/entity?id=${id}`); }
    catch (err) { st.selected = null; markSelected(); showOverview(); return; }
    if (st.selected !== id) return;
    const maxW = Math.max(0.001, ...e.related.map((r) => r.weight));
    side.innerHTML = `
      <h3>${dot(e.type)}<span>${esc(e.name)}</span></h3>
      <div class="gr-meta">${esc(e.type)} · ${e.mentions} session${e.mentions === 1 ? "" : "s"} · first ${day(e.first_seen)} · last ${day(e.last_seen)}</div>
      <div class="gr-acts">
        <button id="grFocus">${st.center === id ? "Centered" : "Explore around"}</button>
        <button id="grRename">Rename</button>
        <select id="grType" title="Type">${TYPES.map((t) => `<option value="${t}"${t === e.type ? " selected" : ""}>${t}</option>`).join("")}</select>
        <button id="grMergeBtn">Merge…</button>
        <button class="danger" id="grDel">Delete</button>
      </div>
      <div id="grMergeBox"></div>
      <h4>Also known as</h4>
      <div class="gr-alias" id="grAliases">${e.aliases.map((a) => `<span class="chip">${esc(a)}<button data-rm="${esc(a)}" title="Remove alias">×</button></span>`).join("")}<input id="grAliasIn" placeholder="+ add alias"></div>
      <h4>Related</h4>
      ${e.related.length ? e.related.slice(0, 16).map((r) => `<div class="gr-rel" data-id="${r.id}" title="${r.count} shared session${r.count === 1 ? "" : "s"}, last ${day(r.last_seen)}">${dot(r.type)}<span>${esc(r.name)} <small class="muted">${esc(r.type)}</small></span><div class="w"><i style="width:${Math.round(100 * r.weight / maxW)}%"></i></div></div>`).join("") : `<div class="muted" style="font-size:13px">Nothing linked yet.</div>`}
      <h4>Sessions</h4>
      ${e.sessions.length ? e.sessions.map((s) => `<div class="gr-ses" data-sid="${esc(s.id)}">${esc(s.title)}<small>${esc(when(s.started_at))}${s.project ? " · " + esc(s.project) : ""}</small></div>`).join("") : `<div class="muted" style="font-size:13px">No sessions.</div>`}`;
    $("grFocus").onclick = () => focusEntity(id);
    $("grRename").onclick = async () => {
      const name = prompt("Rename entity (the old name stays as an alias)", e.name);
      if (name == null || !name.trim() || name.trim() === e.name) return;
      try { await api("/api/graph/rename", { id, name: name.trim() }); toast("Renamed"); load(true); } catch (err) { toast(err.message); }
    };
    $("grType").onchange = async (ev) => { try { await api("/api/graph/rename", { id, type: ev.target.value }); toast("Type updated"); load(true); } catch (err) { toast(err.message); } };
    $("grDel").onclick = async () => {
      if (!(await confirmDialog(`Delete “${e.name}”?`, "It's removed from the graph and won't be extracted again. Your sessions and captures stay."))) return;
      try { await api("/api/graph/delete", { id }); toast("Deleted"); st.selected = null; if (st.center === id) st.center = null; load(true); } catch (err) { toast(err.message); }
    };
    $("grMergeBtn").onclick = () => openMerge(e);
    $("grAliases").onclick = async (ev) => {
      const rm = ev.target.dataset.rm; if (rm == null) return;
      try { await api("/api/graph/aliases", { id, remove: [rm] }); showEntity(id); } catch (err) { toast(err.message); }
    };
    $("grAliasIn").onkeydown = async (ev) => {
      if (ev.key !== "Enter" || !ev.target.value.trim()) return;
      try { const r = await api("/api/graph/aliases", { id, add: [ev.target.value.trim()] }); if (r.refused.length) toast(`“${r.refused[0]}” already belongs to another entity — merge instead`); showEntity(id); } catch (err) { toast(err.message); }
    };
    side.querySelectorAll(".gr-rel").forEach((el) => el.onclick = () => {
      const rid = +el.dataset.id;
      if (st.byId.has(rid)) select(rid); else focusEntity(rid);
    });
    side.querySelectorAll(".gr-ses").forEach((el) => el.onclick = () => HS.openSession(el.dataset.sid));
  }

  function openMerge(e) {
    const box = $("grMergeBox");
    if (box.innerHTML) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="gr-merge"><div style="font-size:13px">Merge another entity <b>into</b> ${esc(e.name)} — its sessions, links and names move here.</div><input type="search" id="grMergeQ" placeholder="Search entities…" autocomplete="off"><div class="opts" id="grMergeOpts"></div></div>`;
    const q = $("grMergeQ"); q.focus();
    let t = 0;
    q.oninput = () => { clearTimeout(t); t = setTimeout(async () => {
      const r = q.value.trim() ? await api(`/api/graph/search?q=${encodeURIComponent(q.value.trim())}&limit=8`).catch(() => ({ entities: [] })) : { entities: [] };
      $("grMergeOpts").innerHTML = r.entities.filter((x) => x.id !== e.id).map((x) => `<div data-id="${x.id}" data-name="${esc(x.name)}">${dot(x.type)} ${esc(x.name)} <small class="muted">${esc(x.type)} · ${x.mentions}</small></div>`).join("") || (q.value.trim() ? `<div class="muted">No match</div>` : "");
    }, 180); };
    $("grMergeOpts").onclick = async (ev) => {
      const el = ev.target.closest("[data-id]"); if (!el) return;
      if (!(await confirmDialog(`Merge “${el.dataset.name}” into “${e.name}”?`, "Its aliases, sessions and links move to this entity. This can't be undone.", "Merge"))) return;
      try { await api("/api/graph/merge", { into: e.id, ids: [+el.dataset.id] }); toast("Merged"); load(true); } catch (err) { toast(err.message); }
    };
  }

  /* ------------------------------------------------------------- search box */
  let sTimer = 0, sItems = [], sIdx = -1;
  const sugg = $("grSugg");
  function renderSugg() {
    sugg.innerHTML = sItems.map((x, i) => `<div data-i="${i}" class="${i === sIdx ? "on" : ""}">${dot(x.type)}<span>${esc(x.name)}</span><small>${esc(x.type)} · ${x.mentions}</small></div>`).join("");
    sugg.classList.toggle("on", sItems.length > 0);
  }
  $("grQ").oninput = () => {
    clearTimeout(sTimer);
    const v = $("grQ").value.trim();
    if (!v) { sItems = []; renderSugg(); return; }
    sTimer = setTimeout(async () => {
      try { sItems = (await api(`/api/graph/search?q=${encodeURIComponent(v)}&limit=10`)).entities; } catch { sItems = []; }
      sIdx = sItems.length ? 0 : -1; renderSugg();
    }, 160);
  };
  $("grQ").onkeydown = (ev) => {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") { ev.preventDefault(); if (sItems.length) { sIdx = (sIdx + (ev.key === "ArrowDown" ? 1 : -1) + sItems.length) % sItems.length; renderSugg(); } }
    else if (ev.key === "Enter" && sItems[sIdx]) { pick(sItems[sIdx]); }
    else if (ev.key === "Escape") { sItems = []; renderSugg(); }
  };
  sugg.onmousedown = (ev) => { const el = ev.target.closest("[data-i]"); if (el) { ev.preventDefault(); pick(sItems[+el.dataset.i]); } };
  $("grQ").onblur = () => setTimeout(() => { sItems = []; renderSugg(); }, 120);
  function pick(x) {
    sItems = []; renderSugg(); $("grQ").value = "";
    if (st.byId.has(x.id) && st.center == null) { select(x.id); setHover(x.id); setTimeout(() => setHover(null), 1200); }
    else focusEntity(x.id);
  }

  /* ------------------------------------------------------------- settings card */
  HS.registerSettings({
    id: "graph",
    title: "Knowledge graph",
    description: "New sessions add people, projects, documents and other entities for free as part of their summary. Older sessions can be backfilled with one small AI call each, using your selected model.",
    html: `
      <div class="field"><label>Status</label><div class="ctl" id="grStat">…</div></div>
      <div class="field"><label>Backfill older sessions</label><div class="ctl">
        <select id="grBfN"><option value="25">25 most recent</option><option value="100" selected>100 most recent</option><option value="500">500 most recent</option><option value="1000">All (up to 1000)</option></select>
        <button id="grBf">Extract entities</button><button class="ghost" id="grCancel" hidden>Cancel</button>
        <div class="gr-prog" id="grProg" hidden><i style="width:0"></i></div><span class="muted" id="grJob" style="font-size:12.5px"></span></div></div>
      <div class="field"><label>Re-index</label><div class="ctl"><button id="grRe">Rebuild graph</button><span class="muted" style="font-size:12.5px">No AI calls: re-links sessions, recomputes links and refreshes session search.</span></div></div>`,
    onLoad: () => refreshStatus(),
  });
  let poll = 0;
  async function refreshStatus() {
    let s;
    try { s = await api("/api/graph/status"); } catch (e) { $("grStat").textContent = e.message; return; }
    $("grStat").innerHTML = `${s.entities} entities · ${s.edges} links · ${s.with_entities} of ${s.summarized} summarised sessions have entities${s.pending_backfill ? ` · <b>${s.pending_backfill}</b> to backfill` : ""}`;
    const j = s.job, running = j && j.running;
    $("grBf").disabled = $("grRe").disabled = !!running;
    $("grCancel").hidden = !running;
    $("grProg").hidden = !running;
    if (running) $("grProg").firstElementChild.style.width = `${j.total ? Math.round((100 * j.done) / j.total) : 5}%`;
    $("grJob").textContent = j && j.kind ? (running ? `${j.kind === "backfill" ? "Extracting" : "Re-indexing"} ${j.done}/${j.total || "…"}` : `Last ${j.kind}: ${j.done}/${j.total}${j.failed ? `, ${j.failed} failed` : ""}${j.error ? ` — ${j.error}` : " — done"}`) : "";
    clearTimeout(poll);
    if (running) poll = setTimeout(refreshStatus, 1000);
    else if (j && j.finished_at && Date.now() - j.finished_at < 3000 && !$("tab-graph").hidden) load(true);
  }
  $("grBf").onclick = async () => {
    try { const r = await api("/api/graph/backfill", { limit: +$("grBfN").value }); if (r.message) toast(r.message); } catch (e) { toast(e.message); }
    refreshStatus();
  };
  $("grCancel").onclick = async () => { await api("/api/graph/cancel", {}).catch(() => {}); refreshStatus(); };
  $("grRe").onclick = async () => { try { await api("/api/graph/reindex", {}); } catch (e) { toast(e.message); } refreshStatus(); };
})();
