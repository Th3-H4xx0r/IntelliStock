import Foundation
import Testing
@testable import IntelliStock

/// Ported from test/features/chatbot/chat_models_test.dart.
struct ChatMessageTests {
    @Test func parsesBasicUserMessage() {
        let msg = ChatMessage(json: [
            "id": "abc",
            "role": "user",
            "content": "Hello",
            "created_at": "2026-06-01T12:00:00Z",
            "status": nil,
        ])
        #expect(msg.id == "abc")
        #expect(msg.role == "user")
        #expect(msg.content == "Hello")
        #expect(msg.createdAt != nil)
        #expect(!msg.isPendingConfirmation)
    }

    @Test func detectsPendingConfirmationStatus() {
        let msg = ChatMessage(json: [
            "id": "tc-1",
            "role": "assistant",
            "content": nil,
            "status": "pending_confirmation",
            "tool_calls": [
                ["id": "tc-call-1", "name": "list_instances", "arguments": ["limit": 5], "safety": "safe"],
            ],
        ])
        #expect(msg.isPendingConfirmation)
        #expect(msg.pendingTool != nil)
        #expect(msg.pendingTool?.name == "list_instances")
        #expect(msg.pendingTool?.safety == "safe")
        #expect(msg.pendingTool?.arguments["limit"] == 5)
    }

    @Test func pendingToolFromDedicatedKeyWinsOverFirstToolCall() {
        let msg = ChatMessage(json: [
            "id": "tc-2",
            "role": "assistant",
            "status": "pending_confirmation",
            "pending_tool": ["id": "pt-explicit", "name": "delete_backtest", "arguments": ["id": "x"], "safety": "destructive"],
            "tool_calls": [["id": "tc-fallback", "name": "read_only", "arguments": [:]]],
        ])
        #expect(msg.pendingTool?.name == "delete_backtest")
        #expect(msg.pendingTool?.safety == "destructive")
    }

    @Test func handlesMissingOrNullFieldsGracefully() {
        let msg = ChatMessage(json: [:])
        #expect(msg.id == "")
        #expect(msg.role == "assistant")
        #expect(msg.content == nil)
        #expect(msg.createdAt == nil)
        #expect(msg.toolCalls.isEmpty)
        #expect(msg.blocks.isEmpty)
        #expect(!msg.isPendingConfirmation)
    }

    @Test func parsesRichBlocksList() {
        let msg = ChatMessage(json: [
            "id": "blk",
            "role": "assistant",
            "blocks": [["type": "markdown", "content": "**hi**"], ["type": "navigate", "route": "/dashboard"]],
        ])
        #expect(msg.blocks.count == 2)
        #expect(msg.blocks[0]["type"] == "markdown")
        #expect(msg.blocks[1]["type"] == "navigate")
        #expect(msg.blocks[1]["route"] == "/dashboard")
    }

