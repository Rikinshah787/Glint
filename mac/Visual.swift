// Visual answers (native side).
//
// - Quick Ask: when the panel opens, capture the window the user was just in (screencapture -l),
//   downscale to <=1280 px, JPEG, and hand it to the engine (/api/visual/screenshot).
// - Overlays: a transparent, click-through, full-screen window that draws callouts, numbered badges
//   and highlight rings from "overlay" events, mapping 0-1 coordinates onto the captured window.
//   Cleared by Esc, the next question, hide_screen_overlay, or a timeout.
// - Glance (Control+Shift+G): screenshot of the front window -> /api/visual/glance -> a small card
//   near the cursor.
// Screen Recording permission is required for captures; without it everything degrades to text.
import AppKit
import Carbon.HIToolbox
import ImageIO

// MARK: - Window capture

struct CapturedWindow {
    let windowID: CGWindowID
    let app: String
    let title: String
    let frame: CGRect          // global display coordinates, top-left origin (CoreGraphics)
}

enum WindowCapture {
    static var hasPermission: Bool { CGPreflightScreenCaptureAccess() }
    private static var asked = false

    /// Ask for Screen Recording once per launch (macOS shows its prompt only the first time).
    static func requestPermissionOnce() {
        guard !asked else { return }
        asked = true
        _ = CGRequestScreenCaptureAccess()
    }

    /// Frontmost normal window of an app, from the on-screen window list (front to back).
    static func frontWindow(of app: NSRunningApplication) -> CapturedWindow? {
        guard let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
                as? [[String: Any]] else { return nil }
        for info in list {
            guard (info[kCGWindowOwnerPID as String] as? pid_t) == app.processIdentifier,
                  (info[kCGWindowLayer as String] as? Int) == 0,
                  (info[kCGWindowAlpha as String] as? Double ?? 1) > 0.05,
                  let id = info[kCGWindowNumber as String] as? CGWindowID,
                  let b = info[kCGWindowBounds as String] as? [String: Any],
                  let rect = CGRect(dictionaryRepresentation: b as CFDictionary),
                  rect.width > 80, rect.height > 60 else { continue }
            let title = info[kCGWindowName as String] as? String ?? ""
            return CapturedWindow(windowID: id, app: app.localizedName ?? "", title: title, frame: rect)
        }
        return nil
    }

    /// Capture one window (no shadow, no sound), downscale and JPEG-encode off the main thread.
    static func capture(_ w: CapturedWindow, maxSide: Int = 1280,
                        completion: @escaping (_ jpeg: Data?, _ size: CGSize) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("glint-shot-\(UUID().uuidString).png")
            defer { try? FileManager.default.removeItem(at: url) }
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
            p.arguments = ["-x", "-o", "-l\(w.windowID)", "-t", "png", url.path]
            var result: (Data?, CGSize) = (nil, .zero)
            if (try? p.run()) != nil {
                p.waitUntilExit()
                if p.terminationStatus == 0 { result = downscaleJPEG(url, maxSide: maxSide) }
            }
            DispatchQueue.main.async { completion(result.0, result.1) }
        }
    }

    static func downscaleJPEG(_ url: URL, maxSide: Int) -> (Data?, CGSize) {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else { return (nil, .zero) }
        let opts: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true,
                                     kCGImageSourceCreateThumbnailWithTransform: true,
                                     kCGImageSourceThumbnailMaxPixelSize: maxSide]
        guard let img = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary) else { return (nil, .zero) }
        let data = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(data, "public.jpeg" as CFString, 1, nil) else { return (nil, .zero) }
        CGImageDestinationAddImage(dest, img, [kCGImageDestinationLossyCompressionQuality: 0.72] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { return (nil, .zero) }
        return (data as Data, CGSize(width: img.width, height: img.height))
    }

    /// CoreGraphics global rect (top-left origin) -> Cocoa global rect (bottom-left origin).
    static func cocoaRect(_ r: CGRect) -> CGRect {
        let h0 = NSScreen.screens.first?.frame.height ?? 0
        return CGRect(x: r.minX, y: h0 - r.maxY, width: r.width, height: r.height)
    }
}

