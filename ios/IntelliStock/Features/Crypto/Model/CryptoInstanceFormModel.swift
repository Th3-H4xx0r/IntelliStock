import Foundation
import Observation
import SwiftUI

/// One coin of the catalog (`_CoinMeta`).
nonisolated struct CryptoCoinMeta: Hashable, Sendable {
    let sym: String
    let name: String
}

/// The catalog, strategies, bands and palette of crypto_instance_sheet.dart.
nonisolated enum CryptoCatalog {
    static let coins: [CryptoCoinMeta] = [
        CryptoCoinMeta(sym: "BTC", name: "Bitcoin"),
        CryptoCoinMeta(sym: "ETH", name: "Ethereum"),
        CryptoCoinMeta(sym: "SOL", name: "Solana"),
        CryptoCoinMeta(sym: "LINK", name: "Chainlink"),
        CryptoCoinMeta(sym: "AVAX", name: "Avalanche"),
        CryptoCoinMeta(sym: "DOT", name: "Polkadot"),
        CryptoCoinMeta(sym: "LTC", name: "Litecoin"),
        CryptoCoinMeta(sym: "UNI", name: "Uniswap"),
        CryptoCoinMeta(sym: "AAVE", name: "Aave"),
        CryptoCoinMeta(sym: "DOGE", name: "Dogecoin"),
        CryptoCoinMeta(sym: "BCH", name: "Bitcoin Cash"),
        CryptoCoinMeta(sym: "MKR", name: "Maker"),
    ]

    static func name(for sym: String) -> String {
        coins.first { $0.sym == sym }?.name ?? sym
    }

    /// `_baseOf`: 'BTC/USD' → 'BTC'; already-base stays as-is.
    static func baseOf(_ pairOrSym: String) -> String {
        let s = pairOrSym.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard let slash = s.firstIndex(of: "/"), slash > s.startIndex else { return s }
        return String(s[..<slash])
    }

    /// Per-coin hues for the table dots, meter and legend (`_kPalette`).
    static let palette: [Color] = [
        Color(red: 0xA7 / 255, green: 0x8B / 255, blue: 0xFA / 255),
        Color(red: 0x7C / 255, green: 0x9B / 255, blue: 0xFF / 255),
        Color(red: 0x5A / 255, green: 0xD1 / 255, blue: 0xE0 / 255),
        Color(red: 0x5E / 255, green: 0xE6 / 255, blue: 0xB8 / 255),
        Color(red: 0xF0 / 255, green: 0xB3 / 255, blue: 0x54 / 255),
        Color(red: 0xF3 / 255, green: 0x79 / 255, blue: 0x9F / 255),
        Color(red: 0xB9 / 255, green: 0x8B / 255, blue: 0xFF / 255),
    ]
    static let dynamicColor = Color(red: 0x7C / 255, green: 0x5C / 255, blue: 0xE6 / 255)

    static func color(_ index: Int) -> Color { palette[index % palette.count] }

    /// `_kStrategies`: display name (lower-cases to the backend id) + blurb.
    static let strategies: [(name: String, blurb: String)] = [
        ("Meanrev", "Mean-Reversion (RSI) — buys oversold majors (RSI-14 < 35) only while above their 200h trend MA (\"healthy dips, not falling knives\"), holds 2, sells when RSI recovers past 55. Mostly in cash. Top backtest performer: +24% over 400d at Binance.US fees while BTC fell −42%, ~8% drawdown. Needs a low-fee venue — loses at Alpaca 0.25%."),
        ("Adaptive", "Adaptive (regime switcher) — holds the whole basket (buy & hold) while the market is above both its 200-day and 50-day trend; switches to gated Mean-Reversion dip-buying when the regime breaks. Prod backtest Oct-23..Mar-24: +132% vs Mean-Reversion's +56% (holding +181%). Crash protection strong but universe-dependent: 2022 with SOL/AVAX lost 19% vs holding's −74.5%. Weakness: chop goes modestly negative. Mean-Reversion = max bear safety; Adaptive = bull participation."),
        ("Connors", "Connors (fast RSI) — quicker cousin of Mean-Reversion: buys deeply oversold dips above the trend MA, exits fast when price snaps back above a short MA. Higher turnover. Backtest: modest but robust +6% over 400d. Also needs a low-fee venue."),
        ("Momentum", "Trend-follows the auto-discovered universe — leans into coins whose momentum is strengthening. Trend strategies were whipsawed in choppy/down backtests; best in a sustained bull run."),
        ("Allocator", "Risk-weights across coins toward balanced target weights. Diversified, steadier exposure; a slower rebalancer."),
        ("Fast", "Tactical Donchian breakout trend-follower; quick in/out on short-term signals. Most responsive, highest turnover."),
        ("Reference", "A simple buy-and-rebalance baseline to benchmark the others against."),
    ]

    /// `_kStrategyRecommendedBand`.
    static let recommendedBand: [String: String] = [
        "meanrev": "low", "adaptive": "low", "connors": "low", "momentum": "medium",
        "allocator": "low", "fast": "high", "reference": "low",
    ]

    static func strategyBlurb(_ name: String) -> String {
        strategies.first { $0.name == name }?.blurb ?? ""
    }

    static func recommendedBand(for strategy: String) -> String? {
        recommendedBand[strategy.lowercased()]
    }

    /// `_kBands`.
    static let bands: [(key: String, label: String)] = [("high", "High"), ("medium", "Medium"), ("low", "Low")]

    /// `_kBandBlurb`.
    static let bandBlurb: [String: String] = [
        "high": "High — checks every ~5 min. Most reactive to fast moves; more trades and turnover.",
        "medium": "Medium — checks every ~15 min. Balanced reactivity vs. turnover. The default.",
        "low": "Low — checks every ~60 min. Calmest; fewest trades, lowest fees, slower to react.",
    ]

    /// Band → granularity seconds (the instance body and the backtest sheet).
    static let bandGranularity: [String: String] = ["high": "300", "medium": "900", "low": "3600"]

    /// "low" → "Low" (`x[0].toUpperCase() + x.substring(1)`).
    static func capitalized(_ s: String) -> String {
        s.isEmpty ? s : s.prefix(1).uppercased() + s.dropFirst()
    }
}

