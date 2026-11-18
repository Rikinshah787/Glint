// Assist: a two-modifier chord (default Option+Command) overloaded by tap / hold / double-tap.
//
//   tap         draft for the focused text field (floating draft panel near the caret)
//   hold        open Quick Ask
//   double-tap  toggle the edge panel docked to the right screen edge
//
// Also: proactive suggestion cards (a small cue dot that expands into a card), and voice input for
// Quick Ask (record → /api/dictation → transcript into the Quick Ask box).
// UI pages live in web/features/assist.html (?mode=draft|card|edge); logic in engine/features/assist.py.
import AppKit
import ApplicationServices
import AVFoundation
import Carbon.HIToolbox
import Speech
import WebKit

// MARK: - Small helpers

final class AssistPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    static func make(size: NSSize, level: NSWindow.Level = .floating) -> AssistPanel {
        let p = AssistPanel(contentRect: NSRect(origin: .zero, size: size),
                            styleMask: [.nonactivatingPanel, .borderless, .fullSizeContentView],
                            backing: .buffered, defer: false)
        p.level = level
        p.isOpaque = false
        p.backgroundColor = .clear
        p.hasShadow = true
        p.hidesOnDeactivate = false
        p.isReleasedWhenClosed = false
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        return p
    }
}

/// Clicks land on the first click even though the panel isn't key yet.
final class AssistWebView: WKWebView {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// The dot shown before a card expands; hovering or clicking it opens the card.
final class AssistCueView: NSView {
    var onActivate: (() -> Void)?
    private var tracking: NSTrackingArea?
    private var hovered = false { didSet { needsDisplay = true } }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let t = tracking { removeTrackingArea(t) }
        let t = NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                               owner: self, userInfo: nil)
        addTrackingArea(t)
        tracking = t
    }

    override func draw(_ dirtyRect: NSRect) {
        let r = bounds.insetBy(dx: hovered ? 1 : 3, dy: hovered ? 1 : 3)
        NSColor.controlAccentColor.withAlphaComponent(0.25).setFill()
        NSBezierPath(ovalIn: r).fill()
        NSColor.controlAccentColor.setFill()
        NSBezierPath(ovalIn: r.insetBy(dx: r.width * 0.25, dy: r.height * 0.25)).fill()
    }

    override func mouseEntered(with event: NSEvent) {
        hovered = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
            guard let self, self.hovered else { return }
            self.onActivate?()
        }
    }

    override func mouseExited(with event: NSEvent) { hovered = false }
    override func mouseDown(with event: NSEvent) { onActivate?() }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// The slim tab on the right screen edge that opens the edge panel.
final class AssistHandleView: NSView {
    var onClick: (() -> Void)?
    private var tracking: NSTrackingArea?
    private var hovered = false { didSet { needsDisplay = true } }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let t = tracking { removeTrackingArea(t) }
        let t = NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                               owner: self, userInfo: nil)
        addTrackingArea(t)
        tracking = t
    }

    override func draw(_ dirtyRect: NSRect) {
        let w: CGFloat = hovered ? 8 : 5
        let r = NSRect(x: bounds.maxX - w, y: 0, width: w, height: bounds.height)
        (hovered ? NSColor.controlAccentColor : NSColor.labelColor.withAlphaComponent(0.35)).setFill()
        NSBezierPath(roundedRect: r, xRadius: w / 2, yRadius: w / 2).fill()
    }

    override func mouseEntered(with event: NSEvent) { hovered = true }
    override func mouseExited(with event: NSEvent) { hovered = false }
    override func mouseDown(with event: NSEvent) { onClick?() }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// What the user is focused on, read through Accessibility.
struct AssistFocus {
    var app: NSRunningApplication
    var appName: String
    var title: String
    var element: AXUIElement?
    var role: String
    var value: String
    var secure: Bool
    var caret: NSRect?      // Cocoa screen coordinates
    var field: NSRect?
    var window: NSRect?
    var editable: Bool { ["AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"].contains(role) }
}

enum AssistAX {
    static func attr(_ el: AXUIElement, _ name: String) -> CFTypeRef? {
        var v: CFTypeRef?
        return AXUIElementCopyAttributeValue(el, name as CFString, &v) == .success ? v : nil
    }

    static func string(_ el: AXUIElement, _ name: String) -> String? { attr(el, name) as? String }

    static func axValue(_ v: CFTypeRef?) -> AXValue? {
        guard let v, CFGetTypeID(v) == AXValueGetTypeID() else { return nil }
        return (v as! AXValue)
    }

