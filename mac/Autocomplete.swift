// Autocomplete everywhere: AI continuations while typing in any app, accepted with Tab.
//
// How it works:
//   - A keyboard/mouse event tap runs on its own thread so it never delays typing. It only
//     swallows keys while a suggestion is visible: Tab (or `) takes the next phrase, Shift+Tab
//     one word, Down cycles options, Esc dismisses, Option+Esc turns autocomplete off in that app.
//     Option+Tab asks for a suggestion right away. Right-clicking a suggestion offers the same.
//   - After a typing pause, the focused field is read through Accessibility (text, caret, caret
//     bounds, font) on a background queue. If the caret sits at the end of a line with enough text,
//     no selection, not in a password field, code editor or terminal, the engine is asked for
//     continuations (POST api/autocomplete/suggest). Fields whose text or caret Accessibility can't
//     see (canvas editors, some Electron apps) fall back to the line rebuilt from recent keystrokes.
//   - The suggestion appears in a small click-through panel at the caret: inline ghost text in the
//     field's own font when the caret position is precise, otherwise a chip under the line.
//     Accepted text is typed as keyboard events; outcomes go back to the engine for stats.
//   - Sentences the user finishes typing (Return, send, a long pause, clicking away) are sent to
//     the engine as local samples of their writing voice (api/autocomplete/learn). Never logged.
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
    /// True when axCaret comes from the text itself (not a guess from the field frame).
    var precise = false
    var fontSize: CGFloat?
    var fontName: String?
    /// Placeholder or label of the text box ("Message #design"), as extra context.
    var hint = ""
    /// "ax" (read through Accessibility) or "keys" (rebuilt from recent keystrokes).
    var source = "ax"
}

private enum ACRead {
    case ok(ACField)
    case skip(String)
}

/// The current line as rebuilt from the user's own keystrokes (main thread only).
private struct TypedLine {
    var text = ""
    /// False after edits we can't mirror (paste, undo, forward delete): not a faithful copy.
    var clean = true
    var pid: pid_t = 0
    var appName = ""
    var bundleID = ""
    var excluded = false
    var learnedLen = 0
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
    /// Suggestion panel frame in global display coordinates (top-left origin), for right-clicks.
    var chipRect = CGRect.null
    var swallowRightUp = false

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

private final class MenuAction: NSObject {
    let run: () -> Void
    init(_ run: @escaping () -> Void) { self.run = run }
    @objc func fire() { run() }
}

// MARK: - Feature

final class AutocompleteFeature: GlintFeature {
    unowned let app: AppDelegate

    // Settings
    private var enabled = true
    private var style = "ghost"
    private var mode = "automatic"
    private var acceptKey = "tab"
    private var disabledApps: Set<String> = []
    private var minChars = 12
    private var pauseMs = 350
    private var learnTyping = true

    private let keys = ACKeyState()
    private let axQueue = DispatchQueue(label: "glint.autocomplete.ax", qos: .userInitiated)
    private var tapThread: Thread?
    fileprivate var tapPort: CFMachPort?
    private var tapRetry: Timer?
    private var engineUp = false

    private var generation = 0
    private var pauseTimer: Timer?
    private var hideTimer: Timer?
    private var learnTimer: Timer?
    private var repositionWork: DispatchWorkItem?
    private let chip = AutocompleteChip()
    private var line = TypedLine()
    private var lastHost: (pid: pid_t, host: String?, at: Date) = (0, nil, .distantPast)
    private var lastPrewarm = Date.distantPast

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
    /// Keeps App Nap from throttling the pause timer and the model calls while autocomplete is on.
    private var activity: NSObjectProtocol?

    init(app: AppDelegate) { self.app = app }

    // MARK: GlintFeature

