// glint-capture: snapshot the text of the frontmost window.
//
//   1. Walk the macOS Accessibility (AX) tree of the focused window and collect
//      text-bearing values.
//   2. If that text is thin (< 200 chars) and --ocr is given, screenshot just that
//      window and run Apple Vision OCR on it.
//
// Prints one JSON object to stdout. Needs Accessibility permission for the app
// that launches it (Terminal / VS Code); OCR also needs Screen Recording.
import AppKit
import ApplicationServices
import Foundation
import Vision

struct Snapshot: Codable {
    var app: String
    var bundle_id: String?
    var title: String
    var url: String?
    var text: String
    var source: String
    var ax_trusted: Bool
    var note: String?
}

let maxNodes = 8000
let maxChars = 40000
let thinThreshold = 200
let args = CommandLine.arguments
let allowOCR = args.contains("--ocr") || args.contains("--force-ocr")
let forceOCR = args.contains("--force-ocr")

// --icon <path-to-.app> <out.png>: render an app's Finder icon to a 64x64 PNG (used by the UI).
if let i = args.firstIndex(of: "--icon"), args.count > i + 2 {
    let icon = NSWorkspace.shared.icon(forFile: args[i + 1])
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 64, pixelsHigh: 64, bitsPerSample: 8,
                                     samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                     colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { exit(1) }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    icon.draw(in: NSRect(x: 0, y: 0, width: 64, height: 64))
    NSGraphicsContext.restoreGraphicsState()
    guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
    do { try png.write(to: URL(fileURLWithPath: args[i + 2])) } catch { exit(1) }
    exit(0)
}

func attr(_ el: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(el, name as CFString, &value) == .success ? value : nil
}

func string(_ el: AXUIElement, _ name: String) -> String? {
    attr(el, name) as? String
}

// Roles whose AXValue is UI chrome rather than content.
let skipRoles: Set<String> = ["AXScrollBar", "AXSlider", "AXProgressIndicator", "AXValueIndicator",
                              "AXMenuBar", "AXMenu", "AXToolbar"]

func collectText(root: AXUIElement) -> (String, String?) {
    var lines: [String] = []
    var chars = 0
    var visited = 0
    var url: String?
    var stack: [(AXUIElement, Int)] = [(root, 0)]

    while let (el, depth) = stack.popLast() {
        visited += 1
        if visited > maxNodes || chars > maxChars { break }
        if depth > 80 { continue }
        let role = string(el, kAXRoleAttribute) ?? ""
        if skipRoles.contains(role) { continue }
        // Never read password fields.
        if string(el, kAXSubroleAttribute) == "AXSecureTextField" { continue }

        if url == nil, role == "AXWebArea", let u = attr(el, "AXURL") as? URL {
            url = u.absoluteString
        }
        if let v = string(el, kAXValueAttribute) {
            let t = v.trimmingCharacters(in: .whitespacesAndNewlines)
            if t.rangeOfCharacter(from: .alphanumerics) != nil && t != lines.last {
                lines.append(t)
                chars += t.count
            }
        }
        if let kids = attr(el, kAXChildrenAttribute) as? [AXUIElement] {
            // Push in reverse so we read top-to-bottom.
            for k in kids.reversed() { stack.append((k, depth + 1)) }
        }
    }
    return (lines.joined(separator: "\n"), url)
}

func frontWindowID(pid: pid_t) -> CGWindowID? {
    let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let info = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else {
        return nil
    }
    for w in info {
        if (w[kCGWindowOwnerPID as String] as? pid_t) == pid,
           (w[kCGWindowLayer as String] as? Int) == 0,
           let id = w[kCGWindowNumber as String] as? CGWindowID {
            return id
        }
    }
    return nil
}

