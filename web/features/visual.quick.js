/* Glint visual answers — Quick Ask side.
   - asks the native shell to capture the window the user was in each time the panel opens
   - forwards live "overlay" events to the native shell, which draws them over the screen
   - shows whether the window is attached, and loads the shared renderers from visual.js */
(() => {
  const HS = window.HS;
  if (!HS) return;
  const post = HS.post || ((m) => window.webkit?.messageHandlers?.glint?.postMessage(m));

  HS.renderers.overlay = (p, el) => {
    if (!p || !p.action) return;
    post({ action: "visual.overlay", payload: p });
    if (p.action !== "show" || !Array.isArray(p.items)) return;
    const labels = p.items.map((i) => i.label).filter(Boolean);
    el.innerHTML = `<div class="gv-ov">Pointed on screen${labels.length ? `: <b>${labels.map(HS.esc).join("</b> · <b>")}</b>` : ""} <span style="opacity:.7">· Esc clears</span></div>`;
  };

  // A new question clears the marks from the previous answer.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing && e.target?.id === "q" && e.target.value.trim()) {
      post({ action: "visual.overlay", payload: { action: "clear" } });
    }
  }, true);

  // Footer hint: which window Quick Ask can see.
  const foot = document.querySelector(".foot");
  const seen = document.createElement("span");
  seen.className = "gv-seen";
  seen.style.cssText = "flex:1;margin:0 12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;opacity:.85";
  foot?.querySelector("#status")?.after(seen);
  window.glintVisualShot = (info = {}) => {
    if (info.ok) { seen.textContent = `Sees: ${info.app || "this window"}`; seen.title = info.title || ""; return; }
    if (info.reason === "permission") {
      seen.innerHTML = `<button style="border:0;background:none;color:var(--accent);cursor:pointer;font:inherit;padding:0">Let Quick Ask see your window…</button>`;
      seen.querySelector("button").onclick = () => post({ action: "visual.openScreenSettings" });
      return;
    }
    seen.textContent = "";
  };

  HS.onOpen.push(() => { seen.textContent = ""; post({ action: "visual.quickOpened" }); });

  const s = document.createElement("script");
  s.src = "/features/visual.js";
  document.body.appendChild(s);
})();
