// Glint: native macOS shell.
//
// Starts the local engine (glint/engine.py) as a child process, shows the web UI in
// a main window, offers a floating Quick Ask panel on a global hotkey (Option+Space),
// and a menu-bar item for capture control. All data stays on this Mac.
import AppKit
import ApplicationServices
import AVFoundation
import Carbon.HIToolbox
import ServiceManagement
import Speech
import WebKit

// MARK: - Engine process

final class Engine {
    let port: Int
    let token: String
    private var process: Process?
    private(set) var ready = false
    var onReady: (() -> Void)?
    var onExit: ((Int32) -> Void)?

    init() {
        port = Int(ProcessInfo.processInfo.environment["GLINT_PORT"] ?? "") ?? 47321
        token = UUID().uuidString.replacingOccurrences(of: "-", with: "")
    }

    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)/")! }

    func start() {
        let root = Bundle.main.object(forInfoDictionaryKey: "GlintProjectRoot") as? String ?? ""
        let python = "\(root)/.venv/bin/python"
        let script = "\(root)/engine/engine.py"
        guard FileManager.default.isExecutableFile(atPath: python) else {
            fail("Python environment not found at \(python). Run scripts/setup.sh, then scripts/build.sh.")
            return
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-W", "ignore", script]
        var env = ProcessInfo.processInfo.environment
        env["GLINT_PORT"] = String(port)
        env["GLINT_TOKEN"] = token
        p.environment = env
        let out = Pipe()
        p.standardOutput = out
        let logURL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/Glint-engine.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        p.standardError = try? FileHandle(forWritingTo: logURL)
        out.fileHandleForReading.readabilityHandler = { [weak self] h in
            guard let self, !self.ready, let line = String(data: h.availableData, encoding: .utf8),
                  line.contains("\"ready\": true") else { return }
            self.ready = true
            h.readabilityHandler = nil
            DispatchQueue.main.async { self.onReady?() }
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.onExit?(proc.terminationStatus) }
        }
        do { try p.run(); process = p } catch { fail("Could not start the engine: \(error)") }
    }

    func stop() {
        guard let p = process, p.isRunning else { return }
        p.terminate()
        p.waitUntilExit()
    }

    /// JSON request to the engine API; completion runs on the main queue.
    func request(_ path: String, _ body: [String: Any]? = nil, timeout: TimeInterval = 60,
                 completion: (([String: Any]?, String?) -> Void)? = nil) {
        var req = URLRequest(url: baseURL.appendingPathComponent(path), timeoutInterval: timeout)
        req.httpMethod = body == nil ? "GET" : "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(token, forHTTPHeaderField: "X-Glint-Token")
        if let body { req.httpBody = try? JSONSerialization.data(withJSONObject: body) }
        URLSession.shared.dataTask(with: req) { data, resp, err in
            let json = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
            let errorText = err?.localizedDescription ?? (status >= 400 ? (json?["error"] as? String ?? "HTTP \(status)") : nil)
            DispatchQueue.main.async { completion?(json, errorText) }
        }.resume()
    }

    func post(_ path: String, _ body: [String: Any]) { request(path, body) }

    private func fail(_ message: String) {
        DispatchQueue.main.async {
            let a = NSAlert()
            a.messageText = "Glint couldn't start"
            a.informativeText = message
            a.runModal()
        }
    }
}

// MARK: - Web views

final class WebBridge: NSObject, WKScriptMessageHandler {
    weak var app: AppDelegate?
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        guard let body = m.body as? [String: Any], let action = body["action"] as? String else { return }
        switch action {
        case "closeQuickAsk": app?.hideQuickAsk()
        case "openMain": app?.hideQuickAsk(); app?.showMain(path: body["path"] as? String)
        case "requestAccessibility": app?.promptAccessibility()
        default:
            // Let native features handle their own messages from web/features scripts.
            _ = app?.features.first { $0.handle(action: action, body: body) }
        }
    }
}

/// Opens links that leave the local UI (target=_blank or external URLs) in the default browser.
final class ExternalLinks: NSObject, WKNavigationDelegate, WKUIDelegate {
    static let shared = ExternalLinks()

