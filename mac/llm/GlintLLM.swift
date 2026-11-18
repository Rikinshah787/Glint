// glint-llm: Apple's on-device language model (FoundationModels) as a JSON-lines server.
//
// The engine starts this once and keeps it running so the model stays warm.
//   stdin : {"id": "1", "instructions": "...", "prompt": "...", "max_tokens": 200,
//            "temperature": 0.3, "stream": false}
//   stdout: {"id": "1", "text": "..."}            final answer
//           {"id": "1", "delta": "..."}           streamed piece (when stream is true)
//           {"id": "1", "error": "...", "code": "context|guardrail|unavailable|other"}
// First line written: {"ready": true|false, "availability": "..."}.
import Foundation
import FoundationModels

let out = FileHandle.standardOutput
let writeLock = NSLock()

func emit(_ obj: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj) else { return }
    writeLock.lock()
    out.write(data)
    out.write("\n".data(using: .utf8)!)
    writeLock.unlock()
}

let model = SystemLanguageModel.default
var available = false
switch model.availability {
case .available:
    available = true
    emit(["ready": true, "availability": "available"])
case .unavailable(let reason):
    emit(["ready": false, "availability": "\(reason)"])
}

if available {
    LanguageModelSession().prewarm()
}

func errorCode(_ error: Error) -> String {
    let text = String(describing: error)
    if text.contains("exceededContextWindowSize") { return "context" }
    if text.contains("guardrailViolation") || text.contains("refusal") { return "guardrail" }
    if text.contains("assetsUnavailable") || text.contains("unavailable") { return "unavailable" }
    return "other"
}

func handle(_ req: [String: Any]) async {
    let id = (req["id"] as? String) ?? "?"
    guard available else {
        emit(["id": id, "error": "Apple Intelligence model is not available on this Mac", "code": "unavailable"])
        return
    }
    let instructions = (req["instructions"] as? String) ?? ""
    let prompt = (req["prompt"] as? String) ?? ""
    var options = GenerationOptions()
    if let t = req["temperature"] as? Double { options.temperature = t }
    if let n = req["max_tokens"] as? Int { options.maximumResponseTokens = n }
    let session = LanguageModelSession(instructions: instructions)
    do {
        if req["stream"] as? Bool == true {
            var sent = ""
            for try await snapshot in session.streamResponse(to: prompt, options: options) {
                let full = snapshot.content
                if full.hasPrefix(sent) {
                    let delta = String(full.dropFirst(sent.count))
                    if !delta.isEmpty { emit(["id": id, "delta": delta]) }
                }
                sent = full
            }
            emit(["id": id, "text": sent])
        } else {
            let response = try await session.respond(to: prompt, options: options)
            emit(["id": id, "text": response.content])
        }
    } catch {
        emit(["id": id, "error": String(describing: error), "code": errorCode(error)])
    }
}

// Read requests on a background thread; answer each concurrently.
let inFlight = DispatchGroup()
Thread.detachNewThread {
    while let line = readLine(strippingNewline: true) {
        guard let data = line.data(using: .utf8),
              let req = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
        inFlight.enter()
        Task { await handle(req); inFlight.leave() }
    }
    inFlight.wait()  // engine closed stdin: finish what was asked, then exit
    exit(0)
}
dispatchMain()
