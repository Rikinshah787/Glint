// Shell feature: floating session HUD (edge-snapping timer pill), focus nudges and once-a-day notices
// (user notifications or a small toast panel), an optional full-screen focus vignette, and native
// permission reporting for the setup wizard (Accessibility, microphone, screen recording, running apps).
import AppKit
import ApplicationServices
import AVFoundation
import UserNotifications

final class ShellFeature: NSObject, GlintFeature, UNUserNotificationCenterDelegate {
    unowned let app: AppDelegate
    private lazy var hud = ShellHUD(feature: self)
    private let toast = ShellToast()
    private let vignette = ShellVignette()
    private var hudEnabled = true
    private var vignetteEnabled = false
    private var notificationsEnabled = true
    private var notificationsAuthorized = false
    private var lastEventId = 0
    private var timers: [Timer] = []
    private var canNotify: Bool { Bundle.main.bundleIdentifier != nil }

    init(app: AppDelegate) {
        self.app = app
        super.init()
    }

    func engineReady() {
        setupNotifications()
        reportNative()
        pollNow()
        timers.append(Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.pollNow() })
        timers.append(Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            self?.pollNudges()
            self?.reportNative()
        })
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in self?.pollNudges() }
    }

    func settingsChanged(_ s: [String: Any]) {
        hudEnabled = s["session_hud"] as? Bool ?? true
        vignetteEnabled = s["focus_vignette"] as? Bool ?? false
        notificationsEnabled = s["notifications"] as? Bool ?? true
        if !hudEnabled { hud.hide() } else { pollNow() }
    }

    func handle(action: String, body: [String: Any]) -> Bool {
        switch action {
        case "shell.permissions":
            reportNative()
        case "shell.requestMic":
            if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
                AVCaptureDevice.requestAccess(for: .audio) { _ in DispatchQueue.main.async { self.reportNative() } }
            } else {
                openPrivacy("mic")
            }
        case "shell.requestScreen":
            if !CGPreflightScreenCaptureAccess() && !CGRequestScreenCaptureAccess() { openPrivacy("screen") }
            reportNative()
        case "shell.openPrivacy":
            openPrivacy(body["pane"] as? String ?? "")
        case "shell.quickAsk":
            app.toggleQuickAsk()
        case "shell.settings":
            app.refreshDictationSetting()
        default:
            return false
        }
        return true
    }

    // MARK: Permissions

    private func micStatus() -> String {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return "granted"
        case .denied, .restricted: return "denied"
        default: return "not_determined"
        }
    }

    /// Tell the engine (and the main window, if open) what only the app can see.
    func reportNative() {
        let apps = NSWorkspace.shared.runningApplications
            .filter { $0.activationPolicy == .regular && $0.bundleIdentifier != Bundle.main.bundleIdentifier }
            .compactMap { $0.localizedName }
        var info: [String: Any] = ["ax": AXIsProcessTrusted(), "mic": micStatus(),
                                   "screen": CGPreflightScreenCaptureAccess() ? "granted" : "not_granted",
                                   "apps": Array(Set(apps)).sorted(),
                                   "notifications": notificationsAuthorized ? "granted" : "not_granted"]
        if !canNotify { info["notifications"] = "unavailable" }
        app.engine.request("api/shell/native", info)
        if let data = try? JSONSerialization.data(withJSONObject: info), let json = String(data: data, encoding: .utf8) {
            app.mainWeb?.evaluateJavaScript("window.glintShellPermissions && window.glintShellPermissions(\(json))")
        }
    }

    private func openPrivacy(_ pane: String) {
        let anchor = ["fda": "Privacy_AllFiles", "screen": "Privacy_ScreenCapture", "mic": "Privacy_Microphone",
                      "ax": "Privacy_Accessibility"][pane] ?? "Privacy"
        let url = pane == "notifications"
            ? "x-apple.systempreferences:com.apple.Notifications-Settings.extension"
            : "x-apple.systempreferences:com.apple.preference.security?\(anchor)"
        if let u = URL(string: url) { NSWorkspace.shared.open(u) }
    }

    // MARK: Session HUD

    private func pollNow() {
        guard app.engine.ready else { return }
        app.engine.request("api/shell/now") { [weak self] json, _ in
            guard let self, let n = json else { return }
            let mode = n["mode"] as? String ?? "idle"
            let hasSession = n["session_id"] as? String != nil
            guard self.hudEnabled, hasSession, mode != "off" else { self.hud.hide(); return }
            let started = (n["session_started_at"] as? Double).map { Date(timeIntervalSince1970: $0 / 1000) }
            self.hud.update(started: started, app: n["app"] as? String ?? "", paused: mode == "paused",
                            private: mode == "private")
        }
    }

    func hudClicked() { app.showMain(path: "home") }

    func setPaused(_ pause: Bool, minutes: Double = 30) {
        let body: [String: Any] = pause ? ["pause_minutes": minutes] : ["pause_minutes": 0, "enabled": true]
        app.engine.request("api/capture", body) { [weak self] _, _ in self?.pollNow() }
    }

    func hideHUDSetting() {
        app.engine.request("api/settings", ["session_hud": false]) { [weak self] _, _ in
            self?.hudEnabled = false
            self?.hud.hide()
        }
    }

    // MARK: Nudges and notices

    private func pollNudges() {
        guard app.engine.ready else { return }
        app.engine.request("api/shell/nudges", ["since": lastEventId]) { [weak self] json, _ in
            guard let self, let json else { return }
            if let latest = json["latest"] as? Int, latest < self.lastEventId { self.lastEventId = 0 }  // engine restarted
            let events = json["events"] as? [[String: Any]] ?? []
            for ev in events {
                if let id = ev["id"] as? Int { self.lastEventId = max(self.lastEventId, id) }
                if ev["dismissed"] as? Bool == true { continue }
                self.present(ev)
            }
        }
    }

    private func present(_ ev: [String: Any]) {
        let id = ev["id"] as? Int ?? 0
        let title = ev["title"] as? String ?? "Glint"
        let body = ev["body"] as? String ?? ""
        if ev["kind"] as? String == "nudge" {
            if vignetteEnabled || ev["vignette"] as? Bool == true { vignette.flash() }
            toast.show(title: title, body: body, buttons: [
                ("Back to work", { [weak self] in self?.dismissNudge(id, focused: false) }),
                ("I was on task", { [weak self] in self?.dismissNudge(id, focused: true) }),
            ], onClick: { [weak self] in self?.app.showMain(path: "home") })
            return
        }
        guard notificationsEnabled else { return }
        let notice = ev["notice"] as? String ?? ""
        if canNotify && notificationsAuthorized {
            let c = UNMutableNotificationContent()
            c.title = title
            c.body = body
            c.userInfo = ["notice": notice]
            UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: "glint.shell.\(notice).\(id)",
                                                                         content: c, trigger: nil))
        } else {
            toast.show(title: title, body: body, buttons: notice == "ax" ? [("Fix", { [weak self] in self?.app.promptAccessibility() })] : [],
                       onClick: { [weak self] in self?.openForNotice(notice) })
        }
    }

    private func dismissNudge(_ id: Int, focused: Bool) {
        app.engine.request("api/shell/nudge/dismiss", ["id": id, "focused": focused])
    }

    private func openForNotice(_ notice: String) {
        if notice == "ax" { app.promptAccessibility() }
        app.showMain(path: notice == "ax" ? "settings" : "home")
    }

    private func setupNotifications() {
        guard canNotify else { return }
        let center = UNUserNotificationCenter.current()
        if center.delegate == nil { center.delegate = self }
        let key = "GlintShell.askedNotifications"
        center.getNotificationSettings { s in
            DispatchQueue.main.async {
                self.notificationsAuthorized = s.authorizationStatus == .authorized || s.authorizationStatus == .provisional
                guard s.authorizationStatus == .notDetermined, !UserDefaults.standard.bool(forKey: key) else { return }
                UserDefaults.standard.set(true, forKey: key)
                center.requestAuthorization(options: [.alert, .sound]) { ok, _ in
                    DispatchQueue.main.async { self.notificationsAuthorized = ok }
                }
            }
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let notice = response.notification.request.content.userInfo["notice"] as? String ?? ""
        DispatchQueue.main.async { self.openForNotice(notice) }
        completionHandler()
    }
}

