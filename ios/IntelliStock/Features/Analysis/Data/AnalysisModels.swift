import Foundation

// The "why it moved" API contract (docs/superpowers/specs/2026-11-14-why-it-moved-design.md,
// section API): the three payloads `/analysis/settings`, `/analysis/moves` and
// `/analysis/portfolio`, read with `init(json:)` like every other model in the
// app. Nullable fields stay optional; a blank string reads as absent.

/// The look-back windows `/analysis/moves` accepts.
nonisolated enum AnalysisRange: String, CaseIterable, Hashable, Sendable {
    case oneMonth = "1M"
    case threeMonths = "3M"
    case sixMonths = "6M"
    case oneYear = "1Y"

    var label: String { rawValue }
}

// MARK: - Settings

/// One registered model the explainer can use (`models[]`).
nonisolated struct AnalysisModelOption: Hashable, Sendable, Identifiable {
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
        id = j["id"].stringOr("")
        name = j["name"].stringOr("")
        provider = j["provider"].stringOr("")
        model = j["model"].stringOr("")
    }

    /// The name, else the model id, else the registry id.
    var displayName: String {
        if !name.isEmpty { return name }
        return model.isEmpty ? id : model
    }
}

/// `GET /analysis/settings` (and the `PUT` reply).
nonisolated struct AnalysisSettings: Hashable, Sendable {
    static let defaultSigma = 2.5
    static let defaultMinMovePct = 3.0
    /// The server's accepted ranges (a value outside is a 400).
    static let sigmaRange: ClosedRange<Double> = 1.0...6.0
    static let minMoveRange: ClosedRange<Double> = 0.5...25

    var modelId: String?
    var modelName: String?
    var sigma: Double
    var minMovePct: Double
    var models: [AnalysisModelOption]

    init(modelId: String?, modelName: String? = nil, sigma: Double, minMovePct: Double, models: [AnalysisModelOption]) {
        self.modelId = modelId
        self.modelName = modelName
        self.sigma = sigma
        self.minMovePct = minMovePct
        self.models = models
    }

    init(json j: JSON) {
        modelId = j["model_id"].nonEmptyString
        modelName = j["model_name"].nonEmptyString
        sigma = j["sigma"].double ?? Self.defaultSigma
        minMovePct = j["min_move_pct"].double ?? Self.defaultMinMovePct
        models = j["models"].objectElements.map(AnalysisModelOption.init(json:)).filter { !$0.id.isEmpty }
    }

    /// The chosen model's label: its registry name, else the server's
    /// `model_name`, else "None".
    var modelLabel: String {
        guard let modelId else { return "None" }
        if let match = models.first(where: { $0.id == modelId }) { return match.displayName }
        return modelName ?? modelId
    }
}

/// The fields a `PUT /analysis/settings` sends: only what changed.
nonisolated struct AnalysisSettingsChange: Equatable, Sendable {
    /// `.some(nil)` sends `"model_id": null` (no model); `nil` leaves it out.
    var modelId: String??
    var sigma: Double?
    var minMovePct: Double?

    init(modelId: String?? = nil, sigma: Double? = nil, minMovePct: Double? = nil) {
        self.modelId = modelId
        self.sigma = sigma
        self.minMovePct = minMovePct
    }

    var isEmpty: Bool { modelId == nil && sigma == nil && minMovePct == nil }

    /// Later fields win.
    func merging(_ other: AnalysisSettingsChange) -> AnalysisSettingsChange {
        AnalysisSettingsChange(
            modelId: other.modelId ?? modelId,
            sigma: other.sigma ?? sigma,
            minMovePct: other.minMovePct ?? minMovePct
        )
    }

    var body: JSON {
        var pairs: [(String, JSON)] = []
        if let modelId { pairs.append(("model_id", modelId.map(JSON.string) ?? .null)) }
        if let sigma { pairs.append(("sigma", .double(sigma))) }
        if let minMovePct { pairs.append(("min_move_pct", .double(minMovePct))) }
        return .object(JSONObject(pairs))
    }

    /// The settings with this change applied (the optimistic value).
    func applied(to settings: AnalysisSettings) -> AnalysisSettings {
        var out = settings
        if let modelId {
            out.modelId = modelId
            out.modelName = modelId.flatMap { id in settings.models.first { $0.id == id }?.displayName }
        }
        if let sigma { out.sigma = sigma }
        if let minMovePct { out.minMovePct = minMovePct }
        return out
    }
}