// MARK: - Overlay

struct OverlayItem {
    enum Kind: String { case callout, badge, highlight }
    let kind: Kind
    let x: CGFloat, y: CGFloat, width: CGFloat, height: CGFloat   // normalized 0-1, y down
    let label: String
    let side: String?
}

final class OverlayView: NSView {
    var items: [OverlayItem] = []
    var target: CGRect = .zero      // captured window, in this view's coordinates
    var darkUI = false
    var accent = NSColor.systemGreen

    override var isFlipped: Bool { false }

    private func point(_ nx: CGFloat, _ ny: CGFloat) -> CGPoint {
        CGPoint(x: target.minX + nx * target.width, y: target.maxY - ny * target.height)
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.clear.set()
        dirtyRect.fill()
        for item in items {
            switch item.kind {
            case .highlight: drawHighlight(item)
            case .badge: drawBadge(item)
            case .callout: drawCallout(item)
            }
        }
    }

    private func chipColors() -> (fill: NSColor, text: NSColor) {
        // Pale UI behind -> dark chip; dark UI behind -> light chip.
        darkUI ? (NSColor(white: 0.98, alpha: 0.96), NSColor(white: 0.08, alpha: 1))
               : (NSColor(white: 0.10, alpha: 0.92), .white)
    }

    private func labelSize(_ s: NSAttributedString) -> CGSize {
        let r = s.boundingRect(with: CGSize(width: 260, height: 200), options: [.usesLineFragmentOrigin, .usesFontLeading])
        return CGSize(width: ceil(r.width) + 22, height: ceil(r.height) + 12)
    }

    private func attributed(_ text: String) -> NSAttributedString {
        NSAttributedString(string: text, attributes: [.font: NSFont.systemFont(ofSize: 13, weight: .semibold),
                                                      .foregroundColor: chipColors().text])
    }

