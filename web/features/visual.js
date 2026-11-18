/* Glint visual answers: renders "visual" events (charts, flowcharts, illustrations) as inline SVG,
   a note for "overlay" events, and the Visual settings card. Loaded by the main window directly and
   by Quick Ask through visual.quick.js. No external libraries; all text is escaped and every SVG
   attribute is generated here from validated values (no raw markup from the model is inserted). */
(() => {
  const HS = window.HS;
  if (!HS || HS.__visual) return;
  HS.__visual = true;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, d = 0) => (Number.isFinite(+v) ? +v : d);
  const r1 = (v) => Math.round(v * 10) / 10;

  /* ---------- styles ---------- */
  const css = `
.gv { margin: 6px 0 12px; padding: 12px 14px 10px; border: 1px solid var(--line); border-radius: 12px; background: var(--gv-surface); color: var(--text); max-width: 640px; }
.gv-root, .gv { --gv-surface: var(--panel-2, var(--chip)); --gv-s1:#2a78d6; --gv-s2:#eb6834; --gv-s3:#1baf7a; --gv-s4:#eda100; --gv-s5:#e87ba4; --gv-s6:#008300; --gv-s7:#6250d6; --gv-s8:#e34948; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .gv { --gv-s1:#3987e5; --gv-s2:#d95926; --gv-s3:#199e70; --gv-s4:#c98500; --gv-s5:#d55181; --gv-s6:#0ca30c; --gv-s7:#9085e9; --gv-s8:#e66767; } }
:root[data-theme="dark"] .gv { --gv-s1:#3987e5; --gv-s2:#d95926; --gv-s3:#199e70; --gv-s4:#c98500; --gv-s5:#d55181; --gv-s6:#0ca30c; --gv-s7:#9085e9; --gv-s8:#e66767; }
.gv-h { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 6px; }
.gv-t { font-weight: 620; font-size: 13.5px; }
.gv-sub { color: var(--muted); font-size: 12px; }
.gv-btn { border: 0; background: none; color: var(--muted); font-size: 11.5px; cursor: pointer; padding: 0 2px; }
.gv-btn:hover { color: var(--accent); }
.gv svg { display: block; width: 100%; height: auto; overflow: visible; font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif; }
.gv svg text { fill: var(--text); }
.gv svg .m { fill: var(--muted); }
.gv svg .grid { stroke: var(--line); stroke-width: 1; }
.gv svg .hit { fill: transparent; }
.gv svg .hit:hover { fill: var(--accent); fill-opacity: .06; }
.gv-legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--muted); margin-top: 6px; }
.gv-legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; }
.gv table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 6px; }
.gv th, .gv td { text-align: left; padding: 3px 6px; border-bottom: 1px solid var(--line); }
.gv td.n, .gv th.n { text-align: right; font-variant-numeric: tabular-nums; }
.gv-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; }
.gv-stat { padding: 8px 10px; border-radius: 10px; background: var(--panel, transparent); border: 1px solid var(--line); }
.gv-stat .l { font-size: 12px; color: var(--muted); }
.gv-stat .v { font-size: 22px; font-weight: 650; letter-spacing: -.01em; font-variant-numeric: tabular-nums; }
.gv-stat .d { font-size: 12px; color: var(--muted); }
.gv pre { margin: 6px 0 0; padding: 8px 10px; border-radius: 8px; background: var(--panel, transparent); border: 1px solid var(--line); overflow: auto; font: 12px ui-monospace, Menlo, monospace; white-space: pre; }
.gv-cap { color: var(--muted); font-size: 12px; margin-top: 6px; }
.gv-ov { display: inline-flex; gap: 6px; align-items: center; font-size: 12px; color: var(--muted); margin: 2px 0 6px; }
.gv-ov b { color: var(--accent); font-weight: 600; }
`;
  const st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  /* ---------- helpers ---------- */
  const SERIES = (i) => `var(--gv-s${(i % 8) + 1})`;
  const TOKENS = { accent: "var(--accent)", muted: "var(--muted)", text: "var(--text)", line: "var(--line)", surface: "var(--gv-surface)", none: "none" };
  for (let i = 1; i <= 8; i++) TOKENS[`series${i}`] = `var(--gv-s${i})`;
  const NAMED = new Set(["black", "white", "gray", "grey", "red", "orange", "yellow", "green", "teal", "blue", "purple", "pink", "brown", "transparent", "currentcolor"]);
  function color(v, fallback) {
    if (v == null || v === "") return fallback;
    const s = String(v).trim();
    if (TOKENS[s]) return TOKENS[s];
    if (NAMED.has(s.toLowerCase())) return s.toLowerCase() === "currentcolor" ? "currentColor" : s;
    if (/^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(s)) return s;
    return fallback;
  }
  let uid = 0;
  const fmt = (v, unit) => {
    const a = Math.abs(v);
    let s = a >= 1e9 ? r1(v / 1e9) + "B" : a >= 1e6 ? r1(v / 1e6) + "M" : a >= 1e4 ? r1(v / 1e3) + "k" : (Number.isInteger(v) ? String(v) : String(Math.round(v * 100) / 100));
    if (!unit) return s;
    return /^[$€£¥]$/.test(unit) ? unit + s : `${s}${unit.length > 2 ? " " : ""}${unit}`;
  };
  function ticks(lo, hi, n = 4) {
    if (lo === hi) { hi = lo + 1; }
    const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
    const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * mag;
    const out = [];
    for (let v = Math.floor(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.round(v / step) * step);
    if (out[out.length - 1] < hi) out.push(out[out.length - 1] + step);
    return out;
  }
  // Greedy word wrap by approximate glyph width.
  function wrap(text, maxPx, size = 13, maxLines = 4) {
    const cw = size * 0.56, max = Math.max(4, Math.floor(maxPx / cw));
    const words = String(text ?? "").split(/\s+/).filter(Boolean), lines = [];
    let cur = "";
    for (const w of words) {
      const next = cur ? `${cur} ${w}` : w;
      if (next.length <= max) { cur = next; continue; }
      if (cur) lines.push(cur);
      cur = w.length > max ? w.slice(0, max - 1) + "…" : w;
    }
    if (cur) lines.push(cur);
    if (lines.length > maxLines) { lines.length = maxLines; lines[maxLines - 1] = lines[maxLines - 1].replace(/.{0,1}$/, "…"); }
    return lines.length ? lines : [""];
  }
  const tspans = (lines, x, y, lh, attrs = "") => `<text x="${x}" y="${y}" ${attrs}>${lines.map((l, i) => `<tspan x="${x}" dy="${i ? lh : 0}">${esc(l)}</tspan>`).join("")}</text>`;
  const trunc = (s, n) => (String(s).length > n ? String(s).slice(0, n - 1) + "…" : String(s));
  function card(el, p, body, extra = "") {
    el.innerHTML = `<figure class="gv" role="group" aria-label="${esc(p.title)}"><div class="gv-h"><div><div class="gv-t">${esc(p.title)}</div>${p.subtitle ? `<div class="gv-sub">${esc(p.subtitle)}</div>` : ""}</div>${extra}</div>${body}</figure>`;
    return el.querySelector(".gv");
  }
  function addTable(fig, head, rows) {
    const btn = fig.querySelector(".gv-btn[data-table]");
    if (!btn) return;
    btn.onclick = () => {
      const t = fig.querySelector("table");
      if (t) { t.remove(); btn.textContent = "Table"; return; }
      fig.insertAdjacentHTML("beforeend", `<table><thead><tr>${head.map((h, i) => `<th class="${i ? "n" : ""}">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${i ? "n" : ""}">${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      btn.textContent = "Hide table";
    };
  }
  const legend = (names) => (names.length > 1 ? `<div class="gv-legend">${names.map((n, i) => `<span><i style="background:${SERIES(i)}"></i>${esc(n)}</span>`).join("")}</div>` : "");

  /* ---------- charts ---------- */
  function barPathV(x, w, yBase, yEnd) {
    const r = Math.min(4, w / 2, Math.abs(yBase - yEnd));
    if (yEnd <= yBase) return `M${x},${yBase}V${yEnd + r}Q${x},${yEnd} ${x + r},${yEnd}H${x + w - r}Q${x + w},${yEnd} ${x + w},${yEnd + r}V${yBase}Z`;
    return `M${x},${yBase}V${yEnd - r}Q${x},${yEnd} ${x + r},${yEnd}H${x + w - r}Q${x + w},${yEnd} ${x + w},${yEnd - r}V${yBase}Z`;
  }
  function barPathH(y, h, xBase, xEnd) {
    const r = Math.min(4, h / 2, Math.abs(xEnd - xBase));
    if (xEnd >= xBase) return `M${xBase},${y}H${xEnd - r}Q${xEnd},${y} ${xEnd},${y + r}V${y + h - r}Q${xEnd},${y + h} ${xEnd - r},${y + h}H${xBase}Z`;
    return `M${xBase},${y}H${xEnd + r}Q${xEnd},${y} ${xEnd},${y + r}V${y + h - r}Q${xEnd},${y + h} ${xEnd + r},${y + h}H${xBase}Z`;
  }
  function chartBar(p) {
    const L = p.labels, S = p.series, all = S.flatMap((s) => s.values);
    const tk = ticks(Math.min(0, ...all), Math.max(0, ...all));
    const lo = tk[0], hi = tk[tk.length - 1];
    const horizontal = L.length > 8 || Math.max(...L.map((l) => l.length)) > 12;
    const tip = (i) => `${L[i]}: ${S.map((s) => `${S.length > 1 ? s.name + " " : ""}${fmt(s.values[i], p.unit)}`).join(", ")}`;
    const direct = S.length === 1 && L.length <= 12;
    if (horizontal) {
      const rowH = S.length > 1 ? 14 + S.length * 9 : 26, W = 560, lw = 130, top = 6, H = top + L.length * rowH + 22;
      const x = (v) => lw + ((v - lo) / (hi - lo)) * (W - lw - 40);
      let g = tk.map((t) => `<line class="grid" x1="${x(t)}" x2="${x(t)}" y1="${top}" y2="${H - 20}"/><text class="m" x="${x(t)}" y="${H - 6}" font-size="11" text-anchor="middle">${esc(fmt(t, p.unit))}</text>`).join("");
      L.forEach((lab, i) => {
        const y0 = top + i * rowH, bh = Math.min(16, (rowH - 8) / S.length);
        g += `<text x="${lw - 8}" y="${y0 + rowH / 2 + 4}" font-size="12" text-anchor="end">${esc(trunc(lab, 20))}</text>`;
        S.forEach((s, j) => {
          const v = s.values[i], by = y0 + (rowH - bh * S.length - 2 * (S.length - 1)) / 2 + j * (bh + 2);
          g += `<path d="${barPathH(by, bh, x(0), x(v))}" style="fill:${S.length > 1 ? SERIES(j) : "var(--accent)"}"/>`;
          if (direct) g += `<text class="m" x="${v >= 0 ? x(v) + 5 : x(v) - 5}" y="${by + bh / 2 + 4}" font-size="11" text-anchor="${v >= 0 ? "start" : "end"}">${esc(fmt(v, p.unit))}</text>`;
        });
        g += `<rect class="hit" x="0" y="${y0}" width="${W}" height="${rowH}"><title>${esc(tip(i))}</title></rect>`;
      });
      return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(p.title)}">${g}</svg>`;
    }
    const W = 560, H = 240, ml = 46, mr = 10, mt = 14, mb = 34, pw = W - ml - mr, ph = H - mt - mb;
    const y = (v) => mt + ph - ((v - lo) / (hi - lo)) * ph, gw = pw / L.length;
    const bw = Math.min(40, (gw * 0.72 - 2 * (S.length - 1)) / S.length);
    let g = tk.map((t) => `<line class="grid" x1="${ml}" x2="${W - mr}" y1="${y(t)}" y2="${y(t)}"/><text class="m" x="${ml - 6}" y="${y(t) + 4}" font-size="11" text-anchor="end">${esc(fmt(t, p.unit))}</text>`).join("");
    L.forEach((lab, i) => {
      const gx = ml + i * gw, start = gx + (gw - (bw * S.length + 2 * (S.length - 1))) / 2;
      S.forEach((s, j) => {
        const v = s.values[i], bx = start + j * (bw + 2);
        g += `<path d="${barPathV(bx, bw, y(0), y(v))}" style="fill:${S.length > 1 ? SERIES(j) : "var(--accent)"}"/>`;
        if (direct) g += `<text class="m" x="${bx + bw / 2}" y="${v >= 0 ? y(v) - 5 : y(v) + 13}" font-size="11" text-anchor="middle">${esc(fmt(v, p.unit))}</text>`;
      });
      g += `<text x="${gx + gw / 2}" y="${H - 14}" font-size="11.5" text-anchor="middle">${esc(trunc(lab, Math.max(4, Math.floor(gw / 6.5))))}</text>`;
      g += `<rect class="hit" x="${gx}" y="${mt}" width="${gw}" height="${ph}"><title>${esc(tip(i))}</title></rect>`;
    });
    g += `<line x1="${ml}" x2="${W - mr}" y1="${y(0)}" y2="${y(0)}" style="stroke:var(--muted);stroke-width:1"/>`;
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(p.title)}">${g}</svg>`;
  }
  function chartLine(p) {
    const L = p.labels, S = p.series, all = S.flatMap((s) => s.values);
    const mn = Math.min(...all), mx = Math.max(...all), pad = (mx - mn || Math.abs(mx) || 1) * 0.08;
    const tk = ticks(mn >= 0 && mn - pad < 0 ? 0 : mn - pad, mx + pad), lo = tk[0], hi = tk[tk.length - 1];
    const endLabels = S.length > 1 && S.length <= 4;
    const W = 560, H = 240, ml = 46, mr = endLabels ? 84 : 14, mt = 12, mb = 34, pw = W - ml - mr, ph = H - mt - mb;
    const x = (i) => ml + (L.length === 1 ? pw / 2 : (i / (L.length - 1)) * pw), y = (v) => mt + ph - ((v - lo) / (hi - lo)) * ph;
    let g = tk.map((t) => `<line class="grid" x1="${ml}" x2="${ml + pw}" y1="${y(t)}" y2="${y(t)}"/><text class="m" x="${ml - 6}" y="${y(t) + 4}" font-size="11" text-anchor="end">${esc(fmt(t, p.unit))}</text>`).join("");
    const every = Math.max(1, Math.ceil(L.length / 8));
    L.forEach((lab, i) => { if (i % every === 0 || i === L.length - 1) g += `<text class="m" x="${x(i)}" y="${H - 14}" font-size="11" text-anchor="${i === 0 && L.length > 1 ? "start" : i === L.length - 1 && L.length > 1 ? "end" : "middle"}">${esc(trunc(lab, 14))}</text>`; });
    S.forEach((s, j) => {
      const c = S.length > 1 ? SERIES(j) : "var(--accent)";
      g += `<path d="${s.values.map((v, i) => `${i ? "L" : "M"}${r1(x(i))},${r1(y(v))}`).join("")}" style="fill:none;stroke:${c};stroke-width:2;stroke-linejoin:round;stroke-linecap:round"/>`;
      if (L.length <= 24) g += s.values.map((v, i) => `<circle cx="${r1(x(i))}" cy="${r1(y(v))}" r="3.5" style="fill:${c};stroke:var(--gv-surface);stroke-width:2"/>`).join("");
      if (endLabels) g += `<text x="${ml + pw + 8}" y="${y(s.values[s.values.length - 1]) + 4}" font-size="11.5" class="m">${esc(trunc(s.name, 12))}</text>`;
    });
    if (S.length === 1) { const i = L.length - 1; g += `<text x="${x(i)}" y="${y(S[0].values[i]) - 9}" font-size="11.5" text-anchor="end">${esc(fmt(S[0].values[i], p.unit))}</text>`; }
    const cw = L.length > 1 ? pw / (L.length - 1) : pw;
    L.forEach((lab, i) => { g += `<rect class="hit" x="${x(i) - cw / 2}" y="${mt}" width="${cw}" height="${ph}"><title>${esc(`${lab}: ${S.map((s) => `${S.length > 1 ? s.name + " " : ""}${fmt(s.values[i], p.unit)}`).join(", ")}`)}</title></rect>`; });
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(p.title)}">${g}</svg>`;
  }
  function chartRing(p) {
    const vals = p.series[0].values, total = vals.reduce((a, b) => a + b, 0), L = p.labels;
    const cx = 100, cy = 100, R = 82, r = 54, W = 560, H = Math.max(200, 30 + L.length * 24);
    let a0 = -Math.PI / 2, g = "";
    const pt = (rad, a) => `${r1(cx + rad * Math.cos(a))},${r1(cy + rad * Math.sin(a))}`;
    vals.forEach((v, i) => {
      if (v <= 0) return;
      const frac = v / total, a1 = a0 + frac * Math.PI * 2, gap = vals.filter((x) => x > 0).length > 1 ? 0.012 : 0;
      const s = a0 + gap, e = Math.max(s + 0.001, a1 - gap), big = e - s > Math.PI ? 1 : 0;
      const d = frac >= 0.9999 ? `M${cx},${cy - R}A${R},${R} 0 1 1 ${cx},${cy + R}A${R},${R} 0 1 1 ${cx},${cy - R}ZM${cx},${cy - r}A${r},${r} 0 1 0 ${cx},${cy + r}A${r},${r} 0 1 0 ${cx},${cy - r}Z`
        : `M${pt(R, s)}A${R},${R} 0 ${big} 1 ${pt(R, e)}L${pt(r, e)}A${r},${r} 0 ${big} 0 ${pt(r, s)}Z`;
      g += `<path d="${d}" style="fill:${SERIES(i)}"><title>${esc(`${L[i]}: ${fmt(v, p.unit)} (${Math.round(frac * 100)}%)`)}</title></path>`;
      a0 = a1;
    });
    g += `<text x="${cx}" y="${cy + 2}" font-size="20" font-weight="650" text-anchor="middle">${esc(fmt(total, p.unit))}</text><text class="m" x="${cx}" y="${cy + 20}" font-size="11" text-anchor="middle">total</text>`;
    L.forEach((lab, i) => {
      const yy = cy - (L.length * 24) / 2 + i * 24 + 16;
      g += `<rect x="220" y="${yy - 10}" width="11" height="11" rx="3" style="fill:${SERIES(i)}"/><text x="240" y="${yy}" font-size="12.5">${esc(trunc(lab, 28))}</text><text class="m" x="${W - 10}" y="${yy}" font-size="12.5" text-anchor="end">${esc(fmt(vals[i], p.unit))} · ${Math.round((vals[i] / total) * 100)}%</text>`;
    });
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(p.title)}"><g transform="translate(0,${(H - 200) / 2})">${g}</g></svg>`;
  }
  function renderChart(p, el) {
    if (p.chart_type === "stats") {
      card(el, p, `<div class="gv-stats">${(p.stats || []).map((s) => `<div class="gv-stat"><div class="l">${esc(s.label)}</div><div class="v">${esc(s.value)}${p.unit && !/[^\d.,\s-]/.test(s.value) ? `<span class="gv-sub"> ${esc(p.unit)}</span>` : ""}</div>${s.delta ? `<div class="d">${/^[-−]/.test(s.delta) ? "▼" : /^\+/.test(s.delta) ? "▲" : ""} ${esc(s.delta)}</div>` : ""}</div>`).join("")}</div>`);
      return;
    }
    if (!Array.isArray(p.labels) || !Array.isArray(p.series) || !p.series.length) return renderUnknown(p, el);
    p.series = p.series.map((s) => ({ name: String(s.name || ""), values: (s.values || []).map((v) => num(v)) }));
    const svg = p.chart_type === "line" ? chartLine(p) : p.chart_type === "ring" ? chartRing(p) : chartBar(p);
    const fig = card(el, p, svg + (p.chart_type === "ring" ? "" : legend(p.series.map((s) => s.name))), `<button class="gv-btn" data-table="1">Table</button>`);
    addTable(fig, ["", ...p.series.map((s) => s.name || "Value")], p.labels.map((l, i) => [l, ...p.series.map((s) => fmt(s.values[i], p.unit))]));
  }

  /* ---------- flowcharts ---------- */
  const ARROW = (id) => `<defs><marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,1L9,5L0,9Z" style="fill:var(--muted)"/></marker></defs>`;
  function flowProcess(p) {
    const steps = p.steps || [], id = `gva${++uid}`, W = 560;
    if (steps.length <= 4) {
      const gap = 26, bw = (W - gap * (steps.length - 1)) / steps.length;
      const boxes = steps.map((s) => ({ t: wrap(s.label, bw - 20, 13, 3), d: s.detail ? wrap(s.detail, bw - 20, 11.5, 4) : [] }));
      // Title baselines start at 50; details follow 4px below; 14px bottom padding.
      const bh = Math.max(...boxes.map((b) => 64 + (b.t.length - 1) * 16 + (b.d.length ? 4 + b.d.length * 15 : 0)));
      let g = ARROW(id);
      boxes.forEach((b, i) => {
        const x = i * (bw + gap);
        g += `<rect x="${x}" y="1" width="${bw}" height="${bh}" rx="10" style="fill:var(--panel, var(--gv-surface));stroke:var(--line)"/>`;
        g += `<circle cx="${x + 18}" cy="20" r="10" style="fill:var(--accent)"/><text x="${x + 18}" y="24" font-size="11" font-weight="700" text-anchor="middle" style="fill:var(--panel, #fff)">${i + 1}</text>`;
        g += tspans(b.t, x + 10, 50, 16, `font-size="13" font-weight="600"`);
        if (b.d.length) g += tspans(b.d, x + 10, 54 + b.t.length * 16, 15, `font-size="11.5" class="m"`);
        if (i < boxes.length - 1) g += `<line x1="${x + bw + 3}" x2="${x + bw + gap - 3}" y1="${bh / 2}" y2="${bh / 2}" style="stroke:var(--muted);stroke-width:1.5" marker-end="url(#${id})"/>`;
      });
      return `<svg viewBox="0 -1 ${W} ${bh + 4}">${g}</svg>`;
    }
    let y = 0, g = ARROW(id);
    const rows = steps.map((s) => { const t = wrap(s.label, 440, 13, 2), d = s.detail ? wrap(s.detail, 440, 11.5, 3) : []; return { t, d, h: 22 + t.length * 16 + d.length * 15 }; });
    rows.forEach(({ t, d, h }, i) => {
      g += `<rect x="40" y="${y}" width="${W - 40}" height="${h}" rx="10" style="fill:var(--panel, var(--gv-surface));stroke:var(--line)"/>`;
      g += `<circle cx="16" cy="${y + h / 2}" r="11" style="fill:var(--accent)"/><text x="16" y="${y + h / 2 + 4}" font-size="11" font-weight="700" text-anchor="middle" style="fill:var(--panel, #fff)">${i + 1}</text>`;
      g += tspans(t, 54, y + 22, 16, `font-size="13" font-weight="600"`);
      if (d.length) g += tspans(d, 54, y + 24 + t.length * 16, 15, `font-size="11.5" class="m"`);
      if (i < rows.length - 1) g += `<line x1="16" x2="16" y1="${y + h / 2 + 13}" y2="${y + h + 14 + rows[i + 1].h / 2 - 14}" style="stroke:var(--muted);stroke-width:1.5" marker-end="url(#${id})"/>`;
      y += h + 14;
    });
    return `<svg viewBox="0 0 ${W} ${y - 12}">${g}</svg>`;
  }
  function flowTimeline(p) {
    let y = 8, g = "";
    const W = 560, items = p.steps || [];
    items.forEach((s, i) => {
      const t = wrap(s.label, 130, 12.5, 2), d = s.detail ? wrap(s.detail, 360, 12.5, 4) : [""], h = Math.max(t.length, d.length) * 16 + 14;
      g += tspans(t, 130, y + 12, 16, `font-size="12.5" font-weight="600" text-anchor="end"`);
      g += `<circle cx="152" cy="${y + 8}" r="6" style="fill:var(--gv-surface);stroke:var(--accent);stroke-width:2.5"/>`;
      if (i < items.length - 1) g += `<line x1="152" x2="152" y1="${y + 16}" y2="${y + h + 2}" style="stroke:var(--line);stroke-width:2"/>`;
      g += tspans(d, 172, y + 12, 16, `font-size="12.5"`);
      y += h;
    });
    return `<svg viewBox="0 0 ${W} ${y}">${g}</svg>`;
  }
  function flowSequence(steps) {
    const actors = [];
    steps.forEach((s) => [s.from, s.to].forEach((a) => { if (!actors.includes(a)) actors.push(a); }));
    const W = 560, colW = W / actors.length, X = (a) => colW * actors.indexOf(a) + colW / 2, id = `gva${++uid}`;
    const top = 40, rowH = 34, H = top + steps.length * rowH + 44;
    let g = ARROW(id);
    actors.forEach((a) => {
      const x = X(a), bw = Math.min(colW - 10, 130);
      g += `<line x1="${x}" x2="${x}" y1="${top}" y2="${H - 34}" style="stroke:var(--line);stroke-width:1.5;stroke-dasharray:4 4"/>`;
      [2, H - 32].forEach((by) => { g += `<rect x="${x - bw / 2}" y="${by}" width="${bw}" height="30" rx="8" style="fill:var(--panel, var(--gv-surface));stroke:var(--line)"/><text x="${x}" y="${by + 19}" font-size="12" font-weight="600" text-anchor="middle">${esc(trunc(a, Math.floor(bw / 7)))}</text>`; });
    });
    steps.forEach((s, i) => {
      const y = top + 22 + i * rowH, x1 = X(s.from), x2 = X(s.to);
      if (x1 === x2) {
        g += `<path d="M${x1},${y - 6}h34v14h-32" style="fill:none;stroke:var(--accent);stroke-width:1.5" marker-end="url(#${id})"/><text x="${x1 + 40}" y="${y + 4}" font-size="11.5">${esc(trunc(s.label, 40))}</text>`;
      } else {
        g += `<line x1="${x1}" x2="${x2 + (x2 > x1 ? -2 : 2)}" y1="${y}" y2="${y}" style="stroke:var(--accent);stroke-width:1.5" marker-end="url(#${id})"/>`;
        g += `<text x="${(x1 + x2) / 2}" y="${y - 6}" font-size="11.5" text-anchor="middle">${esc(trunc(s.label, Math.floor(Math.abs(x2 - x1) / 6.2)))}</text>`;
      }
    });
    return `<svg viewBox="0 0 ${W} ${H}">${g}</svg>`;
  }
  function flowComparison(p) {
    const cols = p.columns || [], W = 560, gap = 12, cw = (W - gap * (cols.length - 1)) / cols.length;
    const laid = cols.map((c) => ({ t: wrap(c.title, cw - 20, 13, 2), pts: (c.points || []).map((pt) => wrap(pt, cw - 34, 12, 4)) }));
    const H = Math.max(...laid.map((c) => 26 + c.t.length * 16 + c.pts.reduce((a, l) => a + l.length * 15 + 8, 0))) + 12;
    let g = "";
    laid.forEach((c, i) => {
      const x = i * (cw + gap), hh = 14 + c.t.length * 16;
      g += `<rect x="${x}" y="0" width="${cw}" height="${H}" rx="10" style="fill:var(--panel, var(--gv-surface));stroke:var(--line)"/>`;
      g += `<path d="M${x},${hh}V10Q${x},0 ${x + 10},0H${x + cw - 10}Q${x + cw},0 ${x + cw},10V${hh}Z" style="fill:${SERIES(i)};fill-opacity:.16"/>`;
      g += tspans(c.t, x + 12, 20, 16, `font-size="13" font-weight="650"`);
      let y = hh + 18;
      c.pts.forEach((l) => { g += `<circle cx="${x + 16}" cy="${y - 4}" r="2.6" style="fill:${SERIES(i)}"/>` + tspans(l, x + 26, y, 15, `font-size="12"`); y += l.length * 15 + 8; });
    });
    return `<svg viewBox="0 0 ${W} ${H}">${g}</svg>`;
  }
  // Layered layout for small directed graphs (decision preset and Mermaid flowcharts).
  function flowGraph(nodes, edges, dir = "TD") {
    const byId = new Map(nodes.map((n) => [n.id, n])), out = new Map(nodes.map((n) => [n.id, []])), inc = new Map(nodes.map((n) => [n.id, []]));
    const state = new Map(), back = new Set();
    const visit = (id) => { state.set(id, 1); for (const e of out.get(id)) { const s = state.get(e.to); if (s === 1) back.add(e); else if (!s) visit(e.to); } state.set(id, 2); };
    edges.forEach((e) => { out.get(e.from).push(e); inc.get(e.to).push(e); });
    nodes.forEach((n) => { if (!state.get(n.id) && !inc.get(n.id).length) visit(n.id); });
    nodes.forEach((n) => { if (!state.get(n.id)) visit(n.id); });
    const layer = new Map();
    const depth = (id, seen = new Set()) => {
      if (layer.has(id)) return layer.get(id);
      if (seen.has(id)) return 0;
      seen.add(id);
      const d = Math.max(0, ...inc.get(id).filter((e) => !back.has(e)).map((e) => depth(e.from, seen) + 1));
      layer.set(id, d); return d;
    };
    nodes.forEach((n) => depth(n.id));
    const layers = [];
    nodes.forEach((n) => { (layers[layer.get(n.id)] ||= []).push(n); });
    for (let i = 1; i < layers.length; i++) {
      const pos = new Map(layers[i - 1].map((n, k) => [n.id, k]));
      const bc = (n) => { const ps = inc.get(n.id).filter((e) => pos.has(e.from)).map((e) => pos.get(e.from)); return ps.length ? ps.reduce((a, b) => a + b, 0) / ps.length : 1e9; };
      layers[i] = layers[i].map((n, k) => [n, bc(n), k]).sort((a, b) => a[1] - b[1] || a[2] - b[2]).map((x) => x[0]);
    }
    const LR = dir === "LR" || dir === "RL", maxPer = Math.max(...layers.map((l) => l.length));
    const W = 560, nodeW = LR ? Math.min(150, (W - 40 * (layers.length - 1)) / layers.length) : Math.min(170, (W - 18 * (maxPer - 1)) / maxPer);
    nodes.forEach((n) => {
      const isD = n.kind === "decision";
      n.lines = wrap(n.label, nodeW - (isD ? 54 : 20), 12.5, isD ? 3 : 4);
      n.w = nodeW; n.h = n.lines.length * 15 + (isD ? 34 : 20);
    });
    const gapMain = 46, gapCross = 18;
    let main = 0;
    layers.forEach((l) => {
      const thick = Math.max(...l.map((n) => (LR ? n.w : n.h)));
      const total = l.reduce((a, n) => a + (LR ? n.h : n.w), 0) + gapCross * (l.length - 1);
      let cross = ((LR ? 0 : W) - total) / 2;
      if (LR) cross = 0;
      l.forEach((n) => {
        if (LR) { n.x = main + (thick - n.w) / 2; n.y = cross; cross += n.h + gapCross; }
        else { n.y = main + (thick - n.h) / 2; n.x = cross; cross += n.w + gapCross; }
      });
      main += thick + gapMain;
    });
    if (LR) { // centre each column vertically
      const H = Math.max(...layers.map((l) => l.reduce((a, n) => a + n.h, 0) + gapCross * (l.length - 1)));
      layers.forEach((l) => { const t = l.reduce((a, n) => a + n.h, 0) + gapCross * (l.length - 1); l.forEach((n) => { n.y += (H - t) / 2; }); });
    }
    const width = LR ? main - gapMain : W, height = LR ? Math.max(...nodes.map((n) => n.y + n.h)) : main - gapMain;
    const id = `gva${++uid}`;
    let g = ARROW(id), labels = "";
    edges.forEach((e) => {
      const a = byId.get(e.from), b = byId.get(e.to);
      let x1, y1, x2, y2, d;
      if (LR) { x1 = a.x + a.w; y1 = a.y + a.h / 2; x2 = b.x; y2 = b.y + b.h / 2; if (back.has(e) || x2 <= x1) { x1 = a.x + a.w / 2; y1 = a.y + a.h; x2 = b.x + b.w / 2; y2 = b.y + b.h; d = `M${x1},${y1}C${x1},${Math.max(y1, y2) + 40} ${x2},${Math.max(y1, y2) + 40} ${x2},${y2 + 2}`; } else { const m = (x1 + x2) / 2; d = `M${x1},${y1}C${m},${y1} ${m},${y2} ${x2 - 2},${y2}`; } }
      else { x1 = a.x + a.w / 2; y1 = a.y + a.h; x2 = b.x + b.w / 2; y2 = b.y; if (back.has(e) || y2 <= y1) { x1 = a.x + a.w; y1 = a.y + a.h / 2; x2 = b.x + b.w; y2 = b.y + b.h / 2; const ox = Math.max(x1, x2) + 30; d = `M${x1},${y1}C${ox},${y1} ${ox},${y2} ${x2 + 2},${y2}`; } else { const m = (y1 + y2) / 2; d = `M${x1},${y1}C${x1},${m} ${x2},${m} ${x2},${y2 - 2}`; } }
      g += `<path d="${d}" style="fill:none;stroke:var(--muted);stroke-width:1.5${back.has(e) ? ";stroke-dasharray:4 3" : ""}" marker-end="url(#${id})"/>`;
      if (e.label) {
        const lx = (x1 + x2) / 2, ly = (y1 + y2) / 2, t = trunc(e.label, 18), tw = t.length * 6.4 + 10;
        labels += `<rect x="${lx - tw / 2}" y="${ly - 9}" width="${tw}" height="17" rx="8" style="fill:var(--gv-surface);stroke:var(--line)"/><text x="${lx}" y="${ly + 4}" font-size="11" text-anchor="middle" class="m">${esc(t)}</text>`;
      }
    });
    nodes.forEach((n) => {
      const cx = n.x + n.w / 2, cy = n.y + n.h / 2;
      if (n.kind === "decision") g += `<polygon points="${cx},${n.y} ${n.x + n.w},${cy} ${cx},${n.y + n.h} ${n.x},${cy}" style="fill:var(--panel, var(--gv-surface));stroke:var(--accent);stroke-width:1.5"/>`;
      else g += `<rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="${n.kind === "start" || n.kind === "end" ? n.h / 2 : 9}" style="fill:${n.kind === "start" || n.kind === "end" ? "var(--accent)" : "var(--panel, var(--gv-surface))"};${n.kind === "start" || n.kind === "end" ? "fill-opacity:.16;" : ""}stroke:${n.kind === "step" ? "var(--line)" : "var(--accent)"};stroke-width:1.2"/>`;
      g += tspans(n.lines, cx, cy - ((n.lines.length - 1) * 15) / 2 + 4.5, 15, `font-size="12.5" text-anchor="middle"${n.kind === "step" ? "" : ` font-weight="600"`}`);
    });
    const pad = LR ? 0 : 0, extra = edges.some((e) => back.has(e)) ? 40 : 0;
    return `<svg viewBox="${-4 - pad} -4 ${width + 8 + extra} ${height + 8 + (LR ? extra : 0)}">${g}${labels}</svg>`;
  }
  // Minimal Mermaid reader: flowchart/graph and sequenceDiagram; anything else is shown as source.
  function parseMermaid(src) {
    const lines = String(src).split(/\n|;/).map((l) => l.replace(/%%.*$/, "").trim()).filter(Boolean);
    if (!lines.length) return null;
    const head = lines[0].match(/^(graph|flowchart)\s*(TD|TB|LR|RL|BT)?\s*$/i);
    if (/^sequenceDiagram/i.test(lines[0])) {
      const steps = [];
      for (const l of lines.slice(1)) {
        const m = l.match(/^([\w ]+?)\s*(-{1,2}>>|-{1,2}>|-{1,2}x|-{1,2}\))\s*\+?-?([\w ]+?)\s*:\s*(.*)$/);
        if (m) steps.push({ from: m[1].trim(), to: m[3].trim(), label: m[4].trim() });
      }
      return steps.length ? { kind: "sequence", steps: steps.slice(0, 24) } : null;
    }
    if (!head) return null;
    const nodes = new Map(), edges = [];
    const shape = (s) => {
      if (!s) return null;
      if (s.startsWith("{")) return { kind: "decision", label: s.slice(1, -1) };
      if (s.startsWith("((") || s.startsWith("([")) return { kind: "start", label: s.slice(2, -2) };
      if (s.startsWith("(")) return { kind: "start", label: s.slice(1, -1) };
      if (s.startsWith("[[")) return { kind: "step", label: s.slice(2, -2) };
      return { kind: "step", label: s.replace(/^[\[>]/, "").replace(/\]$/, "") };
    };
    const node = (id, sh) => {
      const s = shape(sh), clean = (t) => t.replace(/^["']|["']$/g, "").trim();
      if (!nodes.has(id)) nodes.set(id, { id, label: s ? clean(s.label) : id, kind: s ? s.kind : "step" });
      else if (s) Object.assign(nodes.get(id), { label: clean(s.label), kind: s.kind });
    };
    const NODE = /^([A-Za-z0-9_\-]+)\s*(\(\([^)]*\)\)|\(\[[^\]]*\]\)|\[\[[^\]]*\]\]|\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|>[^\]]*\])?/;
    const EDGE = /^\s*(?:--\s*([^-|>][^>]*?)\s*-->|-\.\s*([^.]+?)\s*\.->|-->|---|-\.->|==>|-\.-|===)\s*(?:\|([^|]*)\|)?\s*/;
    for (const l of lines.slice(1)) {
      if (/^(subgraph|end|style|classDef|class|click|linkStyle|direction)\b/i.test(l)) continue;
      let rest = l, prev = null, pendingLabel = null, ok = true;
      while (rest.length) {
        const m = rest.match(NODE);
        if (!m) { ok = false; break; }
        node(m[1], m[2]);
        if (prev) edges.push({ from: prev, to: m[1], label: pendingLabel });
        prev = m[1]; rest = rest.slice(m[0].length).trim();
        if (!rest) break;
        const amp = rest.match(/^&\s*/);
        if (amp) { ok = false; break; }
        const e = rest.match(EDGE);
        if (!e) { ok = false; break; }
        pendingLabel = (e[1] || e[2] || e[3] || "").trim() || null; rest = rest.slice(e[0].length);
      }
      if (!ok && !nodes.size) return null;
    }
    if (!nodes.size || nodes.size > 30) return null;
    return { kind: "graph", dir: (head[2] || "TD").toUpperCase(), nodes: [...nodes.values()], edges };
  }
  function renderFlow(p, el) {
    let svg = "";
    if (p.preset === "process") svg = flowProcess(p);
    else if (p.preset === "timeline") svg = flowTimeline(p);
    else if (p.preset === "sequence") svg = flowSequence(p.steps || []);
    else if (p.preset === "comparison") svg = flowComparison(p);
    else if (p.preset === "decision") svg = flowGraph((p.nodes || []).map((n) => ({ ...n })), (p.edges || []).filter((e) => (p.nodes || []).some((n) => n.id === e.from) && (p.nodes || []).some((n) => n.id === e.to)));
    else if (p.preset === "mermaid") {
      let g = null;
      try { g = parseMermaid(p.mermaid); } catch (e) { g = null; }
      if (g?.kind === "graph") svg = flowGraph(g.nodes, g.edges, g.dir);
      else if (g?.kind === "sequence") svg = flowSequence(g.steps);
      const fig = card(el, p, svg || `<pre>${esc(p.mermaid)}</pre>`, `<button class="gv-btn" data-src="1">${svg ? "Source" : "Copy"}</button>`);
      fig.querySelector("[data-src]").onclick = (ev) => {
        if (!svg) { navigator.clipboard?.writeText(p.mermaid); ev.target.textContent = "Copied"; return; }
        const pre = fig.querySelector("pre");
        if (pre) pre.remove(); else fig.insertAdjacentHTML("beforeend", `<pre>${esc(p.mermaid)}</pre>`);
      };
      return;
    }
    card(el, p, svg);
  }

  /* ---------- illustrations ---------- */
  const PATH_OK = /^[MmLlHhVvCcSsQqTtAaZz0-9eE.,\s+-]{1,6000}$/, POINTS_OK = /^[0-9eE.,\s+-]{1,4000}$/;
  function renderIllustration(p, el) {
    const vb = String(p.view_box || "0 0 400 300").split(/[\s,]+/).map(Number);
    const [vx, vy, vw, vh] = vb.length === 4 && vb.every(Number.isFinite) && vb[2] > 0 && vb[3] > 0 ? vb : [0, 0, 400, 300];
    const id = `gva${++uid}`;
    const n = (v, d = 0) => r1(Math.max(-1e4, Math.min(1e4, num(v, d))));
    const markers = new Map();  // one arrowhead per stroke colour
    const marker = (c) => { if (!markers.has(c)) markers.set(c, `${id}m${markers.size}`); return markers.get(c); };
    let g = "";
    for (const e of (p.elements || []).slice(0, 200)) {
      if (!e || typeof e !== "object") continue;
      const isText = e.type === "text", isLine = e.type === "line" || e.type === "arrow" || e.type === "polyline";
      const fill = color(e.fill, isText ? "var(--text)" : isLine ? "none" : "var(--accent)");
      const stroke = color(e.stroke, isLine ? "var(--text)" : "none");
      let style = `fill:${fill};stroke:${stroke};stroke-width:${Math.max(0, Math.min(40, num(e.stroke_width, isLine ? 2 : 1.5)))}`;
      if (e.opacity != null) style += `;opacity:${Math.max(0, Math.min(1, num(e.opacity, 1)))}`;
      if (e.dash) style += ";stroke-dasharray:6 4";
      if (isLine) style += ";stroke-linecap:round;stroke-linejoin:round";
      const rot = e.rotate ? ` transform="rotate(${n(e.rotate)} ${n(e.cx ?? e.x)} ${n(e.cy ?? e.y)})"` : "";
      const a = `style="${style}"${rot}`;
      switch (e.type) {
        case "rect": g += `<rect x="${n(e.x)}" y="${n(e.y)}" width="${Math.max(0, n(e.width))}" height="${Math.max(0, n(e.height))}" rx="${Math.max(0, n(e.rx))}" ${a}/>`; break;
        case "circle": g += `<circle cx="${n(e.cx ?? e.x)}" cy="${n(e.cy ?? e.y)}" r="${Math.max(0, n(e.r, 10))}" ${a}/>`; break;
        case "ellipse": g += `<ellipse cx="${n(e.cx ?? e.x)}" cy="${n(e.cy ?? e.y)}" rx="${Math.max(0, n(e.rx, 20))}" ry="${Math.max(0, n(e.ry, 10))}" ${a}/>`; break;
        case "line": case "arrow": g += `<line x1="${n(e.x1)}" y1="${n(e.y1)}" x2="${n(e.x2)}" y2="${n(e.y2)}" ${a}${e.type === "arrow" ? ` marker-end="url(#${marker(stroke)})"` : ""}/>`; break;
        case "path": if (PATH_OK.test(String(e.d || ""))) g += `<path d="${esc(e.d)}" ${a}/>`; break;
        case "polygon": case "polyline": if (POINTS_OK.test(String(e.points || ""))) g += `<${e.type} points="${esc(e.points)}" ${a}/>`; break;
        case "text": {
          const anchor = ["start", "middle", "end"].includes(e.anchor) ? e.anchor : "start";
          const size = Math.max(6, Math.min(72, num(e.font_size, 14)));
          g += `<text x="${n(e.x)}" y="${n(e.y)}" font-size="${size}" text-anchor="${anchor}" font-weight="${e.weight === "bold" ? 650 : 400}"${e.font === "mono" ? ` font-family="ui-monospace, Menlo, monospace"` : ""} style="fill:${fill}"${rot}>${esc(String(e.text ?? "").slice(0, 200))}</text>`;
          break;
        }
        default: break;
      }
    }
    const defs = `<defs>${[...markers].map(([c, mid]) => `<marker id="${mid}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,1L9,5L0,9Z" style="fill:${c === "none" ? "var(--text)" : c}"/></marker>`).join("")}</defs>`;
    card(el, p, `<svg viewBox="${vx} ${vy} ${vw} ${vh}" role="img" aria-label="${esc(p.title)}">${defs}${g}</svg>${p.caption ? `<div class="gv-cap">${esc(p.caption)}</div>` : ""}`);
  }

  function renderUnknown(p, el) { card(el, { title: p.title || "Visual" }, `<div class="gv-cap">This visual couldn't be displayed.</div>`); }

  HS.renderers.visual = (p, el) => {
    try {
      if (p?.type === "chart") return renderChart(p, el);
      if (p?.type === "flowchart") return renderFlow(p, el);
      if (p?.type === "illustration") return renderIllustration(p, el);
      renderUnknown(p || {}, el);
    } catch (e) { console.error("visual render failed", e); renderUnknown(p || {}, el); }
  };

  // On-screen marks can't be replayed later; in the chat they read as a short note.
  HS.renderers.overlay ||= (p, el) => {
    if (p?.action !== "show" || !Array.isArray(p.items)) return;
    const labels = p.items.map((i) => i.label).filter(Boolean);
    el.innerHTML = `<div class="gv-ov">Pointed on screen${p.app ? ` in ${esc(p.app)}` : ""}${labels.length ? `: <b>${labels.map(esc).join("</b> · <b>")}</b>` : ""}</div>`;
  };

  Object.assign(HS.toolLabels, {
    create_chart: (i) => `Drew a chart${i.title ? `: ${i.title}` : ""}`,
    create_flowchart: (i) => `Drew a diagram${i.title ? `: ${i.title}` : ""}`,
    create_illustration: (i) => `Sketched${i.title ? ` ${i.title}` : " an illustration"}`,
    draw_screen_overlay: (i) => `Pointed at ${(i.items || []).length || ""} spot${(i.items || []).length === 1 ? "" : "s"} on screen`,
    hide_screen_overlay: () => "Cleared the screen marks",
  });

  /* ---------- settings (main window only) ---------- */
  if (HS.registerSettings) {
    const opts = [
      ["visual_answers", "Answers", "Draw charts and diagrams in answers when they help"],
      ["visual_quick_screenshot", "Quick Ask", "Let Quick Ask see a screenshot of the window you were in"],
      ["visual_overlays", "Point on screen", "Let Quick Ask draw callouts and highlights over your screen"],
      ["visual_glance", "Glance", "Control+Shift+G explains the window in front of you"],
    ];
    const setCard = HS.registerSettings({
      id: "visual", title: "Visual answers",
      description: "Screenshots are taken only when you open Quick Ask or press the Glance shortcut, sent to your AI provider with that question, and never stored. They need Screen Recording permission (System Settings › Privacy & Security). Blocked apps are never captured.",
      html: opts.map(([k, l, d]) => `<div class="field"><label>${HS.esc(l)}</label><div class="ctl"><label><input type="checkbox" data-gv="${k}"> ${HS.esc(d)}</label></div></div>`).join(""),
      onLoad(s) { setCard.querySelectorAll("[data-gv]").forEach((c) => { c.checked = s[c.dataset.gv] !== false; }); },
    });
    setCard.addEventListener("change", async (e) => {
      const k = e.target.dataset.gv; if (!k) return;
      try { await HS.api("/api/settings", { [k]: e.target.checked }); HS.toast("Saved"); } catch (err) { HS.toast(err.message); }
    });
  }
})();
