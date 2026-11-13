import Charts
import SwiftUI

/// LLM token usage — `TokenUsageScreen` in `token_usage_screen.dart`, as the
/// web's Token usage page: the subtitle and telemetry line, the range, one
/// summary card (period cost, calls, the Max plan estimate, telemetry
/// health), the stacked spend trend, top spenders, cost by run and recent
/// calls, refreshed every 10 s. Pull to refresh replaces the refresh button.
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
        .navigationTitle("Token usage")
        .navigationBarTitleDisplayMode(.large)
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
                        .dsClearRow(top: 8)
                }
            }
            .dsList()
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
            .dsList()
            .refreshable { await model.refreshNow() }
        }
    }
}

private struct TokenUsageCallItem: Identifiable {
    let call: RecentCall
    var id: String { call.id ?? "\(call.ts ?? 0)-\(call.model ?? "")" }
}

/// The first load: the header and the summary card as static blocks.
private struct TokenUsagePlaceholder: View {
    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    Skeleton(width: 280, height: 14)
                    Skeleton(width: 180, height: 12)
                }
                .dsClearRow(top: 0)
            }
            DSSection(rowMinHeight: 96) {
                ForEach(0..<4, id: \.self) { _ in
                    VStack(alignment: .leading, spacing: 10) {
                        Skeleton(width: 90, height: 12)
                        Skeleton(width: 120, height: 24)
                        Skeleton(width: 200, height: 12)
                    }
                }
            }
        }
        .dsList()
        .allowsHitTesting(false)
        .accessibilityLabel("Loading token usage")
    }
}

private struct TokenUsageSections: View {
    let data: TokenUsageData
    let range: String
    let onRange: (String) -> Void
    let onCall: (RecentCall) -> Void
    /// The range `data` belongs to, which names the trend's series.
    var loadedRange: String?

    static let subtitle = "Live telemetry across providers, models and strategy call sites. Recent calls merge the in-process buffer with persisted backtest usage rows"