    static func frame(_ el: AXUIElement) -> NSRect? {
        var pt = CGPoint.zero, size = CGSize.zero
        guard let p = axValue(attr(el, kAXPositionAttribute)), AXValueGetValue(p, .cgPoint, &pt),
              let s = axValue(attr(el, kAXSizeAttribute)), AXValueGetValue(s, .cgSize, &size) else { return nil }
        return toCocoa(CGRect(origin: pt, size: size))
    }

    /// AX uses top-left-origin global coordinates; AppKit uses bottom-left of the primary screen.
    static func toCocoa(_ r: CGRect) -> NSRect {
        let h = NSScreen.screens.first?.frame.height ?? 0
        return NSRect(x: r.minX, y: h - r.maxY, width: r.width, height: r.height)
    }

    static func caretRect(_ el: AXUIElement) -> NSRect? {
        guard let range = attr(el, kAXSelectedTextRangeAttribute) else { return nil }
        var out: CFTypeRef?
        guard AXUIElementCopyParameterizedAttributeValue(el, kAXBoundsForRangeParameterizedAttribute as CFString,
                                                         range, &out) == .success,
              let v = axValue(out) else { return nil }
        var r = CGRect.zero
        guard AXValueGetValue(v, .cgRect, &r), r.width >= 0, r.height > 0, r.height < 400 else { return nil }
        return toCocoa(r)
    }

    static func selectAll(_ el: AXUIElement, length: Int) -> Bool {
        var range = CFRange(location: 0, length: length)
        guard let v = AXValueCreate(.cfRange, &range) else { return false }
        return AXUIElementSetAttributeValue(el, kAXSelectedTextRangeAttribute as CFString, v) == .success
    }

    static func read() -> AssistFocus? {
        guard let front = NSWorkspace.shared.frontmostApplication else { return nil }
        let appEl = AXUIElementCreateApplication(front.processIdentifier)
        var title = ""
        var windowRect: NSRect?
        if let w = attr(appEl, kAXFocusedWindowAttribute) {
            let win = w as! AXUIElement
            title = string(win, kAXTitleAttribute) ?? ""
            windowRect = frame(win)
        }
        var focused = attr(appEl, kAXFocusedUIElementAttribute)
        if focused == nil {  // Chromium/Electron apps expose their tree only on request
            AXUIElementSetAttributeValue(appEl, "AXManualAccessibility" as CFString, kCFBooleanTrue)
            focused = attr(appEl, kAXFocusedUIElementAttribute)
        }
        var info = AssistFocus(app: front, appName: front.localizedName ?? "", title: title, element: nil,
                               role: "", value: "", secure: false, caret: nil, field: nil, window: windowRect)
        guard let f = focused else { return info }
        let el = f as! AXUIElement
        info.element = el
        info.role = string(el, kAXRoleAttribute) ?? ""
        info.secure = string(el, kAXSubroleAttribute) == (kAXSecureTextFieldSubrole as String)
        if !info.secure {
            info.value = string(el, kAXValueAttribute) ?? ""
            info.caret = caretRect(el)
        }
        info.field = frame(el)
        return info
    }
}

// MARK: - Feature

final class AssistFeature: GlintFeature {
    unowned let app: AppDelegate
    init(app: AppDelegate) { self.app = app }

    // Settings
    private var enabled = true
    private var cardsEnabled = true
    private var cardInterval: TimeInterval = 90
    private var edgeHandleEnabled = true
    private var chord: NSEvent.ModifierFlags = [.option, .command]

    // Chord state
    private var chordDown = false
    private var chordCancelled = false
    private var chordConsumed = false
    private var firstModifierAt: Date?
    private var holdTimer: Timer?
    private var pendingTap: DispatchWorkItem?
    private var lastTapUpAt: Date?
    private static let holdThreshold: TimeInterval = 0.35
    private static let doubleTapWindow: TimeInterval = 0.3

    // Surfaces
    private var draftPanel: AssistPanel?
    private var edgePanel: AssistPanel?
    private var cardPanel: AssistPanel?
    private var cuePanel: AssistPanel?
    private var handlePanel: AssistPanel?
    private var webs: [String: WKWebView] = [:]
    private var loaded: Set<String> = []
    private var pendingJS: [String: [String]] = [:]
    private let pill = DictationPill()

    // Draft target
    private var draftFocus: AssistFocus?

    // Cards
    private var cardTimer: Timer?
    private var cardCheckWork: DispatchWorkItem?
    private var cardBusy = false
    private var currentCard: [String: Any]?
    private var cardTarget: NSRunningApplication?
    private var cueHideWork: DispatchWorkItem?
    private var cardHideWork: DispatchWorkItem?
    private var cardsPausedUntil = Date.distantPast
    private var undoActions: [String: () -> Void] = [:]