    private func isLocal(_ url: URL?) -> Bool {
        guard let host = url?.host else { return true }
        return host == "127.0.0.1" || host == "localhost"
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, ["http", "https", "mailto"].contains(url.scheme ?? "") {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.navigationType == .linkActivated, let url = navigationAction.request.url,
           !isLocal(url), ["http", "https", "mailto"].contains(url.scheme ?? "") {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }
}

func makeWebView(bridge: WebBridge, transparent: Bool = false) -> WKWebView {
    let cfg = WKWebViewConfiguration()
    cfg.userContentController.add(bridge, name: "glint")
    let wv = WKWebView(frame: .zero, configuration: cfg)
    wv.navigationDelegate = ExternalLinks.shared
    wv.uiDelegate = ExternalLinks.shared
    if transparent { wv.setValue(false, forKey: "drawsBackground") }
    return wv
}

final class QuickAskPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

// MARK: - Global hotkey (Carbon)

final class HotKey {
    private var ref: EventHotKeyRef?
    private static var handlers: [UInt32: () -> Void] = [:]
    private static var installed = false

    init(keyCode: UInt32, modifiers: UInt32, id: UInt32, handler: @escaping () -> Void) {
        HotKey.handlers[id] = handler
        if !HotKey.installed {
            HotKey.installed = true
            var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
            InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
                var hk = EventHotKeyID()
                GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                                  nil, MemoryLayout<EventHotKeyID>.size, nil, &hk)
                DispatchQueue.main.async { HotKey.handlers[hk.id]?() }
                return noErr
            }, 1, &spec, nil, nil)
        }
        let hkID = EventHotKeyID(signature: OSType(0x474C4E54), id: id)  // 'GLNT'
        RegisterEventHotKey(keyCode, modifiers, hkID, GetApplicationEventTarget(), 0, &ref)
    }
}

// MARK: - Dictation (hold Control+Option, speak, release)

final class DictationPill {
    private let panel: NSPanel
    private let label = NSTextField(labelWithString: "")
    private var hideWork: DispatchWorkItem?

    init() {
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 220, height: 40),
                        styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: false)
        panel.level = .statusBar
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.ignoresMouseEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        let fx = NSVisualEffectView(frame: panel.contentRect(forFrameRect: panel.frame))
        fx.material = .hudWindow
        fx.state = .active
        fx.wantsLayer = true
        fx.layer?.cornerRadius = 20
        fx.layer?.masksToBounds = true
        label.font = .systemFont(ofSize: 13, weight: .medium)
        label.alignment = .center
        label.frame = NSRect(x: 10, y: 11, width: 200, height: 18)
        fx.addSubview(label)
        panel.contentView = fx
    }

    func show(_ text: String, autoHide: TimeInterval? = nil) {
        hideWork?.cancel()
        label.stringValue = text
        if let screen = NSScreen.main {
            let f = screen.visibleFrame
            panel.setFrameOrigin(NSPoint(x: f.midX - 110, y: f.minY + 48))
        }
        panel.orderFrontRegardless()
        if let autoHide {
            let w = DispatchWorkItem { [weak self] in self?.hide() }
            hideWork = w
            DispatchQueue.main.asyncAfter(deadline: .now() + autoHide, execute: w)
        }
    }

    func hide() { panel.orderOut(nil) }
}

final class DictationController {
    unowned let engine: Engine
    private let pill = DictationPill()
    private var recorder: AVAudioRecorder?
    private var fileURL: URL?
    private var holdTimer: Timer?
    private var chordDown = false
    private var recording = false
    private var busy = false
    private var target: NSRunningApplication?
    var enabled = true

    init(engine: Engine) { self.engine = engine }