    func start() {
        NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didActivateApplicationNotification,
                                                          object: nil, queue: .main) { [weak self] _ in
            self?.interaction(kind: "switch")
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
        learnTyping = s["autocomplete_learn_typing"] as? Bool ?? true
        let (en, tab, grave) = (enabled, acceptKey != "backtick", acceptKey != "tab")
        keys.with { $0.enabled = en; $0.acceptTab = tab; $0.acceptGrave = grave }
        if enabled && activity == nil {
            activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiatedAllowingIdleSystemSleep, .latencyCritical],
                                                             reason: "Autocomplete responds to typing")
        } else if !enabled, let a = activity {
            ProcessInfo.processInfo.endActivity(a)
            activity = nil
        }
        if !enabled {
            pauseTimer?.invalidate()
            cancelPending()
            hide(outcome: "ignored")
            line = TypedLine()
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
        let types: [CGEventType] = [.keyDown, .leftMouseDown, .rightMouseDown, .rightMouseUp, .otherMouseDown, .scrollWheel]
        let mask = types.reduce(CGEventMask(0)) { $0 | (CGEventMask(1) << $1.rawValue) }
        guard let port = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .defaultTap,
                                           eventsOfInterest: mask, callback: autocompleteTapCallback,
                                           userInfo: Unmanaged.passUnretained(self).toOpaque()) else {
            NSLog("Glint: autocomplete key tap unavailable (Accessibility?)")
            NativeLog.write("ac-tap", "autocomplete: could not start the keyboard tap (grant Accessibility)", every: 60)
            DispatchQueue.main.async { self.tapThread = nil }
            return
        }
        tapPort = port
        NativeLog.write("ac-tap-ok", "autocomplete: keyboard tap running", every: 1)
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
        if type == .rightMouseDown || type == .rightMouseUp {
            let loc = event.location
            let swallow: Bool = keys.with { k in
                if type == .rightMouseUp { defer { k.swallowRightUp = false }; return k.swallowRightUp }
                guard k.visible, k.chipRect.insetBy(dx: -3, dy: -3).contains(loc) else { return false }
                k.swallowRightUp = true
                return true
            }
            if swallow {
                if type == .rightMouseDown { DispatchQueue.main.async { self.showChipMenu(at: loc) } }
                return nil
            }
            if type == .rightMouseDown { DispatchQueue.main.async { self.interaction(kind: "click") } }
            return pass
        }
        guard type == .keyDown else {
            // Scrolling is frequent; it only matters while a suggestion is on screen.
            if type == .scrollWheel {
                if keys.with({ $0.visible }) {
                    let target = pid_t(truncatingIfNeeded: event.getIntegerValueField(.eventTargetUnixProcessID))
                    DispatchQueue.main.async { self.scrolled(targetPID: target) }
                }
            } else {
                DispatchQueue.main.async { self.interaction(kind: "click") }
            }
            return pass
        }
        let code = Int(event.getIntegerValueField(.keyboardEventKeycode))
        let flags = event.flags
        let cmdCtrl = !flags.intersection([.maskCommand, .maskControl]).isEmpty
        let opt = flags.contains(.maskAlternate)
        let shift = flags.contains(.maskShift)
        let ch = Self.printable(event)

