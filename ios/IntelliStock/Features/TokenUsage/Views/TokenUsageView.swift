import Charts
import SwiftUI

/// LLM token usage — `TokenUsageScreen` in `token_usage_screen.dart`: the
/// telemetry header, range, KPI cards, the stacked spend trend, top spenders,
/// cost by run and recent calls, refreshed every 10 s.
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
        .background(DS.Surface.canvas)
        .navigationTitle("Token Usage")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await model?.refreshNow() }
                } label: {
                    if model?.data.isLoading ?? true {
                        ProgressView()
                    } else {
                        Image(systemName: Symbol.named("refresh"))
                    }
                }
                .accessibilityLabel("Refresh")
            }
        }
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
            ScrollView {
                ErrorRow(message: llmErrorText(error)) { Task { await model.refreshNow() } }
                    .padding(20)
            }
            .refreshable { await model.refreshNow() }
        case .loaded(let data):
            TokenUsageBody(model: model, data: data) { selectedCall = $0 }
        }
    }
}

private struct TokenUsageCallItem: Identifiable {
    let call: RecentCall
    var id: String { call.id ?? "\(call.ts ?? 0)-\(call.model ?? "")" }
}

/// The skeleton — the body over sample data, redacted.
private struct TokenUsagePlaceholder: View {
    var body: some View {
        ScrollView {
            TokenUsageSections(data: TokenUsageData(), range: "24h", onRange: { _ in }, onCall: { _ in })
                .redacted(reason: .placeholder)
                .allowsHitTesting(false)
        }
    }
}

private struct TokenUsageBody: View {
    let model: TokenUsageModel
    let data: TokenUsageData
    let onCall: (RecentCall) -> Void

    var body: some View {
        ScrollView {
            TokenUsageSections(
                data: data,
                range: model.range,
                onRange: { range in Task { await model.setRange(range) } },
                onCall: onCall
            )
        }
        .refreshable { await model.refreshNow() }
    }
}

private struct TokenUsageSections: View {
    let data: TokenUsageData
    let range: String
    let onRange: (String) -> Void
    let onCall: (RecentCall) -> Void

    @Environment(AppServices.self) private var services

