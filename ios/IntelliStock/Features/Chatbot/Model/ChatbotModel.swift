import Foundation
import Observation

/// The chatbot's state — `ChatbotState` in `chatbot_controller.dart`.
nonisolated struct ChatbotState: Sendable {
    /// Whether the chat is expanded (false = the floating button only).
    var isOpen = false
    /// The panel takes the whole screen (the sheet's large detent).
    var isFullscreen = false
    var conversations: [Conversation] = []
    /// The currently loaded conversation (with full messages).
    var activeConversation: Conversation?
    /// True while a network call is in flight (turn / confirmTool / load).
    var busy = false
    var error: String?
    var conversationListOpen = false
    var models: [ChatModel] = []
    var modelsLoaded = false
    var toolCatalog: [JSONObject] = []
    /// Last-used model id — remembered across conversations.
    var lastModelId: String?
    /// True after the first bootstrap (avoids duplicate bootstraps).
    var bootstrapped = false

    var messages: [ChatMessage] { activeConversation?.messages ?? [] }

    /// True when the active conversation has no model (first-run picker).
    var needsModel: Bool {
        guard let convo = activeConversation else { return true }
        return convo.modelId == nil || convo.modelId?.isEmpty == true
    }

    var pendingConfirmationMessage: ChatMessage? {
        messages.last(where: \.isPendingConfirmation)
    }
}

/// The chatbot — `ChatbotNotifier`. One per signed-in session: created when
/// the dock first appears signed in (which bootstraps it) and dropped on
/// sign-out, which is the Dart provider's reset.
@Observable
final class ChatbotModel {
    private(set) var state = ChatbotState()

    @ObservationIgnored private let repository: () -> ChatbotRepository
    @ObservationIgnored private let now: () -> Date
    /// The dock's `_navigateHandled`.
    @ObservationIgnored private var navigateTracker = ChatNavigateTracker()

    init(repository: @escaping () -> ChatbotRepository, now: @escaping () -> Date = Date.init) {
        self.repository = repository
        self.now = now
    }

    // MARK: Bootstrap

    func bootstrap() async {
        if state.bootstrapped { return }
        state.bootstrapped = true
        await refreshConversations()
        // Reload the most recent conversation.
        if state.activeConversation == nil, let first = state.conversations.first {
            await loadConversation(first.id)
        }
        await loadToolCatalog()
    }

    // MARK: Conversations

    private func refreshConversations() async {
        do {
            state.conversations = try await repository().conversations()
        } catch {
            report(error)
        }
    }

    private func loadConversation(_ id: String) async {
        do {
            let convo = try await repository().conversation(id)
            // History, not a live turn: its navigate directives already ran
            // when they were new (see the parity Ruling), so don't replay them.
            _ = navigateTracker.newRoutes(in: convo.messages)
            state.activeConversation = convo
            if convo.modelId?.isEmpty == false { state.lastModelId = convo.modelId }
        } catch {
            report(error)
        }
    }

    // MARK: Public API

    func open() { state.isOpen = true }

    func close() {
        state.isOpen = false
        state.isFullscreen = false
    }

    func toggleFullscreen() { state.isFullscreen.toggle() }
    func setFullscreen(_ value: Bool) { state.isFullscreen = value }
    func minimise() { state.isOpen = false }
    func clearError() { state.error = nil }
    func toggleConversationList() { state.conversationListOpen.toggle() }

    /// Switches to the given conversation (loads it from the API).
    func selectConversation(_ id: String) async {
        state.conversationListOpen = false
        await loadConversation(id)
    }

    func refresh() async { await refreshConversations() }

    @discardableResult
    func startNewConversation(modelId: String? = nil, title: String? = nil) async throws -> Conversation {
        state.error = nil
        let useModel = modelId ?? state.lastModelId
        let convo = try await repository().createConversation(modelId: useModel, title: title)
        state.activeConversation = convo
        state.lastModelId = convo.modelId ?? state.lastModelId
        await refreshConversations()
        return convo
    }