/// One allocation row (`_AllocRow`): the coin, its weight 0…100, and the two
/// field texts.
nonisolated struct CryptoAllocRow: Identifiable, Hashable, Sendable {
    let id = UUID()
    let sym: String
    var pct: Double
    var pctText = ""
    var usdText = ""

    var pair: String { "\(sym)/USD" }
    var name: String { CryptoCatalog.name(for: sym) }
}

/// The create / edit crypto instance form — `_CryptoInstanceSheetState`.
@Observable
final class CryptoInstanceFormModel {
    let editInstanceId: String?
    var isEdit: Bool { editInstanceId != nil }

    var instanceIdText = ""
    var name = ""
    private(set) var brokerageId = ""
    var band = "medium"
    private(set) var strategy = "Momentum"
    /// "pct" | "usd".
    var mode = "pct"
    private(set) var rows: [CryptoAllocRow] = []

    private(set) var brokerages: [JSONObject] = []
    private(set) var strategies: [JSONObject] = []
    private(set) var equity: Double = 0
    private(set) var loadingEquity = false
    /// Edit fallback: exact weights not loadable.
    private(set) var weightsUnknown = false
    private(set) var saving = false
    private(set) var err = ""

    @ObservationIgnored private let repository: () -> CryptoRepository

    init(
        editInstanceId: String? = nil,
        editName: String? = nil,
        editBrokerageId: String? = nil,
        editConfig: JSONObject? = nil,
        editStocks: [String]? = nil,
        repository: @escaping () -> CryptoRepository
    ) {
        self.editInstanceId = editInstanceId
        self.repository = repository
        if let editName { name = editName }
        if let editBrokerageId { brokerageId = editBrokerageId }
        if editInstanceId != nil {
            prefill(editConfig, editStocks: editStocks)
        } else {
            // Mirror the approved mockup's starting allocation.
            rows = [CryptoAllocRow(sym: "BTC", pct: 10), CryptoAllocRow(sym: "ETH", pct: 20)]
        }
        syncPctText()
    }