func ocrWindow(pid: pid_t) -> (String, String?) {
    guard let wid = frontWindowID(pid: pid) else { return ("", "no on-screen window to OCR") }
    let png = FileManager.default.temporaryDirectory
        .appendingPathComponent("glint-capture-\(UUID().uuidString).png")
    defer { try? FileManager.default.removeItem(at: png) }

    let proc = Process()
    proc.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    proc.arguments = ["-x", "-o", "-l", String(wid), png.path]
    do { try proc.run(); proc.waitUntilExit() } catch { return ("", "screencapture failed: \(error)") }
    guard FileManager.default.fileExists(atPath: png.path) else {
        return ("", "screenshot failed; grant Screen Recording to the launching app")
    }

    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = true
    do { try VNImageRequestHandler(url: png).perform([req]) } catch {
        return ("", "OCR failed: \(error)")
    }
    // Vision's origin is bottom-left: sort top-to-bottom, then left-to-right.
    let obs = (req.results ?? []).sorted {
        abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.01
            ? $0.boundingBox.midY > $1.boundingBox.midY
            : $0.boundingBox.minX < $1.boundingBox.minX
    }
    let text = obs.compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
    return (text, text.isEmpty ? "OCR found no text (Screen Recording may be denied)" : nil)
}

guard let app = NSWorkspace.shared.frontmostApplication else {
    FileHandle.standardError.write("no frontmost application\n".data(using: .utf8)!)
    exit(1)
}
let pid = app.processIdentifier
let trusted = AXIsProcessTrusted()
let axApp = AXUIElementCreateApplication(pid)

// Chromium and Electron apps only expose their web content tree on request.
// Skipped for code editors, which react by offering screen-reader mode.
let noManualAX: Set<String> = ["com.microsoft.VSCode", "com.microsoft.VSCodeInsiders",
                               "com.todesktop.230313mzl4w4u92", "com.exafunction.windsurf"]
// --peek: only the frontmost app and window title (cheap; polled ~1/s to detect focus changes).
if args.contains("--peek") {
    var w: AXUIElement?
    if let v = attr(axApp, kAXFocusedWindowAttribute) { w = (v as! AXUIElement) }
    let t = w.flatMap { string($0, kAXTitleAttribute) } ?? ""
    let peek = Snapshot(app: app.localizedName ?? "unknown", bundle_id: app.bundleIdentifier, title: t,
                        url: nil, text: "", source: "peek", ax_trusted: trusted, note: nil)
    print(String(data: try! JSONEncoder().encode(peek), encoding: .utf8)!)
    exit(0)
}

var manualAX = false
if !noManualAX.contains(app.bundleIdentifier ?? ""),
   AXUIElementSetAttributeValue(axApp, "AXManualAccessibility" as CFString, kCFBooleanTrue) == .success {
    manualAX = true
    usleep(250_000)
}

var window: AXUIElement?
if let w = attr(axApp, kAXFocusedWindowAttribute) { window = (w as! AXUIElement) }
else if let w = attr(axApp, kAXMainWindowAttribute) { window = (w as! AXUIElement) }

var title = window.flatMap { string($0, kAXTitleAttribute) } ?? ""
var (text, url) = window.map { collectText(root: $0) } ?? ("", nil)
// The web tree builds lazily after AXManualAccessibility is first enabled.
if manualAX && text.count < thinThreshold, let w = window {
    usleep(900_000)
    (text, url) = collectText(root: w)
}
var source = "ax"
var note: String? = trusted ? nil
    : "Accessibility not granted: System Settings → Privacy & Security → Accessibility"

if allowOCR && (forceOCR || text.count < thinThreshold) {
    let (ocrText, ocrNote) = ocrWindow(pid: pid)
    if ocrText.count > text.count {
        text = ocrText
        source = "ocr"
    }
    if let n = ocrNote { note = [note, n].compactMap { $0 }.joined(separator: "; ") }
}
if title.isEmpty { title = app.localizedName ?? "" }

let snap = Snapshot(app: app.localizedName ?? "unknown", bundle_id: app.bundleIdentifier,
                    title: title, url: url, text: text, source: source,
                    ax_trusted: trusted, note: note)
let enc = JSONEncoder()
enc.outputFormatting = [.withoutEscapingSlashes]
print(String(data: try! enc.encode(snap), encoding: .utf8)!)
