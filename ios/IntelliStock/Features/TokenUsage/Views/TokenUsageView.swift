import Charts
import SwiftUI

/// LLM token usage — `TokenUsageScreen` in `token_usage_screen.dart`: the
/// range, the KPIs, the stacked spend trend, top spenders, cost by run and
/// recent calls, refreshed every 10 s. Native form: an inset-grouped list;
/// the KPIs are a `StatGrid`, the trend a chart row, the tables rows. The nav
/// bar holds the only title; pull to refresh replaces the refresh button.
struct TokenUsageView: View {
    @Environment(AppServices.self) private var services
    @State private var model: TokenUsageModel?
    @State private var selectedCall: RecentCall?

    var body: some View {
        Group {
            if let model {
                content(model)
            } else {
                TokenUsagePlaceholder()
            }
        }
        .navigationTitle("Token Usage")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: Binding(
            get: { selectedCall.map(TokenUsageCallItem.init) },
            set: { selectedCall = $0?.call }
        )) { item in
            TokenUsageCallDetail(call: item.call)
        }
        .onAppear {
            if model == nil {
                let services = services
                model = TokenUsageModel(repository: { services.tokenUsageRepository })
            }
        }
        .task(id: model == nil) {
            await model?.poll(lifecycle: services.lifecycle)
        }
    }

    @ViewBuilder
    private func content(_ model: TokenUsageModel) -> some View {
        switch model.data {
        case .loading:
            TokenUsagePlaceholder()
        case .failed(let error):
            List {
                Section {
                    ErrorRow(message: llmErrorText(error)) { Task { await model.refreshNow() } }
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                }
            }
            .listStyle(.insetGrouped)
            .refreshable { await model.refreshNow() }
        case .loaded(let data):
            List {
                TokenUsageSections(
                    data: data,
                    range: model.range,
                    onRange: { range in Task { await model.setRange(range) } },
                    onCall: { selectedCall = $0 },
                    loadedRange: model.loadedRange
                )
            }
            .listStyle(.insetGrouped)
            .refreshable { await model.refreshNow() }
        }
    }
}

private struct TokenUsageCallItem: Identifiable {
    let call: RecentCall
    var id: String { call.id ?? "\(call.ts ?? 0)-\(call.model ?? "")" }
}

/// The skeleton — the sections over sample data, redacted.
private struct TokenUsagePlaceholder: View {
    var body: some View {
        List {
            TokenUsageSections(data: TokenUsageData(), range: "24h", onRange: { _ in }, onCall: { _ in })
        }
        .listStyle(.insetGrouped)
        .redacted(reason: .placeholder)
        .allowsHitTesting(false)
    }
}

private struct TokenUsageSections: View {
    let data: TokenUsageData
    let range: String
    let onRange: (String) -> Void
    let onCall: (RecentCall) -> Void
    /// The range `data` belongs to, which names the trend's series.
    var loadedRange: String?

