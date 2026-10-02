import Foundation

// Plain models for the chatbot feature, ported from
// features/chatbot/data/models/chat.dart. Hand-written init(json:),
// nullable-tolerant.

// MARK: - ToolCall

nonisolated struct ToolCall: Hashable, Sendable, Identifiable {
    let id: String
    let name: String
    let arguments: JSONObject
    let description: String?
    /// 'safe' | 'write' | 'destructive'
    let safety: String

    init(id: String, name: String, arguments: JSONObject = [:], description: String? = nil, safety: String = "write") {
        self.id = id
        self.name = name
        self.arguments = arguments
        self.description = description
        self.safety = safety
    }

    init(json j: JSON) {
        self.init(
            id: j["id"].or(j["tool_call_id"]).stringOr(""),
            name: j["name"].or(j["function"]).stringOr(""),
            arguments: j["arguments"].or(j["input"]).orderedObjectValue,
            description: j["description"].string,
            safety: j["safety"].stringOr("write")
        )
    }
}

// MARK: - ChatMessage

/// A single message in a conversation.
///
/// `status` is normally nil or `'sent'`. When the backend is waiting for the
/// operator to approve a tool call it emits `'pending_confirmation'`; the
/// presentation layer then shows the tool-call card instead of a bubble.
nonisolated struct ChatMessage: Hashable, Sendable, Identifiable {
    let id: String
    /// 'user' | 'assistant' | 'tool'
    let role: String
    let content: String?
    let createdAt: Date?
    /// 'pending_confirmation' | 'sending' | 'failed' | nil
    let status: String?
    /// Tool-call chips shown on an assistant message that triggered tools.
    let toolCalls: [ToolCall]
    /// Populated when `status == 'pending_confirmation'`.
    let pendingTool: ToolCall?
    /// Rich rendered blocks (markdown / table / chart / navigate / stat).
    let blocks: [JSONObject]
    /// Tool-role message source name.
    let name: String?

    init(
        id: String,
        role: String,
        content: String? = nil,
        createdAt: Date? = nil,
        status: String? = nil,
        toolCalls: [ToolCall] = [],
        pendingTool: ToolCall? = nil,
        blocks: [JSONObject] = [],
        name: String? = nil
    ) {
        self.id = id
        self.role = role
        self.content = content
        self.createdAt = createdAt
        self.status = status
        self.toolCalls = toolCalls
        self.pendingTool = pendingTool
        self.blocks = blocks
        self.name = name
    }

    var isPendingConfirmation: Bool { status == "pending_confirmation" }

    init(json j: JSON) {
        let toolCalls = j["tool_calls"].objectElements.map(ToolCall.init(json:))

        // pending_tool is either nested under 'pending_tool' or is the first
        // tool_call when status == 'pending_confirmation'.
        var pendingTool: ToolCall?
        if j["pending_tool"].isObject {
            pendingTool = ToolCall(json: j["pending_tool"])
        } else if j["status"].stringOr("") == "pending_confirmation", let first = toolCalls.first {
            pendingTool = first
        }

        self.init(
            id: j["id"].stringOr(""),
            role: j["role"].stringOr("assistant"),
            content: j["content"].string,
            createdAt: chatParseDate(j["created_at"]),
            status: j["status"].string,
            toolCalls: toolCalls,
            pendingTool: pendingTool,
            blocks: j["blocks"].objectElements.map(\.orderedObjectValue),
            name: j["name"].string
        )
    }
}

// MARK: - Conversation

nonisolated struct Conversation: Hashable, Sendable, Identifiable {
    let id: String
    let title: String?
    /// Claude model id (e.g. `claude-opus-4-5`).
    let modelId: String?
    /// Display name of the model (may be absent on list endpoints).
    let modelName: String?
    let autoConfirmSafeTools: Bool
    let messages: [ChatMessage]
    let messageCount: Int

    init(
        id: String,
        title: String? = nil,
        modelId: String? = nil,
        modelName: String? = nil,
        autoConfirmSafeTools: Bool = false,
        messages: [ChatMessage] = [],
        messageCount: Int = 0
    ) {
        self.id = id
        self.title = title
        self.modelId = modelId
        self.modelName = modelName
        self.autoConfirmSafeTools = autoConfirmSafeTools
        self.messages = messages
        self.messageCount = messageCount
    }

    /// Dart `copyWith`: a nil argument keeps the current value.
    func copyWith(
        title: String? = nil,
        modelId: String? = nil,
        modelName: String? = nil,
        autoConfirmSafeTools: Bool? = nil,
        messages: [ChatMessage]? = nil,
        messageCount: Int? = nil
    ) -> Conversation {
        Conversation(
            id: id,
            title: title ?? self.title,
            modelId: modelId ?? self.modelId,
            modelName: modelName ?? self.modelName,
            autoConfirmSafeTools: autoConfirmSafeTools ?? self.autoConfirmSafeTools,
            messages: messages ?? self.messages,
            messageCount: messageCount ?? self.messageCount
        )
    }

    init(json j: JSON) {
        let messages = j["messages"].objectElements.map(ChatMessage.init(json:))

        // The backend stores this under `settings.auto_confirm_safe_tools`;
        // some responses may also carry it at the top level. Check both.
        let autoConfirm = j["auto_confirm_safe_tools"].boolValue
            ?? (j["settings"].isObject ? j["settings"]["auto_confirm_safe_tools"].boolValue : nil)
            ?? false

        self.init(
            id: j["id"].stringOr(""),
            title: j["title"].string,
            modelId: j["model_id"].string,
            modelName: j["model_name"].string,
            autoConfirmSafeTools: autoConfirm,
            messages: messages,
            messageCount: j["message_count"].int ?? messages.count
        )
    }
}

// MARK: - Model (for the model picker)

nonisolated struct ChatModel: Hashable, Sendable, Identifiable {
    let id: String
    let name: String
    let provider: String
    let model: String

    init(id: String, name: String, provider: String = "", model: String = "") {
        self.id = id
        self.name = name
        self.provider = provider
        self.model = model
    }

    init(json j: JSON) {
        self.init(
            id: j["id"].stringOr(""),
            name: j["name"].or(j["id"]).stringOr(""),
            provider: j["provider"].stringOr(""),
            model: j["model"].stringOr("")
        )
    }
}

// MARK: - Helpers

/// `_parseDate`: nil for null, else `DateTime.parse(v.toString())` or nil.
nonisolated func chatParseDate(_ v: JSON) -> Date? {
    v.isNull ? nil : DartDateTime.tryParse(v.dartDescription)
}
