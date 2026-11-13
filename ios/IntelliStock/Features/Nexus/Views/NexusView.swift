import SwiftUI

/// The Nexus knowledge graph (`/nexus`) — `NexusScreen`, drawn as the web's
/// `NexusView.vue`: the build's status as a dot and a word, the auto-update
/// schedule, historical bootstrap, graph counts, then the built summary and
/// stage runtimes or the build's progress and stepper, and the build logs.
///
/// Start (or Stop while the service runs) is the toolbar's action; Re-run,
/// Auto-update, Full rebuild and Delete edges sit in its More menu, each
/// opening the same sheet as before.
struct NexusView: View {
    @Environment(AppServices.self) private var services
    @State private var model: NexusModel?
    @State private var sheet: NexusSheet?

    enum NexusSheet: String, Identifiable {
        case start, autoUpdate, rebuild, delete
        var id: String { rawValue }
    }

    var body: some View {
        Group {
            if let model {
                switch model.status {
                case .loading:
                    NexusSkeleton()
                case .failed:
                    List {
                        Section {
                            Card(padding: 0) {
                                EmptyState(
                                    title: "Unable to load Nexus status",
                                    subtitle: "Check that the backend is reachable",
                                    actionLabel: "Retry",
                                    onAction: { Task { await model.refreshNow() } }
                                )
                            }
                            .dsClearRow(top: 8)
                        }
                    }
                    .dsList()
                case .loaded(let status):
                    content(model, status)
                }
            } else {
                NexusSkeleton()
            }
        }
        .navigationTitle("Nexus graph")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            if let model, let status = model.statusValue {
                ToolbarItem(placement: .topBarTrailing) { controlsMenu(model, status) }
                ToolbarItem(placement: .topBarTrailing) { primaryControl(model, status) }
            }
        }
        .task {
            let m = model ?? NexusModel(repository: { [services] in services.nexusRepository })
            model = m
            await m.poll(lifecycle: services.lifecycle)
        }
        .sheet(item: $sheet) { which in
            if let model, let status = model.statusValue {
                switch which {
                case .start: NexusStartSheet(status: status, model: model)
                case .autoUpdate: NexusAutoUpdateSheet(status: status, model: model)
                case .rebuild: NexusRebuildSheet(model: model)
                case .delete: NexusDeleteSheet(status: status, model: model)
                }
            }
        }
    }

    // MARK: Toolbar

    private func startLabel(_ status: NexusStatus) -> String {
        status.showBuilt && status.serviceRunning ? "Re-run" : "Start"
    }

    /// Stop while the service runs, else Start (hidden while building, as
    /// the Dart button was); a spinner while a control request is in flight.
    /// A plain toolbar button: iOS 26 draws it on glass.
    @ViewBuilder
    private func primaryControl(_ model: NexusModel, _ status: NexusStatus) -> some View {
        if model.busy {
            ProgressView()
        } else if status.serviceRunning {
            Button("Stop") {
                Task { await model.postControl(["running": false]) }
            }
        } else if !status.isBuilding {
            Button(startLabel(status)) { sheet = .start }
        }
    }

    private func controlsMenu(_ model: NexusModel, _ status: NexusStatus) -> some View {
        let c = status.control
        return ToolbarMenu("Nexus controls") {
            Section {
                if status.serviceRunning, !status.isBuilding {
                    Button {
                        sheet = .start
                    } label: {
                        Label(startLabel(status), systemImage: Symbol.named("play_arrow"))
                    }
                    .disabled(model.busy)
                }
                Button {
                    sheet = .autoUpdate
                } label: {
                    Label("Auto-update", systemImage: Symbol.named("schedule"))
                }
                Button {
                    sheet = .rebuild
                } label: {
                    Label("Full rebuild", systemImage: Symbol.named("reset_wrench"))
                }
                .disabled(model.busy)
            }
            Section {
                Button(role: .destructive) {
                    sheet = .delete
                } label: {
                    Label("Delete edges", systemImage: Symbol.named("delete_sweep"))
                }
                .disabled(model.busy || status.serviceRunning || c.rebuildOperationActive)
            }
        }
    }

    // MARK: Content

    private func content(_ model: NexusModel, _ status: NexusStatus) -> some View {
        List {
            Section {
                header(status)
                    .dsClearRow(top: 4)
            }
            if let message = model.errorMessage {
                Section {
                    ErrorRow(message: message)
                        .dsClearRow()
                }
            }
            autoUpdateSection(status)
            bootstrapSection(status)
            if let summary = status.graphSummary, !summary.relationshipCounts.isEmpty || !summary.nodeCounts.isEmpty {
                graphCounts(summary, building: status.isBuilding)
            }
            if status.showBuilt {
                builtSections(status)
            } else {
                buildingSections(status)
            }
            Section {
                NexusLogsPanel()
                    .dsClearRow(bottom: 24)
            }
        }
        .dsList()
        .refreshable { await model.refreshNow() }
    }

    // MARK: Header

    /// The page's one-line purpose and the build's state: "● Building…",
    /// "● Graph ready" or "● Idle", then the bootstrap's.
    private func header(_ status: NexusStatus) -> some View {
        let build: (String, DSStatusTone) = status.isBuilding
            ? ("Building…", .live)
            : (status.showBuilt ? ("Graph ready", .neutral) : ("Idle", .off))
        let pill = NexusFormat.bootstrapPill(status.bootstrap?.status ?? "disabled")
        return VStack(alignment: .leading, spacing: 8) {
            Text("Knowledge graph builder — S&P 500 company relationships across 10 data sources")
                .font(DS.Font.pageSub)
                .foregroundStyle(DS.Text.muted)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                StatusDot(build.0, tone: build.1, font: DS.Font.caption)
                Text("·")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.faint)
                    .accessibilityHidden(true)
                StatusDot(pill.text, tone: pill.tone, font: DS.Font.caption)
            }
        }
    }

    // MARK: Auto-update

    /// The schedule (the web's card): the summary and next run, the refresh
    /// range, the 13F history and the bootstrap's coverage; Configure in the
    /// header.
    private func autoUpdateSection(_ status: NexusStatus) -> some View {
        let c = status.control
        let range = NexusFormat.rangeLabels(c)
        return DSSection("Auto-update") {
            StatGrid(columns: 2, verticalSpacing: 16) {
                OpsStat(
                    label: "Schedule",
                    value: NexusFormat.autoUpdateSummary(c),
                    detail: "Next run \(c.nextAutoUpdateAt != nil ? fmtDateTime(c.nextAutoUpdateAt) : "not scheduled")"
                )
                OpsStat(label: "Refresh range", value: range.start, detail: "to \(range.end)")
                OpsStat(label: "13F history", value: "\(c.phase7HistoryQuarters) quarter\(c.phase7HistoryQuarters != 1 ? "s" : "")")
                OpsStat(label: "Historical bootstrap", value: NexusFormat.coverageSummary(status))
            }
            .padding(.vertical, 8)
        } accessory: {
            Button("Configure") { sheet = .autoUpdate }
                .buttonStyle(.dsSecondary)
        }
    }

    // MARK: Bootstrap

    private func bootstrapSection(_ status: NexusStatus) -> some View {
        let b = status.bootstrap
        let pill = NexusFormat.bootstrapPill(b?.status ?? "disabled")
        let finished = b?.status == "completed" && b?.completedPhases != nil
        let start = b?.startDate ?? status.control.historicalStartDate ?? "unknown"
        let end = b?.coverageEnd ?? status.control.historicalCoverageEnd ?? "today"
        let subtitle: String? = b?.status == "completed" ? "Historical bootstrap complete for \(start) → \(end)" : nil
        return DSSection("Historical bootstrap", subtitle: subtitle) {
            StatGrid(columns: 2, verticalSpacing: 16) {
                if finished, let b {
                    OpsStat(label: "Coverage", value: coverage(b, status))
                    OpsStat(label: "Duration", value: b.durationSec.map { NexusFormat.fmtDuration($0) } ?? "—")
                    OpsStat(label: "Completed", value: fmtDateTime(b.completedAt))
                    OpsStat(label: "Temporal phases", value: "\(b.completedPhases ?? 0)/\(b.totalPhases ?? 0)")
                } else {
                    OpsStat(label: "Coverage target", value: b.map { coverage($0, status) } ?? NexusFormat.coverageSummary(status))
                    OpsStat(
                        label: "Temporal phases",
                        value: b?.totalPhases.map { "\(b?.completedPhases ?? 0)/\($0) complete" } ?? "—"
                    )
                    OpsStat(label: "Started", value: fmtDateTime(b?.startedAt))
                }
            }
            .padding(.vertical, 8)
        } accessory: {
            StatusDot(pill.text, tone: pill.tone, font: DS.Font.caption)
        }
    }

    private func coverage(_ b: NexusBootstrap, _ status: NexusStatus) -> String {
        if let s = b.startDate, let e = b.coverageEnd { return "\(s) → \(e)" }
        return NexusFormat.coverageSummary(status)
    }

    // MARK: Graph counts

    private func graphCounts(_ summary: NexusGraphSummary, building: Bool) -> some View {
        let companies = summary.nodeCounts["companies"].flatMap { $0.isNull ? nil : $0 }
        let intervals = summary.nodeCounts["edge_intervals"].flatMap { $0.isNull ? nil : $0 }
        return DSSection(
            "Graph counts",
            subtitle: building
                ? "Current Neo4j relationship totals while the graph build is running"
                : "Current Neo4j relationship totals for the built graph"
        ) {
            if companies != nil || intervals != nil {
                HStack(alignment: .top, spacing: 48) {
                    if let companies {
                        OpsStat(label: "Companies", value: NexusFormat.fmtNum(companies), valueFont: DS.Font.figureLarge)
                            .fixedSize()
                    }
                    if let intervals {
                        OpsStat(label: "Intervals", value: NexusFormat.fmtNum(intervals), valueFont: DS.Font.figureLarge)
                            .fixedSize()
                    }
                    Spacer(minLength: 0)
                }
                .padding(.vertical, 8)
            }
            ForEach(Array(summary.relationshipCounts.enumerated()), id: \.offset) { _, rel in
                EntityRow(rel.label, subtitle: rel.key) {
                    EntityRowValue(
                        NexusFormat.fmtNum(rel.activeCount),
                        detail: rel.totalCount.map { $0 != rel.activeCount ? "\(NexusFormat.fmtNum($0)) total" : "All active" } ?? "All active"
                    )
                }
            }
        }
    }

    // MARK: Built

    @ViewBuilder
    private func builtSections(_ status: NexusStatus) -> some View {
        let stages = status.graphBuild?.stages ?? []
        let completed = stages.filter { $0.status == "completed" || $0.status == "skipped" }.count
        let totalRuntime = stages.reduce(0.0) { $0 + ($1.durationSec ?? 0) }
        DSSection {
            VStack(alignment: .leading, spacing: 2) {
                StatusDot("Knowledge graph is built", tone: .neutral)
                Text("\(completed) of \(stages.count) stages completed · ready for strategy use")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                    .padding(.leading, 18)
            }
            .padding(.vertical, 4)
            StatGrid(columns: 2, verticalSpacing: 16) {
                StatCell(label: "Stages done") {
                    figure("\(completed)", suffix: "/\(stages.count)")
                }
                StatCell(label: "SEC tickers") {
                    figure(status.scraper?.index.map(String.init) ?? "—", suffix: status.scraper?.totalTickers.map { "/\($0)" })
                }
                StatCell(label: "SEC edges") {
                    figure(status.scraper?.edgesCount.map(String.init) ?? "—", suffix: nil)
                }
                StatCell(label: "Last updated") {
                    Text(fmtDateTime(status.graphBuild?.lastUpdated))
                        .font(DS.Font.body)
                        .padding(.top, 4)
                }
            }
            .padding(.vertical, 8)
        }
        if !stages.isEmpty {
            DSSection("Stage summary", subtitle: "Per-stage runtime from the last completed build", rowMinHeight: 48) {
                ForEach(Array(stages.enumerated()), id: \.offset) { _, stage in
                    let d = NexusFormat.fmtDuration(stage.durationSec)
                    HStack(spacing: 12) {
                        Image(systemName: NexusFormat.stageIcon(stage.status))
                            .font(.system(size: 16))
                            .foregroundStyle(NexusFormat.stageColor(stage.status))
                            .accessibilityHidden(true)
                        Text(stage.label)
                            .font(DS.Font.body)
                            .foregroundStyle(stage.status == "failed" ? DS.Palette.loss : DS.Text.label)
                            .lineLimit(1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        if !d.isEmpty {
                            Text(d)
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                                .monospacedDigit()
                        }
                    }
                    .accessibilityElement(children: .combine)
                }
            } accessory: {
                VStack(alignment: .trailing, spacing: 4) {
                    Text("Total runtime")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                    Text(totalRuntime > 0 ? NexusFormat.fmtDuration(totalRuntime) : "—")
                        .font(DS.Font.figure)
                        .foregroundStyle(DS.Text.primary)
                        .monospacedDigit()
                }
                .accessibilityElement(children: .combine)
            }
        }
    }

    /// A stat figure with a quieter suffix ("12/14", the "/14" in 14 pt
    /// zinc-500), as the web sets them.
    private func figure(_ value: String, suffix: String?) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 4) {
            Text(value)
                .font(DS.Font.figureLarge)
                .foregroundStyle(DS.Text.primary)
            if let suffix {
                Text(suffix)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
            }
        }
        .monospacedDigit()
        .lineLimit(1)
    }

    // MARK: Building / idle

    @ViewBuilder
    private func buildingSections(_ status: NexusStatus) -> some View {
        let build = status.graphBuild
        let stages = build?.stages ?? []
        let pct = build?.progressPct ?? 0
        DSSection("Build progress") {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .top, spacing: 16) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(build?.currentPhaseLabel ?? (status.isBuilding ? "Building graph…" : "Graph not yet built"))
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.primary)
                        if let message = build?.message {
                            Text(message)
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    VStack(alignment: .trailing, spacing: 4) {
                        HStack(alignment: .firstTextBaseline, spacing: 1) {
                            Text("\(Int(pct.rounded()))")
                                .font(DS.Font.figureLarge)
                                .foregroundStyle(DS.Text.primary)
                            Text("%")
                                .font(DS.Font.base)
                                .foregroundStyle(DS.Text.muted)
                        }
                        .monospacedDigit()
                        if let eta = build?.etaFormatted {
                            Text("~\(eta) remaining")
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                                .monospacedDigit()
                        }
                    }
                }
                OpsProgressBar(fraction: pct / 100, color: status.isBuilding ? DS.Text.soft : DS.Text.faint)
            }
            .padding(.vertical, 8)
            .accessibilityElement(children: .combine)
            .accessibilityValue("\(Int(pct.rounded())) percent")
            if let scraper = status.scraper {
                scraperRow(scraper)
            }
        }
        DSSection("Build stages") {
            if stages.isEmpty {
                EmptyState(title: "No stage data yet", subtitle: "Start the Nexus engine to begin building")
            } else {
                // One row holds the whole stepper so its connecting line
                // runs unbroken between the stages.
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(stages.enumerated()), id: \.offset) { i, stage in
                        NexusStageRow(stage: stage, isLast: i == stages.count - 1)
                    }
                }
                .padding(.vertical, 12)
            }
        }
    }

    /// The SEC EDGAR scraper under the build's progress (the web's).
    private func scraperRow(_ scraper: NexusScraper) -> some View {
        let s = scraper.status ?? "idle"
        let tone: DSStatusTone = s == "running" ? .live : (s == "completed" ? .neutral : .off)
        var parts: [String] = []
        if let index = scraper.index {
            parts.append("Ticker \(index)\(scraper.totalTickers.map { " / \($0)" } ?? "")")
        }
        if let edges = scraper.edgesCount { parts.append("Edges \(edges)") }
        if let eta = scraper.etaFormatted { parts.append("~\(eta) remaining") }
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 12) {
                Text("SEC EDGAR scraper")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.primary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                StatusDot(s.dsSentenceCased, tone: tone, font: DS.Font.caption)
            }
            if !parts.isEmpty {
                Text(parts.joined(separator: " · "))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
            }
            OpsProgressBar(fraction: scraper.progressPct / 100, color: DS.Text.secondary)
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .combine)
    }
}