    /// `startNewConversation` from a button: Dart left a failure unhandled
    /// (the future's error went nowhere); here it lands in `error`.
    func startNewConversationFromUI() async {
        do {
            try await startNewConversation()
        } catch {
            report(error)
        }
    }

    func setModel(_ modelId: String) async {
        guard let convo = state.activeConversation else {
            do {
                try await startNewConversation(modelId: modelId)
            } catch {
                report(error)
            }
            return
        }
        do {
            let updated = try await repository().patchConversation(convo.id, ["model_id": .string(modelId)])
            state.activeConversation = updated
            state.lastModelId = modelId
        } catch {
            report(error)
        }
    }

    func setAutoConfirmSafe(_ value: Bool) async {
        guard let convo = state.activeConversation else { return }
        do {
            state.activeConversation = try await repository().patchConversation(
                convo.id, ["auto_confirm_safe_tools": .bool(value)]
            )
        } catch {
            report(error)
        }
    }

    func clearConversation() async {
        guard let convo = state.activeConversation else { return }
        state.busy = true
        state.error = nil
        do {
            state.activeConversation = try await repository().clear(convo.id)
            state.busy = false
            await refreshConversations()
        } catch {
            state.busy = false
            report(error)
        }
    }

    func deleteConversation() async {
        guard let convo = state.activeConversation else { return }
        state.busy = true
        state.error = nil
        do {
            try await repository().deleteConversation(convo.id)
            state.activeConversation = nil
            state.busy = false
            await refreshConversations()
            // Auto-open the first remaining conversation.
            if let first = state.conversations.first {
                await loadConversation(first.id)
            }
        } catch {
            state.busy = false
            report(error)
        }
    }

    // MARK: Messaging

    func send(_ text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return }

        let convo: Conversation
        if let active = state.activeConversation {
            convo = active
        } else {
            do {
                convo = try await startNewConversation()
            } catch {
                report(error)
                return
            }
        }

        // Optimistic append so the person sees their message immediately.
        let sentAt = now()
        let tempId = "temp-\(DartDateTime.millisecondsSinceEpoch(sentAt))"
        let optimistic = ChatMessage(id: tempId, role: "user", content: trimmed, createdAt: sentAt, status: "sending")
        state.activeConversation = convo.copyWith(messages: convo.messages + [optimistic])
        state.busy = true
        state.error = nil

        do {
            let newMessages = try await repository().turn(convo.id, trimmed)
            let base = (state.activeConversation?.messages ?? []).filter { $0.id != tempId }
            state.activeConversation = (state.activeConversation ?? convo).copyWith(messages: base + newMessages)
            state.busy = false

            // Refresh to pick up the server-assigned title and message count.
            if let fresh = try? await repository().conversation(convo.id) {
                state.activeConversation = fresh
                await refreshConversations()
            }
        } catch {
            if error is CancellationError {
                state.busy = false
                return
            }
            // Mark the optimistic message as failed.
            let messages = (state.activeConversation?.messages ?? []).map { m in
                m.id == tempId
                    ? ChatMessage(id: m.id, role: m.role, content: m.content, createdAt: m.createdAt, status: "failed",
                                  toolCalls: m.toolCalls, pendingTool: m.pendingTool, blocks: m.blocks)
                    : m
            }
            state.activeConversation = (state.activeConversation ?? convo).copyWith(messages: messages)
            state.busy = false
            state.error = chatErrorText(error)
        }
    }

    func confirmTool(_ messageId: String, approved: Bool) async {
        guard let convo = state.activeConversation else { return }
        state.busy = true
        state.error = nil
        do {
            _ = try await repository().confirmTool(convo.id, messageId, approved)
            state.activeConversation = try await repository().conversation(convo.id)
            state.busy = false
        } catch {
            state.busy = false
            report(error)
        }
    }

    /// Navigate directives that arrived since the last call (allowed routes
    /// only, in order); each fires once.
    func takeNavigations() -> [String] {
        navigateTracker.newRoutes(in: state.messages)
    }

    // MARK: Model picker and tool catalog

    func loadModels() async {
        if state.modelsLoaded { return }
        do {
            state.models = try await repository().models()
            state.modelsLoaded = true
        } catch {
            report(error)
        }
    }

    private func loadToolCatalog() async {
        if !state.toolCatalog.isEmpty { return }
        // Not fatal — the tool catalog is informational.
        if let tools = try? await repository().tools() {
            state.toolCatalog = tools
        }
    }

    /// Records a failure as Dart's `error: e.toString()`; a cancellation is
    /// not a failure and leaves the state alone.
    private func report(_ error: any Error) {
        if error is CancellationError { return }
        state.error = chatErrorText(error)
    }
}