    private func drawChip(_ text: NSAttributedString, in rect: CGRect) {
        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowBlurRadius = 10
        shadow.shadowOffset = NSSize(width: 0, height: -2)
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.3)
        shadow.set()
        let path = NSBezierPath(roundedRect: rect, xRadius: 9, yRadius: 9)
        chipColors().fill.setFill()
        path.fill()
        NSGraphicsContext.restoreGraphicsState()
        let border = NSBezierPath(roundedRect: rect.insetBy(dx: 0.75, dy: 0.75), xRadius: 8.5, yRadius: 8.5)
        border.lineWidth = 1.5
        accent.setStroke()
        border.stroke()
        text.draw(with: rect.insetBy(dx: 11, dy: 6), options: [.usesLineFragmentOrigin, .usesFontLeading])
    }

    private func drawHighlight(_ item: OverlayItem) {
        let a = point(item.x, item.y), b = point(item.x + item.width, item.y + item.height)
        let rect = CGRect(x: min(a.x, b.x), y: min(a.y, b.y), width: abs(b.x - a.x), height: abs(b.y - a.y)).insetBy(dx: -5, dy: -5)
        let path = NSBezierPath(roundedRect: rect, xRadius: 9, yRadius: 9)
        accent.withAlphaComponent(0.12).setFill()
        path.fill()
        NSColor.white.withAlphaComponent(0.8).setStroke()
        path.lineWidth = 5.5
        path.stroke()
        accent.setStroke()
        path.lineWidth = 3
        path.stroke()
        guard !item.label.isEmpty else { return }
        let text = attributed(item.label), size = labelSize(text)
        var origin = CGPoint(x: rect.minX, y: rect.maxY + 8)
        if origin.y + size.height > bounds.maxY - 4 { origin.y = rect.minY - size.height - 8 }
        origin.x = min(max(origin.x, bounds.minX + 6), bounds.maxX - size.width - 6)
        drawChip(text, in: CGRect(origin: origin, size: size))
    }

    private func drawBadge(_ item: OverlayItem) {
        let c = point(item.x, item.y), r: CGFloat = 15
        let circle = NSBezierPath(ovalIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowBlurRadius = 8
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.35)
        shadow.set()
        accent.setFill()
        circle.fill()
        NSGraphicsContext.restoreGraphicsState()
        NSColor.white.setStroke()
        circle.lineWidth = 2.5
        circle.stroke()
        let label = String(item.label.prefix(3))
        let s = NSAttributedString(string: label, attributes: [.font: NSFont.systemFont(ofSize: 14, weight: .bold),
                                                              .foregroundColor: NSColor.white])
        let sz = s.size()
        s.draw(at: CGPoint(x: c.x - sz.width / 2, y: c.y - sz.height / 2))
    }

    private func drawCallout(_ item: OverlayItem) {
        let t = point(item.x, item.y)
        let text = attributed(item.label), size = labelSize(text)
        var side = item.side ?? ""
        if side.isEmpty { side = item.x > 0.6 ? "left" : "right" }
        let dx: CGFloat = 70, dy: CGFloat = 46
        var origin: CGPoint
        switch side {
        case "left": origin = CGPoint(x: t.x - dx - size.width, y: t.y + dy - size.height / 2)
        case "top": origin = CGPoint(x: t.x - size.width / 2, y: t.y + dy + 20)
        case "bottom": origin = CGPoint(x: t.x - size.width / 2, y: t.y - dy - 20 - size.height)
        default: origin = CGPoint(x: t.x + dx, y: t.y + dy - size.height / 2)
        }
        // Keep the chip on screen; flip below the target if it would leave the top.
        if origin.y + size.height > bounds.maxY - 6 { origin.y = t.y - dy - size.height / 2 - 10 }
        origin.x = min(max(origin.x, bounds.minX + 6), bounds.maxX - size.width - 6)
        origin.y = min(max(origin.y, bounds.minY + 6), bounds.maxY - size.height - 6)
        let chip = CGRect(origin: origin, size: size)
        // Leader: from the chip edge nearest the target, curving into the target.
        let start = CGPoint(x: t.x < chip.minX ? chip.minX : t.x > chip.maxX ? chip.maxX : chip.midX,
                            y: t.x >= chip.minX && t.x <= chip.maxX ? (t.y > chip.midY ? chip.maxY : chip.minY) : chip.midY)
        let ctrl = CGPoint(x: (start.x + t.x) / 2 + (start.y - t.y) * 0.25, y: (start.y + t.y) / 2 - (start.x - t.x) * 0.25)
        let leader = NSBezierPath()
        leader.move(to: start)
        leader.curve(to: t, controlPoint1: ctrl, controlPoint2: ctrl)
        leader.lineCapStyle = .round
        NSColor.white.withAlphaComponent(0.85).setStroke()
        leader.lineWidth = 5.5
        leader.stroke()
        accent.setStroke()
        leader.lineWidth = 2.5
        leader.stroke()
        // Target marker: ring + dot.
        let ring = NSBezierPath(ovalIn: CGRect(x: t.x - 9, y: t.y - 9, width: 18, height: 18))
        accent.withAlphaComponent(0.22).setFill()
        ring.fill()
        accent.setStroke()
        ring.lineWidth = 2
        ring.stroke()
        let dot = NSBezierPath(ovalIn: CGRect(x: t.x - 3.5, y: t.y - 3.5, width: 7, height: 7))
        accent.setFill()
        dot.fill()
        drawChip(text, in: chip)
    }
}

final class OverlayController {
    private var window: NSWindow?
    private let view = OverlayView()
    private var hideWork: DispatchWorkItem?
    var lastFrame: CGRect?          // CG frame of the last captured window (fallback)
    var accent = NSColor.systemGreen

    var isVisible: Bool { window?.isVisible ?? false }

    func apply(_ p: [String: Any]) {
        switch p["action"] as? String {
        case "show": show(p)
        default: clear()
        }
    }