// MARK: - Shared bits

private final class ShellPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

private final class ShellButton: NSButton {
    var onClick: (() -> Void)?
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    convenience init(title: String, symbol: String? = nil, onClick: @escaping () -> Void) {
        self.init(frame: .zero)
        self.onClick = onClick
        self.title = title
        if let symbol { image = NSImage(systemSymbolName: symbol, accessibilityDescription: title); imagePosition = .imageOnly }
        bezelStyle = .rounded
        target = self
        action = #selector(fire)
    }
    @objc private func fire() { onClick?() }
}

private func makeShellPanel(size: NSSize, level: NSWindow.Level) -> ShellPanel {
    let p = ShellPanel(contentRect: NSRect(origin: .zero, size: size), styleMask: [.nonactivatingPanel, .borderless],
                       backing: .buffered, defer: false)
    p.level = level
    p.isOpaque = false
    p.backgroundColor = .clear
    p.hasShadow = true
    p.hidesOnDeactivate = false
    p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
    return p
}

// MARK: - Session HUD

private final class ShellHUDView: NSVisualEffectView {
    var onDragEnd: (() -> Void)?
    var onClick: (() -> Void)?
    var onMenu: ((NSEvent) -> Void)?
    private var downAt: NSPoint = .zero
    private var originAt: NSPoint = .zero
    private var moved = false

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func mouseDown(with e: NSEvent) {
        downAt = NSEvent.mouseLocation
        originAt = window?.frame.origin ?? .zero
        moved = false
    }

