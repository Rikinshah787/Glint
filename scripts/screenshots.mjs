#!/usr/bin/env node
// Screenshot driver for scripts/screenshots.sh. Drives headless Chrome over the DevTools protocol
// (no npm dependencies: Node 22+ has a global WebSocket) against a running demo engine.
//
//   node scripts/screenshots.mjs --url http://127.0.0.1:47408/ --out docs/images \
//        [--chrome PATH] [--themes light,dark] [--only home,graph] [--frames DIR] [--social] [--tz ZONE]
//
// Edit SHOTS below when the UI changes. Each shot runs `js` inside the app page (helpers from
// PRELUDE are in scope), waits, then saves <name>-<theme>.png at 1440x900 @2x.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : true]);
  return acc;
}, []));
const URL_BASE = (args.url || "http://127.0.0.1:47408/").replace(/\/?$/, "/");
const OUT = resolve(args.out || "docs/images");
const THEMES = String(args.themes || "light,dark").split(",");
const ONLY = args.only ? String(args.only).split(",") : null;
const W = 1440, H = 900, DPR = 2;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ shots
// Helpers available inside `js`: go(view), tab(name), openThread(title), openSessionTitled(title),
// sleep(ms), waitFor(selector). They fall back to clicking nav items when the app's globals change.
const PRELUDE = `
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const waitFor = async (sel, ms = 4000) => { const t = Date.now(); while (Date.now() - t < ms) { const el = document.querySelector(sel); if (el) return el; await sleep(80); } return null; };
const go = async (view) => {
  if (window.glintNavigate) window.glintNavigate(view);
  else if (window.HS?.show) window.HS.show(view);
  else document.querySelector('[data-view="' + view + '"]')?.click();
  await sleep(500);
};
const tab = async (name) => {
  if (typeof switchMemTab === "function") switchMemTab(name);
  else document.querySelector('[data-tab="' + name + '"]')?.click();
  await sleep(500);
};
const findByText = (sel, text) => [...document.querySelectorAll(sel)].find((e) => e.textContent.includes(text));
const openThread = async (title) => { await go("ask"); await sleep(400); (findByText("#threadList .t, [data-thread], .thread", title))?.click(); await sleep(900); };
const openSessionTitled = async (title) => {
  const list = await window.HS.api("/api/sessions?limit=100");
  const s = list.find((x) => x.title === title) || list[0];
  if (s && window.HS.openSession) window.HS.openSession(s.id);
  await sleep(900);
};
`;

const SHOTS = [
  { name: "home", js: `await go("home"); await sleep(900);` },
  { name: "today", js: `await go("today"); await sleep(700);` },
  { name: "memory-sessions", js: `await go("memory"); await tab("sessions"); await openSessionTitled("Verify proration fix on staging");` },
  { name: "timeline", js: `await go("memory"); await tab("timeline"); await sleep(600);` },
  { name: "graph", js: `await go("memory"); await tab("graph"); await sleep(3200);` },
  { name: "ask-chart", js: `await openThread("Hours by project"); await sleep(600);` },
  { name: "insights", js: `await go("insights"); await sleep(600);` },
  { name: "settings", js: `await go("settings"); await sleep(700);` },
  { name: "quick-ask", quick: true },
];

