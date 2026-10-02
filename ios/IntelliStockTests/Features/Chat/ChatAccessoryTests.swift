import Testing
@testable import IntelliStock

/// What the tab bar's chat accessory names (spec 2026-10-02, G1).
struct ChatAccessoryTests {
    private func state(modelId: String?, modelName: String?, catalog: [ChatModel] = []) -> ChatbotState {
        var s = ChatbotState()
        s.activeConversation = Conversation(id: "c1", modelId: modelId, modelName: modelName)
        s.models = catalog
        return s
    }

    @Test func namesTheActiveConversationsModel() {
        #expect(ChatAccessory.modelName(state(modelId: "m1", modelName: "Claude Opus")) == "Claude Opus")
    }

    @Test func fallsBackToTheCatalogNameForTheModelId() {
        let catalog = [ChatModel(id: "m0", name: "Other"), ChatModel(id: "m1", name: "OpenRouter / Gemini")]
        #expect(ChatAccessory.modelName(state(modelId: "m1", modelName: nil, catalog: catalog)) == "OpenRouter / Gemini")
        #expect(ChatAccessory.modelName(state(modelId: "m1", modelName: "  ", catalog: catalog)) == "OpenRouter / Gemini")
    }

    @Test func namesNothingUntilAModelIsPicked() {
        #expect(ChatAccessory.modelName(nil) == nil)
        #expect(ChatAccessory.modelName(ChatbotState()) == nil) // no conversation yet
        #expect(ChatAccessory.modelName(state(modelId: nil, modelName: "Stale")) == nil)
        #expect(ChatAccessory.modelName(state(modelId: "", modelName: "Stale")) == nil)
        #expect(ChatAccessory.modelName(state(modelId: "m9", modelName: nil)) == nil) // not in the catalog
    }

    @Test func showsTheModelPartOfAProviderSlashModelName() {
        #expect(ChatAccessory.shortName("OpenRouter / Gemini 3.7 Flash") == "Gemini 3.7 Flash")
        #expect(ChatAccessory.shortName("Azure / gpt-oss-120b — High") == "gpt-oss-120b — High")
        #expect(ChatAccessory.shortName("Claude Opus") == "Claude Opus")
        #expect(ChatAccessory.shortName("A / B / C") == "C")
        #expect(ChatAccessory.shortName("Broken / ") == "Broken / ")
        #expect(ChatAccessory.shortName("ab/cd") == "ab/cd") // only the spaced separator
    }
}