    var body: some View {
        let telemetry = TelemetryState(data.summary?.telemetryHealth)
        VStack(alignment: .leading, spacing: 20) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 12) {
                    IconTile(systemImage: Symbol.named("payments"))
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 8) {
                            Text("Token Usage").font(.title3.bold())
                            StatusBadge(label: telemetry.label, color: Self.color(telemetry))
                        }
                        Text("Live telemetry across providers, models, and strategy call sites.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
                Picker("Range", selection: Binding(get: { range }, set: { onRange($0) })) {
                    ForEach(TokenUsageModel.ranges, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented)
                if let partial = data.partialError {
                    ErrorRow(message: partial)
                }
            }

            TokenUsageKpiGrid(data: data)
            TokenUsageSpendTrend(rows: data.timeseries)

            TokenUsageSpenders(title: "Top spenders by model", rows: data.topByModel, empty: "No model spend recorded yet.")
            TokenUsageSpenders(title: "Top spenders by call site", rows: data.topByCallSite, empty: "No call-site spend recorded yet.")

            VStack(alignment: .leading, spacing: 8) {
                Text("LLM cost by run").font(.headline)
                if data.byBacktest.isEmpty {
                    TokenUsageEmptyCard(text: "No LLM cost data in this range.")
                } else {
                    ForEach(Array(data.byBacktest.enumerated()), id: \.offset) { _, row in
                        let isBacktest = (row.kind ?? "backtest") == "backtest"
                        if isBacktest, let id = row.backtestId {
                            Button {
                                services.router.push(.backtest(id))
                            } label: {
                                TokenUsageRunRow(row: row, chevron: true)
                            }
                            .buttonStyle(.plain)
                        } else {
                            TokenUsageRunRow(row: row, chevron: false)
                        }
                    }
                }
            }

            VStack(alignment: .leading, spacing: 8) {
                Text("Recent calls").font(.headline)
                if data.recentCalls.isEmpty {
                    TokenUsageEmptyCard(text: "No calls recorded yet.")
                } else {
                    ForEach(Array(data.recentCalls.enumerated()), id: \.offset) { _, call in
                        Button { onCall(call) } label: { TokenUsageCallRow(call: call) }
                            .buttonStyle(.plain)
                    }
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 4)
        .padding(.bottom, 40)
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

// MARK: - KPI cards

private struct TokenUsageKpiGrid: View {
    let data: TokenUsageData

    var body: some View {
        let k = TokenUsageKpis(data.summary)
        let health = data.summary?.telemetryHealth
        Grid(horizontalSpacing: 12, verticalSpacing: 12) {
            GridRow {
                Card(padding: 16) {
                    VStack(alignment: .leading, spacing: 4) {
                        TokenUsageEyebrow("PERIOD COST")
                        Text(fmtUsdCost(k.totalCost)).font(.title3.bold().monospacedDigit())
                        Text("\(fmtTokens(k.totalTokens)) tokens · \(k.totalCalls) calls")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        if k.topProviders.isEmpty {
                            Text("No provider spend").font(.caption2).foregroundStyle(.tertiary)
                        } else {
                            ForEach(Array(k.topProviders.enumerated()), id: \.offset) { _, p in
                                Text("\(p.provider) · \(fmtUsdCost(p.costUsd))")
                                    .font(.caption2)
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 2)
                                    .background(DS.Surface.inset, in: .rect(cornerRadius: 4))
                            }
                        }
                    }
                }
                Card(padding: 16) {
                    VStack(alignment: .leading, spacing: 8) {
                        TokenUsageEyebrow("PERIOD CALLS")
                        Text("\(k.totalCalls)").font(.title3.bold().monospacedDigit())
                        HStack(alignment: .top) {
                            TokenUsageMini(label: "Avg cost", value: fmtUsdCost(k.avgCost))
                            TokenUsageMini(label: "Recent rows", value: "\(data.recentCalls.count)")
                        }
                    }
                    .frame(maxHeight: .infinity, alignment: .top)
                }
            }
            GridRow {
                Card(padding: 16) {
                    VStack(alignment: .leading, spacing: 8) {
                        TokenUsageEyebrow("MAX PLAN ESTIMATE")
                        Text(fmtUsdCost(k.maxPlanUsd)).font(.title3.bold().monospacedDigit())
                        ProgressView(value: k.maxPlanFraction)
                            .tint(DS.Palette.accent)
                        Text(k.maxPlanLabel)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxHeight: .infinity, alignment: .top)
                }
                Card(padding: 16) {
                    VStack(alignment: .leading, spacing: 8) {
                        TokenUsageEyebrow("TELEMETRY HEALTH")
                        HStack(alignment: .top) {
                            TokenUsageMini(label: "Buffer", value: health?.bufferDepth.map(String.init) ?? "—")
                            TokenUsageMini(label: "Last flush", value: health?.lastFlushAgeS.map { "\($0)s" } ?? "—")
                            TokenUsageMini(label: "Errors 24h", value: "\(health?.writeErrors24h ?? 0)")
                        }
                    }
                    .frame(maxHeight: .infinity, alignment: .top)
                }
            }
        }
    }
}

private struct TokenUsageEyebrow: View {
    let text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text)
            .font(.caption.weight(.bold))
            .tracking(1.2)
            .foregroundStyle(.tint)
            .lineLimit(1)
            .minimumScaleFactor(0.8)
    }
}

private struct TokenUsageMini: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label.uppercased())
                .font(.caption2)
                .tracking(0.5)
                .foregroundStyle(.tertiary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
            Text(value)
                .font(.footnote.monospacedDigit())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Spend trend

private struct TokenUsageSpendTrend: View {
    let rows: [TimeseriesRow]

    private static let palette: [Color] = [DS.Palette.accent, DS.Palette.info, DS.Palette.success,
                                           DS.Palette.warning, DS.Palette.teal, DS.Palette.danger]

    var body: some View {
        let trend = SpendTrend.points(rows)
        Card(padding: 16) {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .bottom) {
                    VStack(alignment: .leading, spacing: 2) {
                        TokenUsageEyebrow("SPEND TREND")
                        Text("Cost over time").font(.headline)
                    }
                    Spacer()
                    Text("Stacked by provider").font(.caption2).foregroundStyle(.secondary)
                }
                if trend.points.isEmpty {
                    VStack(spacing: 4) {
                        Text("No usage in this window.").font(.footnote)
                        Text("Calls will appear here after telemetry flushes.")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, minHeight: 180)
                    .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
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
                    .chartLegend(position: .top, alignment: .leading)
                    .chartYAxisLabel("Cost (USD)")
                    .chartYAxis {
                        AxisMarks { value in
                            AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                            AxisValueLabel {
                                if let v = value.as(Double.self) {
                                    Text(SpendTrend.axisLabel(v)).font(.caption2)
                                }
                            }
                        }
                    }
                    .frame(height: 240)
                }
            }
        }
    }
}