    var body: some View {
        let telemetry = TelemetryState(data.summary?.telemetryHealth)
        let k = TokenUsageKpis(data.summary)
        let health = data.summary?.telemetryHealth
        let rangeLabel = Self.rangeLabel(range)

        // The page header: subtitle, the telemetry line and the range.
        Section {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(Self.subtitle)
                        .font(DS.Font.pageSub)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 8) {
                        StatusDot(telemetry.label, tone: Self.tone(telemetry), font: DS.Font.caption)
                        Text("·").foregroundStyle(DS.Text.faint)
                        Text(rangeLabel)
                        Text("·").foregroundStyle(DS.Text.faint)
                        Text("Auto refresh 10s")
                    }
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                    .lineLimit(1)
                }
                DSSegmentedControl("Range", selection: Binding(get: { range }, set: { onRange($0) }), options: TokenUsageModel.ranges) { $0 }
            }
            .dsClearRow(top: 0)
            if let partial = data.partialError {
                ErrorRow(message: partial)
                    .dsClearRow(top: 12)
            }
        }

        // Summary: one card, a block per figure (the web's four columns,
        // stacked on a phone).
        DSSection(rowMinHeight: 88) {
            VStack(alignment: .leading, spacing: 0) {
                (Text("Period cost ") + Text("· \(rangeLabel)").foregroundStyle(DS.Text.faint))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                Text(fmtUsdCost(k.totalCost))
                    .font(DS.Font.figureLarge)
                    .foregroundStyle(DS.Text.primary)
                    .monospacedDigit()
                    .padding(.top, 8)
                Text("\(fmtTokens(k.totalTokens)) tokens across \(k.totalCalls) calls")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                    .padding(.top, 8)
                VStack(spacing: 4) {
                    if k.topProviders.isEmpty {
                        Text("No provider spend yet")
                            .foregroundStyle(DS.Text.muted)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    } else {
                        ForEach(Array(k.topProviders.enumerated()), id: \.offset) { _, p in
                            HStack(spacing: 12) {
                                Text(p.provider)
                                    .foregroundStyle(DS.Text.secondary)
                                    .lineLimit(1)
                                Spacer(minLength: 8)
                                Text(fmtUsdCost(p.costUsd))
                                    .foregroundStyle(DS.Text.label)
                            }
                        }
                    }
                }
                .font(DS.Font.caption)
                .monospacedDigit()
                .padding(.top, 12)
            }
            .padding(.vertical, 8)

            VStack(alignment: .leading, spacing: 0) {
                Text("Period calls")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                Text("\(k.totalCalls)")
                    .font(DS.Font.figureLarge)
                    .foregroundStyle(DS.Text.primary)
                    .monospacedDigit()
                    .padding(.top, 8)
                StatGrid(columns: 2, horizontalSpacing: 16) {
                    StatCell(label: "Avg cost", value: fmtUsdCost(k.avgCost))
                    StatCell(label: "Recent rows", value: "\(data.recentCalls.count)")
                }
                .statValueStyle(.regular)
                .padding(.top, 16)
            }
            .padding(.vertical, 8)

            VStack(alignment: .leading, spacing: 0) {
                Text("Max plan estimate")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                Text(fmtUsdCost(k.maxPlanUsd))
                    .font(DS.Font.figureLarge)
                    .foregroundStyle(DS.Text.primary)
                    .monospacedDigit()
                    .padding(.top, 8)
                TokenUsageMeter(fraction: k.maxPlanFraction)
                    .padding(.top, 16)
                Text(k.maxPlanLabel)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                    .padding(.top, 8)
            }
            .padding(.vertical, 8)

            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Text("Telemetry health")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                    Spacer(minLength: 8)
                    StatusDot(telemetry.label, tone: Self.tone(telemetry), font: DS.Font.caption)
                }
                StatGrid(columns: 3, horizontalSpacing: 16) {
                    StatCell(label: "Buffer", value: health?.bufferDepth.map(String.init) ?? "—")
                    StatCell(label: "Last flush", value: health?.lastFlushAgeS.map { "\($0)s" } ?? "—")
                    StatCell(label: "Errors 24h", value: "\(health?.writeErrors24h ?? 0)")
                }
                .statValueStyle(.regular)
            }
            .padding(.vertical, 8)
        }

        TokenUsageSpendTrend(rows: data.timeseries, range: loadedRange)

        TokenUsageSpenders(title: "Top spenders by model", rows: data.topByModel, empty: "No model spend recorded yet")
        TokenUsageSpenders(title: "Top spenders by call site", rows: data.topByCallSite, empty: "No call-site spend recorded yet")

        DSSection("LLM cost by run", subtitle: "Sorted by cost. A backtest row opens that backtest; live rows are summary only", rowMinHeight: 68) {
            if data.byBacktest.isEmpty {
                Text("No LLM cost data in this range. Backtest or live runs that made LLM calls will appear here")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
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

        DSSection("Recent calls", rowMinHeight: 68) {
            if data.recentCalls.isEmpty {
                Text("No calls recorded yet")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
            } else {
                ForEach(Array(data.recentCalls.enumerated()), id: \.offset) { _, call in
                    Button { onCall(call) } label: { TokenUsageCallRow(call: call) }
                        .buttonStyle(.plain)
                }
            }
        } accessory: {
            Text("Latest 50 rows")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
        }
    }

    /// "Last 24 hours" for the range key.
    static func rangeLabel(_ range: String) -> String {
        switch range {
        case "24h": "Last 24 hours"
        case "7d": "Last 7 days"
        case "30d": "Last 30 days"
        default: range
        }
    }

    /// Green only while telemetry is actually flowing.
    static func tone(_ state: TelemetryState) -> DSStatusTone {
        switch state {
        case .awaiting: .off
        case .healthy: .live
        case .degraded: .error
        case .lagging: .warn
        }
    }
}

/// The Max plan bar (the web's `h-1` track): zinc-300 on the raised track.
private struct TokenUsageMeter: View {
    let fraction: Double

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(DS.Surface.raised)
                Capsule()
                    .fill(DS.Text.label)
                    .frame(width: geo.size.width * min(max(fraction, 0), 1))
            }
        }
        .frame(height: 4)
        .accessibilityElement()
        .accessibilityLabel("Max plan estimate")
        .accessibilityValue("\(Int((fraction * 100).rounded()))%")
    }
}

// MARK: - Spend trend

private struct TokenUsageSpendTrend: View {
    let rows: [TimeseriesRow]
    /// The range the rows were fetched for: a new one draws the bars in again.
    let range: String?

    /// The web chart's series colours: off-white, then the zinc steps.
    private static let palette: [Color] = [DS.Palette.accent] + DS.Chart.series + [DS.Text.secondary, DS.Text.muted]

