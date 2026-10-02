import Foundation
import Observation

/// The create / edit Kalshi instance form — `_KalshiInstanceSheetState` in
/// kalshi_screen.dart. Text fields hold the raw text, as the Dart
/// `TextEditingController`s did; the body parses them at submit with the same
/// `_d` / `_i` fallbacks.
@Observable
final class KalshiInstanceFormModel {
    // Edit mode
    let editInstanceId: String?
    var isEdit: Bool { editInstanceId != nil }

    var brokerageId: String
    var name = ""
    var edge = "4"
    var kelly = "0.125"
    var maxContracts = "50"
    var exposure = "15"
    var leagueCap = "25"
    var minPrice = "15"
    var maxPrice = "90"
    var drawMinEdge = "10"
    /// Order-size range $/trade (0/0 = auto).
    var orderSizeMin = "0"
    var orderSizeMax = "0"
    private(set) var dailyLoss = "100"
    var poll = "60"
    var manualBankroll = "1000"
    var oddsKey = ""
    var oddspapiKey = ""
    var noSharpEdge = "5"
    var marketShrink = "40"
    var sharpWeight: Double = 85
    /// Insertion-ordered (Dart's `LinkedHashSet`).
    var leagues: [String] = ["EPL", "Serie B", "Ligue 2"]
    private(set) var usagePct: Double = 50
    private(set) var balance: Double = 0
    private(set) var loadingBalance = false
    private(set) var dailyLossTouched = false
    private(set) var dailyLossPct: Double = 0.10
    private(set) var risk = "medium"
    /// Off for a new instance — only the pregame strategy is validated.
    var liveMonitoring = false
    /// HARD dry-run: real prices, NO real orders. Safe default for a funded
    /// live account; off places REAL orders.
    var paperMode = true
    var oneBetPerFixture = true
    private(set) var models: [JSONObject] = []
    var selectedModel: String?
    private(set) var creating = false
    private(set) var err = ""

    @ObservationIgnored private let repository: () -> KalshiRepository

    init(
        initialBrokerageId: String,
        editInstanceId: String? = nil,
        editName: String? = nil,
        editConfig: JSONObject? = nil,
        repository: @escaping () -> KalshiRepository
    ) {
        self.brokerageId = initialBrokerageId
        self.editInstanceId = editInstanceId
        self.repository = repository
        if let editConfig { prefill(editConfig, editName: editName) }
    }

    /// `initState`: balance and models load once the sheet opens.
    func start() async {
        async let b: Void = loadBalance()
        async let m: Void = loadModels()
        _ = await (b, m)
    }

    // MARK: Prefill (edit mode)