    private func show(_ p: [String: Any]) {
        var frame = lastFrame ?? .zero
        if let f = p["frame"] as? [String: Any] {
            let r = CGRect(x: num(f["x"]), y: num(f["y"]), width: num(f["w"]), height: num(f["h"]))
            if r.width > 10 && r.height > 10 { frame = r }
        }
        guard frame.width > 10 else { return }
        let items: [OverlayItem] = (p["items"] as? [[String: Any]] ?? []).compactMap { d in
            guard let kind = OverlayItem.Kind(rawValue: d["kind"] as? String ?? "") else { return nil }
            return OverlayItem(kind: kind, x: clamp(num(d["x"])), y: clamp(num(d["y"])),
                               width: clamp(num(d["width"])), height: clamp(num(d["height"])),
                               label: d["label"] as? String ?? "", side: d["side"] as? String)
        }
        guard !items.isEmpty else { return }
        let cocoa = WindowCapture.cocoaRect(frame)
        let screen = NSScreen.screens.first { $0.frame.intersects(cocoa) && $0.frame.contains(CGPoint(x: cocoa.midX, y: cocoa.midY)) }
            ?? NSScreen.screens.first { $0.frame.intersects(cocoa) } ?? NSScreen.main
        guard let screen else { return }
        let w = window ?? makeWindow()
        w.setFrame(screen.frame, display: false)
        view.frame = CGRect(origin: .zero, size: screen.frame.size)
        view.target = cocoa.offsetBy(dx: -screen.frame.minX, dy: -screen.frame.minY)
        view.items = items
        view.darkUI = (p["ui_mode"] as? String) == "dark"
        view.accent = accent
        view.needsDisplay = true
        w.alphaValue = 0
        w.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.18
            w.animator().alphaValue = 1
        }
        hideWork?.cancel()
        let seconds = max(5, min(120, num(p["duration_s"], 40)))
        let work = DispatchWorkItem { [weak self] in self?.clear() }
        hideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
    }

    func clear() {
        hideWork?.cancel()
        hideWork = nil
        guard let w = window, w.isVisible else { return }
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.15
            w.animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            guard let self, self.hideWork == nil else { return }  // a new overlay arrived meanwhile
            w.orderOut(nil)
            self.view.items = []
        })
    }

    private func makeWindow() -> NSWindow {
        let w = NSWindow(contentRect: .zero, styleMask: [.borderless], backing: .buffered, defer: false)
        w.isOpaque = false
        w.backgroundColor = .clear
        w.hasShadow = false
        w.ignoresMouseEvents = true
        w.level = .statusBar
        w.sharingType = .none           // never appear in our own screenshots
        w.isReleasedWhenClosed = false
        w.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        w.contentView = view
        window = w
        return w
    }
}

private func num(_ v: Any?, _ d: CGFloat = 0) -> CGFloat {
    if let n = v as? NSNumber { return CGFloat(truncating: n) }
    if let s = v as? String, let d2 = Double(s) { return CGFloat(d2) }
    return d
}

private func clamp(_ v: CGFloat) -> CGFloat { min(1, max(0, v)) }

// MARK: - Glance card

final class GlanceCard {
    private let panel: NSPanel
    private let fx = NSVisualEffectView()
    private let header = NSTextField(labelWithString: "")
    private let body = NSTextField(wrappingLabelWithString: "")
    private let close = NSButton(title: "✕", target: nil, action: nil)
    private let followUp = NSButton(title: "Ask a follow-up", target: nil, action: nil)
    private var hideWork: DispatchWorkItem?
    private let width: CGFloat = 360
    var onFollowUp: (() -> Void)?

    var isVisible: Bool { panel.isVisible }

