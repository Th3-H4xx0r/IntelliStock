import Foundation
import Observation
import SwiftUI

/// The Nexus graph screen's state — `NexusController` (a `PollingNotifier`):
/// status every 2 s while building, 5 s idle, plus the control actions.
@Observable
final class NexusModel {
    private(set) var status: Loadable<NexusStatus> = .loading
    private(set) var busy = false
    private(set) var errorMessage: String?

    @ObservationIgnored private let repository: () -> NexusRepository

    init(repository: @escaping () -> NexusRepository) {
        self.repository = repository
    }

    var statusValue: NexusStatus? { status.value }

    /// 2 s while building, 5 s idle.
    var interval: Duration {
        statusValue?.isBuilding == true ? .seconds(2) : .seconds(5)
    }

    /// `fetch`: keeps `busy`, clears the error message.
    func refreshNow() async {
        let repo = repository()
        do {
            let s = try await repo.status()
            status = .loaded(s)
            errorMessage = nil
        } catch {
            if marketsIsCancellation(error) { return }
            // A failed refetch after data keeps the data (AsyncValue keeps
            // the previous value); a first failure shows the error view.
            if status.value == nil { status = .failed(error) }
        }
    }

    func poll(lifecycle: AppLifecycle) async {
        await refreshNow()
        await PollingLoop(interval: { [weak self] in self?.interval ?? .seconds(5) }) { [weak self] in
            await self?.refreshNow()
        }.run(lifecycle: lifecycle)
    }

    // MARK: Control actions (`_withBusy`)

    private func withBusy(_ action: () async throws -> Void) async {
        if status.value != nil {
            busy = true
            errorMessage = nil
        }
        do {
            try await action()
        } catch {
            if marketsIsCancellation(error) { busy = false; return }
            if status.value != nil {
                busy = false
                errorMessage = KalshiFormat.errorText(error)
            }
            return
        }
        await refreshNow()
        busy = false
    }

    func postControl(_ body: JSONObject) async {
        let repo = repository()
        await withBusy { try await repo.control(body) }
    }

    func rebuild(_ body: JSONObject) async {
        let repo = repository()
        await withBusy { _ = try await repo.rebuild(body) }
    }

    func deleteEdges(_ body: JSONObject) async {
        let repo = repository()
        await withBusy { try await repo.deleteEdges(body) }
    }

    func fetchCache() async -> NexusCacheInfo? {
        try? await repository().cache()
    }
}

// MARK: - Screen helpers (ported 1:1)