    private func prefill(_ c: JSONObject, editName: String?) {
        func n(_ k: String) -> Num? { c[k].flatMap(\.num) }
        func scaled(_ v: Num, _ by: Double) -> Num {
            // Dart `num * 100` keeps int for int; any double makes a double.
            // Dart's int multiply wraps on overflow; so does `&*` (no trap).
            if case .int(let i) = v, by == 100 { return .int(i &* 100) }
            return .double(v.double * by)
        }
        func divided(_ v: Num) -> Num { .double(v.double / 100) }

        if let editName { name = editName }
        if let v = n("edge_threshold") { edge = KalshiFormat.num(scaled(v, 100)) }
        if let v = n("kelly_fraction") { kelly = KalshiFormat.num(v) }
        if let v = n("max_contracts_per_market") { maxContracts = KalshiFormat.num(v) }
        if let v = n("max_open_exposure_frac") { exposure = KalshiFormat.num(scaled(v, 100)) }
        if let v = n("per_league_cap_frac") { leagueCap = KalshiFormat.num(scaled(v, 100)) }
        if let v = n("min_price_cents") { minPrice = KalshiFormat.num(v) }
        if let v = n("max_price_cents") { maxPrice = KalshiFormat.num(v) }
        if let v = n("draw_min_edge") { drawMinEdge = KalshiFormat.num(scaled(v, 100)) }
        if let v = n("order_size_min_cents") { orderSizeMin = KalshiFormat.num(divided(v)) }
        if let v = n("order_size_max_cents") { orderSizeMax = KalshiFormat.num(divided(v)) }
        if let v = n("daily_loss_cap_cents") {
            dailyLoss = KalshiFormat.num(divided(v))
            dailyLossTouched = true
        }
        if let v = n("poll_seconds") { poll = KalshiFormat.num(v) }
        if let v = n("bankroll_cents") { manualBankroll = KalshiFormat.num(divided(v)) }
        if let v = c["odds_api_key"], !v.isNull { oddsKey = v.dartDescription }
        if let v = c["oddspapi_api_key"], !v.isNull { oddspapiKey = v.dartDescription }
        if let v = n("no_sharp_edge_threshold") { noSharpEdge = KalshiFormat.num(scaled(v, 100)) }
        if let v = n("market_shrink") { marketShrink = KalshiFormat.num(scaled(v, 100)) }
        if let v = n("sharp_weight") { sharpWeight = v.double * 100 }
        if let v = n("bankroll_usage_pct") { usagePct = v.double }
        if let v = c["tier"], !v.isNull { risk = v.dartDescription }
        if let v = c["model"], !v.isNull { selectedModel = v.dartDescription }
        if let v = c["live_monitoring"], !v.isNull { liveMonitoring = v.bool }
        if let v = c["paper_mode"], !v.isNull { paperMode = v.bool }
        if let v = c["one_bet_per_fixture"], !v.isNull { oneBetPerFixture = v.bool }
        if let lg = c["leagues"]?.array, !lg.isEmpty {
            var ordered: [String] = []
            for e in lg.map(\.dartDescription) where !ordered.contains(e) { ordered.append(e) }
            leagues = ordered
        }
    }

    // MARK: Loads

    func loadModels() async {
        do {
            models = try await repository().models()
        } catch {}
    }

    func loadBalance() async {
        guard !brokerageId.isEmpty else { return }
        loadingBalance = true
        do {
            let p = try await repository().portfolio(brokerageId)
            balance = p.cash > 0 ? p.cash : p.value
        } catch {
            if marketsIsCancellation(error) { loadingBalance = false; return }
            balance = 0
        }
        loadingBalance = false
        scaleDailyLoss()
    }

    /// The account picker's `onChanged`.
    func selectBrokerage(_ id: String) async {
        brokerageId = id
        await loadBalance()
    }

    // MARK: Bankroll

    var hasBalance: Bool { balance > 0 }