    var body: some View {
        let telemetry = TelemetryState(data.summary?.telemetryHealth)
        let k = TokenUsageKpis(data.summary)
        let health = data.summary?.telemetryHealth

        // The range picker, at the top of the list.
        Section {
            Picker("Range", selection: Binding(get: { range }, set: { onRange($0) })) {
                ForEach(TokenUsageModel.ranges, id: \.self) { Text($0).tag($0) }
            }
            .pickerStyle(.segmented)
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets())
            if let partial = data.partialError {
                ErrorRow(message: partial)
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 8, leading: 0, bottom: 0, trailing: 0))
            }
        }

        // The old KPI cards' figures in one grid.
        Section {
            StatGrid {
                StatCell(label: "Period cost", value: fmtUsdCost(k.totalCost),
                         footnote: "\(fmtTokens(k.totalTokens)) tokens · \(k.totalCalls) calls")
                StatCell(label: "Period calls", value: "\(k.totalCalls)")
                StatCell(label: "Avg cost", value: fmtUsdCost(k.avgCost))
                StatCell(label: "Recent rows", value: "\(data.recentCalls.count)")
            }
            .padding(.vertical, 4)
            if k.topProviders.isEmpty {
                Text("No provider spend")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(k.topProviders.enumerated()), id: \.offset) { _, p in
                    LabeledContent(p.provider) {
                        Text(fmtUsdCost(p.costUsd)).monospacedDigit()
                    }
                }
            }
        }

        Section("Max plan estimate") {
            VStack(alignment: .leading, spacing: 8) {
                Text(fmtUsdCost(k.maxPlanUsd))
                    .font(.title2.bold().monospacedDigit())
                ProgressView(value: k.maxPlanFraction)
                    .tint(DS.Palette.accent)
                Text(k.maxPlanLabel)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .padding(.vertical, 4)
        }

        Section {
            StatGrid(columns: 3) {
                StatCell(label: "Buffer", value: health?.bufferDepth.map(String.init) ?? "—")
                StatCell(label: "Last flush", value: health?.lastFlushAgeS.map { "\($0)s" } ?? "—")
                StatCell(label: "Errors 24h", value: "\(health?.writeErrors24h ?? 0)")
            }
            .padding(.vertical, 4)
        } header: {
            DSSectionHeader("Telemetry health") {
                StatusBadge(label: telemetry.label, color: Self.color(telemetry))
            }
        }

        TokenUsageSpendTrend(rows: data.timeseries, range: loadedRange)

        TokenUsageSpenders(title: "Top spenders by model", rows: data.topByModel, empty: "No model spend recorded yet.")
        TokenUsageSpenders(title: "Top spenders by call site", rows: data.topByCallSite, empty: "No call-site spend recorded yet.")

        Section("LLM cost by run") {
            if data.byBacktest.isEmpty {
                Text("No LLM cost data in this range.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(data.byBacktest.enumerated()), id: \.offset) { _, row in
                    let isBacktest = (row.kind ?? "backtest") == "backtest"
                    if isBacktest, let id = row.backtestId {
                        NavigationLink(value: Route.backtest(id)) {
                            TokenUsageRunRow(row: row)
                        }
                    } else {
                        TokenUsageRunRow(row: row)
                    }
                }
            }
        }

        Section("Recent calls") {
            if data.recentCalls.isEmpty {
                Text("No calls recorded yet.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(data.recentCalls.enumerated()), id: \.offset) { _, call in
                    Button { onCall(call) } label: { TokenUsageCallRow(call: call) }
                        .foregroundStyle(.primary)
                }
            }
        }
    }

    static func color(_ state: TelemetryState) -> Color {
        switch state {
        case .awaiting: .secondary
        case .healthy: DS.Palette.success
        case .degraded: DS.Palette.danger
        case .lagging: DS.Palette.warning
        }
    }
}

// MARK: - Spend trend

private struct TokenUsageSpendTrend: View {
    let rows: [TimeseriesRow]
    /// The range the rows were fetched for: a new one draws the bars in again.
    let range: String?

    private static let palette: [Color] = [DS.Palette.accent, DS.Palette.info, DS.Palette.success,
                                           DS.Palette.warning, DS.Palette.teal, DS.Palette.danger]

    var body: some View {
        let trend = SpendTrend.points(rows)
        Section {
            if trend.points.isEmpty {
                VStack(spacing: 4) {
                    Text("No usage in this window.").font(.subheadline)
                    Text("Calls will appear here after telemetry flushes.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity, minHeight: 160)
            } else {
                Chart(trend.points, id: \.self) { point in
                    BarMark(
                        x: .value("Time", point.date),
                        y: .value("Cost (USD)", point.cost)
                    )
                    .foregroundStyle(by: .value("Provider", point.provider))
                }
                .chartForegroundStyleScale(domain: trend.providers, range: trend.providers.indices.map {
                    Self.palette[$0 % Self.palette.count]
                })
                // Only the plot draws in, left to right; the axes and legend
                // stay put.
                .chartPlotStyle { $0.chartDrawIn(trigger: AnyHashable(range)) }
                .chartLegend(position: .top, alignment: .leading)
                .chartYAxisLabel("Cost (USD)")
                .chartXAxis {
                    AxisMarks(values: .automatic(desiredCount: 4)) { _ in
                        AxisValueLabel().font(.caption2)
                    }
                }
                .chartYAxis {
                    AxisMarks(values: .automatic(desiredCount: 3)) { value in
                        AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: DS.baselineDash))
                        AxisValueLabel {
                            if let v = value.as(Double.self) {
                                Text(SpendTrend.axisLabel(v)).font(.caption2)
                            }
                        }
                    }
                }
                .frame(height: 220)
                .padding(.vertical, 8)
            }
        } header: {
            Text("Cost over time")
        } footer: {
            Text("Stacked by provider")
        }
    }
}

