// Glint native diagnostics: ~/Library/Logs/Glint-native.log (decisions and failures, never typed text).
import Foundation

enum NativeLog {
    private static let url = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/Glint-native.log")
    private static let lock = NSLock()
    private static var lastWrite: [String: Date] = [:]
    private static let formatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    /// Append a line; repeated lines with the same key are written at most once per `every` seconds.
    static func write(_ key: String, _ message: String, every: TimeInterval = 5) {
        lock.lock()
        defer { lock.unlock() }
        let now = Date()
        if let last = lastWrite[key], now.timeIntervalSince(last) < every { return }
        lastWrite[key] = now
        let line = "[\(formatter.string(from: now))] \(message)\n"
        if let h = try? FileHandle(forWritingTo: url) {
            h.seekToEndOfFile()
            h.write(line.data(using: .utf8)!)
            try? h.close()
        } else {
            try? line.data(using: .utf8)!.write(to: url)
        }
    }
}