    init() {
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 360, height: 120),
                        styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: false)
        panel.level = .floating
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.sharingType = .none
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        fx.material = .popover
        fx.state = .active
        fx.wantsLayer = true
        fx.layer?.cornerRadius = 14
        fx.layer?.masksToBounds = true
        header.font = .systemFont(ofSize: 11.5, weight: .semibold)
        header.textColor = .secondaryLabelColor
        header.lineBreakMode = .byTruncatingTail
        body.font = .systemFont(ofSize: 13.5)
        body.isSelectable = true
        body.preferredMaxLayoutWidth = width - 32
        close.isBordered = false
        close.font = .systemFont(ofSize: 12, weight: .medium)
        close.contentTintColor = .secondaryLabelColor
        close.target = self
        close.action = #selector(dismiss)
        followUp.isBordered = false
        followUp.font = .systemFont(ofSize: 12, weight: .medium)
        followUp.contentTintColor = .controlAccentColor
        followUp.target = self
        followUp.action = #selector(askMore)
        [header, body, close, followUp].forEach { fx.addSubview($0) }
        panel.contentView = fx
    }

    /// Show (or update) the card. `anchor` is a Cocoa screen point (usually the cursor).
    func show(app: String, text: String, anchor: CGPoint?, busy: Bool = false, autoHide: TimeInterval? = 60) {
        header.stringValue = app.isEmpty ? "Glance" : "Glance · \(app)"
        body.attributedStringValue = GlanceCard.render(text, busy: busy)
        followUp.isHidden = busy
        let bodyH = ceil(body.attributedStringValue.boundingRect(
            with: CGSize(width: width - 32, height: 600), options: [.usesLineFragmentOrigin, .usesFontLeading]).height)
        let h = 16 + 16 + 8 + bodyH + (busy ? 14 : 38)
        fx.frame = CGRect(x: 0, y: 0, width: width, height: h)
        header.frame = CGRect(x: 16, y: h - 30, width: width - 56, height: 16)
        close.frame = CGRect(x: width - 34, y: h - 32, width: 22, height: 20)
        body.frame = CGRect(x: 16, y: busy ? 14 : 38, width: width - 32, height: bodyH + 2)
        followUp.frame = CGRect(x: 10, y: 10, width: 130, height: 20)
        var frame = panel.frame
        frame.size = CGSize(width: width, height: h)
        if let a = anchor {
            let screen = NSScreen.screens.first { $0.frame.contains(a) } ?? NSScreen.main
            let vis = screen?.visibleFrame ?? CGRect(x: 0, y: 0, width: 1440, height: 900)
            var o = CGPoint(x: a.x + 18, y: a.y - h - 18)
            if o.x + width > vis.maxX - 8 { o.x = a.x - width - 18 }
            if o.y < vis.minY + 8 { o.y = a.y + 18 }
            o.x = min(max(o.x, vis.minX + 8), vis.maxX - width - 8)
            o.y = min(max(o.y, vis.minY + 8), vis.maxY - h - 8)
            frame.origin = o
        } else {
            frame.origin.y += panel.frame.height - h   // keep the top edge in place while growing
        }
        panel.setFrame(frame, display: true)
        panel.orderFrontRegardless()
        hideWork?.cancel()
        if let autoHide {
            let w = DispatchWorkItem { [weak self] in self?.dismiss() }
            hideWork = w
            DispatchQueue.main.asyncAfter(deadline: .now() + autoHide, execute: w)
        }
    }

    @objc func dismiss() {
        hideWork?.cancel()
        panel.orderOut(nil)
    }

    @objc private func askMore() {
        dismiss()
        onFollowUp?()
    }

    /// Plain text with "- " bullets and **bold**, rendered as an attributed string.
    static func render(_ text: String, busy: Bool) -> NSAttributedString {
        let base: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 13.5),
                                                   .foregroundColor: busy ? NSColor.secondaryLabelColor : NSColor.labelColor]
        let out = NSMutableAttributedString()
        let lines = text.trimmingCharacters(in: .whitespacesAndNewlines).components(separatedBy: "\n")
        for (i, raw) in lines.enumerated() {
            var line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("- ") || line.hasPrefix("* ") { line = "•  " + line.dropFirst(2) }
            line = line.replacingOccurrences(of: "^#+\\s*", with: "", options: .regularExpression)
            let parts = line.components(separatedBy: "**")
            for (j, part) in parts.enumerated() {
                var attrs = base
                if j % 2 == 1 { attrs[.font] = NSFont.systemFont(ofSize: 13.5, weight: .semibold) }
                out.append(NSAttributedString(string: part, attributes: attrs))
            }
            if i < lines.count - 1 { out.append(NSAttributedString(string: "\n", attributes: base)) }
        }
        let para = NSMutableParagraphStyle()
        para.lineSpacing = 2
        para.paragraphSpacing = 3
        out.addAttribute(.paragraphStyle, value: para, range: NSRange(location: 0, length: out.length))
        return out
    }
}

