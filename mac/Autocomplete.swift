// Autocomplete everywhere: AI continuations while typing in any app, accepted with Tab.
//
// How it works:
//   - A keyboard/mouse event tap runs on its own thread so it never delays typing. It only
//     swallows keys while a suggestion is visible: Tab (or `) takes the next phrase, Shift+Tab
//     one word, Down cycles options, Esc dismisses. Option+Tab asks for a suggestion right away.
//   - After a typing pause, the focused field is read through Accessibility (text, caret, caret
//     bounds) on a background queue. If the caret sits at the end of a line with enough text,
//     no selection, not in a password field, code editor or terminal, the engine is asked for
//     continuations (POST api/autocomplete/suggest).
//   - The suggestion appears in a small click-through panel near the caret (`under`, `normal`
//     or `ghost` look). Accepted text is typed as keyboard events; outcomes go back to the engine
//     for stats and to learn the user's writing.
import AppKit
import ApplicationServices
import Carbon.HIToolbox

/// Marks events Glint posts itself so the tap lets them through untouched.
private let syntheticMarker: Int64 = 0x474C_4E54_4143

// MARK: - Field snapshot

struct ACField {
    let pid: pid_t
    let appName: String
    let bundleID: String
    let title: String
    let urlHost: String?
    let before: String
    let after: String
    /// Caret line rectangle in AX coordinates (top-left origin of the primary screen).
    let axCaret: CGRect
}

private enum ACRead {
    case ok(ACField)
    case skip(String)
}

// MARK: - State shared with the event-tap thread

private final class ACKeyState {
    private let lock = NSLock()
    private var _visible = false
    private var _remaining = ""
    private var _options = 0
    var enabled = true
    var acceptTab = true
    var acceptGrave = false

    func with<T>(_ f: (ACKeyState) -> T) -> T { lock.lock(); defer { lock.unlock() }; return f(self) }
    var visible: Bool { get { _visible } set { _visible = newValue } }
    var remaining: String { get { _remaining } set { _remaining = newValue } }
    var options: Int { get { _options } set { _options = newValue } }
}

private func autocompleteTapCallback(proxy: CGEventTapProxy, type: CGEventType, event: CGEvent,
                                     refcon: UnsafeMutableRawPointer?) -> Unmanaged<CGEvent>? {
    guard let refcon else { return Unmanaged.passUnretained(event) }
    let me = Unmanaged<AutocompleteFeature>.fromOpaque(refcon).takeUnretainedValue()
    return me.onTapEvent(type: type, event: event)
}

// MARK: - Feature

final class AutocompleteFeature: GlintFeature {
    unowned let app: AppDelegate

    // Settings
    private var enabled = true
    private var style = "under"
    private var mode = "automatic"
    private var acceptKey = "tab"
    private var disabledApps: Set<String> = []
    private var minChars = 12
    private var pauseMs = 450

    private let keys = ACKeyState()
    private let axQueue = DispatchQueue(label: "glint.autocomplete.ax", qos: .userInitiated)
    private var tapThread: Thread?
    fileprivate var tapPort: CFMachPort?
    private var tapRetry: Timer?
    private var engineUp = false

    private var generation = 0
    private var pauseTimer: Timer?
    private var hideTimer: Timer?
    private var repositionWork: DispatchWorkItem?
    private let chip = AutocompleteChip()

    private struct Pending { let seq: Int; let field: ACField; let gen: Int; let summoned: Bool; var typed = ""; var poisoned = false }
    private var pending: Pending?
    private var requestSeq = 0
    private var task: URLSessionDataTask?

    private struct Shown {
        var options: [String]
        var index: Int
        var remaining: String
        var added = ""           // text that ended up in the field after the snapshot (accepted or typed along)
        var acceptedChars = 0
        var field: ACField
        var caret: NSRect        // Cocoa screen coordinates
        let shownAt = Date()
    }
    private var shown: Shown?

    private var manualAXDone: Set<pid_t> = []

    init(app: AppDelegate) { self.app = app }

    // MARK: GlintFeature