// MARK: - Rows

private struct TokenUsageSpenders: View {
    let title: String
    let rows: [SpenderRow]
    let empty: String

    var body: some View {
        Section(title) {
            if rows.isEmpty {
                Text(empty)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    EntityRow(row.key, subtitle: "\(row.calls ?? 0) calls · \(fmtTokens(row.tokens)) tokens") {
                        EntityRowValue(fmtUsdCost(row.costUsd))
                    }
                }
            }
        }
    }
}

/// One run's LLM cost: the label, then kind · instance · when; cost, tokens
/// and calls, and the ok count, trailing.
private struct TokenUsageRunRow: View {
    let row: BacktestUsageRow

    var body: some View {
        let kind = row.kind ?? "backtest"
        let failed = (row.failedCalls ?? 0) > 0
        let kindLabel: String = kind.prefix(1).uppercased() + String(kind.dropFirst())
        let parts: [String?] = [kindLabel, row.instanceId, row.firstTs.map { fmtDateTime($0) }]
        let subtitle = parts.compactMap { $0 }.joined(separator: " · ")
        EntityRow(row.displayLabel ?? "#\(row.backtestId ?? "?")", subtitle: subtitle, subtitleLineLimit: 2) {
            VStack(alignment: .trailing, spacing: 2) {
                Text(fmtUsdCost(row.costUsd))
                    .font(.body)
                Text("\(fmtTokens(row.tokens)) · \(row.calls ?? 0) calls")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Text("\(row.okCalls ?? 0)/\(row.calls ?? 0) ok")
                    .font(.footnote)
                    .foregroundStyle(failed ? DS.Palette.warning : DS.Palette.success)
            }
            .monospacedDigit()
            .lineLimit(1)
        }
    }
}

/// One recent call: the model, then provider · strategy / call site; cost,
/// tokens in and out, and how long ago, trailing. Tapping shows the raw JSON.
private struct TokenUsageCallRow: View {
    let call: RecentCall

    var body: some View {
        let parts: [String?] = [call.provider, "\(call.strategy ?? "—") / \(call.callSite ?? "—")"]
        let subtitle = parts.compactMap { $0 }.joined(separator: " · ")
        EntityRow(call.model ?? "—", subtitle: subtitle, subtitleLineLimit: 2) {
            VStack(alignment: .trailing, spacing: 2) {
                Text(fmtUsdCost(call.totalCostUsd))
                    .font(.body)
                Text("↑\(fmtTokens(call.inputTokens)) ↓\(fmtTokens(call.outputTokens))")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                if let ts = call.ts {
                    Text(fmtRelative(ts))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .monospacedDigit()
            .lineLimit(1)
        }
    }
}

/// `_CallDetailOverlay` → a sheet with the call's raw JSON.
private struct TokenUsageCallDetail: View {
    let call: RecentCall
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                Text(verbatim: (try? JSON.object(call.raw).dartEncoded(indent: "  ")) ?? "")
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                    .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
                    .padding(16)
            }
            .background(DS.Surface.canvas)
            .navigationTitle(call.model ?? call.provider ?? "Call detail")
            .navigationSubtitle("Recent call")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        dismiss()
                    } label: {
                        Image(systemName: Symbol.named("close"))
                    }
                    .accessibilityLabel("Close")
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }
}