// Quick Ask: a wallpaper with a document window behind, the real /quick page in an iframe on top,
// and a canned answer streamed through the page's own renderer (no AI call, so it is deterministic).
const QUICK_Q = "When is the Billing v2 go/no-go, and is anything left?";
const QUICK_EVENTS = [
  ["thread", { id: "demo" }],
  ["tool", { name: "search_memory", input: { query: "Billing v2 go/no-go" } }],
  ["tool", { name: "get_recent_context", input: { since_hours: 24 } }],
  ...("**Thursday at 10:00.** Nothing is left:\n\n- You verified the BIL-142 fix on staging this morning: one prorated "
    + "line item of $412.90, as expected.\n- That was the last launch criterion, so the checklist is **12 / 12**.\n"
    + "- Your go/no-go doc recommends **GO** for new self-serve signups on Monday.")
    .match(/.{1,9}/gs).map((d) => ["text", { delta: d }]),
  ["done", {}],
];
const QUICK_SCENE = (dark) => `
document.head.insertAdjacentHTML("beforeend", \`<style>
  html, body { margin: 0; height: 100%; overflow: hidden; }
  body > *:not(#demoWall) { display: none !important; }
  #demoWall { position: fixed; inset: 0; background: ${dark
    ? "radial-gradient(120% 90% at 20% 0%, #3b3f6b 0%, #1d2033 45%, #111219 100%)"
    : "radial-gradient(120% 90% at 20% 0%, #c9d8ff 0%, #e8defc 45%, #f6efe6 100%)"}; font: 14px -apple-system, BlinkMacSystemFont, sans-serif; }
  #demoDoc { position: absolute; left: 150px; top: 120px; width: 1000px; height: 690px; border-radius: 14px; background: ${dark ? "#1f2026" : "#ffffff"};
    color: ${dark ? "#d9d9de" : "#2b2b2b"}; box-shadow: 0 30px 80px rgba(0,0,0,.25); overflow: hidden; }
  #demoDoc .dq-bar { height: 38px; display: flex; align-items: center; gap: 8px; padding: 0 14px; border-bottom: 1px solid ${dark ? "#2c2d35" : "#ececec"}; }
  #demoDoc .dq-bar i { width: 12px; height: 12px; border-radius: 50%; display: inline-block; }
  #demoDoc .dq-page { padding: 34px 70px; line-height: 1.7; opacity: .75; }
  #demoDoc h1 { font-size: 28px; margin: 0 0 14px; }
  #demoQuick { position: absolute; left: 360px; top: 230px; width: 720px; height: 460px; border: 0; background: transparent;
    filter: drop-shadow(0 28px 60px rgba(0,0,0,.35)); }
</style>\`);
const wall = document.createElement("div"); wall.id = "demoWall";
wall.innerHTML = \`<div id="demoDoc"><div class="dq-bar"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i><i style="background:#28c840"></i>
  <span style="margin-left:12px;opacity:.6">Billing v2 — Go/no-go summary</span></div><div class="dq-page"><h1>Billing v2 — Go/no-go summary</h1>
  <p><b>Recommendation:</b> GO for new self-serve signups on Monday.</p><p><b>Launch criteria:</b> 12 / 12 done (checklist linked).</p>
  <p><b>Rollout:</b> Day 1 new signups (flag billing_v2_new), Day 8 monthly renewals.</p>
  <p><b>Rollback:</b> flip billing_v2_new off; invoices fall back to legacy; no data migration needed.</p>
  <p><b>Launch-day owners:</b> Priya (eng on-call), Tom (support), Aisha (finance), Sam (dashboards), Maya (comms).</p></div></div>
  <iframe id="demoQuick" src="/quick" allowtransparency="true"></iframe>\`;
document.body.appendChild(wall);
`;
const QUICK_ASK = (typeDelay) => `
const f = document.getElementById("demoQuick");
await new Promise((r) => { if (f.contentDocument?.readyState === "complete" && f.contentWindow.ask) r(); else f.onload = () => r(); });
await sleep(400);
const w = f.contentWindow, input = w.document.getElementById("q");
const events = ${JSON.stringify(QUICK_EVENTS)};
w.fetch = async () => new Response(new ReadableStream({ async start(c) {
  const enc = new TextEncoder();
  for (const [ev, data] of events) { c.enqueue(enc.encode("event: " + ev + "\\ndata: " + JSON.stringify(data) + "\\n\\n")); await sleep(ev === "tool" ? 350 : 28); }
  c.close();
} }), { status: 200, headers: { "Content-Type": "text/event-stream" } });
const q = ${JSON.stringify(QUICK_Q)};
for (let i = 1; i <= q.length; i++) { input.value = q.slice(0, i); if (${typeDelay}) await sleep(${typeDelay}); }
await sleep(200);
window.__quickDone = false;
Promise.resolve(w.ask()).then(() => { window.__quickDone = true; });
`;