    // Quick Ask voice
    private var voiceRecorder: AVAudioRecorder?
    private var voiceURL: URL?
    private var voiceMeter: Timer?
    private var voiceStarted = Date()

    // MARK: lifecycle

    func start() {
        NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged) { [weak self] e in self?.flagsChanged(e.modifierFlags) }
        NSEvent.addLocalMonitorForEvents(matching: .flagsChanged) { [weak self] e in self?.flagsChanged(e.modifierFlags); return e }
        NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] _ in self?.otherKey() }
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] e in self?.otherKey(); return e }
        // ⌥⌘-drag and ⌥⌘-click are Finder/app gestures, not the assist chord.
        NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in self?.otherKey() }
        NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didActivateApplicationNotification,
                                                          object: nil, queue: .main) { [weak self] n in
            self?.appActivated(n.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication)
        }
        NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
                                               object: nil, queue: .main) { [weak self] _ in self?.layoutHandle() }
    }

    func engineReady() {
        rescheduleCardTimer()
        layoutHandle()
    }

    func settingsChanged(_ s: [String: Any]) {
        enabled = s["assist_enabled"] as? Bool ?? true
        cardsEnabled = s["assist_cards_enabled"] as? Bool ?? true
        edgeHandleEnabled = s["assist_edge_panel"] as? Bool ?? true
        // Control+Option belongs to Dictate, so it can never be the assist chord.
        chord = (s["assist_chord"] as? String) == "cmd_ctrl" ? [.control, .command] : [.option, .command]
        let interval = max(20, (s["assist_card_interval_s"] as? NSNumber)?.doubleValue ?? 90)
        if interval != cardInterval || cardTimer == nil {
            cardInterval = interval
            rescheduleCardTimer()
        }
        if !enabled {
            hideDraft()
            hideEdge()
            hideCard(outcome: "ignored")
        } else if !cardsEnabled {
            hideCard(outcome: "ignored")
        }
        layoutHandle()
    }

    func handle(action: String, body: [String: Any]) -> Bool {
        guard action.hasPrefix("assist.") else { return false }
        let panel = body["panel"] as? String ?? ""
        switch action {
        case "assist.ready":
            loaded.insert(panel)
            for js in pendingJS.removeValue(forKey: panel) ?? [] { webs[panel]?.evaluateJavaScript(js) }
        case "assist.resize": resize(panel: panel, height: (body["height"] as? NSNumber)?.doubleValue ?? 0)
        case "assist.close":
            switch panel {
            case "draft": hideDraft()
            case "edge": hideEdge()
            case "card": hideCard(outcome: nil)
            default: break
            }
        case "assist.insert": insertDraft(text: body["text"] as? String ?? "", mode: body["mode"] as? String ?? "write")
        case "assist.copy":
            copyToClipboard(body["text"] as? String ?? "")
            pill.show("Copied", autoHide: 0.9)
        case "assist.draftNow":
            hideEdge()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { self.startDraft() }
        case "assist.quickAsk":
            hideEdge()
            if app.quickPanel?.isVisible != true { app.toggleQuickAsk() }
        case "assist.openMain":
            hideEdge()
            app.showMain(path: body["path"] as? String)
        case "assist.pauseCards":
            let minutes = (body["minutes"] as? NSNumber)?.doubleValue ?? 60
            cardsPausedUntil = Date().addingTimeInterval(max(0, minutes) * 60)
            if minutes > 0 { hideCard(outcome: "dismissed") }
            pill.show(minutes > 0 ? "Suggestions paused" : "Suggestions resumed", autoHide: 1.2)
        case "assist.checkCard": checkForCard(force: true)
        case "assist.cardAction": cardAction(body)
        case "assist.cardTouched": scheduleCardHide()
        case "assist.undo": undo(body["undo_token"] as? String ?? "")
        case "assist.voice.start": startVoice()
        case "assist.voice.stop": stopVoice()
        case "assist.voice.cancel": cancelVoice()
        default: return false
        }
        return true
    }

    // MARK: chord detection

    private func flagsChanged(_ raw: NSEvent.ModifierFlags) {
        let flags = raw.intersection([.control, .option, .command, .shift])
        if flags.isEmpty { firstModifierAt = nil } else if firstModifierAt == nil { firstModifierAt = Date() }
        let isChord = flags == chord
        if isChord && !chordDown {
            chordDown = true
            chordCancelled = false
            chordConsumed = false
            // Both keys must go down together; a modifier held for a while is someone else's shortcut.
            guard enabled, Date().timeIntervalSince(firstModifierAt ?? Date()) < 0.8 else {
                chordCancelled = true
                return
            }
            holdTimer = Timer.scheduledTimer(withTimeInterval: Self.holdThreshold, repeats: false) { [weak self] _ in
                self?.chordHeld()
            }
        } else if !isChord && chordDown {
            chordDown = false
            holdTimer?.invalidate()
            holdTimer = nil
            if !flags.isSubset(of: chord) { chordCancelled = true }  // an extra modifier joined
            if !chordCancelled && !chordConsumed { chordTapped() }
        }
    }

    private func otherKey() {
        guard chordDown else { return }
        chordCancelled = true  // a real shortcut like ⌥⌘Esc, not the assist gesture
        holdTimer?.invalidate()
        holdTimer = nil
    }

    private func chordHeld() {
        guard chordDown, !chordCancelled else { return }
        chordConsumed = true
        pendingTap?.cancel()
        pendingTap = nil
        if app.quickPanel?.isVisible != true { app.toggleQuickAsk() }
    }

    private func chordTapped() {
        let now = Date()
        if let p = pendingTap, let last = lastTapUpAt, now.timeIntervalSince(last) < Self.doubleTapWindow + Self.holdThreshold {
            p.cancel()
            pendingTap = nil
            lastTapUpAt = nil
            toggleEdge()
            return
        }
        lastTapUpAt = now
        let work = DispatchWorkItem { [weak self] in
            self?.pendingTap = nil
            self?.tapAction()
        }
        pendingTap = work
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.doubleTapWindow, execute: work)
    }

    private func tapAction() {
        if draftPanel?.isVisible == true {
            run("draft", "window.assistDraftAgain && window.assistDraftAgain()")
        } else {
            startDraft()
        }
    }

    // MARK: web surfaces

    private func web(_ panel: String, in window: AssistPanel) -> WKWebView {
        if let w = webs[panel] { return w }
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(app.bridge, name: "glint")
        let wv = AssistWebView(frame: window.contentView?.bounds ?? .zero, configuration: cfg)
        wv.setValue(false, forKey: "drawsBackground")
        wv.autoresizingMask = [.width, .height]
        window.contentView = wv
        var comps = URLComponents(url: app.engine.baseURL.appendingPathComponent("features/assist.html"),
                                  resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "mode", value: panel)]
        wv.load(URLRequest(url: comps.url!))
        webs[panel] = wv
        return wv
    }

    /// Run JS in a panel's page, queueing it until the page reports it is ready.
    private func run(_ panel: String, _ js: String) {
        if loaded.contains(panel), let w = webs[panel] { w.evaluateJavaScript(js) } else { pendingJS[panel, default: []].append(js) }
    }

    private func json(_ obj: Any) -> String {
        guard let d = try? JSONSerialization.data(withJSONObject: obj, options: [.fragmentsAllowed]),
              let s = String(data: d, encoding: .utf8) else { return "null" }
        return s
    }

    private func ensurePanel(_ name: String, size: NSSize, level: NSWindow.Level = .floating) -> AssistPanel? {
        guard app.engine.ready else { return nil }
        let existing: AssistPanel?
        switch name {
        case "draft": existing = draftPanel
        case "edge": existing = edgePanel
        case "card": existing = cardPanel
        default: existing = nil
        }
        if let existing { return existing }
        let p = AssistPanel.make(size: size, level: level)
        _ = web(name, in: p)
        switch name {
        case "draft": draftPanel = p
        case "edge": edgePanel = p
        case "card": cardPanel = p
        default: break
        }
        return p
    }

    private func resize(panel: String, height: Double) {
        let p: AssistPanel?
        switch panel {
        case "draft": p = draftPanel
        case "card": p = cardPanel
        default: p = nil
        }
        guard let p, height > 40 else { return }
        var f = p.frame
        let screen = (p.screen ?? NSScreen.main)?.visibleFrame ?? f
        let h = min(CGFloat(height), screen.height - 40)
        f.origin.y += f.height - h  // grow downward from the top edge
        f.size.height = h
        if f.minY < screen.minY + 8 { f.origin.y = screen.minY + 8 }
        p.setFrame(f, display: true)
    }

    private func clamp(_ r: NSRect) -> NSRect {
        let screen = (NSScreen.screens.first { $0.frame.intersects(r) } ?? NSScreen.main)?.visibleFrame ?? r
        var f = r
        f.origin.x = min(max(f.minX, screen.minX + 8), screen.maxX - f.width - 8)
        f.origin.y = min(max(f.minY, screen.minY + 8), screen.maxY - f.height - 8)
        return f
    }

    // MARK: drafting

    private func startDraft() {
        guard enabled, app.engine.ready else { return }
        guard AXIsProcessTrusted() else {
            pill.show("Allow Accessibility for Glint to draft", autoHide: 2.5)
            app.promptAccessibility()
            return
        }
        guard let focus = AssistAX.read() else { return }
        if focus.app.processIdentifier == ProcessInfo.processInfo.processIdentifier {
            pill.show("Click into a text field in another app first", autoHide: 2)
            return
        }
        if focus.secure {
            pill.show("Assist never reads password fields", autoHide: 2)
            return
        }
        guard let panel = ensurePanel("draft", size: NSSize(width: 440, height: 220)) else { return }
        draftFocus = focus
        // Below the caret if we know it, else below the field, else near the mouse.
        let anchor = focus.caret ?? focus.field.map { f in
            f.height > 160 ? NSRect(x: f.minX, y: f.minY + 40, width: 0, height: 0) : f
        } ?? NSRect(origin: NSEvent.mouseLocation, size: .zero)
        var frame = NSRect(x: anchor.minX - 12, y: anchor.minY - panel.frame.height - 8,
                           width: panel.frame.width, height: panel.frame.height)
        let screen = (NSScreen.screens.first { $0.frame.contains(anchor.origin) } ?? NSScreen.main)?.visibleFrame
        if let screen, frame.minY < screen.minY { frame.origin.y = anchor.maxY + 8 }  // no room below: go above
        panel.setFrame(clamp(frame), display: false)
        panel.makeKeyAndOrderFront(nil)
        let field = String(focus.value.suffix(8000))
        run("draft", "window.assistDraft(\(json(["app": focus.appName, "title": focus.title, "field_text": field, "editable": focus.editable])))")
    }

    private func hideDraft() {
        guard draftPanel?.isVisible == true else { return }
        draftPanel?.orderOut(nil)
        run("draft", "window.assistReset && window.assistReset()")
    }

    private func insertDraft(text: String, mode: String) {
        guard let focus = draftFocus, !text.isEmpty else { hideDraft(); return }
        draftPanel?.orderOut(nil)
        let replace = mode != "continue" && !focus.value.isEmpty
        focus.app.activate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            if replace {
                let length = (focus.value as NSString).length
                if !(focus.element.map { AssistAX.selectAll($0, length: length) } ?? false) && focus.editable {
                    self.sendKey(kVK_ANSI_A, flags: .maskCommand)
                }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + (replace ? 0.08 : 0)) {
                self.paste(text)
                self.pill.show("Inserted", autoHide: 0.9)
            }
        }
        run("draft", "window.assistReset && window.assistReset()")
    }

    // MARK: pasteboard + keys

    private func snapshotClipboard() -> [[NSPasteboard.PasteboardType: Data]] {
        (NSPasteboard.general.pasteboardItems ?? []).map { item in
            var d: [NSPasteboard.PasteboardType: Data] = [:]
            for t in item.types { if let v = item.data(forType: t) { d[t] = v } }
            return d
        }
    }

    private func restoreClipboard(_ saved: [[NSPasteboard.PasteboardType: Data]]) {
        let pb = NSPasteboard.general
        pb.clearContents()
        for d in saved {
            let item = NSPasteboardItem()
            for (t, v) in d { item.setData(v, forType: t) }
            pb.writeObjects([item])
        }
    }

    private func sendKey(_ code: Int, flags: CGEventFlags) {
        let src = CGEventSource(stateID: .combinedSessionState)
        let down = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(code), keyDown: true)
        let up = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(code), keyDown: false)
        down?.flags = flags
        up?.flags = flags
        down?.post(tap: .cghidEventTap)
        up?.post(tap: .cghidEventTap)
    }

    /// Paste into whatever has keyboard focus now, then put the user's clipboard back.
    private func paste(_ text: String) {
        let pb = NSPasteboard.general
        let saved = snapshotClipboard()
        pb.clearContents()
        pb.setString(text, forType: .string)
        pb.setData(Data(), forType: NSPasteboard.PasteboardType("org.nspasteboard.ConcealedType"))
        let ours = pb.changeCount
        sendKey(kVK_ANSI_V, flags: .maskCommand)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
            guard pb.changeCount == ours else { return }  // the user copied something new meanwhile
            self.restoreClipboard(saved)
        }
    }

    /// Copy and return a closure that restores the previous clipboard (for undo).
    @discardableResult
    private func copyToClipboard(_ text: String) -> () -> Void {
        let pb = NSPasteboard.general
        let saved = snapshotClipboard()
        pb.clearContents()
        pb.setString(text, forType: .string)
        let ours = pb.changeCount
        return { [weak self] in
            guard pb.changeCount == ours else { return }
            self?.restoreClipboard(saved)
        }
    }

    // MARK: edge panel

    private func toggleEdge() {
        guard enabled else { return }
        if edgePanel?.isVisible == true { hideEdge(); return }
        let screen = NSScreen.main?.visibleFrame ?? .zero
        let size = NSSize(width: 340, height: min(620, screen.height - 80))
        guard let p = ensurePanel("edge", size: size) else { return }
        p.setFrame(NSRect(x: screen.maxX - size.width - 10, y: screen.midY - size.height / 2,
                          width: size.width, height: size.height), display: false)
        p.makeKeyAndOrderFront(nil)
        let front = NSWorkspace.shared.frontmostApplication
        let ctx: [String: Any] = ["app": front?.localizedName ?? "", "cards_paused": cardsPausedUntil > Date(),
                                  "cards": cardsEnabled]
        run("edge", "window.assistEdgeOpened && window.assistEdgeOpened(\(json(ctx)))")
    }

    private func hideEdge() { edgePanel?.orderOut(nil) }

    private func layoutHandle() {
        guard enabled, edgeHandleEnabled, app.engine.ready, let screen = NSScreen.main else {
            handlePanel?.orderOut(nil)
            return
        }
        if handlePanel == nil {
            let p = AssistPanel.make(size: NSSize(width: 12, height: 64), level: .statusBar)
            p.hasShadow = false
            let v = AssistHandleView(frame: NSRect(x: 0, y: 0, width: 12, height: 64))
            v.onClick = { [weak self] in self?.toggleEdge() }
            p.contentView = v
            handlePanel = p
        }
        let f = screen.visibleFrame
        handlePanel?.setFrameOrigin(NSPoint(x: screen.frame.maxX - 12, y: f.midY - 32))
        handlePanel?.orderFrontRegardless()
    }

    // MARK: proactive cards

    private func rescheduleCardTimer() {
        cardTimer?.invalidate()
        cardTimer = Timer.scheduledTimer(withTimeInterval: cardInterval, repeats: true) { [weak self] _ in
            self?.checkForCard(force: false)
        }
    }

    private func appActivated(_ running: NSRunningApplication?) {
        let mine = running?.processIdentifier == ProcessInfo.processInfo.processIdentifier
        if !mine {
            // A draft belongs to its window; withdraw it when another app comes forward.
            if let f = draftFocus, draftPanel?.isVisible == true, f.app.processIdentifier != running?.processIdentifier {
                hideDraft()
            }
            if currentCard != nil, cardTarget?.processIdentifier != running?.processIdentifier {
                hideCard(outcome: "ignored")
            }
        }
        cardCheckWork?.cancel()
        let w = DispatchWorkItem { [weak self] in self?.checkForCard(force: false) }
        cardCheckWork = w
        DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: w)  // let capture read the new window first
    }

    private func checkForCard(force: Bool) {
        guard enabled, cardsEnabled, app.engine.ready, !cardBusy, currentCard == nil,
              draftPanel?.isVisible != true, AXIsProcessTrusted(), force || Date() > cardsPausedUntil,
              let front = NSWorkspace.shared.frontmostApplication,
              front.processIdentifier != ProcessInfo.processInfo.processIdentifier else { return }
        let focus = AssistAX.read()
        if focus?.secure == true { return }
        cardBusy = true
        let body: [String: Any] = ["app": front.localizedName ?? "", "title": focus?.title ?? "", "force": force]
        app.engine.request("api/assist/card", body, timeout: 90) { [weak self] json, _ in
            guard let self else { return }
            self.cardBusy = false
            guard let card = json?["card"] as? [String: Any],
                  NSWorkspace.shared.frontmostApplication?.processIdentifier == front.processIdentifier else { return }
            self.showCue(card, target: front, window: focus?.window)
        }
    }

    private func showCue(_ card: [String: Any], target: NSRunningApplication, window: NSRect?) {
        currentCard = card
        cardTarget = target
        if cuePanel == nil {
            let p = AssistPanel.make(size: NSSize(width: 22, height: 22), level: .floating)
            p.hasShadow = false
            let v = AssistCueView(frame: NSRect(x: 0, y: 0, width: 22, height: 22))
            v.onActivate = { [weak self] in self?.expandCard() }
            p.contentView = v
            cuePanel = p
        }
        let screen = NSScreen.main?.visibleFrame ?? .zero
        let w = window ?? screen
        let origin = NSPoint(x: min(w.maxX, screen.maxX) - 40, y: min(w.maxY, screen.maxY) - 56)
        cuePanel?.setFrame(clamp(NSRect(origin: origin, size: NSSize(width: 22, height: 22))), display: true)
        cuePanel?.orderFrontRegardless()
        cueHideWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.cardPanel?.isVisible != true else { return }
            self.hideCard(outcome: "ignored")
        }
        cueHideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 45, execute: work)
    }

    private func expandCard() {
        guard let card = currentCard, let cue = cuePanel, cue.isVisible,
              let p = ensurePanel("card", size: NSSize(width: 340, height: 200)) else { return }
        cueHideWork?.cancel()
        cue.orderOut(nil)
        let c = cue.frame
        p.setFrame(clamp(NSRect(x: c.maxX - p.frame.width, y: c.maxY - p.frame.height,
                                width: p.frame.width, height: p.frame.height)), display: false)
        p.orderFrontRegardless()
        run("card", "window.assistCard(\(json(card)))")
        if let id = card["id"] as? String { app.engine.post("api/assist/outcome", ["id": id, "outcome": "shown"]) }
        scheduleCardHide()
    }

    /// Cards fade after 45 s without interaction (postponed while the pointer is over them).
    private func scheduleCardHide(after seconds: TimeInterval = 45) {
        cardHideWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self, let p = self.cardPanel, p.isVisible else { return }
            if p.frame.contains(NSEvent.mouseLocation) { self.scheduleCardHide(after: 10); return }
            self.hideCard(outcome: "ignored")
        }
        cardHideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
    }

    private func hideCard(outcome: String?) {
        if let outcome, let id = currentCard?["id"] as? String {
            if outcome == "dismissed" {
                app.engine.post("api/assist/action", ["card_id": id, "action_id": "dismiss"])
            } else {
                app.engine.post("api/assist/outcome", ["id": id, "outcome": outcome])
            }
        }
        currentCard = nil
        cueHideWork?.cancel()
        cardHideWork?.cancel()
        cuePanel?.orderOut(nil)
        cardPanel?.orderOut(nil)
    }

    private func cardAction(_ body: [String: Any]) {
        guard let cardId = body["card_id"] as? String, let actionId = body["action_id"] as? String else { return }
        if actionId == "dismiss" { hideCard(outcome: "dismissed"); return }
        let target = cardTarget
        let front = NSWorkspace.shared.frontmostApplication
        scheduleCardHide()
        app.engine.request("api/assist/action", ["card_id": cardId, "action_id": actionId,
                                                 "app": front?.localizedName ?? ""]) { [weak self] json, err in
            guard let self else { return }
            var res = json ?? ["result": "failed", "error": err ?? "Something went wrong"]
            let result = res["result"] as? String ?? "failed"
            let token = res["undo_token"] as? String
            switch result {
            case "applied":
                guard let text = res["text"] as? String, let target else { res["result"] = "failed"; break }
                self.withTargetFocused(target) { self.paste(text) }
                if let token {
                    self.undoActions[token] = { [weak self] in
                        self?.withTargetFocused(target) { self?.sendKey(kVK_ANSI_Z, flags: .maskCommand) }
                    }
                }
            case "copied":
                let restore = self.copyToClipboard(res["text"] as? String ?? "")
                if let token { self.undoActions[token] = restore }
            case "opened":
                if let s = res["url"] as? String, let url = URL(string: s), ["http", "https"].contains(url.scheme ?? "") {
                    NSWorkspace.shared.open(url)
                } else {
                    res["result"] = "failed"
                }
            default: break
            }
            res.removeValue(forKey: "text")
            self.run("card", "window.assistActionResult(\(self.json(res)))")
            self.scheduleCardHide(after: result == "applied" || result == "copied" ? 12 : 4)
        }
    }

    /// The card panel may hold keyboard focus after a click; step it aside so keys reach the target app.
    private func withTargetFocused(_ target: NSRunningApplication, _ body: @escaping () -> Void) {
        let p = cardPanel
        let wasVisible = p?.isVisible == true
        p?.orderOut(nil)
        target.activate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            body()
            if wasVisible { DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { p?.orderFrontRegardless() } }
        }
    }

    private func undo(_ token: String) {
        app.engine.request("api/assist/undo", ["undo_token": token]) { [weak self] json, _ in
            guard let self else { return }
            let result = json?["result"] as? String ?? "unavailable"
            if result == "undone" { self.undoActions.removeValue(forKey: token)?() }
            self.run("card", "window.assistActionResult(\(self.json(["result": result])))")
            self.scheduleCardHide(after: 3)
        }
    }

    // MARK: Quick Ask voice

    private func voiceState(_ payload: [String: Any]) {
        app.quickWeb?.evaluateJavaScript("window.assistVoiceState && window.assistVoiceState(\(json(payload)))")
    }

    private func startVoice() {
        guard voiceRecorder == nil else { return }
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .notDetermined:
            voiceState(["state": "idle", "notice": "Allow microphone access, then press the mic again"])
            AVCaptureDevice.requestAccess(for: .audio) { _ in }
            return
        case .denied, .restricted:
            voiceState(["state": "error", "error": "Microphone access is off for Glint (System Settings → Privacy)"])
            return
        default: break
        }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("glint-quickvoice-\(UUID().uuidString).m4a")
        let settings: [String: Any] = [AVFormatIDKey: Int(kAudioFormatMPEG4AAC), AVSampleRateKey: 16000,
                                       AVNumberOfChannelsKey: 1, AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue]
        guard let r = try? AVAudioRecorder(url: url, settings: settings) else {
            voiceState(["state": "error", "error": "Couldn't start the microphone"])
            return
        }
        r.isMeteringEnabled = true
        guard r.record() else {
            voiceState(["state": "error", "error": "Couldn't start the microphone"])
            return
        }
        voiceRecorder = r
        voiceURL = url
        voiceStarted = Date()
        voiceState(["state": "recording"])
        voiceMeter = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            guard let self, let r = self.voiceRecorder else { return }
            if self.app.quickPanel?.isVisible != true { self.cancelVoice(); return }  // panel closed
            if Date().timeIntervalSince(self.voiceStarted) > 90 { self.stopVoice(); return }
            r.updateMeters()
            let level = max(0, min(1, (Double(r.averagePower(forChannel: 0)) + 50) / 50))
            self.voiceState(["state": "recording", "level": level])
        }
    }

    private func endRecording() -> TimeInterval {
        voiceMeter?.invalidate()
        voiceMeter = nil
        let d = voiceRecorder?.currentTime ?? 0
        voiceRecorder?.stop()
        voiceRecorder = nil
        return d
    }

    private func cleanupVoice() {
        if let u = voiceURL { try? FileManager.default.removeItem(at: u) }
        voiceURL = nil
    }

    private func cancelVoice() {
        guard voiceRecorder != nil else { return }
        _ = endRecording()
        cleanupVoice()
        voiceState(["state": "idle"])
    }

    private func stopVoice() {
        guard voiceRecorder != nil else { return }
        let duration = endRecording()
        guard duration >= 0.4, let url = voiceURL, let data = try? Data(contentsOf: url) else {
            cleanupVoice()
            voiceState(["state": "idle", "notice": "Too short — hold the mic a little longer"])
            return
        }
        voiceState(["state": "transcribing"])
        app.engine.request("api/dictation", ["audio_b64": data.base64EncodedString(), "filename": "question.m4a",
                                             "app": "Quick Ask"], timeout: 120) { [weak self] json, err in
            guard let self else { return }
            if let err {
                self.cleanupVoice()
                return self.voiceState(["state": "error", "error": err])
            }
            if json?["needs_local"] as? Bool == true {
                self.transcribeOnDevice(url) { text in
                    self.cleanupVoice()
                    if let text, !text.isEmpty { self.voiceState(["state": "done", "text": text]) }
                    else { self.voiceState(["state": "error", "error": "Didn't catch that"]) }
                }
            } else {
                self.cleanupVoice()
                let text = (json?["text"] as? String) ?? ""
                self.voiceState(text.isEmpty ? ["state": "error", "error": "Didn't catch that"] : ["state": "done", "text": text])
            }
        }
    }

    private func transcribeOnDevice(_ url: URL, completion: @escaping (String?) -> Void) {
        SFSpeechRecognizer.requestAuthorization { status in
            DispatchQueue.main.async {
                guard status == .authorized, let rec = SFSpeechRecognizer(), rec.isAvailable else {
                    self.voiceState(["state": "error", "error": "Add an OpenAI key or allow Speech Recognition"])
                    self.cleanupVoice()
                    return
                }
                let req = SFSpeechURLRecognitionRequest(url: url)
                req.requiresOnDeviceRecognition = rec.supportsOnDeviceRecognition
                req.addsPunctuation = true
                var done = false
                rec.recognitionTask(with: req) { result, error in
                    if done { return }
                    if let result, result.isFinal {
                        done = true
                        let text = result.bestTranscription.formattedString
                        DispatchQueue.main.async { completion(text) }
                    } else if error != nil {
                        done = true
                        DispatchQueue.main.async { completion(nil) }
                    }
                }
            }
        }
    }
}