// MARK: - Rows

private struct TokenUsageSpenders: View {
    let title: String
    let rows: [SpenderRow]
    let empty: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            if rows.isEmpty {
                TokenUsageEmptyCard(text: empty)
            } else {
                Card(padding: EdgeInsets()) {
                    VStack(spacing: 0) {
                        ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                            HStack(spacing: 12) {
                                Text(verbatim: row.key)
                                    .font(.system(.footnote, design: .monospaced))
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                stat("\(row.calls ?? 0)", "calls")
                                stat(fmtTokens(row.tokens), "tokens")
                                Text(fmtUsdCost(row.costUsd))
                                    .font(.footnote.weight(.semibold).monospacedDigit())
                            }
                            .padding(.horizontal, 16)
                            .padding(.vertical, 10)
                            if index < rows.count - 1 {
                                Divider().padding(.leading, 16)
                            }
                        }
                    }
                }
            }
        }
    }

    private func stat(_ value: String, _ label: String) -> some View {
        VStack(spacing: 0) {
            Text(value).font(.footnote.monospacedDigit())
            Text(label).font(.caption2).foregroundStyle(.tertiary)
        }
    }
}

private struct TokenUsageRunRow: View {
    let row: BacktestUsageRow
    let chevron: Bool

    var body: some View {
        let kind = row.kind ?? "backtest"
        let failed = (row.failedCalls ?? 0) > 0
        Card(padding: 14) {
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(verbatim: row.displayLabel ?? "#\(row.backtestId ?? "?")")
                        .font(.system(.footnote, design: .monospaced))
                        .lineLimit(1)
                    HStack(spacing: 8) {
                        AppBadge(label: kind, color: kind == "live" ? DS.Palette.success : DS.Palette.accent)
                        if let instance = row.instanceId {
                            Text(instance).font(.caption2).foregroundStyle(.secondary)
                        }
                        if let first = row.firstTs {
                            Text(fmtDateTime(first)).font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                }
                Spacer(minLength: 8)
                VStack(alignment: .trailing, spacing: 2) {
                    Text(fmtUsdCost(row.costUsd)).font(.footnote.weight(.semibold).monospacedDigit())
                    Text("\(fmtTokens(row.tokens)) · \(row.calls ?? 0) calls")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Text("\(row.okCalls ?? 0)/\(row.calls ?? 0) ok")
                        .font(.caption2)
                        .foregroundStyle(failed ? DS.Palette.warning : DS.Palette.success)
                }
                if chevron {
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.tertiary)
                }
            }
        }
        .contentShape(Rectangle())
    }
}

private struct TokenUsageCallRow: View {
    let call: RecentCall

    var body: some View {
        Card(padding: EdgeInsets(top: 10, leading: 14, bottom: 10, trailing: 14)) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        if let provider = call.provider {
                            Text(provider).font(.footnote)
                        }
                        Text(verbatim: call.model ?? "—")
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                    Text("\(call.strategy ?? "—") / \(call.callSite ?? "—")")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 10)
                VStack(alignment: .trailing, spacing: 1) {
                    Text(fmtUsdCost(call.totalCostUsd)).font(.footnote.weight(.medium).monospacedDigit())
                    Text("↑\(fmtTokens(call.inputTokens)) ↓\(fmtTokens(call.outputTokens))")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    if let ts = call.ts {
                        Text(fmtRelative(ts)).font(.caption2).foregroundStyle(.tertiary)
                    }
                }
            }
        }
        .contentShape(Rectangle())
    }
}

private struct TokenUsageEmptyCard: View {
    let text: String

    var body: some View {
        Card(padding: 16) {
            Text(text)
                .font(.footnote)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity)
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
            .navigationSubtitle("RECENT CALL")
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
