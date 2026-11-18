// Voice mode for Quick Ask: a mic button next to the input. Click to start/stop (or press and hold);
// the native Assist feature records, transcribes through /api/dictation (or on-device recognition),
// and the transcript is put into the box and asked.
(() => {
  const input = document.getElementById("q");
  const status = document.getElementById("status");
  if (!input || !input.parentElement) return;

  const css = document.createElement("style");
  css.textContent = `
    .as-mic { flex: none; width: 30px; height: 30px; border-radius: 50%; border: 1px solid var(--line); background: transparent;
      color: var(--muted); display: grid; place-items: center; cursor: pointer; position: relative; padding: 0; }
    .as-mic:hover { color: var(--text); border-color: var(--accent); }
    .as-mic svg { width: 15px; height: 15px; }
    .as-mic.rec { color: #fff; background: var(--danger); border-color: var(--danger); }
    .as-mic.rec::before { content: ""; position: absolute; inset: -4px; border-radius: 50%; border: 2px solid var(--danger);
      opacity: .35; transform: scale(calc(1 + var(--lvl, 0) * .35)); transition: transform .1s; }
    .as-mic.busy { cursor: progress; }
    .as-mic.busy svg { animation: as-spin 1s linear infinite; }
    @keyframes as-spin { to { transform: rotate(360deg); } }`;
  document.head.appendChild(css);

  const MIC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
  const STOP = '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>';
  const SPIN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 3a9 9 0 1 0 9 9"/></svg>';

  const btn = document.createElement("button");
  btn.className = "as-mic"; btn.type = "button"; btn.title = "Ask by voice (click to start and stop, or press and hold)";
  btn.innerHTML = MIC;
  input.after(btn);

  let state = "idle", pressedAt = 0, restore = null;
  const setStatus = (t) => { if (!status) return; if (restore === null) restore = status.textContent; status.textContent = t; };
  const clearStatus = () => { if (status && restore !== null) status.textContent = restore; restore = null; };

  function setState(s) {
    state = s;
    btn.classList.toggle("rec", s === "recording");
    btn.classList.toggle("busy", s === "transcribing");
    btn.innerHTML = s === "recording" ? STOP : s === "transcribing" ? SPIN : MIC;
    if (s !== "recording") btn.style.removeProperty("--lvl");
  }

  const start = () => { setState("recording"); setStatus("Listening… click the mic to finish"); HS.post({ action: "assist.voice.start" }); };
  const stop = () => { setState("transcribing"); setStatus("Transcribing…"); HS.post({ action: "assist.voice.stop" }); };

  btn.addEventListener("mousedown", (e) => {
    e.preventDefault();
    if (state === "idle") { pressedAt = Date.now(); start(); }
    else if (state === "recording") { pressedAt = 0; stop(); }
  });
  btn.addEventListener("mouseup", () => {
    // Press-and-hold: releasing after a real hold ends the recording.
    if (state === "recording" && pressedAt && Date.now() - pressedAt > 450) stop();
    pressedAt = 0;
  });

  // Called by the native side with {state, level?, text?, error?, notice?}.
  window.assistVoiceState = (m) => {
    if (m.state === "recording") {
      if (state !== "recording") setState("recording");
      if (typeof m.level === "number") btn.style.setProperty("--lvl", m.level.toFixed(2));
      return;
    }
    if (m.state === "transcribing") { setState("transcribing"); setStatus("Transcribing…"); return; }
    setState("idle");
    clearStatus();
    if (m.state === "done" && m.text) {
      input.value = m.text;
      input.focus();
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    } else if (m.error || m.notice) {
      setStatus(m.error || m.notice);
      setTimeout(clearStatus, 3500);
    }
  };

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state === "recording") { HS.post({ action: "assist.voice.cancel" }); setState("idle"); clearStatus(); }
  }, true);
  (HS.onOpen || []).push(() => { if (state !== "transcribing") { if (state === "recording") HS.post({ action: "assist.voice.cancel" }); setState("idle"); clearStatus(); } });
})();
