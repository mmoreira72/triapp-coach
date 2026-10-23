import Foundation
import Capacitor
#if canImport(FoundationModels)
import FoundationModels
#endif

/// Apple's on-device language model (Foundation Models, iOS 26+ on Apple Intelligence devices).
/// JS: Capacitor.Plugins.LocalLLM.isAvailable() -> {available, reason}; generate({system, prompt}) -> {text}
@objc(LocalLLMPlugin)
public class LocalLLMPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "LocalLLMPlugin"
    public let jsName = "LocalLLM"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "isAvailable", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "generate", returnType: CAPPluginReturnPromise)
    ]

    @objc func isAvailable(_ call: CAPPluginCall) {
        #if canImport(FoundationModels)
        if #available(iOS 26.0, *) {
            switch SystemLanguageModel.default.availability {
            case .available:
                call.resolve(["available": true, "reason": ""])
            case .unavailable(let why):
                call.resolve(["available": false, "reason": String(describing: why)])
            }
            return
        }
        #endif
        call.resolve(["available": false, "reason": "requires iOS 26"])
    }

    @objc func generate(_ call: CAPPluginCall) {
        guard let prompt = call.getString("prompt"), !prompt.isEmpty else {
            call.reject("prompt vazio")
            return
        }
        let system = call.getString("system") ?? ""
        #if canImport(FoundationModels)
        if #available(iOS 26.0, *) {
            Task {
                do {
                    let session = LanguageModelSession(instructions: system)
                    let r = try await session.respond(to: prompt)
                    call.resolve(["text": r.content])
                } catch {
                    call.reject("modelo do iPhone falhou: \(error.localizedDescription)")
                }
            }
            return
        }
        #endif
        call.reject("requires iOS 26")
    }
}