// ------------------------------------------------------------------ CDP plumbing
function findChrome() {
  const c = [args.chrome, process.env.CHROME,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"].filter(Boolean);
  const hit = c.find((p) => existsSync(p));
  if (!hit) throw new Error("No Chrome/Chromium found. Install Google Chrome or pass --chrome /path/to/chrome.");
  return hit;
}

async function launch() {
  const profile = mkdtempSync(join(tmpdir(), "glint-shots-"));
  const proc = spawn(findChrome(), ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--hide-scrollbars", "--force-color-profile=srgb",
    "--disable-extensions", "--mute-audio", "about:blank"], { stdio: "ignore" });
  const portFile = join(profile, "DevToolsActivePort");
  for (let i = 0; i < 100 && !existsSync(portFile); i++) await sleep(100);
  const [port, path] = readFileSync(portFile, "utf8").trim().split("\n");
  const ws = new WebSocket(`ws://127.0.0.1:${port}${path}`);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let seq = 0; const pending = new Map(), listeners = [];
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) {
      const { r, j } = pending.get(msg.id); pending.delete(msg.id);
      msg.error ? j(new Error(`${msg.error.message} ${msg.error.data || ""}`)) : r(msg.result);
    } else for (const l of listeners) l(msg);
  };
  const send = (method, params = {}, sessionId) => new Promise((r, j) => {
    const id = ++seq; pending.set(id, { r, j });
    ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const close = () => { try { ws.close(); } catch {} proc.kill(); setTimeout(() => rmSync(profile, { recursive: true, force: true }), 500); };
  return { send, listeners, close };
}

async function newPage(b, { width = W, height = H, dpr = DPR, theme = "light", transparent = false } = {}) {
  const { targetId } = await b.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await b.send("Target.attachToTarget", { targetId, flatten: true });
  const s = (m, p) => b.send(m, p, sessionId);
  await s("Page.enable"); await s("Runtime.enable");
  await s("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: dpr, mobile: false });
  await s("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
  if (transparent) await s("Emulation.setDefaultBackgroundColorOverride", { color: { r: 0, g: 0, b: 0, a: 0 } });
  if (args.tz) await s("Emulation.setTimezoneOverride", { timezoneId: String(args.tz) });
  const page = {
    s,
    async goto(url) {
      const loaded = new Promise((r) => {
        const l = (m) => { if (m.sessionId === sessionId && m.method === "Page.loadEventFired") { b.listeners.splice(b.listeners.indexOf(l), 1); r(); } };
        b.listeners.push(l);
      });
      await s("Page.navigate", { url }); await loaded;
    },
    async eval(js) {
      const r = await s("Runtime.evaluate", { expression: `(async () => { ${PRELUDE}\n${js}\n })()`, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    },
    async shot(file, format = "png") {
      const { data } = await s("Page.captureScreenshot", { format, ...(format === "jpeg" ? { quality: 92 } : {}) });
      if (file) writeFileSync(file, Buffer.from(data, "base64"));
      return data;
    },
    close: () => b.send("Target.closeTarget", { targetId }),
  };
  return page;
}

async function openApp(page) {
  await page.goto(URL_BASE);
  await page.eval(`await waitFor(".nav, [data-view]", 8000); await sleep(1200);`);
}

// ------------------------------------------------------------------ runs
async function stills(b) {
  mkdirSync(OUT, { recursive: true });
  for (const theme of THEMES) {
    const page = await newPage(b, { theme });
    await openApp(page);
    for (const shot of SHOTS) {
      if (ONLY && !ONLY.includes(shot.name)) continue;
      const file = join(OUT, `${shot.name}-${theme}.png`);
      try {
        if (shot.quick) {
          await openApp(page);
          await page.eval(QUICK_SCENE(theme === "dark") + QUICK_ASK(0) + `
            for (let i = 0; i < 100 && !window.__quickDone; i++) await sleep(100); await sleep(400);`);
          await page.shot(file);
          await openApp(page);
        } else {
          await page.eval(shot.js);
          await page.shot(file);
        }
        console.log(`  ${file.replace(process.cwd() + "/", "")}`);
      } catch (e) { console.error(`  ${shot.name}-${theme}: ${e.message}`); }
    }
    await page.close();
  }
}

// Animated walkthrough: frames at 1x into --frames DIR plus frames.json ({file, ms}); make_gif.py
// assembles them.
async function walkthrough(b, dir) {
  mkdirSync(dir, { recursive: true });
  const page = await newPage(b, { dpr: 1, theme: "light" });
  await openApp(page);
  const frames = []; let n = 0;
  const grab = async (ms) => { const f = join(dir, `f${String(n++).padStart(4, "0")}.png`); await page.shot(f); frames.push({ file: f, ms }); };
  const hold = async (js, ms) => { await page.eval(js); await grab(ms); };
  await hold(`await go("home"); await sleep(900);`, 2200);
  await hold(`await go("memory"); await tab("sessions"); await sleep(500);`, 1200);
  await hold(`await openSessionTitled("Verify proration fix on staging");`, 2400);
  await hold(`await tab("timeline"); await sleep(500);`, 1800);
  await page.eval(`await tab("graph");`);
  for (let i = 0; i < 6; i++) { await sleep(260); await grab(i === 5 ? 2400 : 200); }
  await hold(`await openThread("Hours by project"); await sleep(500);`, 2800);
  // Quick Ask: type, then stream the answer.
  await page.eval(QUICK_SCENE(false));
  await page.eval(`await sleep(900);`);
  await grab(700);
  const q = QUICK_Q;
  await page.eval(`const f = document.getElementById("demoQuick"); await new Promise((r) => { if (f.contentWindow?.ask) r(); else f.onload = () => r(); }); await sleep(300);`);
  for (let i = 3; i <= q.length; i += 4) {
    await page.eval(`document.getElementById("demoQuick").contentWindow.document.getElementById("q").value = ${JSON.stringify(q)}.slice(0, ${i});`);
    await grab(70);
  }
  page.eval(QUICK_ASK(0));
  for (let i = 0; i < 40; i++) {
    await sleep(120); await grab(120);
    if (await page.eval(`return window.__quickDone === true;`)) break;
  }
  await sleep(300); await grab(3600);
  writeFileSync(join(dir, "frames.json"), JSON.stringify(frames, null, 1));
  console.log(`  ${frames.length} walkthrough frames`);
  await page.close();
}

// 1280x640 social preview: logo + pitch + a screenshot, rendered by Chrome from inline HTML.
async function social(b) {
  const page = await newPage(b, { width: 1280, height: 640, dpr: 1, theme: "dark" });
  const shotPath = [join(OUT, "home-dark.png"), join(OUT, "memory-sessions-dark.png")].find((p) => existsSync(p));
  const logoPath = join(OUT, "logo.svg");
  const img = shotPath ? `data:image/png;base64,${readFileSync(shotPath).toString("base64")}` : "";
  const logo = existsSync(logoPath) ? `<img class="logo" src="data:image/svg+xml;base64,${readFileSync(logoPath).toString("base64")}">`
    : `<div class="logo mark">◐</div>`;
  const html = `<!doctype html><html><head><meta charset="utf-8"><style>
    body { margin: 0; width: 1280px; height: 640px; overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
      background: radial-gradient(110% 120% at 0% 0%, #23413a 0%, #14191b 55%, #0e1011 100%); color: #f3f1ec; }
    .copy { position: absolute; left: 72px; top: 150px; width: 520px; }
    .logo { width: 76px; height: 76px; border-radius: 18px; }
    .mark { display: grid; place-items: center; background: #6cc3a6; color: #10201b; font-size: 44px; }
    h1 { font-size: 76px; letter-spacing: -2px; margin: 26px 0 6px; }
    p { font-size: 30px; line-height: 1.3; margin: 0; color: #cfd8d3; }
    .tags { margin-top: 30px; display: flex; gap: 10px; flex-wrap: wrap; }
    .tags span { font-size: 17px; padding: 6px 14px; border-radius: 999px; background: rgba(255,255,255,.08); color: #d8e6df; }
    .shot { position: absolute; left: 640px; top: 90px; width: 900px; border-radius: 14px; box-shadow: 0 40px 90px rgba(0,0,0,.55);
      border: 1px solid rgba(255,255,255,.08); }
  </style></head><body><div class="copy">${logo}<h1>Glint</h1><p>Your Mac's memory.<br>Private, local, open source.</p>
    <div class="tags"><span>On-device AI</span><span>MCP for Claude &amp; Cursor</span><span>MIT</span></div></div>
    ${img ? `<img class="shot" src="${img}">` : ""}</body></html>`;
  const { frameTree } = await page.s("Page.getFrameTree");
  await page.s("Page.setDocumentContent", { frameId: frameTree.frame.id, html });
  await sleep(600);
  const file = join(OUT, "social-preview.png");
  await page.shot(file);
  console.log(`  ${file.replace(process.cwd() + "/", "")}`);
  await page.close();
}

const b = await launch();
try {
  if (!args["skip-stills"]) await stills(b);
  if (args.frames) await walkthrough(b, resolve(args.frames));
  if (args.social) await social(b);
} finally { b.close(); }