    func start() {
        NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didActivateApplicationNotification,
                                                          object: nil, queue: .main) { [weak self] _ in
            self?.interaction()
        }
    }

    func engineReady() {
        engineUp = true
        apply(app.settings)
        ensureTap()
    }

    func settingsChanged(_ s: [String: Any]) { apply(s) }

    func handle(action: String, body: [String: Any]) -> Bool {
        guard action == "autocomplete.settings" else { return false }
        if let s = body["settings"] as? [String: Any] { apply(s) }
        return true
    }

    private func apply(_ s: [String: Any]) {
        guard !s.isEmpty else { return }
        enabled = s["autocomplete_enabled"] as? Bool ?? true
        if let v = s["autocomplete_style"] as? String, ["under", "normal", "ghost"].contains(v) { style = v }
        if let v = s["autocomplete_mode"] as? String, ["automatic", "on_demand"].contains(v) { mode = v }
        if let v = s["autocomplete_accept_key"] as? String, ["tab", "backtick", "both"].contains(v) { acceptKey = v }
        disabledApps = Set((s["autocomplete_disabled_apps"] as? [String] ?? [])
            .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }.filter { !$0.isEmpty })
        if let v = (s["autocomplete_min_chars"] as? NSNumber)?.intValue { minChars = max(1, v) }
        if let v = (s["autocomplete_pause_ms"] as? NSNumber)?.intValue { pauseMs = min(3000, max(150, v)) }
        let (en, tab, grave) = (enabled, acceptKey != "backtick", acceptKey != "tab")
        keys.with { $0.enabled = en; $0.acceptTab = tab; $0.acceptGrave = grave }
        if !enabled {
            pauseTimer?.invalidate()
            cancelPending()
            hide(outcome: "ignored")
        } else {
            ensureTap()
            if let s = shown { render(s) }   // pick up a new look immediately
        }
    }

    // MARK: Event tap (own thread)

    private func ensureTap() {
        guard tapThread == nil, enabled, engineUp else { return }
        guard AXIsProcessTrusted() else {
            // Accessibility not granted yet (the app prompts at launch); check back quietly.
            if tapRetry == nil {
                tapRetry = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] t in
                    guard let self, AXIsProcessTrusted() else { return }
                    t.invalidate()
                    self.tapRetry = nil
                    self.ensureTap()
                }
            }
            return
        }
        let t = Thread { [weak self] in self?.runTap() }
        t.name = "glint.autocomplete.keys"
        t.qualityOfService = .userInteractive
        tapThread = t
        t.start()
    }

    private func runTap() {
        let types: [CGEventType] = [.keyDown, .leftMouseDown, .rightMouseDown, .otherMouseDown, .scrollWheel]
        let mask = types.reduce(CGEventMask(0)) { $0 | (CGEventMask(1) << $1.rawValue) }
        guard let port = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .defaultTap,
                                           eventsOfInterest: mask, callback: autocompleteTapCallback,
                                           userInfo: Unmanaged.passUnretained(self).toOpaque()) else {
            NSLog("Glint: autocomplete key tap unavailable (Accessibility?)")
            DispatchQueue.main.async { self.tapThread = nil }
            return
        }
        tapPort = port
        let src = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, port, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), src, .commonModes)
        CGEvent.tapEnable(tap: port, enable: true)
        CFRunLoopRun()
    }

    /// Runs on the tap thread: decide quickly whether to swallow, hand the rest to the main thread.
    fileprivate func onTapEvent(type: CGEventType, event: CGEvent) -> Unmanaged<CGEvent>? {
        let pass = Unmanaged.passUnretained(event)
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let p = tapPort { CGEvent.tapEnable(tap: p, enable: true) }
            return pass
        }
        if event.getIntegerValueField(.eventSourceUserData) == syntheticMarker { return pass }
        guard type == .keyDown else {
            // Scrolling is frequent; it only matters while a suggestion is on screen.
            if type != .scrollWheel || keys.with({ $0.visible }) {
                DispatchQueue.main.async { self.interaction() }
            }
            return pass
        }
        let code = Int(event.getIntegerValueField(.keyboardEventKeycode))
        let flags = event.flags
        let cmdCtrl = !flags.intersection([.maskCommand, .maskControl]).isEmpty
        let opt = flags.contains(.maskAlternate)
        let shift = flags.contains(.maskShift)
        let ch = Self.printable(event)

        enum Act { case none, summon, accept(Bool), cycle, dismiss, typedAlong(String), hideThenType }
        let act: Act = keys.with { k in
            guard k.enabled else { return .none }
            if code == kVK_Tab && opt && !cmdCtrl { return .summon }
            guard k.visible else { return .none }
            let isAccept = (code == kVK_Tab && k.acceptTab) || (code == kVK_ANSI_Grave && k.acceptGrave)
            if isAccept && !cmdCtrl && !opt { return .accept(shift) }
            if code == kVK_DownArrow && k.options > 1 && !cmdCtrl && !opt && !shift { return .cycle }
            if code == kVK_Escape { k.visible = false; return .dismiss }
            if !cmdCtrl, let ch, k.remaining.hasPrefix(ch) {
                k.remaining.removeFirst(ch.count)
                return .typedAlong(ch)
            }
            k.visible = false
            return .hideThenType
        }
        switch act {
        case .summon:
            DispatchQueue.main.async { self.summon() }
            return nil
        case .accept(let oneWord):
            DispatchQueue.main.async { self.accept(oneWord: oneWord) }
            return nil
        case .cycle:
            DispatchQueue.main.async { self.cycle() }
            return nil
        case .dismiss:
            DispatchQueue.main.async { self.hide(outcome: "dismissed") }
            return nil
        case .typedAlong(let c):
            DispatchQueue.main.async { self.typedAlong(c) }
            return pass
        case .hideThenType:
            DispatchQueue.main.async { self.hide(outcome: "ignored"); self.keyActivity(ch) }
            return pass
        case .none:
            DispatchQueue.main.async { self.keyActivity(ch) }
            return pass
        }
    }

    /// The text a key press produces, if it is ordinary printable text.
    private static func printable(_ e: CGEvent) -> String? {
        var len = 0
        var buf = [UniChar](repeating: 0, count: 8)
        e.keyboardGetUnicodeString(maxStringLength: 8, actualStringLength: &len, unicodeString: &buf)
        guard len > 0 else { return nil }
        let s = String(utf16CodeUnits: buf, count: len)
        for u in s.unicodeScalars {
            if CharacterSet.controlCharacters.contains(u) || (0xF700...0xF8FF).contains(u.value) { return nil }
        }
        return s
    }

    // MARK: Typing and pauses (main thread)

    private func keyActivity(_ ch: String?) {
        generation += 1
        if var p = pending {
            if let ch { p.typed += ch } else { p.poisoned = true }
            pending = p
        }
        pauseTimer?.invalidate()
        guard enabled, mode == "automatic", engineUp else { return }
        pauseTimer = Timer.scheduledTimer(withTimeInterval: Double(pauseMs) / 1000, repeats: false) { [weak self] _ in
            self?.requestSuggestion(summoned: false)
        }
    }

    /// Clicks, scrolling and app switches end whatever suggestion is in progress.
    private func interaction() {
        generation += 1
        pauseTimer?.invalidate()
        pending?.poisoned = true
        if shown != nil { hide(outcome: "ignored") }
    }

    private func summon() {
        pauseTimer?.invalidate()
        if shown != nil { hide(outcome: "ignored") }
        requestSuggestion(summoned: true)
    }

    // MARK: Reading the focused field

    private static let excludedBundleIDs: Set<String> = [
        "com.microsoft.VSCode", "com.microsoft.VSCodeInsiders", "com.vscodium", "com.todesktop.230313mzl4w4u92",
        "com.exafunction.windsurf", "com.apple.dt.Xcode", "com.apple.Terminal", "com.googlecode.iterm2",
        "dev.warp.Warp-Stable", "dev.warp.Warp", "dev.zed.Zed", "dev.zed.Zed-Preview", "com.mitchellh.ghostty",
        "net.kovidgoyal.kitty", "org.alacritty", "io.alacritty", "com.github.wez.wezterm", "co.zeit.hyper",
        "com.panic.Nova", "org.vim.MacVim", "com.qvacua.VimR", "com.apple.ScriptEditor2", "com.google.android.studio",
        "com.bitwarden.desktop", "com.apple.keychainaccess", "com.apple.Passwords", "com.apple.loginwindow",
        "com.lastpass.LastPass", "com.dashlane.dashlanephonefinal", "org.keepassxc.keepassxc",
    ]
    private static let excludedPrefixes = ["com.jetbrains.", "com.sublimetext.", "com.sublimemerge", "com.1password",
                                           "com.agilebits.", "com.nova."]
    private static let excludedNames: Set<String> = [
        "code", "cursor", "windsurf", "xcode", "terminal", "iterm2", "iterm", "warp", "ghostty", "kitty", "alacritty",
        "wezterm", "hyper", "zed", "nova", "sublime text", "intellij idea", "pycharm", "webstorm", "goland", "clion",
        "rider", "rubymine", "phpstorm", "datagrip", "fleet", "android studio", "1password", "bitwarden",
        "keychain access", "passwords",
    ]
    private static let chromiumBrowsers: Set<String> = [
        "com.google.Chrome", "com.google.Chrome.beta", "com.google.Chrome.canary", "com.brave.Browser",
        "com.microsoft.edgemac", "company.thebrowser.Browser", "com.vivaldi.Vivaldi", "com.operasoftware.Opera",
        "org.chromium.Chromium", "ai.perplexity.comet",
    ]

    private func excluded(_ a: NSRunningApplication) -> Bool {
        let bid = a.bundleIdentifier ?? ""
        let name = (a.localizedName ?? "").lowercased()
        if bid == Bundle.main.bundleIdentifier || name == "glint" { return true }
        if Self.excludedBundleIDs.contains(bid) || Self.excludedPrefixes.contains(where: { bid.hasPrefix($0) }) { return true }
        if Self.excludedNames.contains(name) { return true }
        return disabledApps.contains(name) || disabledApps.contains(bid.lowercased())
    }

    private func requestSuggestion(summoned: Bool) {
        guard enabled, engineUp, shown == nil, AXIsProcessTrusted(), !IsSecureEventInputEnabled(),
              let front = NSWorkspace.shared.frontmostApplication, !excluded(front) else { return }
        let gen = generation
        let pid = front.processIdentifier
        let name = front.localizedName ?? ""
        let bid = front.bundleIdentifier ?? ""
        let bundleURL = front.bundleURL
        let needManual = !manualAXDone.contains(pid)
        if needManual { manualAXDone.insert(pid) }
        let minNeeded = summoned ? 1 : minChars
        axQueue.async { [weak self] in
            if needManual { Self.enableManualAccessibility(pid: pid, bundleID: bid, bundleURL: bundleURL) }
            let r = Self.readField(pid: pid, appName: name, bundleID: bid, minChars: minNeeded)
            DispatchQueue.main.async {
                guard let self, gen == self.generation, self.shown == nil else { return }
                if case .ok(let field) = r { self.send(field, gen: gen, summoned: summoned) }
            }
        }
    }

    /// Chromium and Electron apps only build their accessibility tree for web content on request.
    private static func enableManualAccessibility(pid: pid_t, bundleID: String, bundleURL: URL?) {
        var chromium = chromiumBrowsers.contains(bundleID)
        if !chromium, let u = bundleURL {
            let fw = u.appendingPathComponent("Contents/Frameworks")
            chromium = FileManager.default.fileExists(atPath: fw.appendingPathComponent("Electron Framework.framework").path)
                || FileManager.default.fileExists(atPath: fw.appendingPathComponent("Chromium Embedded Framework.framework").path)
        }
        guard chromium else { return }
        let el = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(el, 0.3)
        AXUIElementSetAttributeValue(el, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    }

    private static func attr(_ el: AXUIElement, _ name: String) -> CFTypeRef? {
        var v: CFTypeRef?
        return AXUIElementCopyAttributeValue(el, name as CFString, &v) == .success ? v : nil
    }

    private static func element(_ v: CFTypeRef?) -> AXUIElement? {
        guard let v, CFGetTypeID(v) == AXUIElementGetTypeID() else { return nil }
        return (v as! AXUIElement)
    }

    private static func rangeValue(_ v: CFTypeRef?) -> CFRange? {
        guard let v, CFGetTypeID(v) == AXValueGetTypeID() else { return nil }
        var r = CFRange()
        return AXValueGetValue(v as! AXValue, .cfRange, &r) ? r : nil
    }

    private static func bounds(_ el: AXUIElement, location: Int, length: Int) -> CGRect? {
        var range = CFRange(location: location, length: length)
        guard let rv = AXValueCreate(.cfRange, &range) else { return nil }
        var out: CFTypeRef?
        guard AXUIElementCopyParameterizedAttributeValue(el, kAXBoundsForRangeParameterizedAttribute as CFString,
                                                         rv, &out) == .success,
              let out, CFGetTypeID(out) == AXValueGetTypeID() else { return nil }
        var rect = CGRect.zero
        guard AXValueGetValue(out as! AXValue, .cgRect, &rect), rect.height > 1, rect.height < 400 else { return nil }
        return rect
    }

    /// Reads the focused text field and applies the local trigger rules. Runs off the main thread.
    private static func readField(pid: pid_t, appName: String, bundleID: String, minChars: Int) -> ACRead {
        let appEl = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(appEl, 0.4)
        guard let focused = element(attr(appEl, kAXFocusedUIElementAttribute)) else { return .skip("no_focus") }
        AXUIElementSetMessagingTimeout(focused, 0.4)
        let role = attr(focused, kAXRoleAttribute) as? String ?? ""
        let subrole = attr(focused, kAXSubroleAttribute) as? String ?? ""
        if subrole == "AXSecureTextField" || role == "AXSecureTextField" { return .skip("secure") }
        if subrole == "AXSearchField" { return .skip("search_field") }
        let textRoles: Set<String> = ["AXTextArea", "AXTextField", "AXComboBox"]
        var settable: DarwinBoolean = false
        AXUIElementIsAttributeSettable(focused, kAXValueAttribute as CFString, &settable)
        guard textRoles.contains(role) || settable.boolValue else { return .skip("not_text") }
        guard let value = attr(focused, kAXValueAttribute) as? String else { return .skip("no_text") }
        guard let sel = rangeValue(attr(focused, kAXSelectedTextRangeAttribute)) else { return .skip("no_caret") }
        if sel.length > 0 { return .skip("selection") }
        let ns = value as NSString
        let loc = sel.location
        guard loc >= 0, loc <= ns.length, ns.length < 300_000 else { return .skip("bad_caret") }
        let before = ns.substring(to: loc)
        let after = ns.substring(from: loc)
        // Suggestions only come at the end of a line, with enough of the user's own text.
        if let line = after.split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: false).first,
           !line.trimmingCharacters(in: .whitespaces).isEmpty { return .skip("mid_line") }
        if before.trimmingCharacters(in: .whitespacesAndNewlines).count < minChars { return .skip("short") }
        guard let lastLine = before.components(separatedBy: "\n").last,
              !lastLine.trimmingCharacters(in: .whitespaces).isEmpty else { return .skip("empty_line") }

        // Caret position: the right edge of the character before the caret.
        var caret: CGRect?
        if loc > 0, let r = bounds(focused, location: loc - 1, length: 1) {
            caret = CGRect(x: r.maxX, y: r.minY, width: 1, height: r.height)
        }
        if caret == nil, let r = bounds(focused, location: loc, length: 0) {
            caret = CGRect(x: r.minX, y: r.minY, width: 1, height: r.height)
        }
        guard let caretRect = caret else { return .skip("no_bounds") }

        let window = element(attr(appEl, kAXFocusedWindowAttribute))
        let title = window.flatMap { attr($0, kAXTitleAttribute) as? String } ?? ""
        // Browsers: the page URL lives on the enclosing web area.
        var host: String?
        var node: AXUIElement? = focused
        for _ in 0..<40 {
            guard let n = node else { break }
            if (attr(n, kAXRoleAttribute) as? String) == "AXWebArea" {
                if let u = attr(n, "AXURL") as? URL { host = u.host }
                break
            }
            node = element(attr(n, kAXParentAttribute))
        }
        return .ok(ACField(pid: pid, appName: appName, bundleID: bundleID, title: title, urlHost: host,
                           before: before, after: after, axCaret: caretRect))
    }

    /// AX rectangles use a top-left origin on the primary screen; AppKit uses bottom-left.
    private static func cocoaRect(_ r: CGRect) -> NSRect? {
        guard let primary = NSScreen.screens.first else { return nil }
        let rect = NSRect(x: r.minX, y: primary.frame.maxY - r.maxY, width: r.width, height: r.height)
        let probe = NSPoint(x: rect.minX, y: rect.midY)
        guard NSScreen.screens.contains(where: { $0.frame.insetBy(dx: -2, dy: -2).contains(probe) }) else { return nil }
        return rect
    }

    // MARK: Engine round trip

    private func cancelPending() {
        task?.cancel()
        task = nil
        pending = nil
    }

    private func send(_ field: ACField, gen: Int, summoned: Bool, isRetry: Bool = false) {
        cancelPending()
        requestSeq += 1
        let seq = requestSeq
        pending = Pending(seq: seq, field: field, gen: gen, summoned: summoned)
        var body: [String: Any] = ["app": field.appName, "bundle_id": field.bundleID, "title": field.title,
                                   "text_before": String(field.before.suffix(6000)),
                                   "text_after": String(field.after.prefix(2000)), "n": 3]
        if let h = field.urlHost { body["url_host"] = h }
        if summoned { body["summoned"] = true }
        var req = URLRequest(url: app.engine.baseURL.appendingPathComponent("api/autocomplete/suggest"), timeoutInterval: 5)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(app.engine.token, forHTTPHeaderField: "X-Glint-Token")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        let t = URLSession.shared.dataTask(with: req) { [weak self] data, _, _ in
            let json = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            DispatchQueue.main.async { self?.received(json, seq: seq, isRetry: isRetry) }
        }
        task = t
        t.resume()
    }

    private func received(_ json: [String: Any]?, seq: Int, isRetry: Bool) {
        guard let p = pending, p.seq == seq else { return }
        task = nil
        let options = (json?["suggestions"] as? [String] ?? []).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        if options.isEmpty {
            pending = nil
            // The model was slower than the chip budget; its answer is being cached, so ask once more
            // if the user is still sitting at the same spot.
            if json?["quiet_reason"] as? String == "slow", !isRetry,
               let ms = (json?["retry_ms"] as? NSNumber)?.doubleValue {
                let gen = generation
                DispatchQueue.main.asyncAfter(deadline: .now() + ms / 1000) { [weak self] in
                    guard let self, self.generation == gen, self.shown == nil, self.pending == nil else { return }
                    self.send(p.field, gen: gen, summoned: p.summoned, isRetry: true)
                }
            }
            return
        }
        pending = nil
        if p.poisoned { return }
        // Keep only options that agree with what the user typed while we waited.
        let typed = p.typed
        let live = options.compactMap { o -> String? in
            guard o.hasPrefix(typed), o.count > typed.count else { return nil }
            let rest = String(o.dropFirst(typed.count))
            return rest.trimmingCharacters(in: .whitespaces).isEmpty ? nil : rest
        }
        guard !live.isEmpty else { return }
        if typed.isEmpty && generation == p.gen {
            present(live, field: p.field)
            return
        }
        // The caret moved: re-read the field before showing anything.
        let expect = String((p.field.before + typed).suffix(40))
        let gen = generation
        let f = p.field
        axQueue.async { [weak self] in
            let r = Self.readField(pid: f.pid, appName: f.appName, bundleID: f.bundleID, minChars: 1)
            DispatchQueue.main.async {
                guard let self, self.generation == gen, self.shown == nil,
                      case .ok(let fresh) = r, fresh.before.hasSuffix(expect) else { return }
                self.present(live, field: fresh)
            }
        }
    }

    // MARK: Showing, accepting, hiding

    private func present(_ options: [String], field: ACField) {
        guard enabled, let caret = Self.cocoaRect(field.axCaret) else { return }
        // Another app may have come forward while we waited.
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == field.pid else { return }
        let s = Shown(options: options, index: 0, remaining: options[0], field: field, caret: caret)
        shown = s
        syncKeys()
        render(s)
        post(outcome: "shown", s, includeText: false)
        armHideTimer()
    }

    private func syncKeys() {
        let (vis, rem, n) = (shown != nil, shown?.remaining ?? "", shown?.options.count ?? 0)
        keys.with { $0.visible = vis; $0.remaining = rem; $0.options = n }
    }

    private func render(_ s: Shown) {
        let label = acceptKey == "backtick" ? "`" : "tab"
        chip.show(text: s.remaining, head: Self.nextPhrase(s.remaining), style: style, caret: s.caret,
                  keyLabel: label, index: s.index, count: s.acceptedChars > 0 ? 1 : s.options.count)
    }

    private func armHideTimer() {
        hideTimer?.invalidate()
        hideTimer = Timer.scheduledTimer(withTimeInterval: 25, repeats: false) { [weak self] _ in
            self?.hide(outcome: "ignored")
        }
    }

    private func accept(oneWord: Bool) {
        guard var s = shown else { return }
        let piece = oneWord ? Self.nextWord(s.remaining) : Self.nextPhrase(s.remaining)
        guard !piece.isEmpty else { return }
        s.remaining = String(s.remaining.dropFirst(piece.count))
        s.added += piece
        s.acceptedChars += piece.count
        s.options = [s.added + s.remaining]   // once taken, other options no longer apply
        s.index = 0
        generation += 1
        pending = nil
        Self.typeText(piece)
        if s.remaining.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            shown = s
            hide(outcome: "accepted")
            // Offer the next stretch once the inserted text has landed.
            if mode == "automatic" {
                pauseTimer?.invalidate()
                pauseTimer = Timer.scheduledTimer(withTimeInterval: Double(pauseMs) / 1000 + 0.15, repeats: false) { [weak self] _ in
                    self?.requestSuggestion(summoned: false)
                }
            }
            return
        }
        shown = s
        syncKeys()
        armHideTimer()
        reposition(after: 0.09)
    }

    private func cycle() {
        guard var s = shown, s.options.count > 1 else { return }
        s.index = (s.index + 1) % s.options.count
        s.remaining = s.options[s.index]
        shown = s
        syncKeys()
        render(s)
        armHideTimer()
    }

    private func typedAlong(_ ch: String) {
        guard var s = shown, s.remaining.hasPrefix(ch) else { return }
        s.remaining = String(s.remaining.dropFirst(ch.count))
        s.added += ch
        s.options = [s.added + s.remaining]   // the user has committed to this option
        s.index = 0
        generation += 1
        shown = s
        if s.remaining.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            hide(outcome: "ignored")   // the user wrote it all themselves
            return
        }
        armHideTimer()
        reposition(after: 0.05)
    }

    /// After text lands in the field, move the chip to the new caret position.
    private func reposition(after delay: TimeInterval) {
        repositionWork?.cancel()
        guard let s = shown else { return }
        let f = s.field
        let expect = String((f.before + s.added).suffix(40))
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.axQueue.async {
                let r = Self.readField(pid: f.pid, appName: f.appName, bundleID: f.bundleID, minChars: 1)
                DispatchQueue.main.async {
                    guard var cur = self.shown else { return }
                    guard case .ok(let fresh) = r, fresh.before.hasSuffix(expect),
                          let rect = Self.cocoaRect(fresh.axCaret) else {
                        // Text may still be arriving (slow apps); show at the estimate rather than vanish.
                        self.render(cur)
                        return
                    }
                    cur.caret = rect
                    self.shown = cur
                    self.render(cur)
                }
            }
        }
        repositionWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    private func hide(outcome: String) {
        hideTimer?.invalidate()
        repositionWork?.cancel()
        chip.hide()
        guard let s = shown else { syncKeys(); return }
        shown = nil
        syncKeys()
        let final = s.acceptedChars > 0 ? "accepted" : outcome
        post(outcome: final, s, includeText: final != "dismissed")
    }

    private func post(outcome: String, _ s: Shown, includeText: Bool) {
        var body: [String: Any] = ["outcome": outcome, "app": s.field.appName, "bundle_id": s.field.bundleID,
                                   "chars": s.acceptedChars, "shown_ms": Int(Date().timeIntervalSince(s.shownAt) * 1000)]
        if let h = s.field.urlHost { body["url_host"] = h }
        if includeText { body["text"] = String((s.field.before + s.added).suffix(2000)) }
        app.engine.request("api/autocomplete/outcome", body, timeout: 10)
    }

    // MARK: Chunking

    /// Next phrase: up to a clause-ending punctuation mark or four words, with its leading space.
    static func nextPhrase(_ s: String) -> String { take(s, maxWords: 4) }
    static func nextWord(_ s: String) -> String { take(s, maxWords: 1) }

    private static func take(_ s: String, maxWords: Int) -> String {
        let chars = Array(s)
        var i = 0
        while i < chars.count && chars[i].isWhitespace && !chars[i].isNewline { i += 1 }
        var words = 0
        while i < chars.count {
            if chars[i].isNewline { i += 1; break }
            while i < chars.count && !chars[i].isWhitespace { i += 1 }
            words += 1
            if ",.;:!?)".contains(chars[i - 1]) || words >= maxWords { break }
            var j = i
            while j < chars.count && chars[j].isWhitespace && !chars[j].isNewline { j += 1 }
            i = j
        }
        let piece = String(chars[0..<i])
        let rest = String(chars[i...])
        // Don't leave a lone trailing period or space behind.
        if rest.trimmingCharacters(in: .whitespaces.union(.punctuationCharacters)).isEmpty, !rest.contains("\n") { return s }
        return piece
    }

    // MARK: Typing text into the focused app

    /// Types text as keyboard events carrying Unicode strings (at most 16 UTF-16 units each);
    /// line breaks go in as Shift+Return so chat apps don't send the message.
    static func typeText(_ text: String) {
        let src = CGEventSource(stateID: .privateState)
        var buf: [UniChar] = []
        func postKey(_ key: CGKeyCode, flags: CGEventFlags, units: [UniChar]?) {
            for down in [true, false] {
                guard let e = CGEvent(keyboardEventSource: src, virtualKey: key, keyDown: down) else { continue }
                e.flags = flags
                if let units { units.withUnsafeBufferPointer { e.keyboardSetUnicodeString(stringLength: units.count, unicodeString: $0.baseAddress) } }
                e.setIntegerValueField(.eventSourceUserData, value: syntheticMarker)
                e.post(tap: .cghidEventTap)
            }
        }
        func flush() {
            guard !buf.isEmpty else { return }
            postKey(0, flags: [], units: buf)
            buf.removeAll()
        }
        for ch in text {
            if ch.isNewline {
                flush()
                postKey(CGKeyCode(kVK_Return), flags: .maskShift, units: nil)
                continue
            }
            let units = Array(String(ch).utf16)
            if buf.count + units.count > 16 { flush() }
            buf.append(contentsOf: units)
        }
        flush()
    }
}