    private func prefill(_ cfg: JSONObject?, editStocks: [String]?) {
        let allocs = cfg?["allocations"]?.arrayValue ?? []
        let band = KalshiPregame.str(cfg?["band"]).lowercased()
        if !band.isEmpty { self.band = band }
        let strat = KalshiPregame.str(cfg?["strategy"]).trimmingCharacters(in: .whitespacesAndNewlines)
        if !strat.isEmpty {
            strategy = strat.prefix(1).uppercased() + strat.dropFirst().lowercased()
        }
        if !allocs.isEmpty {
            for a in allocs where a.isObject {
                let sym = CryptoCatalog.baseOf(KalshiPregame.str(a["symbol"]))
                let pct = (a["pct"].double ?? 0) * 100
                if !sym.isEmpty { rows.append(CryptoAllocRow(sym: sym, pct: pct)) }
            }
            return
        }
        // Fallback: the API doesn't surface crypto_config, so rebuild the
        // fixed coins from the stored universe at an even split and warn.
        let stocks = editStocks ?? []
        if !stocks.isEmpty {
            weightsUnknown = true
            let even = Double(dartToStringAsFixed(100.0 / Double(stocks.count), 2)) ?? 0
            for p in stocks { rows.append(CryptoAllocRow(sym: CryptoCatalog.baseOf(p), pct: even)) }
        }
    }

    // MARK: Selectors

    func loadSelectors() async {
        let repo = repository()
        do {
            let b = try await repo.brokerages()
            let s = try await repo.strategies()
            brokerages = b
            strategies = s
            // Default to the first crypto-capable account.
            if brokerageId.isEmpty, let first = Self.cryptoBrokerages(b).first {
                brokerageId = KalshiPregame.str(first["id"])
            }
        } catch {
            // Non-fatal — selectors stay empty.
        }
        await loadEquity()
    }

    /// Crypto trades on Alpaca or Binance.US; falls back to every account.
    static func cryptoBrokerages(_ all: [JSONObject]) -> [JSONObject] {
        let crypto = all.filter {
            let t = KalshiPregame.str($0["brokerage_type"]).lowercased()
            return t.contains("alpaca") || t.contains("binance")
        }
        return crypto.isEmpty ? all : crypto
    }

    func loadEquity() async {
        guard !brokerageId.isEmpty else { return }
        loadingEquity = true
        let v = await repository().accountEquity(brokerageId)
        equity = v
        loadingEquity = false
        syncUsdText()
    }

    func selectBrokerage(_ id: String) async {
        brokerageId = id
        await loadEquity()
    }

    /// The strategy dropdown: auto-applies the recommended band.
    func selectStrategy(_ s: String) {
        strategy = s
        if let rec = CryptoCatalog.recommendedBand(for: s) { band = rec }
    }

    var selectedBrokerage: JSONObject? {
        brokerages.first { KalshiPregame.str($0["id"]) == brokerageId }
    }

    // MARK: Allocation maths

    var fixedSum: Double { rows.reduce(0) { $0 + $1.pct } }
    var dynPct: Double { min(max(100 - fixedSum, 0), 100) }
    var over: Bool { fixedSum > 100.0001 }

    /// `_fmtNum`: integral → int, else 1 dp.
    static func fmtNum(_ v: Double) -> String {
        if v == v.rounded() { return String(Int(v)) }
        return dartToStringAsFixed(v, 1)
    }

    /// `_fmtUsd`: `$N` rounded.
    static func fmtUsd(_ v: Double) -> String { "$\(Int(v.rounded()))" }

    func syncPctText() {
        for i in rows.indices { rows[i].pctText = Self.fmtNum(rows[i].pct) }
        syncUsdText()
    }

    func syncUsdText() {
        for i in rows.indices { rows[i].usdText = String(Int((rows[i].pct / 100 * equity).rounded())) }
    }

