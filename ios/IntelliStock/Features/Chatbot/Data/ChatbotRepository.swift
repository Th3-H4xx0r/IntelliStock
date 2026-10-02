import Foundation

/// HTTP wrapper for all `/chatbot/*` and `/models` endpoints, ported from
/// features/chatbot/data/chatbot_repository.dart. Every method returns a
/// decoded model; callers never touch raw JSON (except `tools`).
nonisolated struct ChatbotRepository: Sendable {
    let client: ApiClient

    // MARK: Conversations

    /// GET /chatbot/conversations → `{conversations: [...]}`
    func conversations() async throws -> [Conversation] {
        let data = try await client.get("/chatbot/conversations")
        return data["conversations"].objectElements.map(Conversation.init(json:))
    }

    /// POST /chatbot/conversations  body: `{model_id?, title?}`
    func createConversation(modelId: String? = nil, title: String? = nil) async throws -> Conversation {
        var body: JSONObject = [:]
        if let modelId { body["model_id"] = .string(modelId) }
        if let title { body["title"] = .string(title) }
        return Conversation(json: try await client.post("/chatbot/conversations", body: .object(body)))
    }

    /// GET /chatbot/conversations/:id
    func conversation(_ id: String) async throws -> Conversation {
        Conversation(json: try await client.get("/chatbot/conversations/\(id)"))
    }

    /// PATCH /chatbot/conversations/:id  body: `{model_id?}` or
    /// `{auto_confirm_safe_tools}`
    func patchConversation(_ id: String, _ body: JSONObject) async throws -> Conversation {
        Conversation(json: try await client.patch("/chatbot/conversations/\(id)", body: .object(body)))
    }

    /// DELETE /chatbot/conversations/:id
    func deleteConversation(_ id: String) async throws {
        _ = try await client.delete("/chatbot/conversations/\(id)")
    }

    /// POST /chatbot/conversations/:id/clear
    func clear(_ id: String) async throws -> Conversation {
        Conversation(json: try await client.post("/chatbot/conversations/\(id)/clear"))
    }

    // MARK: Messaging

    /// POST /chatbot/conversations/:id/turn  body: `{content}`
    ///
    /// Synchronous (no streaming) — returns `{messages: [...]}` once the full
    /// turn is complete (user echo + assistant reply + optional tool
    /// messages).
    func turn(_ id: String, _ content: String) async throws -> [ChatMessage] {
        let data = try await client.post("/chatbot/conversations/\(id)/turn", body: ["content": .string(content)])
        return data["messages"].objectElements.map(ChatMessage.init(json:))
    }

    /// POST /chatbot/conversations/:id/confirm-tool  body: `{message_id, approved}`
    func confirmTool(_ conversationId: String, _ messageId: String, _ approved: Bool) async throws -> JSONObject {
        try await client.post(
            "/chatbot/conversations/\(conversationId)/confirm-tool",
            body: ["message_id": .string(messageId), "approved": .bool(approved)]
        ).orderedObjectValue
    }

    // MARK: Catalog

    /// GET /chatbot/tools → `{tools: [...]}`
    func tools() async throws -> [JSONObject] {
        try await client.get("/chatbot/tools")["tools"].objectElements.map(\.orderedObjectValue)
    }

    // MARK: Model picker

    /// GET /models — reused for the model picker. Accepts `{models: [...]}`,
    /// `{data: [...]}` or a bare list.
    func models() async throws -> [ChatModel] {
        let data = try await client.get("/models")
        let raw: JSON
        if data.isObject {
            raw = data["models"].or(data["data"])
        } else {
            raw = data
        }
        return raw.objectElements.map(ChatModel.init(json:))
    }
}