/// One stepper row of the build stages (the web's): a glyph — a live dot
/// while it runs — on a hairline that leads to the next stage, the label
/// and message, a substep bar while a multi-step stage runs, and the
/// duration or state on the right.
private struct NexusStageRow: View {
    let stage: NexusStage
    let isLast: Bool

    private var lineColor: Color {
        switch stage.status {
        case "completed", "skipped": DS.Text.faint
        case "running", "stopped": DS.Chart.series[2]
        default: DS.Line.divider
        }
    }

    private var labelColor: Color {
        switch stage.status.lowercased() {
        case "running": DS.Text.primary
        case "stopped", "completed", "skipped": DS.Text.label
        case "failed": DS.Palette.loss
        default: DS.Text.muted
        }
    }

    var body: some View {
        let running = stage.status.lowercased() == "running"
        let duration = NexusFormat.fmtDuration(stage.durationSec)
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(stage.label)
                    .font(DS.Font.body)
                    .foregroundStyle(labelColor)
                if let message = stage.message {
                    Text(message)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if running, (stage.totalSubsteps ?? 0) > 1 {
                    HStack(spacing: 12) {
                        OpsProgressBar(fraction: stage.substepFraction, color: DS.Text.label)
                            .frame(width: 96)
                        Text("\(stage.substepsCompleted ?? 0)/\(stage.totalSubsteps ?? 0) substeps")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.secondary)
                            .monospacedDigit()
                    }
                    .padding(.top, 6)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            trailing(duration)
        }
        .padding(.leading, 32)
        .padding(.bottom, isLast ? 0 : 20)
        .background(alignment: .topLeading) {
            if !isLast {
                Rectangle()
                    .fill(lineColor)
                    .frame(width: DS.Line.width)
                    .frame(maxHeight: .infinity)
                    .padding(.top, 24)
                    .padding(.bottom, 4)
                    .padding(.leading, 9.5)
            }
        }
        .overlay(alignment: .topLeading) {
            Group {
                if running {
                    DSStatusDot(.live)
                } else {
                    Image(systemName: NexusFormat.stageIcon(stage.status))
                        .font(.system(size: 17))
                        .foregroundStyle(NexusFormat.stageColor(stage.status))
                }
            }
            .frame(width: 20, height: 20)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private func trailing(_ duration: String) -> some View {
        if !duration.isEmpty {
            Text(duration)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
        } else if stage.status == "running" {
            Text("Running")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.label)
        } else if stage.status == "stopped" {
            StatusDot("Stopped", tone: .warn, font: DS.Font.caption)
        } else if stage.status == "pending" {
            Text("Pending")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.faint)
        }
    }
}

/// The first load (the web's): a header line and blocks where the cards
/// will be.
private struct NexusSkeleton: View {
    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    Skeleton(width: 280, height: 14)
                    Skeleton(width: 160, height: 12)
                }
                .dsClearRow(top: 4)
            }
            Section {
                VStack(spacing: 12) {
                    Skeleton(height: 80, radius: DS.Radius.card)
                    ForEach(0..<4, id: \.self) { _ in
                        Skeleton(height: 56, radius: DS.Radius.card)
                    }
                }
                .dsClearRow()
            }
        }
        .dsList()
        .disabled(true)
        .accessibilityLabel("Loading")
    }
}