// MARK: - Feature

final class VisualFeature: GlintFeature {
    unowned let app: AppDelegate
    private let overlay = OverlayController()
    private let card = GlanceCard()
    private var glanceKey: HotKey?
    private var lastExternalApp: NSRunningApplication?
    private var selfActivatedAt = Date.distantPast
    private var quickShotEnabled = true
    private var glanceEnabled = true
    private var glanceSeq = 0

    init(app: AppDelegate) { self.app = app }

    func start() {
        let me = ProcessInfo.processInfo.processIdentifier
        if let front = NSWorkspace.shared.frontmostApplication, front.processIdentifier != me { lastExternalApp = front }
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] n in
            guard let self, let a = n.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication else { return }
            if a.processIdentifier == me { self.selfActivatedAt = Date() } else { self.lastExternalApp = a }
        }
        glanceKey = HotKey(keyCode: UInt32(kVK_ANSI_G), modifiers: UInt32(controlKey | shiftKey), id: 20) { [weak self] in
            self?.glance()
        }
        let esc: (NSEvent) -> Void = { [weak self] e in
            guard let self, e.keyCode == UInt16(kVK_Escape) else { return }
            if self.card.isVisible { self.card.dismiss(); return }
            // While Quick Ask is up, Esc closes the panel and the marks stay; the next Esc clears them.
            if self.overlay.isVisible && !(self.app.quickPanel?.isVisible ?? false) { self.overlay.clear() }
        }
        NSEvent.addGlobalMonitorForEvents(matching: .keyDown, handler: esc)
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { e in esc(e); return e }
        NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in
            guard let self, self.card.isVisible else { return }
            self.card.dismiss()   // a click elsewhere dismisses the Glance card
        }
        card.onFollowUp = { [weak self] in self?.app.toggleQuickAsk() }
    }

    func settingsChanged(_ s: [String: Any]) {
        quickShotEnabled = s["visual_quick_screenshot"] as? Bool ?? true
        glanceEnabled = s["visual_glance"] as? Bool ?? true
        overlay.accent = Self.accentColor(s["accent"] as? String)
    }

    func handle(action: String, body: [String: Any]) -> Bool {
        switch action {
        case "visual.quickOpened": captureForQuickAsk(); return true
        case "visual.overlay":
            if let p = body["payload"] as? [String: Any] { overlay.apply(p) }
            return true
        case "visual.openScreenSettings":
            WindowCapture.requestPermissionOnce()
            if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") {
                NSWorkspace.shared.open(url)
            }
            return true
        default: return false
        }
    }

    // MARK: Quick Ask screenshot

    private func notifyQuick(_ info: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: info),
              let json = String(data: data, encoding: .utf8) else { return }
        app.quickWeb?.evaluateJavaScript("window.glintVisualShot && window.glintVisualShot(\(json))")
    }

    private func captureForQuickAsk() {
        guard quickShotEnabled, app.engine.ready else { return }
        let opened = Int(Date().timeIntervalSince1970 * 1000)
        // Mark a capture in flight right away so a fast first question waits for it, then decide
        // after the app-activation notification has landed.
        app.engine.request("api/visual/pending", ["opened_at": opened])
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { [weak self] in self?.captureForQuickAsk(opened: opened) }
    }

    private func captureForQuickAsk(opened: Int) {
        // If Glint was already the active app (e.g. the main window), there's no outside window to show.
        guard Date().timeIntervalSince(selfActivatedAt) < 2.0, let target = lastExternalApp, !target.isTerminated else {
            app.engine.request("api/visual/screenshot", ["failed": true, "opened_at": opened, "reason": "no_window"])
            return
        }
        guard WindowCapture.hasPermission else {
            WindowCapture.requestPermissionOnce()
            app.engine.request("api/visual/screenshot", ["failed": true, "opened_at": opened, "reason": "permission"])
            notifyQuick(["ok": false, "reason": "permission"])
            return
        }
        guard let win = WindowCapture.frontWindow(of: target) else {
            app.engine.request("api/visual/screenshot", ["failed": true, "opened_at": opened, "reason": "no_window"])
            return
        }
        WindowCapture.capture(win) { [weak self] jpeg, size in
            guard let self else { return }
            guard let jpeg else {
                self.app.engine.request("api/visual/screenshot", ["failed": true, "opened_at": opened, "reason": "capture"])
                return
            }
            self.overlay.lastFrame = win.frame
            let body: [String: Any] = [
                "opened_at": opened, "image_b64": jpeg.base64EncodedString(), "app": win.app, "title": win.title,
                "width": Int(size.width), "height": Int(size.height),
                "frame": ["x": win.frame.minX, "y": win.frame.minY, "w": win.frame.width, "h": win.frame.height],
            ]
            self.app.engine.request("api/visual/screenshot", body) { json, _ in
                let ok = json?["attached"] as? Bool ?? false
                self.notifyQuick(["ok": ok, "app": win.app, "title": win.title, "reason": json?["reason"] ?? ""])
            }
        }
    }

    // MARK: Glance

    private func glance() {
        guard glanceEnabled, app.engine.ready else { return }
        let me = ProcessInfo.processInfo.processIdentifier
        var target = NSWorkspace.shared.frontmostApplication
        if target?.processIdentifier == me { target = lastExternalApp }
        let anchor = NSEvent.mouseLocation
        guard WindowCapture.hasPermission else {
            WindowCapture.requestPermissionOnce()
            card.show(app: "", text: "Glance needs **Screen Recording** permission. Turn on Glint in System Settings › Privacy & Security › Screen Recording, then try again.",
                      anchor: anchor, autoHide: 12)
            return
        }
        guard let target, let win = WindowCapture.frontWindow(of: target) else {
            card.show(app: "", text: "There's no window to look at.", anchor: anchor, autoHide: 3)
            return
        }
        glanceSeq += 1
        let seq = glanceSeq
        card.show(app: win.app, text: "Looking…", anchor: anchor, busy: true, autoHide: nil)
        WindowCapture.capture(win) { [weak self] jpeg, _ in
            guard let self, seq == self.glanceSeq else { return }
            guard let jpeg else {
                self.card.show(app: win.app, text: "Couldn't capture this window.", anchor: nil, autoHide: 4)
                return
            }
            self.app.engine.request("api/visual/glance", ["image_b64": jpeg.base64EncodedString(), "app": win.app,
                                                          "title": win.title], timeout: 90) { json, err in
                guard seq == self.glanceSeq, self.card.isVisible else { return }  // dismissed meanwhile
                let text = err ?? (json?["text"] as? String) ?? "Nothing to explain here."
                self.card.show(app: win.app, text: text, anchor: nil, autoHide: err == nil ? 90 : 8)
            }
        }
    }

    static func accentColor(_ name: String?) -> NSColor {
        let hex: [String: UInt32] = ["green": 0x2FAE7C, "teal": 0x1F9FB0, "blue": 0x3B82F6, "violet": 0x8B5CF6,
                                     "rose": 0xE5487A, "amber": 0xE59A1F, "slate": 0x64748B]
        guard let v = hex[name ?? ""] else { return NSColor(srgbRed: 0.18, green: 0.68, blue: 0.49, alpha: 1) }
        return NSColor(srgbRed: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255,
                       blue: CGFloat(v & 0xFF) / 255, alpha: 1)
    }
}
