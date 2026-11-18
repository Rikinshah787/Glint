// Native feature registry.
//
// Each feature lives in its own file (mac/<Name>.swift), implements GlintFeature, and is added
// to makeFeatures() below. The app delegate calls start() at launch, engineReady() once the local
// engine answers, settingsChanged() whenever settings are re-read (every ~30 s), and handle() for
// messages posted by web pages via window.webkit.messageHandlers.glint.postMessage({action, ...}).
import AppKit

protocol GlintFeature: AnyObject {
    /// Called once at launch, before the engine is ready.
    func start()
    /// Called when the engine is up (engine API calls are safe from here on).
    func engineReady()
    /// Called with the latest settings dictionary from the engine.
    func settingsChanged(_ settings: [String: Any])
    /// Return true if this feature handled the web message.
    func handle(action: String, body: [String: Any]) -> Bool
}

extension GlintFeature {
    func start() {}
    func engineReady() {}
    func settingsChanged(_ settings: [String: Any]) {}
    func handle(action: String, body: [String: Any]) -> Bool { false }
}

/// Add one line per feature, e.g. `AutocompleteFeature(app: app),`
func makeFeatures(app: AppDelegate) -> [GlintFeature] {
    return [
        AssistFeature(app: app),
        AutocompleteFeature(app: app),
        ShellFeature(app: app),
        VisualFeature(app: app),
    ]
}
