import Foundation
import Testing
@testable import IntelliStock

/// A routing stub for the chatbot endpoints: answers by `METHOD path`.
nonisolated final class ChatStubRoutes: @unchecked Sendable {
    private let lock = NSLock()
    private var routes: [String: (Int, String)] = [:]
    /// Keeps the stub registered for as long as the routes live (a test that
    /// drops its stub would otherwise send requests to the real network).
    var stub: DataStub?

    func set(_ key: String, _ json: String, status: Int = 200) {
        lock.withLock { routes[key] = (status, json) }
    }

    func answer(_ request: URLRequest) -> (status: Int, body: String) {
        let key = "\(request.method) \(request.path)"
        return lock.withLock { routes[key] } ?? (404, #"{"detail":"no route \#(key)"}"#)
    }
}

@MainActor
@Suite struct ChatbotModelTests {
    private func make() -> (ChatbotModel, DataStub, ChatStubRoutes) {
        let stub = DataStub()
        let routes = ChatStubRoutes()
        routes.stub = stub
        stub.handler = { routes.answer($0) }
        let client = stub.client
        let model = ChatbotModel(
            repository: { ChatbotRepository(client: client) },
            now: { Date(timeIntervalSince1970: 1_700_000_000) }
        )
        return (model, stub, routes)
    }

    private func keys(_ stub: DataStub) -> [String] {
        stub.requests.map { "\($0.method) \($0.path)" }
    }

    private func body(_ request: URLRequest?) throws -> JSON {
        try JSON(data: request?.httpBody ?? Data())
    }

    @Test func bootstrapLoadsTheListTheFirstConversationAndTheTools() async {
        let (model, stub, routes) = make()
        routes.set("GET /chatbot/conversations", #"{"conversations":[{"id":"c1","title":"One"},{"id":"c2"}]}"#)
        routes.set("GET /chatbot/conversations/c1", #"{"id":"c1","title":"One","model_id":"m1","messages":[{"id":"a","role":"user","content":"hi"}]}"#)
        routes.set("GET /chatbot/tools", #"{"tools":[{"name":"list_instances","safety":"safe"}]}"#)

        await model.bootstrap()
        await model.bootstrap()   // once only
        #expect(keys(stub) == ["GET /chatbot/conversations", "GET /chatbot/conversations/c1", "GET /chatbot/tools"])
        #expect(model.state.conversations.map(\.id) == ["c1", "c2"])
        #expect(model.state.activeConversation?.id == "c1")
        #expect(model.state.lastModelId == "m1")
        #expect(model.state.toolCatalog.count == 1)
        #expect(!model.state.needsModel)
        #expect(model.state.messages.count == 1)
    }

    @Test func bootstrapFailureRecordsTheError() async {
        let (model, _, routes) = make()
        routes.set("GET /chatbot/conversations", #"{"detail":"down"}"#, status: 500)
        await model.bootstrap()
        #expect(model.state.error == "down")
        #expect(model.state.needsModel)
    }

    @Test func openCloseAndToggles() {
        let (model, _, _) = make()
        model.open()
        model.toggleFullscreen()
        #expect(model.state.isOpen && model.state.isFullscreen)
        model.close()
        #expect(!model.state.isOpen && !model.state.isFullscreen)
        model.open()
        model.minimise()
        #expect(!model.state.isOpen)
        model.toggleConversationList()
        #expect(model.state.conversationListOpen)
    }

    @Test func setModelWithNoConversationStartsOne() async throws {
        let (model, stub, routes) = make()
        routes.set("POST /chatbot/conversations", #"{"id":"n1","model_id":"m9"}"#)
        routes.set("GET /chatbot/conversations", #"{"conversations":[{"id":"n1"}]}"#)
        await model.setModel("m9")
        let post = stub.requests.first { $0.method == "POST" }
        #expect(try body(post) == ["model_id": "m9"])
        #expect(model.state.activeConversation?.id == "n1")
        #expect(model.state.lastModelId == "m9")

        // A second new conversation reuses the last model.
        routes.set("POST /chatbot/conversations", #"{"id":"n2","model_id":"m9"}"#)
        await model.startNewConversationFromUI()
        let posts = stub.requests.filter { $0.method == "POST" }
        #expect(try body(posts.last) == ["model_id": "m9"])
    }

    @Test func setModelPatchesTheActiveConversation() async throws {
        let (model, stub, routes) = make()
        routes.set("POST /chatbot/conversations", #"{"id":"c1"}"#)
        routes.set("GET /chatbot/conversations", #"{"conversations":[]}"#)
        await model.startNewConversationFromUI()
        #expect(model.state.needsModel)

        routes.set("PATCH /chatbot/conversations/c1", #"{"id":"c1","model_id":"m2","model_name":"Opus"}"#)
        await model.setModel("m2")
        #expect(stub.last?.method == "PATCH")
        #expect(try body(stub.last) == ["model_id": "m2"])
        #expect(model.state.activeConversation?.modelName == "Opus")
        #expect(model.state.lastModelId == "m2")

        routes.set("PATCH /chatbot/conversations/c1", #"{"id":"c1","model_id":"m2","settings":{"auto_confirm_safe_tools":true}}"#)
        await model.setAutoConfirmSafe(true)
        #expect(try body(stub.last) == ["auto_confirm_safe_tools": true])
        #expect(model.state.activeConversation?.autoConfirmSafeTools == true)
    }

    @Test func sendAppendsOptimisticallyThenReplacesWithTheServerMessages() async throws {
        let (model, stub, routes) = make()
        routes.set("POST /chatbot/conversations", #"{"id":"c1","model_id":"m1"}"#)
        routes.set("GET /chatbot/conversations", #"{"conversations":[{"id":"c1"}]}"#)
        await model.startNewConversationFromUI()

        routes.set("POST /chatbot/conversations/c1/turn", #"{"messages":[{"id":"u1","role":"user","content":"hello"},{"id":"a1","role":"assistant","content":"hi"}]}"#)
        routes.set("GET /chatbot/conversations/c1", #"{"id":"c1","title":"Greeting","model_id":"m1","messages":[{"id":"u1","role":"user","content":"hello"},{"id":"a1","role":"assistant","content":"hi"}]}"#)
        await model.send("  hello  ")

        let turn = stub.requests.first { $0.path == "/chatbot/conversations/c1/turn" }
        #expect(try body(turn) == ["content": "hello"])
        #expect(model.state.messages.map(\.id) == ["u1", "a1"])
        #expect(model.state.activeConversation?.title == "Greeting")
        #expect(!model.state.busy)

        let before = stub.requests.count
        await model.send("   ")
        #expect(stub.requests.count == before)
    }

    @Test func aFailedTurnMarksTheOptimisticMessage() async {
        let (model, _, routes) = make()
        routes.set("POST /chatbot/conversations", #"{"id":"c1","model_id":"m1"}"#)
        routes.set("GET /chatbot/conversations", #"{"conversations":[]}"#)
        await model.startNewConversationFromUI()

        routes.set("POST /chatbot/conversations/c1/turn", #"{"detail":"model offline"}"#, status: 502)
        await model.send("hi")
        let last = model.state.messages.last
        #expect(last?.id == "temp-1700000000000")
        #expect(last?.status == "failed")
        #expect(last?.content == "hi")
        #expect(model.state.error == "model offline")
        #expect(!model.state.busy)

        model.clearError()
        #expect(model.state.error == nil)
    }

    @Test func confirmToolPostsAndReloads() async throws {
        let (model, stub, routes) = make()
        routes.set("POST /chatbot/conversations", #"{"id":"c1","model_id":"m1"}"#)
        routes.set("GET /chatbot/conversations", #"{"conversations":[]}"#)
        await model.startNewConversationFromUI()

        routes.set("POST /chatbot/conversations/c1/confirm-tool", #"{"ok":true}"#)
        routes.set("GET /chatbot/conversations/c1", #"{"id":"c1","model_id":"m1","messages":[{"id":"t","role":"assistant","status":"pending_confirmation","tool_calls":[{"id":"x","name":"stop_instance","safety":"destructive"}]}]}"#)
        await model.confirmTool("msg-1", approved: false)
        let confirm = stub.requests.first { $0.path.hasSuffix("/confirm-tool") }
        #expect(try body(confirm) == ["message_id": "msg-1", "approved": false])
        #expect(model.state.pendingConfirmationMessage?.pendingTool?.name == "stop_instance")
    }

    @Test func clearAndDeleteTheConversation() async {
        let (model, stub, routes) = make()
        routes.set("POST /chatbot/conversations", #"{"id":"c1","model_id":"m1"}"#)
        routes.set("GET /chatbot/conversations", #"{"conversations":[{"id":"c1"},{"id":"c2"}]}"#)
        await model.startNewConversationFromUI()

        routes.set("POST /chatbot/conversations/c1/clear", #"{"id":"c1","model_id":"m1","messages":[]}"#)
        await model.clearConversation()
        #expect(keys(stub).contains("POST /chatbot/conversations/c1/clear"))
        #expect(!model.state.busy)

        routes.set("DELETE /chatbot/conversations/c1", "{}")
        routes.set("GET /chatbot/conversations", #"{"conversations":[{"id":"c2"}]}"#)
        routes.set("GET /chatbot/conversations/c2", #"{"id":"c2","model_id":"m1"}"#)
        await model.deleteConversation()
        #expect(model.state.activeConversation?.id == "c2")
        #expect(!model.state.busy)
    }

    @Test func loadModelsOnlyOnce() async {
        let (model, stub, routes) = make()
        routes.set("GET /models", #"{"models":[{"id":"m1","name":"Opus","provider":"claude-cli","model":"opus"}]}"#)
        await model.loadModels()
        await model.loadModels()
        #expect(stub.requests.count == 1)
        #expect(model.state.models.map(\.name) == ["Opus"])
        #expect(model.state.modelsLoaded)
    }

    @Test func liveNavigateDirectivesFireOnceAndHistoryDoesNot() async {
        let (model, _, routes) = make()
        routes.set("GET /chatbot/conversations", #"{"conversations":[{"id":"c1"}]}"#)
        routes.set("GET /chatbot/conversations/c1", #"{"id":"c1","model_id":"m1","messages":[{"id":"old","role":"assistant","blocks":[{"type":"navigate","route":"/nexus"}]}]}"#)
        routes.set("GET /chatbot/tools", #"{"tools":[]}"#)
        await model.bootstrap()
        #expect(model.takeNavigations().isEmpty)

        routes.set("POST /chatbot/conversations/c1/turn", #"{"messages":[{"id":"n1","role":"assistant","blocks":[{"type":"navigate","route":"/dashboard"},{"type":"navigate","route":"https://evil"}]}]}"#)
        routes.set("GET /chatbot/conversations/c1", #"{"detail":"x"}"#, status: 500)
        await model.send("take me home")
        #expect(model.takeNavigations() == ["/dashboard"])
        #expect(model.takeNavigations().isEmpty)
    }
}

/// `chat_models_test.dart` › navigate-directive parsing, and the dock's rules.
@Suite struct ChatNavigateTests {
    @Test func identifiesNavigateBlocksInMessages() {
        let msg = ChatMessage(json: [
            "id": "nav-1", "role": "assistant", "content": "Taking you to dashboard.",
            "blocks": [["type": "navigate", "route": "/dashboard"]],
        ])
        let directives = ChatNavigate.directives(in: [msg])
        #expect(directives.map(\.route) == ["/dashboard"])
        #expect(directives.first?.key == "nav-1:/dashboard")
    }

    @Test func filtersNavigateBlocksFromTheVisibleList() {
        let msg = ChatMessage(json: [
            "id": "nav-2", "role": "assistant",
            "blocks": [
                ["type": "markdown", "content": "Hello"],
                ["type": "navigate", "route": "/instances"],
                ["type": "stat", "label": "Count", "value": "5"],
            ],
        ])
        let visible = ChatNavigate.visibleBlocks(msg)
        #expect(visible.count == 2)
        #expect(!visible.contains { $0["type"] == "navigate" })
    }

    @Test func allowedRoutes() {
        #expect(ChatNavigate.isAllowedRoute("/dashboard"))
        #expect(ChatNavigate.isAllowedRoute("/instances/abc"))
        #expect(ChatNavigate.isAllowedRoute("/token-usage"))
        #expect(!ChatNavigate.isAllowedRoute("/dashboardx"))
        #expect(!ChatNavigate.isAllowedRoute("dashboard"))
        #expect(!ChatNavigate.isAllowedRoute("//dashboard"))
        #expect(!ChatNavigate.isAllowedRoute("/kalshi"))
    }

    @Test func trackerFiresEachDirectiveOnce() {
        var tracker = ChatNavigateTracker()
        let msg = ChatMessage(json: ["id": "m", "blocks": [["type": "navigate", "route": "/models"], ["type": "navigate", "route": ""]]])
        #expect(tracker.newRoutes(in: [msg]) == ["/models"])
        #expect(tracker.newRoutes(in: [msg]).isEmpty)
    }
}

/// The rich-block and settings helpers.
@Suite struct ChatBlockFormatTests {
    @Test func tableCells() {
        #expect(ChatBlockFormat.cell(.null) == "")
        #expect(ChatBlockFormat.cell(5) == "5")
        #expect(ChatBlockFormat.cell(5.0) == "5")
        #expect(ChatBlockFormat.cell(2.345) == "2.35")
        #expect(ChatBlockFormat.cell("AAPL") == "AAPL")
        #expect(ChatBlockFormat.cell(true) == "true")
    }

    @Test func modelSubtitleTrimsStrayDots() {
        #expect(ChatBlockFormat.modelSubtitle(provider: "openai", model: "gpt") == "openai · gpt")
        #expect(ChatBlockFormat.modelSubtitle(provider: "", model: "gpt") == "gpt")
        #expect(ChatBlockFormat.modelSubtitle(provider: "openai", model: "") == "openai ")
    }

    @Test func toolTextTruncatesAt200() {
        let long = String(repeating: "x", count: 250)
        #expect(ChatBlockFormat.toolText(name: "list", content: long) == "list → " + String(repeating: "x", count: 200) + "…")
        #expect(ChatBlockFormat.toolText(name: nil, content: "ok") == "ok")
    }

    @Test func prettyArguments() {
        #expect(ChatBlockFormat.prettyArguments([:]) == "")
        #expect(ChatBlockFormat.prettyArguments(["a": 1]) == "{\n  \"a\": 1\n}")
    }

    @Test func chartSeries() {
        let portfolio = ChatChartSeries.build(["type": "portfolio", "timestamps": [1, 2, 3], "values": [10, "11.5", "x"]])
        #expect(portfolio.count == 1)
        #expect(portfolio[0].points == [ChatChartPoint(x: "0", y: 10), ChatChartPoint(x: "1", y: 11.5)])
        #expect(portfolio[0].kind == .line)

        let generic = ChatChartSeries.build([
            "type": "chart",
            "series": [["name": "A", "data": [["Mon", 1], ["Tue", 2]]], ["data": [3, 4]]],
        ])
        #expect(generic.map(\.name) == ["A", "Series 1"])
        #expect(generic[0].kind == .bar)
        #expect(generic[0].points.map(\.x) == ["Mon", "Tue"])
        #expect(generic[1].points.map(\.y) == [3, 4])

        let lines = ChatChartSeries.build(["type": "chart", "chart_type": "line", "series": [["data": [1]]]])
        #expect(lines[0].kind == .line)

        let price = ChatChartSeries.build(["type": "price", "series": [["symbol": "AAPL", "points": [["ts": 1, "value": 190]]]]])
        #expect(price[0].name == "AAPL")
        #expect(price[0].points == [ChatChartPoint(x: "0", y: 190)])

        #expect(ChatChartSeries.build(["type": "chart"]).isEmpty)
    }

    @Test func toolGroups() {
        let groups = ChatToolGroups([
            ["name": "list_instances", "safety": "safe"],
            ["function": "start_backtest"],
            ["tool": "delete_instance", "safety": "destructive"],
            ["safety": "safe"],
        ])
        #expect(groups.safe == ["list_instances"])
        #expect(groups.confirm == ["start_backtest"])
        #expect(groups.destructive == ["delete_instance"])
    }

    @Test func toolCardTiers() {
        #expect(ChatToolCallCard.tier("safe").label == "Run tool")
        #expect(ChatToolCallCard.tier("destructive").label == "DESTRUCTIVE — confirm carefully")
        #expect(ChatToolCallCard.tier("write").label == "This will change your workspace")
        #expect(ChatToolCallCard.tier("anything").label == "This will change your workspace")
    }
}