/// Dart's `e.toString()` for a caught error (`ApiError.toString()` is its message).
nonisolated func chatErrorText(_ error: any Error) -> String {
    (error as? ApiError)?.message ?? error.localizedDescription
}

// MARK: - Navigate directives (`_ChatbotDockState._handleNavigates`)

nonisolated enum ChatNavigate {
    /// Allowed route prefixes for navigate directives.
    static let allowedRoutePrefixes = [
        "/dashboard", "/instances", "/backtests", "/strategies", "/brokerages",
        "/agent-runs", "/nexus", "/models", "/token-usage", "/settings",
    ]

    /// True only for a known safe in-app path.
    static func isAllowedRoute(_ route: String) -> Bool {
        if !route.hasPrefix("/") { return false }
        if route.hasPrefix("//") { return false }
        return allowedRoutePrefixes.contains { route == $0 || route.hasPrefix($0 + "/") }
    }

    /// Every `(key, route)` navigate directive in `messages`, in order, where
    /// the key is `messageId:route`. Disallowed routes are included (the dock
    /// marks them handled without navigating).
    static func directives(in messages: [ChatMessage]) -> [(key: String, route: String)] {
        var out: [(String, String)] = []
        for message in messages {
            for block in message.blocks {
                let type = block["type"]?.string ?? ""
                let route = block["route"]?.string ?? ""
                if type == "navigate", !route.isEmpty {
                    out.append(("\(message.id):\(route)", route))
                }
            }
        }
        return out
    }

    /// The blocks a bubble renders (navigate blocks are the dock's).
    static func visibleBlocks(_ message: ChatMessage) -> [JSONObject] {
        message.blocks.filter { ($0["type"] ?? .string("")).dartDescription != "navigate" }
    }
}

/// Tracks which navigate directives already fired — the dock's
/// `_navigateHandled` set.
nonisolated struct ChatNavigateTracker: Sendable {
    private(set) var handled: Set<String> = []

    /// Marks every directive in `messages` handled and returns the allowed
    /// routes that had not fired yet, in order.
    mutating func newRoutes(in messages: [ChatMessage]) -> [String] {
        var routes: [String] = []
        for directive in ChatNavigate.directives(in: messages) where !handled.contains(directive.key) {
            handled.insert(directive.key)
            if ChatNavigate.isAllowedRoute(directive.route) {
                routes.append(directive.route)
            }
        }
        return routes
    }
}

// MARK: - Rich-block helpers (`chat_rich_block.dart`)

