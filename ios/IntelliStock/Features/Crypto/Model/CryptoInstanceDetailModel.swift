import Foundation
import Observation
import SwiftUI

/// A crypto instance's detail — `_CryptoInstanceDetailScreenState` in
/// crypto_instance_detail_screen.dart. Backtests re-poll every 4 s while any
/// is still running.
@Observable
final class CryptoInstanceDetailModel {
    let instanceId: String

    private(set) var inst: Instance?
    private(set) var brokerages: [JSONObject] = []
    private(set) var value: Double?
    private(set) var backtests: [InstanceBacktestRow] = []
    private(set) var loading = true
    private(set) var error: String?
    private(set) var busy = false

    static let pollInterval: Duration = .seconds(4)

    /// `_cadence`.
    static let cadence: [String: String] = ["high": "~5 min", "medium": "~15 min", "low": "~60 min"]

    @ObservationIgnored private let repository: () -> CryptoRepository

    init(instanceId: String, repository: @escaping () -> CryptoRepository) {
        self.instanceId = instanceId
        self.repository = repository
    }

    // MARK: Loads

    func load() async {
        let repo = repository()
        do {
            var inst = try await repo.getInstance(instanceId)
            // The detail endpoint omits crypto_config; backfill it (band +
            // allocations) from the list endpoint, which includes it.
            if inst.cryptoConfig == nil {
                if let list = try? await repo.listInstances(),
                   let match = list.first(where: { $0.id == instanceId }) {
                    inst.cryptoConfig = match.cryptoConfig
                    if inst.stocks.isEmpty { inst.stocks = match.stocks }
                }
            }
            let brokerages = try await repo.brokerages()
            let backtests = try await repo.instanceBacktests(instanceId)
            var value: Double?
            if let bid = inst.brokerageId, !bid.isEmpty { value = await repo.accountEquity(bid) }
            self.inst = inst
            self.brokerages = brokerages
            self.backtests = backtests
            self.value = value
            loading = false
        } catch {
            if marketsIsCancellation(error) { return }
            self.error = KalshiFormat.errorText(error)
            loading = false
        }
    }

    func refreshBacktests() async {
        if let rows = try? await repository().instanceBacktests(instanceId) { backtests = rows }
    }

    /// Whether any backtest is still in flight (the 4 s poll's guard).
    var anyRunning: Bool {
        backtests.contains { ["running", "queued", "pending", "paused"].contains($0.status.lowercased()) }
    }

    func poll(lifecycle: AppLifecycle) async {
        await load()
        await PollingLoop(interval: { Self.pollInterval }) { [weak self] in
            guard let self, self.inst != nil, self.anyRunning else { return }
            await self.refreshBacktests()
        }.run(lifecycle: lifecycle)
    }

    func retry() async {
        error = nil
        loading = true
        await load()
    }

    func toggleRun() async {
        guard let inst else { return }
        busy = true
        defer { busy = false }
        let repo = repository()
        do {
            if inst.runCommand {
                try await repo.stopInstance(inst.id)
            } else {
                try await repo.startInstance(inst.id)
            }
            let fresh = try await repo.getInstance(instanceId)
            self.inst = fresh
        } catch {
            // The refreshed state reflects reality.
        }
    }

    // MARK: Derived

    var config: JSONObject { inst?.cryptoConfig ?? JSONObject() }
    var band: String { KalshiPregame.str(config["band"]).lowercased() }
    var strategy: String { KalshiPregame.str(config["strategy"]) }

    var brokerage: JSONObject? {
        // `b?['id']?.toString() == inst.brokerageId` (null matches null).
        brokerages.first { b in
            b["id"].flatMap { $0.isNull ? nil : $0.dartDescription } == inst?.brokerageId
        }
    }

    var isPaper: Bool { brokerage?["alpaca_paper"] == .bool(true) }

    /// Fixed allocation slices plus the Dynamic remainder (> 0.01).
    var slices: [SectorSlice] {
        var out: [SectorSlice] = []
        for a in (config["allocations"]?.arrayValue ?? []) where a.isObject {
            let pct = (a["pct"].double ?? 0) * 100
            if pct > 0 {
                let sym = String(KalshiPregame.str(a["symbol"]).split(separator: "/", omittingEmptySubsequences: false).first ?? "")
                out.append(SectorSlice(sector: sym, value: pct, pct: pct))
            }
        }
        let dyn = 100 - out.reduce(0) { $0 + $1.value }
        if dyn > 0.01 { out.append(SectorSlice(sector: "Dynamic", value: dyn, pct: dyn)) }
        return out
    }

    /// The allocation chips: "SYM N%" per allocation, then "Dynamic N%".
    var allocChips: [(text: String, dynamic: Bool)] {
        var chips: [(String, Bool)] = []
        var fixed = 0.0
        for a in (config["allocations"]?.arrayValue ?? []) where a.isObject {
            let pct = (a["pct"].double ?? 0) * 100
            fixed += pct
            let sym = String(KalshiPregame.str(a["symbol"]).split(separator: "/", omittingEmptySubsequences: false).first ?? "")
            chips.append(("\(sym) \(Int(pct.rounded()))%", false))
        }
        let dyn = Int(min(max(100 - fixed, 0), 100).rounded())
        chips.append(("Dynamic \(dyn)%", true))
        return chips
    }

    // MARK: Formatting (screen helpers)

    /// `_fmtUsd`: `$1,234` (rounded, comma-grouped).
    static func fmtUsd(_ n: Double?) -> String {
        let v = Int((n ?? 0).rounded())
        let digits = String(abs(v))
        var grouped = ""
        for (i, ch) in digits.enumerated() {
            if i > 0, (digits.count - i) % 3 == 0 { grouped.append(",") }
            grouped.append(ch)
        }
        return "$\(v < 0 ? "-" : "")\(grouped)"
    }

    /// `_fmtDuration`: "Nh Nm Ns".
    static func fmtDuration(_ s: Int?) -> String {
        let v = s ?? 0
        return "\(v / 3600)h \(dartMod(v, 3600) / 60)m \(dartMod(v, 60))s"
    }

    static func fmtPnl(_ n: Double?) -> String {
        guard let n else { return "—" }
        return "\(n >= 0 ? "+" : "")\(fmtUsd(n))"
    }

    static func fmtPct(_ n: Double?) -> String {
        guard let n else { return "—" }
        return "\(n >= 0 ? "+" : "")\(dartToStringAsFixed(n, 2))%"
    }

    static func pnlColor(_ n: Double?) -> Color {
        guard let n, n != 0 else { return .primary }
        return n > 0 ? DS.Palette.success : DS.Palette.danger
    }

    static func btStatusColor(_ s: String) -> Color {
        switch s.lowercased() {
        case "finished", "completed", "done": DS.Palette.success
        case "running", "queued", "pending": DS.Palette.info
        case "error", "failed": DS.Palette.danger
        case "paused": DS.Palette.warning
        default: .secondary
        }
    }
}