    @Test func parsesToolRoleMessageWithName() {
        let msg = ChatMessage(json: ["id": "tr-1", "role": "tool", "name": "list_instances", "content": #"{"instances": []}"#])
        #expect(msg.role == "tool")
        #expect(msg.name == "list_instances")
    }
}

struct ConversationTests {
    @Test func parsesCompleteConversationWithMessages() {
        let conv = Conversation(json: [
            "id": "conv-1",
            "title": "My Chat",
            "model_id": "claude-opus-4-5",
            "model_name": "Claude Opus",
            "auto_confirm_safe_tools": true,
            "message_count": 3,
            "messages": [
                ["id": "m1", "role": "user", "content": "Hi"],
                ["id": "m2", "role": "assistant", "content": "Hello"],
            ],
        ])
        #expect(conv.id == "conv-1")
        #expect(conv.title == "My Chat")
        #expect(conv.modelId == "claude-opus-4-5")
        #expect(conv.autoConfirmSafeTools)
        #expect(conv.messages.count == 2)
        #expect(conv.messageCount == 3)
    }

    @Test func handlesEmptyOrMissingMessages() {
        let conv = Conversation(json: ["id": "x"])
        #expect(conv.messages.isEmpty)
        #expect(!conv.autoConfirmSafeTools)
        #expect(conv.messageCount == 0)
    }

    @Test func readsAutoConfirmSafeToolsNestedUnderSettings() {
        // The backend stores/returns the flag at settings.auto_confirm_safe_tools,
        // NOT at the top level — parsing the wrong place made the toggle revert.
        #expect(Conversation(json: ["id": "c", "settings": ["auto_confirm_safe_tools": true]]).autoConfirmSafeTools)
        #expect(!Conversation(json: ["id": "c", "settings": ["auto_confirm_safe_tools": false]]).autoConfirmSafeTools)
    }

    @Test func copyWithPreservesUnchangedFields() {
        let original = Conversation(id: "x", title: "Old", modelId: "mod")
        let updated = original.copyWith(title: "New")
        #expect(updated.title == "New")
        #expect(updated.modelId == "mod")
        #expect(updated.id == "x")
    }
}

struct ToolCallTests {
    @Test func mapsIdVariants() {
        let tc = ToolCall(json: [
            "tool_call_id": "tc-abc",
            "function": "my_func",
            "input": ["key": "val"],
            "safety": "write",
            "description": "Does stuff",
        ])
        #expect(tc.id == "tc-abc")
        #expect(tc.name == "my_func")
        #expect(tc.arguments["key"] == "val")
        #expect(tc.safety == "write")
        #expect(tc.description == "Does stuff")
    }

    @Test func defaultsSafetyToWrite() {
        #expect(ToolCall(json: ["id": "x", "name": "foo"]).safety == "write")
    }
}

struct NavigateDirectiveTests {
    @Test func identifiesNavigateBlocksInMessages() {
        let msg = ChatMessage(json: [
            "id": "nav-1",
            "role": "assistant",
            "content": "Taking you to dashboard.",
            "blocks": [["type": "navigate", "route": "/dashboard"]],
        ])
        let navigates = msg.blocks
            .filter { $0["type"] == "navigate" }
            .map { $0["route"]?.string ?? "" }
            .filter { !$0.isEmpty }
        #expect(navigates == ["/dashboard"])
    }

    @Test func filtersOutNavigateBlocksFromVisibleList() {
        let msg = ChatMessage(json: [
            "id": "nav-2",
            "role": "assistant",
            "blocks": [
                ["type": "markdown", "content": "Hello"],
                ["type": "navigate", "route": "/instances"],
                ["type": "stat", "label": "Count", "value": "5"],
            ],
        ])
        let visible = msg.blocks.filter { $0["type"] != "navigate" }
        #expect(visible.count == 2)
        #expect(!visible.contains { $0["type"] == "navigate" })
    }
}

struct ChatModelTests {
    @Test func parsesStandardModelEntry() {
        let m = ChatModel(json: ["id": "claude-opus-4-5", "name": "Claude Opus 4.5", "provider": "anthropic", "model": "claude-opus-4-5-20251201"])
        #expect(m.id == "claude-opus-4-5")
        #expect(m.name == "Claude Opus 4.5")
        #expect(m.provider == "anthropic")
    }

    @Test func fallsBackToIdAsName() {
        #expect(ChatModel(json: ["id": "some-model"]).name == "some-model")
    }
}

struct ChatbotRepositoryTests {
    @Test func modelsAcceptsModelsDataOrABareList() async throws {
        let stub = DataStub(json: #"{"models": [{"id": "a"}]}"#)
        let repo = ChatbotRepository(client: stub.client)
        #expect(try await repo.models().map(\.id) == ["a"])
        stub.respond(json: #"{"data": [{"id": "b"}]}"#)
        #expect(try await repo.models().map(\.id) == ["b"])
        stub.respond(json: #"[{"id": "c"}, 3]"#)
        #expect(try await repo.models().map(\.id) == ["c"])
        #expect(stub.last?.path == "/models")
    }

    @Test func createConversationSendsOnlyTheGivenKeys() async throws {
        let stub = DataStub(json: #"{"id": "n"}"#)
        let repo = ChatbotRepository(client: stub.client)
        _ = try await repo.createConversation()
        #expect(stub.last?.jsonBody == [:])
        _ = try await repo.createConversation(modelId: "m", title: "t")
        #expect(stub.last?.jsonBody == ["model_id": "m", "title": "t"])
    }

    @Test func confirmToolBody() async throws {
        let stub = DataStub()
        _ = try await ChatbotRepository(client: stub.client).confirmTool("c1", "m1", true)
        #expect(stub.last?.path == "/chatbot/conversations/c1/confirm-tool")
        #expect(stub.last?.jsonBody == ["message_id": "m1", "approved": true])
    }
}