nonisolated enum ChatBlockFormat {
    /// The table's `_fmtCell`: integral numbers with no decimals, others with 2.
    static func cell(_ v: JSON) -> String {
        switch v {
        case .null: return ""
        case .int(let i): return String(i)
        case .double(let d):
            return dartToStringAsFixed(d, d.rounded(.towardZero) == d ? 0 : 2)
        default: return v.dartDescription
        }
    }

    /// `'${provider} · ${model}'.trim()` without a leading or trailing `·`.
    static func modelSubtitle(provider: String, model: String) -> String {
        var s = "\(provider) · \(model)".trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix("·") {
            s.removeFirst()
            s = String(s.drop(while: \.isWhitespace))
        } else if s.hasSuffix("·") {
            s.removeLast()
        }
        return s
    }

    /// The tool card's 2-space indented arguments.
    static func prettyArguments(_ arguments: JSONObject) -> String {
        guard !arguments.isEmpty, let data = try? JSON.object(arguments).dartEncoded(indent: "  ") else { return "" }
        return data
    }

    /// A tool-role bubble: `name → ` + the content cut at 200 characters.
    static func toolText(name: String?, content: String) -> String {
        let prefix = name.map { "\($0) → " } ?? ""
        let body = content.count > 200 ? String(content.prefix(200)) + "…" : content
        return prefix + body
    }
}

/// One plottable point of a chart block.
nonisolated struct ChatChartPoint: Hashable, Sendable {
    let x: String
    let y: Double
}

/// One series of a chart block.
nonisolated struct ChatChartSeries: Hashable, Sendable, Identifiable {
    enum Kind: Sendable { case line, bar }

    let id: Int
    let name: String
    let kind: Kind
    let points: [ChatChartPoint]

    /// `_ChartBlock._buildSeries`.
    static func build(_ block: JSONObject) -> [ChatChartSeries] {
        let type = (block["type"] ?? .null).or("chart").dartDescription
        let chartType = (block["chart_type"] ?? .null).or("bar").dartDescription

        if type == "portfolio" {
            let timestamps = (block["timestamps"] ?? .null).arrayValue
            let values = (block["values"] ?? .null).arrayValue
            var points: [ChatChartPoint] = []
            for i in 0..<min(timestamps.count, values.count) {
                if let v = Num.tryParse(values[i].dartDescription) {
                    points.append(ChatChartPoint(x: String(i), y: v.double))
                }
            }
            return points.isEmpty ? [] : [ChatChartSeries(id: 0, name: "", kind: .line, points: points)]
        }

        let raw = (block["series"] ?? .null).objectElements
        return raw.enumerated().map { i, s in
            let name = s["symbol"].or(s["name"]).or(.string("Series \(i)")).dartDescription
            if let pointsRaw = s["points"].array {
                var points: [ChatChartPoint] = []
                for p in pointsRaw where p.isObject {
                    if let v = Num.tryParse(p["value"].dartDescription) {
                        points.append(ChatChartPoint(x: String(points.count), y: v.double))
                    }
                }
                return ChatChartSeries(id: i, name: name, kind: .line, points: points)
            }
            var points: [ChatChartPoint] = []
            for item in s["data"].arrayValue {
                if let pair = item.array, pair.count >= 2 {
                    if let y = Num.tryParse(pair[1].dartDescription) {
                        points.append(ChatChartPoint(x: pair[0].dartDescription, y: y.double))
                    }
                } else if let y = Num.tryParse(item.dartDescription) {
                    points.append(ChatChartPoint(x: String(points.count), y: y.double))
                }
            }
            return ChatChartSeries(id: i, name: name, kind: chartType == "bar" ? .bar : .line, points: points)
        }
    }
}

/// The settings sheet's tool groups (`_ToolsSection`).
nonisolated struct ChatToolGroups: Equatable, Sendable {
    var safe: [String] = []
    var confirm: [String] = []
    var destructive: [String] = []

    init(_ catalog: [JSONObject]) {
        for tool in catalog {
            let entry = JSON.object(tool)
            let name = entry["name"].or(entry["function"]).or(entry["tool"]).or("").dartDescription
            if name.isEmpty { continue }
            switch entry["safety"].or("write").dartDescription {
            case "safe": safe.append(name)
            case "destructive": destructive.append(name)
            default: confirm.append(name)
            }
        }
    }
}