    override func mouseDragged(with e: NSEvent) {
        let p = NSEvent.mouseLocation
        let dx = p.x - downAt.x, dy = p.y - downAt.y
        if abs(dx) + abs(dy) > 3 { moved = true }
        if moved { window?.setFrameOrigin(NSPoint(x: originAt.x + dx, y: originAt.y + dy)) }
    }

    override func mouseUp(with e: NSEvent) {
        if moved { onDragEnd?() } else { onClick?() }
    }

    override func rightMouseDown(with e: NSEvent) { onMenu?(e) }
}

final class ShellHUD: NSObject {
    private unowned let feature: ShellFeature
    private let panel: ShellPanel
    private let view: ShellHUDView
    private let dot = NSView()
    private let label = NSTextField(labelWithString: "")
    private var pauseButton: ShellButton!
    private var started: Date?
    private var appName = ""
    private var paused = false
    private var isPrivate = false
    private var tick: Timer?
    private let size = NSSize(width: 236, height: 34)
    private let placementKey = "GlintShell.hudPlacement"

    init(feature: ShellFeature) {
        self.feature = feature
        panel = makeShellPanel(size: size, level: .floating)
        view = ShellHUDView(frame: NSRect(origin: .zero, size: size))
        super.init()
        view.material = .hudWindow
        view.state = .active
        view.wantsLayer = true
        view.layer?.cornerRadius = size.height / 2
        view.layer?.masksToBounds = true
        dot.wantsLayer = true
        dot.layer?.cornerRadius = 4
        dot.frame = NSRect(x: 14, y: (size.height - 8) / 2, width: 8, height: 8)
        label.font = .monospacedDigitSystemFont(ofSize: 12.5, weight: .medium)
        label.lineBreakMode = .byTruncatingTail
        label.frame = NSRect(x: 30, y: (size.height - 17) / 2, width: size.width - 70, height: 17)
        pauseButton = ShellButton(title: "Pause", symbol: "pause.fill") { [weak self] in self?.togglePause() }
        pauseButton.isBordered = false
        pauseButton.contentTintColor = .secondaryLabelColor
        pauseButton.frame = NSRect(x: size.width - 36, y: (size.height - 24) / 2, width: 26, height: 24)
        pauseButton.toolTip = "Pause or resume capture"
        [dot, label, pauseButton].forEach { view.addSubview($0) }
        view.toolTip = "Click to open Glint · drag to any screen edge"
        view.onClick = { [weak self] in self?.feature.hudClicked() }
        view.onDragEnd = { [weak self] in self?.snap(animated: true) }
        view.onMenu = { [weak self] e in self?.showMenu(e) }
        panel.contentView = view
    }