nonisolated enum NexusFormat {
    /// `_stageColor`.
    static func stageColor(_ status: String) -> Color {
        switch status.lowercased() {
        case "running": DS.Palette.info
        case "completed", "skipped": DS.Palette.success
        case "stopped": DS.Palette.warning
        case "failed": DS.Palette.danger
        default: Color(uiColor: .tertiaryLabel)
        }
    }

    /// `_stageIcon` (material names).
    static func stageIcon(_ status: String) -> String {
        switch status.lowercased() {
        case "completed": "checkmark.circle.fill"
        case "skipped": "minus.circle.fill"
        case "stopped": "pause.circle.fill"
        case "failed": "xmark.circle.fill"
        default: "circle"
        }
    }

    /// `_fmtDuration`: "", "N.Ns", "Nm Ns" / "Nm", "Nh Nm" / "Nh".
    static func fmtDuration(_ sec: Double?) -> String {
        guard let sec else { return "" }
        if sec < 60 { return "\(dartToStringAsFixed(sec, 1))s" }
        if sec >= 3600 {
            let h = Int(sec / 3600)
            let m = Int((dartMod(sec, 3600) / 60).rounded(.towardZero))
            return m > 0 ? "\(h)h \(m)m" : "\(h)h"
        }
        let m = Int(sec / 60)
        let s = Int(dartMod(sec, 60).rounded())
        return s > 0 ? "\(m)m \(s)s" : "\(m)m"
    }

    /// `_autoUpdateSummary`.
    static func autoUpdateSummary(_ c: NexusControl) -> String {
        if !c.autoUpdateEnabled { return "Disabled" }
        let hours = c.autoUpdateIntervalHours
        if dartMod(hours, 24) == 0 {
            let d = hours / 24
            return "Every \(d) day\(d == 1 ? "" : "s")"
        }
        return "Every \(hours) hour\(hours == 1 ? "" : "s")"
    }

    /// The auto-update range labels: control labels, else the option, else
    /// the fallback phase label.
    static func rangeLabels(_ c: NexusControl) -> (start: String, end: String) {
        func label(_ explicit: String?, _ value: Int, _ fallback: NexusPhaseOption) -> String {
            if let explicit { return explicit }
            if c.phaseOptions.isEmpty { return fallback.label }
            return (c.phaseOptions.first { $0.value == value } ?? fallback).label
        }
        return (
            label(c.autoUpdateStartPhaseLabel, c.autoUpdateStartPhase, NexusPhaseOption(value: 3, label: "Phase 2b: SEC sector/industry")),
            label(c.autoUpdateEndPhaseLabel, c.autoUpdateEndPhase, NexusPhaseOption(value: 14, label: "Phase 12: ETF universe"))
        )
    }

    /// `_GraphCountsCard._fmtNum`: k / M with one decimal.
    static func fmtNum(_ v: JSON?) -> String {
        guard let v, !v.isNull else { return "—" }
        let parsed: Num? = v.isNum ? v.num : Num.tryParse(v.dartDescription)
        guard let parsed else { return "—" }
        let n = parsed.int
        if n >= 1_000_000 { return "\(dartToStringAsFixed(Double(n) / 1_000_000, 1))M" }
        if n >= 1000 { return "\(dartToStringAsFixed(Double(n) / 1000, 1))k" }
        return String(n)
    }

    static func fmtNum(_ v: Int?) -> String { fmtNum(v.map { JSON.int($0) }) }

    /// Bootstrap badge text and colour.
    static func bootstrapPill(_ s: String) -> (text: String, color: Color) {
        switch s {
        case "completed": ("Bootstrap Ready", DS.Palette.success)
        case "running": ("Bootstrap Running", DS.Palette.info)
        case "never_built": ("Never Built", .secondary)
        case "disabled": ("Disabled", .secondary)
        case "partial": ("Bootstrap Partial", DS.Palette.warning)
        default: ("Bootstrap Pending", .secondary)
        }
    }

    /// The bootstrap card's coverage line.
    static func coverageSummary(_ status: NexusStatus) -> String {
        guard let b = status.bootstrap else { return "Disabled" }
        if let s = b.startDate, let e = b.coverageEnd { return "\(s) → \(e)" }
        if let h = status.control.historicalStartDate { return "From \(h)" }
        return "Disabled"
    }

    /// The Start modal's body, keys in the Dart order.
    static func startBody(selectedPhases: [Int], historyQuarters: Int, historicalMode: Bool, historicalStartDate: String, forceBootstrapRebuild: Bool) -> JSONObject {
        var pairs: [(String, JSON)] = [
            ("running", true),
            ("phase7_history_quarters", .int(historyQuarters)),
            ("historical_mode_enabled", .bool(historicalMode)),
        ]
        if historicalMode, !historicalStartDate.isEmpty { pairs.append(("historical_start_date", .string(historicalStartDate))) }
        if forceBootstrapRebuild { pairs.append(("force_bootstrap_rebuild", true)) }
        pairs.append(("selected_phases", .array(selectedPhases.sorted().map(JSON.int))))
        return JSONObject(pairs)
    }

    /// The auto-update modal's body.
    static func autoUpdateBody(enabled: Bool, intervalHours: Int, startPhase: Int, endPhase: Int) -> JSONObject {
        var pairs: [(String, JSON)] = [
            ("auto_update_enabled", .bool(enabled)),
            ("auto_update_interval_hours", .int(intervalHours)),
            ("auto_update_start_phase", .int(startPhase)),
            ("auto_update_end_phase", .int(endPhase)),
        ]
        if enabled { pairs.append(("running", true)) }
        return JSONObject(pairs)
    }

    /// The Full Rebuild body.
    static func rebuildBody(destructive: Bool, forceBootstrap: Bool, cachePaths: [String]) -> JSONObject {
        JSONObject([
            ("confirm", true),
            ("destructive", .bool(destructive)),
            ("force_bootstrap_rebuild", .bool(forceBootstrap)),
            ("delete_cache_paths", .array(cachePaths.map(JSON.string))),
        ])
    }

    /// The log row stamp "MM-dd, HH:mm:ss" (local).
    static func logStamp(_ ts: Date) -> String {
        let c = Calendar.current.dateComponents([.month, .day, .hour, .minute, .second], from: ts)
        func two(_ n: Int?) -> String { String(format: "%02d", n ?? 0) }
        return "\(two(c.month))-\(two(c.day)), \(two(c.hour)):\(two(c.minute)):\(two(c.second))"
    }

    /// The logs panel's friendly status.
    static func friendlyStatus(_ finalStatus: String?) -> String {
        switch (finalStatus ?? "").lowercased() {
        case "running", "building": "Building"
        case "completed": "Completed"
        case "failed": "Failed"
        case "none": "Not running"
        default: "Idle"
        }
    }

    /// "nexus-<last 8 of the build id | latest>.log".
    static func logFileName(_ buildId: String?) -> String {
        guard let id = buildId else { return "nexus-latest.log" }
        return "nexus-\(id.count > 8 ? String(id.suffix(8)) : id).log"
    }
}