// MARK: - Moves

/// One trading day (`bars[]`, oldest first). The first bar of a series may
/// carry null parts.
nonisolated struct MoveBar: Hashable, Sendable {
    /// `YYYY-MM-DD`, the key events match on.
    let date: String
    let close: Double?
    let volume: Double?
    let retPct: Double?
    let marketPct: Double?
    let sectorPct: Double?
    let specificPct: Double?
    let flagged: Bool

    init(json j: JSON) {
        date = j["date"].stringOr("")
        close = j["close"].double
        volume = j["volume"].double
        retPct = j["ret_pct"].double
        marketPct = j["market_pct"].double
        sectorPct = j["sector_pct"].double
        specificPct = j["specific_pct"].double
        flagged = j["flagged"].bool
    }
}

/// One cited article (`sources[]`).
nonisolated struct MoveSource: Hashable, Sendable {
    let title: String
    let url: String
    let source: String
    let publishedAt: String?

    init(json j: JSON) {
        title = j["title"].stringOr("")
        url = j["url"].stringOr("")
        source = j["source"].stringOr("")
        publishedAt = j["published_at"].nonEmptyString
    }
}

/// Where an event is in the explain pipeline. An unknown value reads as
/// `unexplained`, so a newer server never hides an event.
nonisolated enum MoveEventStatus: String, Hashable, Sendable {
    case unexplained, pending, explained, failed

    init(raw: String?) {
        self = raw.flatMap { MoveEventStatus(rawValue: $0.lowercased()) } ?? .unexplained
    }
}

/// One flagged day (`events[]`, newest first). For `unexplained` and
/// `pending` the category, headline, explanation, confidence, model and time
/// are null and `sources` is empty.
nonisolated struct MoveEvent: Hashable, Sendable, Identifiable {
    /// `SYMBOL|YYYY-MM-DD`.
    let id: String
    let symbol: String
    let date: String
    let close: Double?
    let retPct: Double?
    let marketPct: Double?
    let sectorPct: Double?
    let specificPct: Double?
    let status: MoveEventStatus
    let category: String?
    let categoryLabel: String?
    let headline: String?
    let explanation: String?
    /// `low`, `medium` or `high`.
    let confidence: String?
    let sources: [MoveSource]
    let modelName: String?
    let explainedAt: String?
    let error: String?

    init(json j: JSON) {
        symbol = j["symbol"].stringOr("")
        date = j["date"].stringOr("")
        id = j["id"].nonEmptyString ?? "\(symbol)|\(date)"
        close = j["close"].double
        retPct = j["ret_pct"].double
        marketPct = j["market_pct"].double
        sectorPct = j["sector_pct"].double
        specificPct = j["specific_pct"].double
        status = MoveEventStatus(raw: j["status"].string)
        category = j["category"].nonEmptyString
        categoryLabel = j["category_label"].nonEmptyString
        headline = j["headline"].nonEmptyString
        explanation = j["explanation"].nonEmptyString
        confidence = j["confidence"].nonEmptyString
        sources = j["sources"].objectElements.map(MoveSource.init(json:))
        modelName = j["model_name"].nonEmptyString
        explainedAt = j["explained_at"].nonEmptyString
        error = j["error"].nonEmptyString
    }

    var isExplained: Bool { status == .explained }

    /// "Explain moves" covers these: never asked, or asked and failed.
    var needsExplaining: Bool { status == .unexplained || status == .failed }
}

/// One category the server knows (`categories[]`).
nonisolated struct MoveCategory: Hashable, Sendable {
    let key: String
    let label: String

    init(key: String, label: String) {
        self.key = key
        self.label = label
    }

    init(json j: JSON) {
        key = j["key"].stringOr("")
        label = j["label"].stringOr("")
    }
}