    /// A typed "NaN" or "Infinity" bankroll is rejected (read as 0): it
    /// crashed the daily-loss scaling.
    var effectiveBankroll: Double {
        if hasBalance { return (balance * usagePct / 100).rounded() }
        let typed = JSON.parseDouble(manualBankroll.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
        return typed.isFinite ? typed : 0
    }

    func scaleDailyLoss() {
        if dailyLossTouched { return }
        // `.round().clamp(1, 1 << 30)`: saturate before the clamp, so a
        // 21-digit bankroll cannot overflow `Int`.
        let v = Int(dartTruncating: (effectiveBankroll * dailyLossPct).rounded()) ?? 1
        dailyLoss = String(min(max(v, 1), 1 << 30))
    }

    /// The bankroll-usage slider: rescales the daily-loss cap.
    func setUsagePct(_ v: Double) {
        usagePct = v
        scaleDailyLoss()
    }

    /// A user edit of the daily-loss field (`onChanged` marks it touched).
    func editDailyLoss(_ text: String) {
        dailyLoss = text
        dailyLossTouched = true
    }

    // MARK: Presets

    func applyPreset(_ level: String) {
        guard let p = KalshiRiskPreset.named(level) else { return }
        risk = level
        edge = KalshiFormat.num(p.edge)
        kelly = KalshiFormat.num(p.kelly)
        maxContracts = KalshiFormat.num(p.maxC)
        exposure = KalshiFormat.num(p.exp)
        leagueCap = KalshiFormat.num(p.lcap)
        poll = KalshiFormat.num(p.poll)
        orderSizeMin = KalshiFormat.num(p.osmin)
        orderSizeMax = KalshiFormat.num(p.osmax)
        usagePct = p.usage.double
        dailyLossPct = p.dlpct.double
        dailyLossTouched = false
        scaleDailyLoss()
    }

    var riskBlurb: String { KalshiRiskPreset.named(risk)?.blurb ?? "" }

    func toggleLeague(_ league: String) {
        if let i = leagues.firstIndex(of: league) {
            leagues.remove(at: i)
        } else {
            leagues.append(league)
        }
    }

    // MARK: Submit

    private func d(_ text: String, _ fallback: Double) -> Double {
        JSON.parseDouble(text.trimmingCharacters(in: .whitespacesAndNewlines)) ?? fallback
    }

    private func i(_ text: String, _ fallback: Int) -> Int {
        JSON.parseInt(text.trimmingCharacters(in: .whitespacesAndNewlines)) ?? fallback
    }

    /// The create / PATCH body, keys in the Dart order.
    func body() -> JSONObject {
        let trimmedOdds = oddsKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedPapi = oddspapiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        var pairs: [(String, JSON)] = [
            ("name", .string(name.trimmingCharacters(in: .whitespacesAndNewlines))),
            ("leagues", .array(leagues.map(JSON.string))),
            ("edge_threshold", .double(d(edge, 3) / 100)),
            ("kelly_fraction", .double(d(kelly, 0.25))),
            ("max_contracts_per_market", .int(i(maxContracts, 50))),
            ("max_open_exposure_frac", .double(d(exposure, 60) / 100)),
            ("per_league_cap_frac", .double(d(leagueCap, 25) / 100)),
            ("min_price_cents", .int(i(minPrice, 15))),
            ("max_price_cents", .int(i(maxPrice, 90))),
            ("draw_min_edge", .double(d(drawMinEdge, 10) / 100)),
            ("order_size_min_dollars", .double(d(orderSizeMin, 0))),
            ("order_size_max_dollars", .double(d(orderSizeMax, 0))),
            ("daily_loss_cap_dollars", .double(d(dailyLoss, 100))),
            ("bankroll_dollars", .double(effectiveBankroll)),
            ("poll_seconds", .int(i(poll, 60))),
            ("bankroll_usage_pct", .int(Int(dartTruncating: usagePct.rounded()) ?? 0)),
            ("live_monitoring", .bool(liveMonitoring)),
        ]
        if !trimmedOdds.isEmpty { pairs.append(("odds_api_key", .string(trimmedOdds))) }
        pairs += [
            ("sharp_weight", .double(sharpWeight / 100)),
            ("tier", .string(risk)),
            ("model", JSON(selectedModel)),
            // Dry-run by default; the backend forces paper on live
            // brokerages unless this is off.
            ("paper_mode", .bool(paperMode)),
            ("no_sharp_edge_threshold", .double(d(noSharpEdge, 5) / 100)),
            ("market_shrink", .double(d(marketShrink, 40) / 100)),
            ("one_bet_per_fixture", .bool(oneBetPerFixture)),
        ]
        if !trimmedPapi.isEmpty { pairs.append(("oddspapi_api_key", .string(trimmedPapi))) }
        return JSONObject(pairs)
    }

    /// `_submit`. Returns the brokerage id on success (the sheet closes and
    /// calls `onCreated(bid)`); nil when validation or the request failed.
    func submit() async -> String? {
        if name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            err = "Name is required"
            return nil
        }
        if leagues.isEmpty {
            err = "Pick at least one league"
            return nil
        }
        creating = true
        err = ""
        defer { creating = false }
        do {
            let repo = repository()
            if let editInstanceId {
                try await repo.updateInstance(editInstanceId, body())
            } else {
                try await repo.createInstance(brokerageId, body())
            }
            return brokerageId
        } catch {
            if !marketsIsCancellation(error) { err = KalshiFormat.errorText(error) }
            return nil
        }
    }
}