    func update(started: Date?, app: String, paused: Bool, private isPrivate: Bool) {
        self.started = started
        self.appName = app
        self.paused = paused
        self.isPrivate = isPrivate
        if !panel.isVisible {
            place()
            panel.orderFrontRegardless()
            tick = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.render() }
        }
        render()
    }

    func hide() {
        tick?.invalidate()
        tick = nil
        panel.orderOut(nil)
    }

    private func render() {
        var text = "–"
        if let started {
            let s = max(0, Int(Date().timeIntervalSince(started)))
            text = s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, (s % 3600) / 60, s % 60)
                             : String(format: "%d:%02d", s / 60, s % 60)
        }
        let what = paused ? "Paused" : isPrivate ? "Private" : appName
        label.stringValue = what.isEmpty ? text : "\(text)  ·  \(what)"
        dot.layer?.backgroundColor = (paused ? NSColor.systemOrange : isPrivate ? NSColor.systemGray : NSColor.systemGreen).cgColor
        pauseButton.image = NSImage(systemSymbolName: paused ? "play.fill" : "pause.fill", accessibilityDescription: paused ? "Resume" : "Pause")
    }

    private func togglePause() {
        paused.toggle()
        render()
        feature.setPaused(paused)
    }

    private func showMenu(_ e: NSEvent) {
        let m = NSMenu()
        let items: [(String, () -> Void)] = [
            ("Open Glint", { [weak self] in self?.feature.hudClicked() }),
            (paused ? "Resume capture" : "Pause for 30 minutes", { [weak self] in self?.togglePause() }),
            ("Pause for 1 hour", { [weak self] in self?.paused = true; self?.render(); self?.feature.setPaused(true, minutes: 60) }),
            ("Hide session HUD", { [weak self] in self?.feature.hideHUDSetting() }),
        ]
        for (title, fn) in items {
            let item = NSMenuItem(title: title, action: #selector(ShellMenuAction.fire), keyEquivalent: "")
            let target = ShellMenuAction(fn)
            item.target = target
            item.representedObject = target  // keep the target alive with the item
            m.addItem(item)
        }
        NSMenu.popUpContextMenu(m, with: e, for: view)
    }

    // Placement: {edge: top|bottom|left|right, along: 0.08–0.92} persisted in UserDefaults.
    private func placement() -> (String, CGFloat) {
        let d = UserDefaults.standard.dictionary(forKey: placementKey)
        return (d?["edge"] as? String ?? "top", CGFloat(d?["along"] as? Double ?? 0.82))
    }

    private func screen() -> NSScreen? {
        let c = NSPoint(x: panel.frame.midX, y: panel.frame.midY)
        return NSScreen.screens.first { $0.frame.contains(c) } ?? NSScreen.main
    }

    private func frame(edge: String, along: CGFloat, in vf: NSRect) -> NSRect {
        let m: CGFloat = 8, w = size.width, h = size.height
        var o: NSPoint
        switch edge {
        case "bottom": o = NSPoint(x: vf.minX + along * vf.width - w / 2, y: vf.minY + m)
        case "left": o = NSPoint(x: vf.minX + m, y: vf.minY + along * vf.height - h / 2)
        case "right": o = NSPoint(x: vf.maxX - w - m, y: vf.minY + along * vf.height - h / 2)
        default: o = NSPoint(x: vf.minX + along * vf.width - w / 2, y: vf.maxY - h - m)
        }
        o.x = min(max(o.x, vf.minX + m), vf.maxX - w - m)
        o.y = min(max(o.y, vf.minY + m), vf.maxY - h - m)
        return NSRect(origin: o, size: size)
    }

    private func place() {
        guard let vf = NSScreen.main?.visibleFrame else { return }
        let (edge, along) = placement()
        panel.setFrame(frame(edge: edge, along: along, in: vf), display: true)
    }

    private func snap(animated: Bool) {
        guard let vf = screen()?.visibleFrame else { return }
        let c = NSPoint(x: panel.frame.midX, y: panel.frame.midY)
        let dist = ["left": c.x - vf.minX, "right": vf.maxX - c.x, "bottom": c.y - vf.minY, "top": vf.maxY - c.y]
        let edge = dist.min { $0.value < $1.value }?.key ?? "top"
        let raw = edge == "top" || edge == "bottom" ? (c.x - vf.minX) / vf.width : (c.y - vf.minY) / vf.height
        let along = min(max(raw, 0.08), 0.92)
        UserDefaults.standard.set(["edge": edge, "along": Double(along)], forKey: placementKey)
        let target = frame(edge: edge, along: along, in: vf)
        if animated {
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 0.18
                panel.animator().setFrame(target, display: true)
            }
        } else {
            panel.setFrame(target, display: true)
        }
    }
}

private final class ShellMenuAction: NSObject {
    let fn: () -> Void
    init(_ fn: @escaping () -> Void) { self.fn = fn }
    @objc func fire() { fn() }
}

// MARK: - Toast panel (non-activating, top-right, auto-hides)

private final class ShellClickView: NSVisualEffectView {
    var onClick: (() -> Void)?
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseUp(with e: NSEvent) { onClick?() }
}

final class ShellToast {
    private let width: CGFloat = 340
    private var panel: ShellPanel?
    private var hideWork: DispatchWorkItem?