    var body: some View {
        let trend = SpendTrend.points(rows)
        DSSection("Cost over time", subtitle: "Stacked by provider") {
            if trend.points.isEmpty {
                EmptyState(
                    title: "No usage in this window",
                    subtitle: "Backtest and chatbot calls will appear here after telemetry flushes"
                )
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
                .chartLegend(position: .top, alignment: .leading, spacing: 12) {
                    ChatFlowLayout(spacing: 12, runSpacing: 6) {
                        ForEach(Array(trend.providers.enumerated()), id: \.offset) { i, provider in
                            HStack(spacing: 6) {
                                RoundedRectangle(cornerRadius: 2, style: .continuous)
                                    .fill(Self.palette[i % Self.palette.count])
                                    .frame(width: 8, height: 8)
                                Text(provider)
                                    .font(DS.Font.caption)
                                    .foregroundStyle(DS.Text.secondary)
                            }
                        }
                    }
                }
                .chartXAxis {
                    AxisMarks(values: .automatic(desiredCount: 4)) { _ in
                        // Hours for a day, days for a week or a month, so
                        // the labels never run into each other.
                        AxisValueLabel(format: range == "24h" || range == nil
                            ? .dateTime.hour(.defaultDigits(amPM: .abbreviated))
                            : .dateTime.month(.abbreviated).day())
                            .font(DS.Font.micro.monospacedDigit())
                            .foregroundStyle(DS.Text.muted)
                    }
                }
                .chartYAxis {
                    AxisMarks(values: .automatic(desiredCount: 3)) { value in
                        AxisGridLine(stroke: StrokeStyle(lineWidth: DS.Line.width))
                            .foregroundStyle(DS.Chart.grid)
                        AxisValueLabel {
                            if let v = value.as(Double.self) {
                                Text(SpendTrend.axisLabel(v))
                                    .font(DS.Font.micro.monospacedDigit())
                                    .foregroundStyle(DS.Text.muted)
                            }
                        }
                    }
                }
                .frame(height: 220)
                .padding(.vertical, 12)
                .accessibilityLabel("Cost over time, stacked by provider")
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
        DSSection(title) {
            if rows.isEmpty {
                Text(empty)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
            } else {
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    EntityRow(row.key, subtitle: "\(row.calls ?? 0) calls · \(fmtTokens(row.tokens)) tokens") {
                        EntityRowValue(fmtUsdCost(row.costUsd))
                    }
                }
            }
        } accessory: {
            Text("Top 10")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
        }
    }
}

/// One run's LLM cost: the label, then kind · instance · when; cost, tokens
/// and calls, and the ok count (loss red when any call failed), trailing.
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
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.primary)
                Text("\(fmtTokens(row.tokens)) · \(row.calls ?? 0) calls")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                Text("\(row.okCalls ?? 0)/\(row.calls ?? 0) ok")
                    .font(DS.Font.caption)
                    .foregroundStyle(failed ? DS.Palette.loss : DS.Text.secondary)
            }
            .monospacedDigit()
            .lineLimit(1)
        }
        .contentShape(Rectangle())
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
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.primary)
                Text("↑\(fmtTokens(call.inputTokens)) ↓\(fmtTokens(call.outputTokens))")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                if let ts = call.ts {
                    Text(fmtRelative(ts))
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
            }
            .monospacedDigit()
            .lineLimit(1)
        }
        .contentShape(Rectangle())
    }
}

/// `_CallDetailOverlay` → a sheet shaped like the web's modal: the model
/// and "Recent call" in the header, the raw JSON in a bordered block on the
/// page colour.
private struct TokenUsageCallDetail: View {
    let call: RecentCall
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(call.model ?? call.provider ?? "Call detail")
                        .font(DS.Font.sectionTitle)
                        .foregroundStyle(DS.Text.primary)
                        .lineLimit(1)
                        .accessibilityAddTraits(.isHeader)
                    Text("Recent call")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Button { dismiss() } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.dsIcon)
                .accessibilityLabel("Close")
            }
            .padding(.horizontal, 24)
            .padding(.top, 20)
            .padding(.bottom, 16)

            ScrollView {
                Text(verbatim: (try? JSON.object(call.raw).dartEncoded(indent: "  ")) ?? "")
                    .font(DS.Font.mono)
                    .foregroundStyle(DS.Text.label)
                    .lineSpacing(6)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(16)
                    .background(DS.Surface.page, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
                    .overlay {
                        RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                            .strokeBorder(DS.Line.divider, lineWidth: DS.Line.width)
                    }
                    .padding(.horizontal, 24)
                    .padding(.bottom, 24)
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .presentationBackground(DS.Surface.card)
    }
}
