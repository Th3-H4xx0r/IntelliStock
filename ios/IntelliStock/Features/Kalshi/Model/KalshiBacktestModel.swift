import Foundation
import Observation

/// The Kalshi backtest launcher — `_KalshiBacktestScreenState` in
/// kalshi_backtest_screen.dart. Numeric settings are doubles (the Dart
/// fields); each has a text mirror for its field. The brokerage's backtest
/// list refreshes every 3 s.
@Observable
final class KalshiBacktestModel {
    /// One numeric setting: its label, value and the field text.
    enum Field: String, CaseIterable, Sendable {
        case bankroll, usage, edge, noSharp, kelly, contracts, exposure, leagueCap
        case minPrice, maxPrice, drawMin, orderMin, orderMax, sharpWeight, daily

        /// The Dart labels, in the Wrap order.
        var label: String {
            switch self {
            case .bankroll: "Bankroll ($)"
            case .usage: "Usage (%)"
            case .edge: "Edge (%)"
            case .noSharp: "No-sharp (%)"
            case .kelly: "Kelly"
            case .contracts: "Max contracts"
            case .exposure: "Exposure (%)"
            case .leagueCap: "League cap (%)"
            case .minPrice: "Min price (¢)"
            case .maxPrice: "Max price (¢)"
            case .drawMin: "Draw min (%)"
            case .orderMin: "Order min ($)"
            case .orderMax: "Order max ($)"
            case .sharpWeight: "Sharp wt (%)"
            case .daily: "Daily loss (%)"
            }
        }
    }

    let instanceId: String
    private(set) var bid = ""
    var oddsKey = ""
    private(set) var tier = "max"
    var leagues: [String] = ["World Cup"]
    var start: Date?
    var end: Date?
    private(set) var values: [Field: Double] = [
        .bankroll: 54, .edge: 2, .noSharp: 3, .kelly: 0.2, .contracts: 100,
        .exposure: 40, .leagueCap: 40, .usage: 70, .daily: 15,
        .minPrice: 15, .maxPrice: 90, .drawMin: 10, .orderMin: 8, .orderMax: 15,
        .sharpWeight: 85,
    ]
    private(set) var texts: [Field: String] = [:]
    private(set) var analystMaxCalls: Double = 10
    private(set) var models: [JSONObject] = []
    private(set) var modelId: String?
    var useLlm = false

    private(set) var submitting = false
    private(set) var err: String?
    private(set) var backtests: [JSONObject] = []

    static let tickInterval: Duration = .seconds(3)

    @ObservationIgnored private let repository: () -> KalshiRepository
    /// The instance config has seeded the form.
    @ObservationIgnored private var configLoaded = false

    init(instanceId: String, repository: @escaping () -> KalshiRepository) {
        self.instanceId = instanceId
        self.repository = repository
        syncTexts()
    }

    func value(_ f: Field) -> Double { values[f] ?? 0 }
    func text(_ f: Field) -> String { texts[f] ?? "" }

    /// A field edit: `double.tryParse(t) ?? value`.
    func edit(_ f: Field, _ text: String) {
        texts[f] = text
        values[f] = JSON.parseDouble(text) ?? value(f)
    }

    private func set(_ f: Field, _ v: Double) {
        values[f] = v
        texts[f] = KalshiFormat.num(v)
    }

    private func syncTexts() {
        for f in Field.allCases { texts[f] = KalshiFormat.num(value(f)) }
    }

    func applyPreset(_ key: String) {
        guard let p = KalshiBacktestPreset.named(key) else { return }
        tier = key
        set(.edge, p.edge); set(.kelly, p.kelly); set(.contracts, p.contracts)
        set(.exposure, p.exposure); set(.leagueCap, p.leagueCap); set(.usage, p.usage)
        set(.daily, p.daily); set(.orderMin, p.omin); set(.orderMax, p.omax)
    }

    /// The model dropdown: picking one turns the analyst on.
    func selectModel(_ id: String?) {
        modelId = id
        useLlm = id != nil
    }

    func toggleLeague(_ l: String) {
        if let i = leagues.firstIndex(of: l) { leagues.remove(at: i) } else { leagues.append(l) }
    }

    // MARK: Load