    func show(title: String, body: String, buttons: [(String, () -> Void)], onClick: @escaping () -> Void,
              duration: TimeInterval = 12) {
        hide()
        let titleField = NSTextField(labelWithString: title)
        titleField.font = .systemFont(ofSize: 13, weight: .semibold)
        let bodyField = NSTextField(wrappingLabelWithString: body)
        bodyField.font = .systemFont(ofSize: 12.5)
        bodyField.textColor = .secondaryLabelColor
        bodyField.preferredMaxLayoutWidth = width - 32
        let bodyHeight = ceil(bodyField.sizeThatFits(NSSize(width: width - 32, height: 400)).height)
        let buttonRow: CGFloat = buttons.isEmpty ? 0 : 34
        let height = 16 + 18 + 4 + bodyHeight + buttonRow + 18
        let p = makeShellPanel(size: NSSize(width: width, height: height), level: .statusBar)
        let v = ShellClickView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        v.material = .popover
        v.state = .active
        v.wantsLayer = true
        v.layer?.cornerRadius = 14
        v.layer?.masksToBounds = true
        v.onClick = { [weak self] in onClick(); self?.hide() }
        titleField.frame = NSRect(x: 16, y: height - 16 - 18, width: width - 32, height: 18)
        bodyField.frame = NSRect(x: 16, y: height - 16 - 18 - 4 - bodyHeight, width: width - 32, height: bodyHeight)
        v.addSubview(titleField)
        v.addSubview(bodyField)
        var x: CGFloat = 16
        for (label, fn) in buttons {
            let b = ShellButton(title: label) { [weak self] in fn(); self?.hide() }
            b.controlSize = .small
            b.sizeToFit()
            b.frame = NSRect(x: x, y: 14, width: b.frame.width + 8, height: 24)
            x += b.frame.width + 8
            v.addSubview(b)
        }
        let bar = NSView(frame: NSRect(x: 0, y: 0, width: width, height: 3))
        bar.wantsLayer = true
        bar.layer?.backgroundColor = NSColor.controlAccentColor.cgColor
        bar.layer?.anchorPoint = .zero
        v.addSubview(bar)
        p.contentView = v
        if let vf = NSScreen.main?.visibleFrame {
            p.setFrameOrigin(NSPoint(x: vf.maxX - width - 14, y: vf.maxY - height - 14))
        }
        p.alphaValue = 0
        p.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { $0.duration = 0.2; p.animator().alphaValue = 1 }
        // Shrinking timer bar.
        let shrink = CABasicAnimation(keyPath: "bounds.size.width")
        shrink.fromValue = width
        shrink.toValue = 0
        shrink.duration = duration
        shrink.fillMode = .forwards
        shrink.isRemovedOnCompletion = false
        bar.layer?.add(shrink, forKey: "timer")
        panel = p
        let w = DispatchWorkItem { [weak self] in self?.hide() }
        hideWork = w
        DispatchQueue.main.asyncAfter(deadline: .now() + duration, execute: w)
    }

    func hide() {
        hideWork?.cancel()
        hideWork = nil
        guard let p = panel else { return }
        panel = nil
        NSAnimationContext.runAnimationGroup({ $0.duration = 0.2; p.animator().alphaValue = 0 }) { p.orderOut(nil) }
    }
}

// MARK: - Focus vignette (click-through edge darkening)

final class ShellVignette {
    private var window: NSWindow?

    func flash(hold: TimeInterval = 4) {
        guard window == nil, let screen = NSScreen.main else { return }
        let w = NSWindow(contentRect: screen.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        w.level = .floating
        w.isOpaque = false
        w.backgroundColor = .clear
        w.ignoresMouseEvents = true
        w.hasShadow = false
        w.isReleasedWhenClosed = false
        w.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        let v = NSView(frame: NSRect(origin: .zero, size: screen.frame.size))
        v.wantsLayer = true
        let g = CAGradientLayer()
        g.type = .radial
        g.frame = v.bounds
        g.colors = [NSColor.clear.cgColor, NSColor.clear.cgColor, NSColor.black.withAlphaComponent(0.55).cgColor]
        g.locations = [0, 0.62, 1]
        g.startPoint = CGPoint(x: 0.5, y: 0.5)
        g.endPoint = CGPoint(x: 1.05, y: 1.05)
        v.layer?.addSublayer(g)
        w.contentView = v
        w.alphaValue = 0
        w.orderFrontRegardless()
        window = w
        NSAnimationContext.runAnimationGroup { $0.duration = 0.8; w.animator().alphaValue = 1 }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8 + hold) { [weak self] in
            NSAnimationContext.runAnimationGroup({ $0.duration = 1.4; w.animator().alphaValue = 0 }) {
                w.orderOut(nil)
                self?.window = nil
            }
        }
    }
}