    func install() {
        NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged) { [weak self] e in self?.flagsChanged(e.modifierFlags) }
        NSEvent.addLocalMonitorForEvents(matching: .flagsChanged) { [weak self] e in self?.flagsChanged(e.modifierFlags); return e }
        NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] e in self?.keyDown(e) }
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] e in self?.keyDown(e); return e }
    }

    private func flagsChanged(_ flags: NSEvent.ModifierFlags) {
        let chord = flags.intersection([.control, .option, .command, .shift]) == [.control, .option]
        if chord && !chordDown {
            chordDown = true
            guard enabled, !busy else { return }
            holdTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: false) { [weak self] _ in self?.begin() }
        } else if !chord && chordDown {
            chordDown = false
            holdTimer?.invalidate()
            holdTimer = nil
            if recording { finish() }
        }
    }

    private func keyDown(_ e: NSEvent) {
        if recording && e.keyCode == UInt16(kVK_Escape) { cancel("Cancelled"); return }
        // Another key while holding the chord means a Control+Option shortcut, not dictation.
        holdTimer?.invalidate()
        holdTimer = nil
        if recording { cancel(nil) }
    }

    private func begin() {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { _ in }
            return
        case .denied, .restricted:
            pill.show("Microphone access is off for Glint", autoHide: 2.5)
            return
        default: break
        }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("glint-dictation-\(UUID().uuidString).m4a")
        let settings: [String: Any] = [AVFormatIDKey: Int(kAudioFormatMPEG4AAC), AVSampleRateKey: 16000,
                                       AVNumberOfChannelsKey: 1, AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue]
        guard let r = try? AVAudioRecorder(url: url, settings: settings), r.record() else {
            pill.show("Couldn't start the microphone", autoHide: 2.5)
            return
        }
        recorder = r
        fileURL = url
        target = NSWorkspace.shared.frontmostApplication
        recording = true
        pill.show("● Listening…  release to insert")
    }

    private func stopRecorder() -> TimeInterval {
        let d = recorder?.currentTime ?? 0
        recorder?.stop()
        recorder = nil
        recording = false
        return d
    }

    private func cancel(_ message: String?) {
        _ = stopRecorder()
        cleanup()
        if let message { pill.show(message, autoHide: 1.2) } else { pill.hide() }
    }

    private func cleanup() {
        if let u = fileURL { try? FileManager.default.removeItem(at: u) }
        fileURL = nil
    }

    private func finish() {
        let duration = stopRecorder()
        guard duration >= 0.4, let url = fileURL, let data = try? Data(contentsOf: url) else {
            cancel(nil)
            return
        }
        busy = true
        pill.show("Transcribing…")
        let app = target?.localizedName ?? ""
        engine.request("api/dictation", ["audio_b64": data.base64EncodedString(), "filename": "dictation.m4a", "app": app],
                       timeout: 120) { [weak self] json, err in
            guard let self else { return }
            if let err { return self.fail(err) }
            if json?["needs_local"] as? Bool == true {
                self.transcribeOnDevice(url) { text in
                    guard let text, !text.isEmpty else { return self.fail("Didn't catch that") }
                    self.pill.show("Polishing…")
                    self.engine.request("api/dictation/polish", ["text": text, "app": app]) { j, _ in
                        self.insert((j?["text"] as? String) ?? text)
                    }
                }
            } else if let text = json?["text"] as? String, !text.isEmpty {
                self.insert(text)
            } else {
                self.fail("Didn't catch that")
            }
        }
    }

    private func transcribeOnDevice(_ url: URL, completion: @escaping (String?) -> Void) {
        SFSpeechRecognizer.requestAuthorization { status in
            DispatchQueue.main.async {
                guard status == .authorized, let rec = SFSpeechRecognizer(), rec.isAvailable else {
                    self.fail("Add an OpenAI key or allow Speech Recognition")
                    return
                }
                let req = SFSpeechURLRecognitionRequest(url: url)
                req.requiresOnDeviceRecognition = rec.supportsOnDeviceRecognition
                req.addsPunctuation = true
                var done = false
                rec.recognitionTask(with: req) { result, error in
                    if done { return }
                    if let result, result.isFinal { done = true; DispatchQueue.main.async { completion(result.bestTranscription.formattedString) } }
                    else if error != nil { done = true; DispatchQueue.main.async { completion(nil) } }
                }
            }
        }
    }

    private func fail(_ message: String) {
        busy = false
        cleanup()
        pill.show(message, autoHide: 2.5)
    }

    /// Paste into the app that was focused when dictation started, then restore the clipboard.
    private func insert(_ text: String) {
        let pb = NSPasteboard.general
        let saved: [[NSPasteboard.PasteboardType: Data]] = (pb.pasteboardItems ?? []).map { item in
            var d: [NSPasteboard.PasteboardType: Data] = [:]
            for t in item.types { if let v = item.data(forType: t) { d[t] = v } }
            return d
        }
        pb.clearContents()
        pb.setString(text, forType: .string)
        pb.setData(Data(), forType: NSPasteboard.PasteboardType("org.nspasteboard.ConcealedType"))
        let ours = pb.changeCount
        target?.activate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) {
            let src = CGEventSource(stateID: .combinedSessionState)
            let down = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(kVK_ANSI_V), keyDown: true)
            let up = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(kVK_ANSI_V), keyDown: false)
            down?.flags = .maskCommand
            up?.flags = .maskCommand
            down?.post(tap: .cghidEventTap)
            up?.post(tap: .cghidEventTap)
            self.pill.show("Inserted", autoHide: 0.9)
            self.busy = false
            self.cleanup()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                guard pb.changeCount == ours else { return }  // the user copied something new meanwhile
                pb.clearContents()
                for d in saved {
                    let item = NSPasteboardItem()
                    for (t, v) in d { item.setData(v, forType: t) }
                    pb.writeObjects([item])
                }
            }
        }
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let engine = Engine()
    let bridge = WebBridge()
    var mainWindow: NSWindow?
    var mainWeb: WKWebView?
    var quickPanel: QuickAskPanel?
    var appBeforeQuickAsk: NSRunningApplication?
    var quickWeb: WKWebView?
    var statusItem: NSStatusItem?
    var hotKey: HotKey?
    lazy var dictation = DictationController(engine: engine)
    var features: [GlintFeature] = []
    var settings: [String: Any] = [:]

    func applicationDidFinishLaunching(_ n: Notification) {
        bridge.app = self
        buildMenu()
        buildStatusItem()
        features = makeFeatures(app: self)
        features.forEach { $0.start() }
        engine.onReady = { [weak self] in
            self?.showMain(path: nil)
            self?.preloadQuickAsk()
            self?.refreshDictationSetting()
            self?.features.forEach { $0.engineReady() }
        }
        dictation.install()
        Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in self?.refreshDictationSetting() }
        engine.onExit = { code in
            if code != 0 && code != 15 {
                let a = NSAlert()
                a.messageText = "The Glint engine stopped (code \(code))"
                a.informativeText = "See ~/Library/Logs/Glint-engine.log for details."
                a.runModal()
            }
        }
        engine.start()
        hotKey = HotKey(keyCode: UInt32(kVK_ANSI_K), modifiers: UInt32(cmdKey | shiftKey), id: 1) { [weak self] in
            self?.toggleQuickAsk()
        }
        if !AXIsProcessTrusted() { promptAccessibility() }
    }

    func applicationWillTerminate(_ n: Notification) { engine.stop() }

    /// Re-read settings every 30 s: dictation on/off, open-at-login, and notify features.
    func refreshDictationSetting() {
        guard engine.ready else { return }
        engine.request("api/settings") { [weak self] json, _ in
            guard let self, let s = json?["settings"] as? [String: Any] else { return }
            self.settings = s
            if let on = s["dictation_enabled"] as? Bool { self.dictation.enabled = on }
            if let login = s["open_at_login"] as? Bool { self.setOpenAtLogin(login) }
            self.features.forEach { $0.settingsChanged(s) }
        }
    }

    func setOpenAtLogin(_ on: Bool) {
        let service = SMAppService.mainApp
        do {
            if on && service.status != .enabled { try service.register() }
            if !on && service.status == .enabled { try service.unregister() }
        } catch {
            NSLog("Glint: open-at-login change failed: \(error.localizedDescription)")
        }
    }

    func applicationShouldHandleReopen(_ s: NSApplication, hasVisibleWindows: Bool) -> Bool {
        showMain(path: nil)
        return true
    }

    func promptAccessibility() {
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(opts)
    }

    // Main window
    func showMain(path: String?) {
        guard engine.ready else { return }
        if mainWindow == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1240, height: 800),
                             styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                             backing: .buffered, defer: false)
            w.titlebarAppearsTransparent = true
            w.titleVisibility = .hidden
            w.minSize = NSSize(width: 820, height: 560)
            w.isReleasedWhenClosed = false
            w.setFrameAutosaveName("GlintMain")
            w.center()
            let wv = makeWebView(bridge: bridge)
            w.contentView = wv
            w.delegate = self
            mainWindow = w
            mainWeb = wv
            wv.load(URLRequest(url: engine.baseURL))
        }
        if let path, let wv = mainWeb {
            wv.evaluateJavaScript("window.glintNavigate && window.glintNavigate(\(jsString(path)))")
        }
        NSApp.activate(ignoringOtherApps: true)
        mainWindow?.makeKeyAndOrderFront(nil)
    }

    // Quick Ask
    func preloadQuickAsk() {
        let size = NSSize(width: 720, height: 460)
        let p = QuickAskPanel(contentRect: NSRect(origin: .zero, size: size),
                              styleMask: [.nonactivatingPanel, .borderless, .fullSizeContentView],
                              backing: .buffered, defer: false)
        p.level = .floating
        p.isOpaque = false
        p.backgroundColor = .clear
        p.hasShadow = true
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        p.isMovableByWindowBackground = true
        let wv = makeWebView(bridge: bridge, transparent: true)
        p.contentView = wv
        wv.load(URLRequest(url: engine.baseURL.appendingPathComponent("quick")))
        quickPanel = p
        quickWeb = wv
    }

    func toggleQuickAsk() {
        guard let p = quickPanel else { return }
        if p.isVisible { hideQuickAsk(); return }
        let front = NSWorkspace.shared.frontmostApplication
        appBeforeQuickAsk = front?.bundleIdentifier == Bundle.main.bundleIdentifier ? nil : front
        if let screen = NSScreen.main {
            let f = screen.visibleFrame
            p.setFrameOrigin(NSPoint(x: f.midX - p.frame.width / 2, y: f.maxY - p.frame.height - f.height * 0.12))
        }
        NSApp.activate(ignoringOtherApps: true)
        p.makeKeyAndOrderFront(nil)
        quickWeb?.evaluateJavaScript("window.glintQuickOpened && window.glintQuickOpened()")
    }

    func hideQuickAsk() {
        guard let p = quickPanel, p.isVisible else { return }
        p.orderOut(nil)
        // Hand focus back to where the user was typing (macOS 14+ needs the active app to yield first).
        if let prev = appBeforeQuickAsk {
            if #available(macOS 14.0, *) { NSApp.yieldActivation(to: prev) }
            prev.activate()
        }
        appBeforeQuickAsk = nil
    }

    // Menus
    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Glint", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Settings…", action: #selector(openSettings), keyEquivalent: ",")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Glint", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Quit Glint", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)
        let winItem = NSMenuItem()
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        winItem.submenu = win
        main.addItem(winItem)
        NSApp.mainMenu = main
    }

    func buildStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName: "eye.circle", accessibilityDescription: "Glint")
        let m = NSMenu()
        m.addItem(withTitle: "Open Glint", action: #selector(openMain), keyEquivalent: "").target = self
        let qa = m.addItem(withTitle: "Quick Ask", action: #selector(quickAskMenu), keyEquivalent: "k")
        qa.keyEquivalentModifierMask = [.command, .shift]
        qa.target = self
        m.addItem(.separator())
        m.addItem(withTitle: "Capture now", action: #selector(captureNow), keyEquivalent: "").target = self
        m.addItem(withTitle: "Pause capture for 30 minutes", action: #selector(pause30), keyEquivalent: "").target = self
        m.addItem(withTitle: "Pause capture for 1 hour", action: #selector(pause60), keyEquivalent: "").target = self
        m.addItem(withTitle: "Pause until 9am tomorrow", action: #selector(pauseTomorrow), keyEquivalent: "").target = self
        m.addItem(withTitle: "Pause until I resume", action: #selector(pauseIndefinite), keyEquivalent: "").target = self
        m.addItem(withTitle: "Resume capture", action: #selector(resume), keyEquivalent: "").target = self
        m.addItem(.separator())
        m.addItem(withTitle: "Settings…", action: #selector(openSettings), keyEquivalent: "").target = self
        m.addItem(withTitle: "Quit Glint", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        item.menu = m
        statusItem = item
    }

    @objc func openMain() { showMain(path: nil) }
    @objc func openSettings() { showMain(path: "settings") }
    @objc func quickAskMenu() { toggleQuickAsk() }
    @objc func captureNow() { engine.post("api/capture", ["now": true]) }
    @objc func pause30() { engine.post("api/capture", ["pause_minutes": 30]) }
    @objc func pause60() { engine.post("api/capture", ["pause_minutes": 60]) }
    @objc func pauseTomorrow() { engine.post("api/capture", ["pause": "tomorrow"]) }
    @objc func pauseIndefinite() { engine.post("api/capture", ["pause": "indefinite"]) }
    @objc func resume() { engine.post("api/capture", ["pause_minutes": 0, "enabled": true]) }

    private func jsString(_ s: String) -> String {
        let data = try? JSONSerialization.data(withJSONObject: [s])
        let arr = data.flatMap { String(data: $0, encoding: .utf8) } ?? "[\"\"]"
        return String(arr.dropFirst().dropLast())
    }
}