/// `GET /analysis/moves?symbol=&range=`.
nonisolated struct MoveAnalysis: Hashable, Sendable {
    let symbol: String
    let range: String
    let sector: String?
    let sectorEtf: String?
    let beta: Double?
    let modelConfigured: Bool
    /// Events still being explained; the screen polls while above zero.
    let pending: Int
    let bars: [MoveBar]
    let events: [MoveEvent]
    let categories: [MoveCategory]

    init(json j: JSON) {
        symbol = j["symbol"].stringOr("")
        range = j["range"].stringOr("")
        sector = j["sector"].nonEmptyString
        sectorEtf = j["sector_etf"].nonEmptyString
        beta = j["beta"].double
        modelConfigured = j["model_configured"].bool
        pending = max(j["pending"].intOr(0), 0)
        bars = j["bars"].objectElements.map(MoveBar.init(json:))
        events = j["events"].objectElements.map(MoveEvent.init(json:))
        categories = j["categories"].objectElements.map(MoveCategory.init(json:)).filter { !$0.key.isEmpty }
    }

    /// Events that are unexplained or failed: what "Explain moves" queues.
    var unexplainedCount: Int { events.filter(\.needsExplaining).count }

    /// The label for a category key: the server's list, else an event's
    /// `category_label`, else the key itself.
    func label(for key: String) -> String {
        if let c = categories.first(where: { $0.key == key }), !c.label.isEmpty { return c.label }
        if let e = events.first(where: { $0.category == key }), let label = e.categoryLabel { return label }
        return key
    }
}

// MARK: - Portfolio

/// `totals` of `/analysis/portfolio`: the account's move, split three ways.
nonisolated struct PortfolioMoveTotals: Hashable, Sendable {
    let contributionPct: Double?
    let marketPct: Double?
    let sectorPct: Double?
    let specificPct: Double?

    init(json j: JSON) {
        contributionPct = j["contribution_pct"].double
        marketPct = j["market_pct"].double
        sectorPct = j["sector_pct"].double
        specificPct = j["specific_pct"].double
    }
}

/// One holding's last-session move (`holdings[]`). The three parts are
/// contributions to the account, so they add up to `contributionPct`.
nonisolated struct PortfolioHoldingMove: Hashable, Sendable, Identifiable {
    var id: String { symbol }
    let symbol: String
    let weightPct: Double?
    let retPct: Double?
    let contributionPct: Double?
    let marketPct: Double?
    let sectorPct: Double?
    let specificPct: Double?
    let flagged: Bool
    /// The day's event when it was flagged.
    let event: MoveEvent?

    init(json j: JSON) {
        symbol = j["symbol"].stringOr("")
        weightPct = j["weight_pct"].double
        retPct = j["ret_pct"].double
        contributionPct = j["contribution_pct"].double
        marketPct = j["market_pct"].double
        sectorPct = j["sector_pct"].double
        specificPct = j["specific_pct"].double
        flagged = j["flagged"].bool
        event = j["event"].isObject ? MoveEvent(json: j["event"]) : nil
    }
}

/// `GET /analysis/portfolio?brokerage_id=`. `asOf` is nil and `holdings`
/// empty when the account has no positions.
nonisolated struct PortfolioMoves: Hashable, Sendable {
    let asOf: String?
    let spyPct: Double?
    let totals: PortfolioMoveTotals?
    let holdings: [PortfolioHoldingMove]

    init(json j: JSON) {
        asOf = j["as_of"].nonEmptyString
        spyPct = j["spy_pct"].double
        totals = j["totals"].isObject ? PortfolioMoveTotals(json: j["totals"]) : nil
        holdings = j["holdings"].objectElements.map(PortfolioHoldingMove.init(json:)).filter { !$0.symbol.isEmpty }
    }
}

nonisolated extension JSON {
    /// A string field, nil when null, missing or blank.
    var nonEmptyString: String? {
        guard let s = string?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { return nil }
        return s
    }
}