    func load() async {
        let repo = repository()
        let id = instanceId
        do {
            async let detail = repo.instanceDetail(id)
            async let models = repo.models()
            let (d, m) = try await (detail, models)
            // The form is seeded once: a reappear must not overwrite what
            // the person typed since.
            configLoaded = true
            if err == Self.loadError { err = nil }
            self.models = m
            let c = d["config"]?.orderedObject ?? JSONObject()
            bid = KalshiPregame.str(d["brokerage_id"])
            func n(_ k: String) -> Double? { c[k].flatMap { $0.isNull ? nil : $0.double } }
            if let t = c["tier"], !t.isNull { tier = t.dartDescription }
            if let v = n("edge_threshold") { set(.edge, v * 100) }
            if let v = n("no_sharp_edge_threshold") { set(.noSharp, v * 100) }
            if let v = n("kelly_fraction") { set(.kelly, v) }
            if let v = n("max_contracts_per_market") { set(.contracts, v) }
            if let v = n("max_open_exposure_frac") { set(.exposure, v * 100) }
            if let v = n("per_league_cap_frac") { set(.leagueCap, v * 100) }
            if let v = n("bankroll_usage_pct") { set(.usage, v) }
            if let v = n("daily_loss_cap_frac") { set(.daily, v * 100) }
            if let v = n("min_price_cents") { set(.minPrice, v) }
            if let v = n("max_price_cents") { set(.maxPrice, v) }
            if let v = n("draw_min_edge") { set(.drawMin, v * 100) }
            if let v = n("order_size_min_cents") { set(.orderMin, v / 100) }
            if let v = n("order_size_max_cents") { set(.orderMax, v / 100) }
            if let v = n("sharp_weight") { set(.sharpWeight, v * 100) }
            if let v = n("bankroll_cents") { set(.bankroll, v / 100) }
            if let v = n("analyst_max_calls") { analystMaxCalls = v }
            if let lg = c["leagues"]?.array, !lg.isEmpty { leagues = lg.map(\.dartDescription) }
            if let k = c["oddspapi_api_key"], !k.isNull { oddsKey = k.dartDescription }
            if let m = c["model"], !m.isNull, !m.dartDescription.isEmpty {
                modelId = m.dartDescription
                useLlm = true
            }
            await loadBacktests()
        } catch {
            if !marketsIsCancellation(error) { err = Self.loadError }
        }
    }

    static let loadError = "Couldn't load the instance config."

    func loadBacktests() async {
        guard !bid.isEmpty else { return }
        do {
            backtests = try await repository().listBacktests(bid)
        } catch {}
    }

    /// The screen's `.task`: seed the form from the config once (again only
    /// when that never landed), then refresh the backtest list every 3 s.
    /// A reappear reuses this model, so it only restarts the list poll.
    func poll(lifecycle: AppLifecycle) async {
        if configLoaded {
            await loadBacktests()
        } else {
            await load()
        }
        guard !Task.isCancelled else { return }
        await PollingLoop(interval: { Self.tickInterval }) { [weak self] in
            await self?.loadBacktests()
        }.run(lifecycle: lifecycle)
    }

    // MARK: Submit

    var canSubmit: Bool { !submitting && !bid.isEmpty }

    func body() -> JSONObject {
        var config: [(String, JSON)] = [
            ("tier", .string(tier)),
            ("edge_threshold", .double(value(.edge) / 100)),
            ("no_sharp_edge_threshold", .double(value(.noSharp) / 100)),
            ("kelly_fraction", .double(value(.kelly))),
            ("max_contracts_per_market", .double(value(.contracts))),
            ("max_open_exposure_frac", .double(value(.exposure) / 100)),
            ("per_league_cap_frac", .double(value(.leagueCap) / 100)),
            ("bankroll_usage_pct", .double(value(.usage))),
            ("daily_loss_cap_frac", .double(value(.daily) / 100)),
            ("min_price_cents", .double(value(.minPrice))),
            ("max_price_cents", .double(value(.maxPrice))),
            ("draw_min_edge", .double(value(.drawMin) / 100)),
            ("order_size_min_dollars", .double(value(.orderMin))),
            ("order_size_max_dollars", .double(value(.orderMax))),
            ("sharp_weight", .double(value(.sharpWeight) / 100)),
        ]
        if !oddsKey.isEmpty { config.append(("oddspapi_api_key", .string(oddsKey))) }
        if useLlm, let modelId { config.append(("model", .string(modelId))) }
        config.append(("use_llm", .bool(useLlm && modelId != nil)))
        config.append(("analyst_max_calls", .double(analystMaxCalls)))
        return JSONObject([
            ("instance_id", .string(instanceId)),
            ("leagues", .array(leagues.map(JSON.string))),
            ("start_date", .string(start.map(KalshiFormat.ymd) ?? "")),
            ("end_date", .string(end.map(KalshiFormat.ymd) ?? "")),
            ("bankroll_dollars", .double(value(.bankroll))),
            ("config", .object(JSONObject(config))),
        ])
    }

    /// `_submit`. Returns the new backtest id to open, or nil.
    func submit() async -> String? {
        err = nil
        guard let start, let end else {
            err = "Pick a start and end date."
            return nil
        }
        if KalshiFormat.ymd(start).compare(KalshiFormat.ymd(end), options: .literal) == .orderedDescending {
            err = "Start must be on/before end."
            return nil
        }
        if leagues.isEmpty {
            err = "Select at least one league."
            return nil
        }
        submitting = true
        defer { submitting = false }
        do {
            let id = try await repository().createBacktest(bid, body())
            await loadBacktests()
            return id
        } catch {
            if !marketsIsCancellation(error) { err = "Failed to start the backtest." }
            return nil
        }
    }

    func stopBacktest(_ id: String) async {
        try? await repository().stopBacktest(id)
        await loadBacktests()
    }

    func deleteBacktest(_ id: String) async {
        try? await repository().deleteBacktest(id)
        await loadBacktests()
    }
}