// MARK: - Suggestion chip (non-activating, click-through)

final class AutocompleteChip {
    private final class ChipPanel: NSPanel {
        override var canBecomeKey: Bool { false }
        override var canBecomeMain: Bool { false }
    }

    private let panel: ChipPanel
    private let card = NSVisualEffectView()
    private let plain = NSView()
    private let label = NSTextField(wrappingLabelWithString: "")
    private let badge = NSTextField(labelWithString: "")

    init() {
        panel = ChipPanel(contentRect: NSRect(x: 0, y: 0, width: 10, height: 10),
                          styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: true)
        panel.level = .popUpMenu
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.ignoresMouseEvents = true
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient, .ignoresCycle]
        card.material = .popover
        card.state = .active
        card.wantsLayer = true
        card.layer?.cornerRadius = 7
        card.layer?.masksToBounds = true
        card.layer?.borderWidth = 0.5
        card.layer?.borderColor = NSColor.separatorColor.cgColor
        label.isSelectable = false
        label.drawsBackground = false
        badge.font = .systemFont(ofSize: 10, weight: .medium)
        badge.textColor = .secondaryLabelColor
        badge.wantsLayer = true
        badge.layer?.cornerRadius = 3
        badge.layer?.borderWidth = 0.5
        badge.layer?.borderColor = NSColor.tertiaryLabelColor.cgColor
        badge.alignment = .center
    }

    func hide() { panel.orderOut(nil) }

    func show(text: String, head: String, style: String, caret: NSRect, keyLabel: String, index: Int, count: Int) {
        let ghost = style == "ghost"
        let size = ghost ? min(22, max(11, caret.height * 0.78)) : 13
        let font = NSFont.systemFont(ofSize: size)
        let shownText = ghost ? text : text.trimmingCharacters(in: .whitespaces)
        let headLen = min((ghost ? head : head.trimmingCharacters(in: .whitespaces)).count, shownText.count)
        let headPart = String(shownText.prefix(headLen))
        let tailPart = String(shownText.dropFirst(headLen))
        let attr = NSMutableAttributedString()
        attr.append(NSAttributedString(string: headPart, attributes: [
            .font: ghost ? font : NSFont.systemFont(ofSize: size, weight: .medium),
            .foregroundColor: ghost ? NSColor.secondaryLabelColor : NSColor.labelColor]))
        attr.append(NSAttributedString(string: tailPart, attributes: [
            .font: font, .foregroundColor: ghost ? NSColor.tertiaryLabelColor : NSColor.secondaryLabelColor]))
        label.attributedStringValue = attr
        badge.stringValue = count > 1 ? " \(keyLabel) · \(index + 1)/\(count) ↓ " : " \(keyLabel) "

        let lineH = ceil(font.ascender - font.descender + font.leading) + 1
        let maxW: CGFloat = ghost ? 560 : 440
        label.maximumNumberOfLines = ghost ? 1 : 3
        label.lineBreakMode = ghost ? .byTruncatingTail : .byWordWrapping
        label.cell?.truncatesLastVisibleLine = true
        let natural = attr.boundingRect(with: NSSize(width: maxW, height: 10_000),
                                        options: [.usesLineFragmentOrigin, .usesFontLeading]).size
        let textW = ceil(min(maxW, ghost ? attr.size().width : natural.width)) + 4
        let textH = ghost ? lineH : min(ceil(natural.height), lineH * 3) + 1
        label.preferredMaxLayoutWidth = textW
        let badgeSize = badge.intrinsicContentSize
        let padX: CGFloat = ghost ? 0 : 8, padY: CGFloat = ghost ? 0 : 4, gap: CGFloat = 6
        let w = padX * 2 + textW + gap + badgeSize.width
        let h = max(textH, badgeSize.height) + padY * 2

        let container = ghost ? plain : card
        container.subviews.forEach { $0.removeFromSuperview() }
        container.frame = NSRect(x: 0, y: 0, width: w, height: h)
        label.frame = NSRect(x: padX, y: h - padY - textH, width: textW, height: textH)
        badge.frame = NSRect(x: padX + textW + gap, y: h - padY - (min(textH, lineH) + badgeSize.height) / 2,
                             width: badgeSize.width, height: badgeSize.height)
        badge.layer?.backgroundColor = ghost ? NSColor.windowBackgroundColor.withAlphaComponent(0.85).cgColor : nil
        container.addSubview(label)
        container.addSubview(badge)
        panel.contentView = container
        panel.hasShadow = !ghost

        var origin: NSPoint
        switch style {
        case "ghost":   // as if already typed, right at the caret
            origin = NSPoint(x: caret.minX + 1, y: caret.midY - h / 2 - 0.5)
        case "normal":  // a chip right after the typed words
            origin = NSPoint(x: caret.minX + 4, y: caret.midY - h / 2)
        default:        // under: hangs just below the current line
            origin = NSPoint(x: caret.minX - 6, y: caret.minY - h - 3)
        }
        let screen = NSScreen.screens.first { $0.frame.contains(NSPoint(x: caret.minX, y: caret.midY)) } ?? NSScreen.main
        if let vf = screen?.visibleFrame {
            if origin.y < vf.minY { origin.y = caret.maxY + 3 }          // no room below: go above the line
            if origin.x + w > vf.maxX { origin.x = max(vf.minX, vf.maxX - w) }
            origin.x = max(vf.minX, origin.x)
            origin.y = min(max(vf.minY, origin.y), vf.maxY - h)
        }
        panel.setFrame(NSRect(origin: origin, size: NSSize(width: w, height: h)), display: true)
        panel.orderFrontRegardless()
    }
}
