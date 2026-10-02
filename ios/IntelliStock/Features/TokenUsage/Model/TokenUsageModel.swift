import Foundation
import Observation

/// Token usage — `TokenUsageController`: everything fetched every 10 s
/// (paused in the background), with a selectable range.
@Observable
final class TokenUsageModel {
    static let interval: Duration = .seconds(10)
    static let ranges = ["24h", "7d", "30d"]

    private(set) var range = "24h"
    private(set) var data: Loadable<TokenUsageData> = .loading

    @ObservationIgnored private let repository: () -> TokenUsageRepository

    init(repository: @escaping () -> TokenUsageRepository) {
        self.repository = repository
    }

    /// `PollingNotifier.build` then the interval poller.
    func poll(lifecycle: AppLifecycle?, sleep: @escaping PollingSleep = realPollingSleep) async {
        await refreshNow()
        await PollingLoop(interval: { Self.interval }, sleep: sleep) { [weak self] in
            await self?.refreshNow()
        }
        .run(lifecycle: lifecycle)
    }

    /// `refreshNow` (pull to refresh, the refresh button, each tick).
    func refreshNow() async {
        // A failing endpoint becomes `partialError`; only a cancellation
        // throws, and a cancelled refresh leaves the state as it was.
        do {
            data = .loaded(try await repository().fetchAllUnlessCancelled(range))
        } catch is CancellationError {
            return
        } catch {
            data = .failed(error)
        }
    }

    /// Changes the range and refreshes at once.
    func setRange(_ newRange: String) async {
        range = newRange
        await refreshNow()
    }
}

/// The header pill — `_TelemetryState`.
nonisolated enum TelemetryState: Sendable {
    case awaiting, healthy, degraded, lagging

    init(_ health: TelemetryHealth?) {
        guard let health else {
            self = .awaiting
            return
        }
        if health.writeErrors24h > 0 {
            self = .degraded
        } else if (health.lastFlushAgeS?.double ?? 0) > 30 {
            self = .lagging
        } else {
            self = .healthy
        }
    }

    var label: String {
        switch self {
        case .awaiting: "Awaiting data"
        case .healthy: "Healthy"
        case .degraded: "Degraded"
        case .lagging: "Lagging"
        }
    }
}

/// The KPI numbers — `_KpiGrid`.
nonisolated struct TokenUsageKpis: Equatable, Sendable {
    static let maxPlanBudget = 100.0

    let totalCalls: Int
    let totalTokens: Int
    let totalCost: Double
    let avgCost: Double
    let maxPlanUsd: Double
    /// 0…1 of the $100 Claude Max budget.
    let maxPlanFraction: Double
    /// The three providers with the highest cost.
    let topProviders: [ProviderBreakdown]

    init(_ summary: UsageSummary?) {
        totalCalls = summary?.totalCalls ?? 0
        totalTokens = summary?.totalTokens ?? 0
        totalCost = summary?.totalCostUsd ?? 0
        avgCost = totalCalls > 0 ? totalCost / Double(totalCalls) : 0
        maxPlanUsd = summary?.maxPlanEstimateUsd ?? 0
        maxPlanFraction = Self.maxPlanBudget > 0 ? min(max(maxPlanUsd / Self.maxPlanBudget, 0), 1) : 0
        // Dart's List.sort is not stable either way; a stable sort keeps ties in server order.
        let sorted = (summary?.byProvider ?? []).enumerated().sorted { a, b in
            let ca = a.element.costUsd ?? 0
            let cb = b.element.costUsd ?? 0
            return ca == cb ? a.offset < b.offset : ca > cb
        }
        topProviders = sorted.prefix(3).map(\.element)
    }

    /// `N% of $100 Claude Max budget`.
    var maxPlanLabel: String {
        "\(Int((maxPlanFraction * 100).rounded(.toNearestOrAwayFromZero)))% of $\(Int(Self.maxPlanBudget)) Claude Max budget"
    }
}

/// One stacked bar segment of the spend trend.
nonisolated struct SpendTrendPoint: Hashable, Sendable {
    let provider: String
    let date: Date
    let cost: Double
}

nonisolated enum SpendTrend {
    /// `_buildSeries`: cost summed per provider per bucket, providers in
    /// first-seen order, buckets ascending.
    static func points(_ rows: [TimeseriesRow]) -> (providers: [String], points: [SpendTrendPoint]) {
        var providers: [String] = []
        var buckets: [String: [Int: Double]] = [:]
        for row in rows {
            if buckets[row.provider] == nil {
                providers.append(row.provider)
                buckets[row.provider] = [:]
            }
            buckets[row.provider]![row.bucketStartTs, default: 0] += row.costUsd ?? 0
        }
        var points: [SpendTrendPoint] = []
        for provider in providers {
            for (ts, cost) in buckets[provider]!.sorted(by: { $0.key < $1.key }) {
                points.append(SpendTrendPoint(provider: provider, date: DartDateTime.fromMillisecondsSinceEpoch(ts), cost: cost))
            }
        }
        return (providers, points)
    }

    /// The y-axis label: 4 decimals below $1, else 2.
    static func axisLabel(_ v: Double) -> String {
        v < 1 ? "$" + dartToStringAsFixed(v, 4) : "$" + dartToStringAsFixed(v, 2)
    }
}