    func onPctChanged(_ id: UUID, _ raw: String) {
        guard let i = rows.firstIndex(where: { $0.id == id }) else { return }
        rows[i].pctText = raw
        let v = JSON.parseDouble(raw.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
        rows[i].pct = min(max(v, 0), 100)
        rows[i].usdText = String(Int((rows[i].pct / 100 * equity).rounded()))
    }

    func onUsdChanged(_ id: UUID, _ raw: String) {
        guard let i = rows.firstIndex(where: { $0.id == id }) else { return }
        rows[i].usdText = raw
        let usd = JSON.parseDouble(raw.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
        let pct = equity > 0 ? usd / equity * 100 : 0
        rows[i].pct = min(max(pct, 0), 100)
        rows[i].pctText = Self.fmtNum(rows[i].pct)
    }

    func addCoin(_ sym: String) {
        rows.append(CryptoAllocRow(sym: sym, pct: 0, pctText: "0", usdText: "0"))
    }

    func removeCoin(_ id: UUID) {
        rows.removeAll { $0.id == id }
    }

    /// Catalog coins not yet in the table.
    var remainingCoins: [CryptoCoinMeta] {
        let have = Set(rows.map(\.sym))
        return CryptoCatalog.coins.filter { !have.contains($0.sym) }
    }

    /// Donut slices: each positive row, then the Dynamic remainder.
    var slices: [SectorSlice] {
        var out = rows.filter { $0.pct > 0 }.map { SectorSlice(sector: $0.sym, value: $0.pct, pct: $0.pct) }
        if dynPct > 0 { out.append(SectorSlice(sector: "Dynamic", value: dynPct, pct: dynPct)) }
        return out
    }

    /// `_resolveStrategyId`: the Strategies doc whose name matches.
    func resolveStrategyId() -> Int? {
        let want = strategy.lowercased()
        for s in strategies where KalshiPregame.str(s["name"]).lowercased() == want {
            if let id = JSON.parseInt(KalshiPregame.str(s["id"])) { return id }
        }
        return nil
    }

    // MARK: Submit

    var granularity: String { CryptoCatalog.bandGranularity[band] ?? "3600" }

    var cryptoConfig: JSONObject {
        let allocations: [JSON] = rows.filter { $0.pct > 0 }.map { r in
            .object(JSONObject([
                ("symbol", .string(r.pair)),
                ("pct", .double(Double(dartToStringAsFixed(r.pct / 100, 4)) ?? 0)),
            ]))
        }
        return JSONObject([
            ("band", .string(band)),
            ("strategy", .string(strategy.lowercased())),
            ("allocations", .array(allocations)),
        ])
    }

    var fixedStocks: [String] { rows.filter { $0.pct > 0 }.map(\.pair) }

    /// The PATCH body: every editable field.
    func editBody() -> JSONObject {
        var pairs: [(String, JSON)] = [("name", .string(name.trimmingCharacters(in: .whitespacesAndNewlines)))]
        if !brokerageId.isEmpty { pairs.append(("brokerage_id", .string(brokerageId))) }
        pairs += [
            ("granularity", .string(granularity)),
            ("crypto_config", .object(cryptoConfig)),
            ("stocks", .array(fixedStocks.map(JSON.string))),
        ]
        return JSONObject(pairs)
    }

    /// The POST body.
    func createBody() -> JSONObject {
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        var pairs: [(String, JSON)] = [("id", .string(instanceIdText.trimmingCharacters(in: .whitespacesAndNewlines)))]
        if !trimmedName.isEmpty { pairs.append(("name", .string(trimmedName))) }
        pairs += [
            ("granularity", .string(granularity)),
            ("run_command", .bool(false)),
            ("kind", .string("crypto")),
        ]
        if !brokerageId.isEmpty { pairs.append(("brokerage_id", .string(brokerageId))) }
        if let sid = resolveStrategyId() { pairs.append(("strategy_id", .int(sid))) }
        pairs += [
            ("stocks", .array(fixedStocks.map(JSON.string))),
            ("crypto_config", .object(cryptoConfig)),
        ]
        return JSONObject(pairs)
    }

    /// `_submit`. True when saved (the sheet closes and calls `onSaved`).
    func submit() async -> Bool {
        if !isEdit, instanceIdText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            err = "Instance ID is required"
            return false
        }
        if over {
            err = "Over-allocated — fixed weights exceed 100%"
            return false
        }
        saving = true
        err = ""
        do {
            let repo = repository()
            if let editInstanceId {
                _ = try await repo.updateInstance(editInstanceId, editBody())
            } else {
                _ = try await repo.createInstance(createBody())
            }
            return true
        } catch {
            if !marketsIsCancellation(error) { err = KalshiFormat.errorText(error) }
            saving = false
            return false
        }
    }
}