        enum Act { case none, summon, accept(Bool), cycle, dismiss, turnOff, typedAlong(String), hideThenType }
        let act: Act = keys.with { k in
            guard k.enabled else { return .none }
            if code == kVK_Tab && opt && !cmdCtrl { return .summon }
            guard k.visible else { return .none }
            let isAccept = (code == kVK_Tab && k.acceptTab) || (code == kVK_ANSI_Grave && k.acceptGrave)
            if isAccept && !cmdCtrl && !opt { return .accept(shift) }
            if code == kVK_DownArrow && k.options > 1 && !cmdCtrl && !opt && !shift { return .cycle }
            if code == kVK_Escape { k.visible = false; return opt ? .turnOff : .dismiss }
            if !cmdCtrl, let ch, k.remaining.hasPrefix(ch) {
                k.remaining.removeFirst(ch.count)
                return .typedAlong(ch)
            }
            k.visible = false
            return .hideThenType
        }
        let key = KeyInfo(ch: ch, code: code, cmdCtrl: cmdCtrl, cmd: flags.contains(.maskCommand), opt: opt, shift: shift)
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
        case .turnOff:
            DispatchQueue.main.async { self.turnOffForCurrentApp() }
            return nil
        case .typedAlong(let c):
            DispatchQueue.main.async { self.typedAlong(c) }
            return pass
        case .hideThenType:
            DispatchQueue.main.async { self.hide(outcome: "ignored", why: "typed"); self.keyActivity(key) }
            return pass
        case .none:
            DispatchQueue.main.async { self.keyActivity(key) }
            return pass
        }
    }

    fileprivate struct KeyInfo { let ch: String?; let code: Int; let cmdCtrl: Bool; let cmd: Bool; let opt: Bool; let shift: Bool }

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

    private func keyActivity(_ key: KeyInfo) {
        generation += 1
        let ch = key.cmdCtrl ? nil : key.ch
        if var p = pending {
            if let ch { p.typed += ch } else { p.poisoned = true }
            pending = p
        }
        trackLine(key)
        pauseTimer?.invalidate()
        guard enabled, mode == "automatic", engineUp else { return }
        // The model slows down after a few idle seconds: wake it as a typing burst starts.
        if ch != nil, !line.excluded, Date().timeIntervalSince(lastPrewarm) > 5 {
            lastPrewarm = Date()
            app.engine.request("api/autocomplete/prewarm", [:], timeout: 5)
        }
        pauseTimer = Timer.scheduledTimer(withTimeInterval: Double(pauseMs) / 1000, repeats: false) { [weak self] _ in
            self?.requestSuggestion(summoned: false)
        }
    }

    /// Clicks, scrolling and app switches end whatever suggestion is in progress. Clicks and app
    /// switches also end the typed line (it may have just been sent with a button).
    private func interaction(kind: String) {
        generation += 1
        pauseTimer?.invalidate()
        pending?.poisoned = true
        if shown != nil { hide(outcome: "ignored", why: kind) }
        if kind != "scroll" { endLine(trigger: kind) }
    }

    /// Scrolling over another app's window changes nothing; scrolling the field's own window moves
    /// the text, so the suggestion follows the caret (it stays where it was if the caret can't be read).
    private func scrolled(targetPID: pid_t) {
        guard let s = shown, targetPID == s.field.pid else { return }
        reposition(after: 0.12)
    }

    private func summon() {
        pauseTimer?.invalidate()
        if shown != nil { hide(outcome: "ignored") }
        requestSuggestion(summoned: true)
    }

    // MARK: The typed line (keystroke mirror used for learning and as a fallback field)

    private func trackLine(_ key: KeyInfo) {
        guard enabled else { return }
        if IsSecureEventInputEnabled() { line = TypedLine(); return }
        guard let front = NSWorkspace.shared.frontmostApplication else { return }
        if front.processIdentifier != line.pid {
            endLine(trigger: "switch")
            line.pid = front.processIdentifier
            line.appName = front.localizedName ?? ""
            line.bundleID = front.bundleIdentifier ?? ""
            line.excluded = excluded(front) || isIDE(front)
        }
        if key.cmdCtrl {
            switch key.code {
            case kVK_Return, kVK_ANSI_KeypadEnter: endLine(trigger: "send")           // Cmd+Return sends in many apps
            case kVK_Delete where key.cmd: line.text = String(line.text.split(separator: "\n", omittingEmptySubsequences: false)
                                                                .dropLast().joined(separator: "\n"))
            case kVK_ANSI_C, kVK_ANSI_B, kVK_ANSI_I, kVK_ANSI_U, kVK_ANSI_S: break      // copy, styles, save
            default: line.clean = false                                                  // paste, undo, select all…
            }
            return
        }
        switch key.code {
        case kVK_Return, kVK_ANSI_KeypadEnter:
            if key.shift || key.opt { line.text += "\n" } else { endLine(trigger: "return") }
        case kVK_Delete:
            if key.opt {
                var t = line.text
                while let last = t.last, last.isWhitespace { t.removeLast() }
                while let last = t.last, !last.isWhitespace { t.removeLast() }
                line.text = t
            } else if !line.text.isEmpty { line.text.removeLast() } else { line.clean = false }
        case kVK_ForwardDelete:
            line.clean = false
        case kVK_LeftArrow, kVK_RightArrow, kVK_UpArrow, kVK_DownArrow, kVK_Home, kVK_End, kVK_PageUp, kVK_PageDown, kVK_Tab:
            endLine(trigger: "nav")
        case kVK_Escape:
            break
        default:
            if let ch = key.ch { line.text += ch }
        }
        if line.text.count > 2000 { line.text = String(line.text.suffix(1500)) }
        learnTimer?.invalidate()
        learnTimer = Timer.scheduledTimer(withTimeInterval: 12, repeats: false) { [weak self] _ in
            self?.learnLine(trigger: "pause")
        }
    }

    /// The line is finished (sent, Return, clicked away): learn from it, then start a new one.
    private func endLine(trigger: String) {
        learnLine(trigger: trigger)
        let (pid, name, bid, ex) = (line.pid, line.appName, line.bundleID, line.excluded)
        line = TypedLine()
        line.pid = pid; line.appName = name; line.bundleID = bid; line.excluded = ex
        learnTimer?.invalidate()
    }

    private func learnLine(trigger: String) {
        let text = line.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard learnTyping, enabled, engineUp, line.clean, !line.excluded, text.count >= 15,
              text.count >= line.learnedLen + 6, !IsSecureEventInputEnabled() else { return }
        line.learnedLen = text.count
        var body: [String: Any] = ["app": line.appName, "bundle_id": line.bundleID, "text": String(text.suffix(2000)),
                                   "trigger": trigger]
        if lastHost.pid == line.pid, Date().timeIntervalSince(lastHost.at) < 120, let h = lastHost.host { body["url_host"] = h }
        app.engine.request("api/autocomplete/learn", body, timeout: 10)
    }

    private struct TypedSnapshot { let text: String; let pid: pid_t }

    private func typedSnapshot() -> TypedSnapshot? {
        guard line.clean, !line.text.isEmpty else { return nil }
        return TypedSnapshot(text: line.text, pid: line.pid)
    }

    // MARK: Reading the focused field

    private static let excludedBundleIDs: Set<String> = [
        "com.apple.dt.Xcode", "com.apple.Terminal", "com.googlecode.iterm2",
        "dev.warp.Warp-Stable", "dev.warp.Warp", "dev.zed.Zed", "dev.zed.Zed-Preview", "com.mitchellh.ghostty",
        "net.kovidgoyal.kitty", "org.alacritty", "io.alacritty", "com.github.wez.wezterm", "co.zeit.hyper",
        "com.panic.Nova", "org.vim.MacVim", "com.qvacua.VimR", "com.apple.ScriptEditor2", "com.google.android.studio",
        "com.bitwarden.desktop", "com.apple.keychainaccess", "com.apple.Passwords", "com.apple.loginwindow",
        "com.lastpass.LastPass", "com.dashlane.dashlanephonefinal", "org.keepassxc.keepassxc",
    ]
    private static let excludedPrefixes = ["com.jetbrains.", "com.sublimetext.", "com.sublimemerge", "com.1password",
                                           "com.agilebits.", "com.nova."]
    private static let excludedNames: Set<String> = [
        "xcode", "terminal", "iterm2", "iterm", "warp", "ghostty", "kitty", "alacritty",
        "wezterm", "hyper", "zed", "nova", "sublime text", "intellij idea", "pycharm", "webstorm", "goland", "clion",
        "rider", "rubymine", "phpstorm", "datagrip", "fleet", "android studio", "1password", "bitwarden",
        "keychain access", "passwords",
    ]
    /// Code editors where chat panels, commit boxes and other prose inputs get suggestions, but the
    /// code editor and the terminal never do.
    private static let ideBundleIDs: Set<String> = [
        "com.microsoft.VSCode", "com.microsoft.VSCodeInsiders", "com.vscodium", "com.todesktop.230313mzl4w4u92",
        "com.exafunction.windsurf",
    ]
    private static let ideNames: Set<String> = ["code", "code - insiders", "vscodium", "cursor", "windsurf"]
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

    private func isIDE(_ a: NSRunningApplication) -> Bool {
        Self.ideBundleIDs.contains(a.bundleIdentifier ?? "") || Self.ideNames.contains((a.localizedName ?? "").lowercased())
    }

    private func requestSuggestion(summoned: Bool) {
        guard enabled, engineUp, shown == nil, let front = NSWorkspace.shared.frontmostApplication else { return }
        let appLabel = front.localizedName ?? "?"
        if !AXIsProcessTrusted() { NativeLog.write("ac-ax", "autocomplete: Accessibility not granted"); return }
        if IsSecureEventInputEnabled() {
            NativeLog.write("ac-secure", "autocomplete: secure keyboard input is on (a password field or a terminal's "
                            + "Secure Keyboard Entry), so nothing can be suggested", every: 30)
            return
        }
        if excluded(front) { NativeLog.write("ac-ex-\(appLabel)", "autocomplete: \(appLabel) is excluded", every: 60); return }
        let gen = generation
        let pid = front.processIdentifier
        let name = front.localizedName ?? ""
        let bid = front.bundleIdentifier ?? ""
        let ide = isIDE(front)
        let bundleURL = front.bundleURL
        let needManual = !manualAXDone.contains(pid)
        if needManual { manualAXDone.insert(pid) }
        let minNeeded = summoned ? 1 : minChars
        let typed = ide ? nil : typedSnapshot().flatMap { $0.pid == pid ? $0 : nil }
        axQueue.async { [weak self] in
            let t0 = Date()
            var r: ACRead
            if needManual {
                Self.enableManualAccessibility(pid: pid, bundleID: bid, bundleURL: bundleURL)
                r = Self.readField(pid: pid, appName: name, bundleID: bid, minChars: minNeeded, ide: ide, typed: typed)
                if case .skip(let why) = r, ["no_focus", "not_text", "no_text"].contains(why) {
                    usleep(350_000)  // Chromium builds its web accessibility tree on first request
                    r = Self.readField(pid: pid, appName: name, bundleID: bid, minChars: minNeeded, ide: ide, typed: typed)
                }
            } else {
                r = Self.readField(pid: pid, appName: name, bundleID: bid, minChars: minNeeded, ide: ide, typed: typed)
            }
            let ms = Int(Date().timeIntervalSince(t0) * 1000)
            switch r {
            case .skip(let why):
                NativeLog.write("ac-skip-\(name)-\(why)", "autocomplete: skip \(why) in \(name)", every: 10)
            case .ok(let f):
                NativeLog.write("ac-ok-\(name)-\(f.source)", "autocomplete: asking in \(name) (read \(ms) ms, "
                                + "\(f.source), caret \(f.precise ? "precise" : "estimated"))", every: 30)
            }
            DispatchQueue.main.async {
                guard let self, gen == self.generation, self.shown == nil else { return }
                if case .ok(let field) = r {
                    self.lastHost = (pid, field.urlHost, Date())
                    self.send(field, gen: gen, summoned: summoned)
                }
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

    private static func rectValue(_ v: CFTypeRef?) -> CGRect? {
        guard let v, CFGetTypeID(v) == AXValueGetTypeID() else { return nil }
        var rect = CGRect.zero
        guard AXValueGetValue(v as! AXValue, .cgRect, &rect), rect.height > 1, rect.height < 400 else { return nil }
        return rect
    }

    private static func bounds(_ el: AXUIElement, location: Int, length: Int) -> CGRect? {
        var range = CFRange(location: location, length: length)
        guard let rv = AXValueCreate(.cfRange, &range) else { return nil }
        var out: CFTypeRef?
        guard AXUIElementCopyParameterizedAttributeValue(el, kAXBoundsForRangeParameterizedAttribute as CFString,
                                                         rv, &out) == .success else { return nil }
        return rectValue(out)
    }

    /// Caret bounds through text markers (Chromium and WebKit web content), when ranges fail.
    private static func markerCaret(_ el: AXUIElement) -> CGRect? {
        guard let marker = attr(el, "AXSelectedTextMarkerRange") else { return nil }
        var out: CFTypeRef?
        guard AXUIElementCopyParameterizedAttributeValue(el, "AXBoundsForTextMarkerRange" as CFString, marker, &out) == .success,
              let r = rectValue(out), r.height < 120 else { return nil }
        return CGRect(x: r.maxX, y: r.minY, width: 1, height: r.height)
    }

    /// Font of the character before the caret (for ghost text that looks typed).
    private static func font(_ el: AXUIElement, at loc: Int) -> (CGFloat?, String?) {
        var range = CFRange(location: loc, length: 1)
        guard loc >= 0, let rv = AXValueCreate(.cfRange, &range) else { return (nil, nil) }
        var out: CFTypeRef?
        guard AXUIElementCopyParameterizedAttributeValue(el, kAXAttributedStringForRangeParameterizedAttribute as CFString,
                                                         rv, &out) == .success,
              let a = out as? NSAttributedString, a.length > 0 else { return (nil, nil) }
        guard let f = a.attribute(NSAttributedString.Key("AXFont"), at: 0, effectiveRange: nil)
                as? [String: Any] else { return (nil, nil) }
        let size = (f["AXFontSize"] as? NSNumber).map { CGFloat($0.doubleValue) }
        return (size.flatMap { $0 >= 6 && $0 <= 72 ? $0 : nil }, f["AXFontName"] as? String)
    }

    private static func frame(_ el: AXUIElement) -> CGRect? {
        guard let pos = attr(el, kAXPositionAttribute), let size = attr(el, kAXSizeAttribute),
              CFGetTypeID(pos) == AXValueGetTypeID(), CFGetTypeID(size) == AXValueGetTypeID() else { return nil }
        var p = CGPoint.zero, sz = CGSize.zero
        guard AXValueGetValue(pos as! AXValue, .cgPoint, &p), AXValueGetValue(size as! AXValue, .cgSize, &sz),
              sz.height > 1 else { return nil }
        return CGRect(origin: p, size: sz)
    }

    /// Code editors: allow prose inputs (chat, commit message, comments), never the code editor
    /// or the integrated terminal. Returns a skip reason, or nil when the input is allowed.
    private static func ideSkipReason(_ focused: AXUIElement) -> String? {
        func label(_ el: AXUIElement) -> String {
            [kAXDescriptionAttribute, kAXTitleAttribute, kAXPlaceholderValueAttribute as String, kAXHelpAttribute,
             kAXRoleDescriptionAttribute, "AXDOMIdentifier", kAXIdentifierAttribute]
                .compactMap { attr(el, $0) as? String }.joined(separator: " ").lowercased()
        }
        func classes(_ el: AXUIElement) -> String {
            ((attr(el, "AXDOMClassList") as? [String]) ?? []).joined(separator: " ").lowercased()
        }
        let allow = ["chat", "commit", "source control", "scm", "message", "prompt", "composer", "cascade", "comment",
                     "copilot", "ask ", "agent", "aichat", "interactive-input", "inline-chat"]
        let deny = ["editor content", "terminal", "xterm", "not accessible", "screen reader optimized"]
        let own = label(focused) + " " + classes(focused)
        if deny.contains(where: { own.contains($0) }) { return "code_editor" }
        if allow.contains(where: { own.contains($0) }) { return nil }
        var node = element(attr(focused, kAXParentAttribute))
        var chain = ""
        for _ in 0..<14 {
            guard let n = node else { break }
            chain += " " + classes(n) + " " + label(n)
            node = element(attr(n, kAXParentAttribute))
        }
        if chain.contains("xterm") || chain.contains("terminal") { return "terminal" }
        if allow.contains(where: { chain.contains($0) }) { return nil }
        return "code_editor"
    }

    /// Reads the focused text field and applies the local trigger rules. Runs off the main thread.
    private static func readField(pid: pid_t, appName: String, bundleID: String, minChars: Int, ide: Bool,
                                  typed: TypedSnapshot?) -> ACRead {
        let appEl = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(appEl, 0.4)
        guard let focused = element(attr(appEl, kAXFocusedUIElementAttribute)) else { return .skip("no_focus") }
        AXUIElementSetMessagingTimeout(focused, 0.4)
        let role = attr(focused, kAXRoleAttribute) as? String ?? ""
        let subrole = attr(focused, kAXSubroleAttribute) as? String ?? ""
        if subrole == "AXSecureTextField" || role == "AXSecureTextField" { return .skip("secure") }
        if subrole == "AXSearchField" { return .skip("search_field") }
        if ide, let why = ideSkipReason(focused) { return .skip(why) }
        let window = element(attr(appEl, kAXFocusedWindowAttribute))
        let title = window.flatMap { attr($0, kAXTitleAttribute) as? String } ?? ""
        let host = urlHost(focused)
        let hint = ([kAXPlaceholderValueAttribute as String, kAXDescriptionAttribute, kAXTitleAttribute]
            .compactMap { attr(focused, $0) as? String }.first { !$0.isEmpty } ?? "").prefix(80)

        func fallback(_ why: String) -> ACRead {
            // Accessibility can't see this field's text or caret: use the line rebuilt from keystrokes.
            guard let t = typed, t.pid == pid else { return .skip(why) }
            let editable = ["AXTextArea", "AXTextField", "AXComboBox", "AXWebArea", "AXGroup"].contains(role)
                && (attr(focused, "AXEditableAncestor") != nil || ["AXTextArea", "AXTextField", "AXComboBox"].contains(role)
                    || { var s: DarwinBoolean = false; AXUIElementIsAttributeSettable(focused, kAXValueAttribute as CFString, &s); return s.boolValue }())
            guard editable else { return .skip(why) }
            let lastLine = t.text.components(separatedBy: "\n").last ?? ""
            guard t.text.trimmingCharacters(in: .whitespacesAndNewlines).count >= minChars,
                  !lastLine.trimmingCharacters(in: .whitespaces).isEmpty else { return .skip("short") }
            guard let f = frame(focused), f.height <= 90 else { return .skip("\(why)_no_anchor") }
            let lineH = min(22, max(14, f.height))
            // A tiny focus target (canvas editors park it at the caret) is the caret; else the box's last line.
            let caret = f.width < 40 ? CGRect(x: f.maxX, y: f.midY - lineH / 2, width: 1, height: lineH)
                                     : CGRect(x: f.minX + 8, y: f.maxY - lineH - 4, width: 1, height: lineH)
            var field = ACField(pid: pid, appName: appName, bundleID: bundleID, title: title, urlHost: host,
                                before: t.text, after: "", axCaret: caret)
            field.hint = String(hint)
            field.source = "keys"
            return .ok(field)
        }

        let textRoles: Set<String> = ["AXTextArea", "AXTextField", "AXComboBox"]
        var settable: DarwinBoolean = false
        AXUIElementIsAttributeSettable(focused, kAXValueAttribute as CFString, &settable)
        guard textRoles.contains(role) || settable.boolValue else { return fallback("not_text") }
        guard let value = attr(focused, kAXValueAttribute) as? String else { return fallback("no_text") }
        guard let sel = rangeValue(attr(focused, kAXSelectedTextRangeAttribute)) else { return fallback("no_caret") }
        if sel.length > 0 { return .skip("selection") }
        let ns = value as NSString
        let loc = sel.location
        guard loc >= 0, loc <= ns.length, ns.length < 300_000 else { return .skip("bad_caret") }
        if ns.length == 0 { return fallback("short") }   // canvas editors expose an empty field
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
        var precise = false
        if loc > 0, let r = bounds(focused, location: loc - 1, length: 1), r.width < 200, r.height < 120 {
            caret = CGRect(x: r.maxX, y: r.minY, width: 1, height: r.height)
            precise = true
        }
        if caret == nil, let r = bounds(focused, location: loc, length: 0), r.height < 120 {
            caret = CGRect(x: r.minX, y: r.minY, width: 1, height: r.height)
            precise = true
        }
        if caret == nil, let r = markerCaret(focused) {
            caret = r
            precise = true
        }
        if caret == nil, let f = frame(focused), f.height > 4 {
            // No caret geometry (some web text boxes): anchor to the field's lower-left corner.
            let lineH = min(22, max(14, f.height))
            caret = CGRect(x: f.minX + 8, y: f.minY + f.height - lineH - 4, width: 1, height: lineH)
        }
        guard let caretRect = caret else { return fallback("no_bounds") }
        var field = ACField(pid: pid, appName: appName, bundleID: bundleID, title: title, urlHost: host,
                            before: before, after: after, axCaret: caretRect)
        field.precise = precise
        if precise { (field.fontSize, field.fontName) = font(focused, at: loc - 1) }
        field.hint = String(hint)
        return .ok(field)
    }

    /// Browsers: the page URL lives on the enclosing web area.
    private static func urlHost(_ focused: AXUIElement) -> String? {
        var node: AXUIElement? = focused
        for _ in 0..<40 {
            guard let n = node else { break }
            if (attr(n, kAXRoleAttribute) as? String) == "AXWebArea" {
                if let u = attr(n, "AXURL") as? URL { return u.host }
                return nil
            }
            node = element(attr(n, kAXParentAttribute))
        }
        return nil
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
                                   "text_after": String(field.after.prefix(2000)), "n": 3,
                                   "source": field.source, "caret_precise": field.precise]
        if let h = field.urlHost { body["url_host"] = h }
        if !field.hint.isEmpty { body["field_hint"] = field.hint }
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
            // The model was slower than the budget; its answer is being cached, so ask once more
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
        if p.poisoned { NativeLog.write("ac-drop", "autocomplete: answer dropped (clicked or edited while waiting)", every: 0); return }
        // Keep only options that agree with what the user typed while we waited.
        let typed = p.typed
        let live = options.compactMap { o -> String? in
            guard o.hasPrefix(typed), o.count > typed.count else { return nil }
            let rest = String(o.dropFirst(typed.count))
            return rest.trimmingCharacters(in: .whitespaces).isEmpty ? nil : rest
        }
        guard !live.isEmpty else { NativeLog.write("ac-drop", "autocomplete: answer dropped (typing went another way)", every: 0); return }
        if typed.isEmpty && generation == p.gen {
            present(live, field: p.field)
            return
        }
        let expect = String((p.field.before + typed).suffix(40))
        if p.field.source == "keys" {
            // No caret to re-read: trust the keystroke mirror.
            guard line.clean, line.text.hasSuffix(expect) else { return }
            present(live, field: p.field)
            return
        }
        // The caret moved: re-read the field before showing anything.
        let gen = generation
        let f = p.field
        axQueue.async { [weak self] in
            let r = Self.readField(pid: f.pid, appName: f.appName, bundleID: f.bundleID, minChars: 1, ide: false, typed: nil)
            DispatchQueue.main.async {
                guard let self, self.generation == gen, self.shown == nil,
                      case .ok(let fresh) = r, fresh.before.hasSuffix(expect) else { return }
                self.present(live, field: fresh)
            }
        }
    }

    // MARK: Showing, accepting, hiding

    private func present(_ options: [String], field: ACField) {
        guard enabled, let caret = Self.cocoaRect(field.axCaret) else {
            NativeLog.write("ac-drop", "autocomplete: answer dropped (caret off screen)", every: 0); return
        }
        // Another app may have come forward while we waited.
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == field.pid else {
            NativeLog.write("ac-drop", "autocomplete: answer dropped (another app is in front)", every: 0); return
        }
        NativeLog.write("ac-show", "autocomplete: showing \(effectiveStyle(field)) in \(field.appName) "
                        + "(\(options.count) option\(options.count == 1 ? "" : "s"), \(field.source))", every: 0)
        let s = Shown(options: options, index: 0, remaining: options[0], field: field, caret: caret)
        shown = s
        syncKeys()
        render(s)
        post(outcome: "shown", s, includeText: false)
        armHideTimer()
    }

    private func syncKeys() {
        let (vis, rem, n) = (shown != nil, shown?.remaining ?? "", shown?.options.count ?? 0)
        keys.with { $0.visible = vis; $0.remaining = rem; $0.options = n; if !vis { $0.chipRect = .null } }
    }

    /// Ghost text needs a precise caret; otherwise the chip hangs under the line.
    private func effectiveStyle(_ f: ACField) -> String {
        style == "ghost" && !f.precise ? "under" : style
    }

    private func render(_ s: Shown) {
        let label = acceptKey == "backtick" ? "`" : "Tab"
        let frame = chip.show(text: s.remaining, head: Self.nextPhrase(s.remaining), style: effectiveStyle(s.field),
                              caret: s.caret, keyLabel: label, index: s.index,
                              count: s.acceptedChars > 0 ? 1 : s.options.count,
                              fontSize: s.field.fontSize, fontName: s.field.fontName)
        if let primary = NSScreen.screens.first {
            let cg = CGRect(x: frame.minX, y: primary.frame.maxY - frame.maxY, width: frame.width, height: frame.height)
            keys.with { $0.chipRect = cg }
        }
    }

    private func armHideTimer() {
        hideTimer?.invalidate()
        hideTimer = Timer.scheduledTimer(withTimeInterval: 25, repeats: false) { [weak self] _ in
            self?.hide(outcome: "ignored", why: "timeout")
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
        if line.pid == s.field.pid { line.text += piece }
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
        if line.pid == s.field.pid { line.text += ch }
        if s.remaining.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            hide(outcome: "ignored")   // the user wrote it all themselves
            return
        }
        armHideTimer()
        reposition(after: 0.05)
    }

    /// After text lands in the field, move the suggestion to the new caret position.
    private func reposition(after delay: TimeInterval) {
        repositionWork?.cancel()
        guard let s = shown else { return }
        if s.field.source == "keys" { render(s); return }   // no caret to follow
        let f = s.field
        let expect = String((f.before + s.added).suffix(40))
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.axQueue.async {
                let r = Self.readField(pid: f.pid, appName: f.appName, bundleID: f.bundleID, minChars: 1, ide: false, typed: nil)
                DispatchQueue.main.async {
                    guard var cur = self.shown else { return }
                    guard case .ok(let fresh) = r, fresh.before.hasSuffix(expect),
                          let rect = Self.cocoaRect(fresh.axCaret) else {
                        // Text may still be arriving (slow apps); show at the estimate rather than vanish.
                        self.render(cur)
                        return
                    }
                    cur.caret = rect
                    cur.field.precise = fresh.precise
                    if fresh.fontSize != nil { cur.field.fontSize = fresh.fontSize; cur.field.fontName = fresh.fontName }
                    self.shown = cur
                    self.render(cur)
                }
            }
        }
        repositionWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    private func hide(outcome: String, why: String = "") {
        hideTimer?.invalidate()
        repositionWork?.cancel()
        chip.hide()
        guard let s = shown else { syncKeys(); return }
        shown = nil
        syncKeys()
        let final = s.acceptedChars > 0 ? "accepted" : outcome
        NativeLog.write("ac-hide", "autocomplete: \(final)\(why.isEmpty ? "" : " (\(why))") after \(Int(Date().timeIntervalSince(s.shownAt) * 1000)) ms"
                        + (s.acceptedChars > 0 ? " (\(s.acceptedChars) chars taken)" : ""), every: 0)
        post(outcome: final, s, includeText: final != "dismissed" && s.field.source == "ax")
    }

    private func post(outcome: String, _ s: Shown, includeText: Bool) {
        var body: [String: Any] = ["outcome": outcome, "app": s.field.appName, "bundle_id": s.field.bundleID,
                                   "chars": s.acceptedChars, "shown_ms": Int(Date().timeIntervalSince(s.shownAt) * 1000)]
        if let h = s.field.urlHost { body["url_host"] = h }
        if includeText { body["text"] = String((s.field.before + s.added).suffix(2000)) }
        app.engine.request("api/autocomplete/outcome", body, timeout: 10)
    }

    // MARK: Turning off per app

    private func showChipMenu(at cgPoint: CGPoint) {
        guard let s = shown, let primary = NSScreen.screens.first else { return }
        let name = s.field.appName.isEmpty ? "this app" : s.field.appName
        let menu = NSMenu()
        let off = MenuAction { [weak self] in self?.turnOffForCurrentApp() }
        let dismiss = MenuAction { [weak self] in self?.hide(outcome: "dismissed") }
        let i1 = NSMenuItem(title: "Turn off autocomplete in \(name)", action: #selector(MenuAction.fire), keyEquivalent: "")
        i1.target = off
        let i2 = NSMenuItem(title: "Dismiss suggestion", action: #selector(MenuAction.fire), keyEquivalent: "")
        i2.target = dismiss
        menu.addItem(i1)
        menu.addItem(i2)
        withExtendedLifetime((off, dismiss)) {
            _ = menu.popUp(positioning: nil, at: NSPoint(x: cgPoint.x, y: primary.frame.maxY - cgPoint.y), in: nil)
        }
    }

    private func turnOffForCurrentApp() {
        guard let s = shown else { return }
        let name = s.field.appName
        let caret = s.caret
        hide(outcome: "dismissed")
        guard !name.isEmpty else { return }
        var list = (app.settings["autocomplete_disabled_apps"] as? [String]) ?? Array(disabledApps)
        if !list.contains(where: { $0.lowercased() == name.lowercased() }) { list.append(name) }
        disabledApps.insert(name.lowercased())
        line.excluded = line.pid == s.field.pid ? true : line.excluded
        NativeLog.write("ac-off-\(name)", "autocomplete: turned off in \(name) (Option+Esc or menu)", every: 1)
        app.engine.request("api/settings", ["autocomplete_disabled_apps": list]) { [weak self] json, _ in
            guard let self, let st = json?["settings"] as? [String: Any] else { return }
            self.app.settings = st
            self.apply(st)
        }
        chip.showNotice("Autocomplete off in \(name) · turn it back on in Glint Settings", caret: caret)
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
    private var noticeTimer: Timer?

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
        badge.textColor = .secondaryLabelColor
        badge.wantsLayer = true
        badge.layer?.cornerRadius = 3
        badge.layer?.borderWidth = 0.5
        badge.layer?.borderColor = NSColor.tertiaryLabelColor.cgColor
        badge.alignment = .center
    }

    func hide() {
        noticeTimer?.invalidate()
        panel.orderOut(nil)
    }

    /// Shows the suggestion and returns the panel frame (Cocoa screen coordinates).
    @discardableResult
    func show(text: String, head: String, style: String, caret: NSRect, keyLabel: String, index: Int, count: Int,
              fontSize: CGFloat? = nil, fontName: String? = nil) -> NSRect {
        noticeTimer?.invalidate()
        let ghost = style == "ghost"
        // Ghost text uses the field's own font when Accessibility reports it, else one sized to the line.
        let size = ghost ? (fontSize ?? min(28, max(10, (caret.height / 1.22).rounded()))) : 13
        let font = ghost ? (fontName.flatMap { NSFont(name: $0, size: size) } ?? NSFont.systemFont(ofSize: size))
                         : NSFont.systemFont(ofSize: size)
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
        badge.font = .systemFont(ofSize: ghost ? 9 : 10, weight: .medium)
        badge.stringValue = count > 1 ? " \(keyLabel) · \(index + 1)/\(count) ↓ " : " \(keyLabel) "

        let lineH = ceil(font.ascender - font.descender + font.leading) + 1
        let maxW: CGFloat = ghost ? 620 : 440
        label.maximumNumberOfLines = ghost ? 1 : 3
        label.lineBreakMode = ghost ? .byTruncatingTail : .byWordWrapping
        label.cell?.truncatesLastVisibleLine = true
        let natural = attr.boundingRect(with: NSSize(width: maxW, height: 10_000),
                                        options: [.usesLineFragmentOrigin, .usesFontLeading]).size
        let textW = ceil(min(maxW, ghost ? attr.size().width : natural.width)) + 4
        let textH = ghost ? lineH : min(ceil(natural.height), lineH * 3) + 1
        label.preferredMaxLayoutWidth = textW
        let badgeSize = badge.intrinsicContentSize
        let padX: CGFloat = ghost ? 0 : 8, padY: CGFloat = ghost ? 0 : 4, gap: CGFloat = ghost ? 4 : 6
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
        case "ghost":   // as if already typed, right at the caret (the label cell insets text by 2 pt)
            origin = NSPoint(x: caret.minX - 1, y: caret.midY - h / 2)
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
        let frame = NSRect(origin: origin, size: NSSize(width: w, height: h))
        panel.setFrame(frame, display: true)
        panel.orderFrontRegardless()
        return frame
    }

    /// A short message in the chip's place (e.g. after turning autocomplete off in an app).
    func showNotice(_ text: String, caret: NSRect) {
        show(text: text, head: "", style: "under", caret: caret, keyLabel: "✓", index: 0, count: 1)
        noticeTimer = Timer.scheduledTimer(withTimeInterval: 2.6, repeats: false) { [weak self] _ in
            self?.panel.orderOut(nil)
        }
    }
}
