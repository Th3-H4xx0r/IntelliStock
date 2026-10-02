import SwiftUI

/// The Nexus knowledge graph (`/nexus`) — `NexusScreen`: build status, the
/// auto-update schedule, historical bootstrap, graph counts, stages, build
/// logs, and the Start / Stop / Rebuild / Delete-edges controls.
///
/// An inset-grouped list under the inline title. Start (or Stop while the
/// service runs) is the toolbar's prominent action; Re-run, Auto-update,
/// Full Rebuild and Delete Edges sit in its More menu, each opening the same
/// sheet as before.
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
                    ProgressView()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                case .failed:
                    ContentUnavailableView {
                        Label("Unable to load Nexus status", systemImage: Symbol.named("hub"))
                    } description: {
                        Text("Check that the backend is reachable.")
                    } actions: {
                        Button("Retry") { Task { await model.refreshNow() } }
                    }
                case .loaded(let status):
                    content(model, status)
                }
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.canvas)
        .navigationTitle("Nexus Graph")
        .navigationBarTitleDisplayMode(.inline)
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
    @ViewBuilder
    private func primaryControl(_ model: NexusModel, _ status: NexusStatus) -> some View {
        if model.busy {
            ProgressView()
        } else if status.serviceRunning {
            Button("Stop") {
                Task { await model.postControl(["running": false]) }
            }
            .dsProminentButton()
        } else if !status.isBuilding {
            Button(startLabel(status)) { sheet = .start }
                .dsProminentButton()
        }
    }

    private func controlsMenu(_ model: NexusModel, _ status: NexusStatus) -> some View {
        let c = status.control
        return ToolbarMenu("Nexus Controls") {
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
                    Label("Full Rebuild", systemImage: Symbol.named("reset_wrench"))
                }
                .disabled(model.busy)
            }
            Section {
                Button(role: .destructive) {
                    sheet = .delete
                } label: {
                    Label("Delete Edges", systemImage: Symbol.named("delete_sweep"))
                }
                .disabled(model.busy || status.serviceRunning || c.rebuildOperationActive)
            }
        }
    }

    // MARK: Content

    private func content(_ model: NexusModel, _ status: NexusStatus) -> some View {
        List {
            statusSection(status)
            if let message = model.errorMessage {
                Section {
                    ErrorRow(message: message)
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                }
            }
            autoUpdateSection(status)
            bootstrapSection(status)
            if let summary = status.graphSummary, !summary.relationshipCounts.isEmpty || !summary.nodeCounts.isEmpty {
                graphCounts(summary)
            }
            if status.showBuilt {
                builtSections(status)
            } else {
                buildingSections(status)
            }
            Section("Build logs") {
                NexusLogsPanel()
                    .listRowInsets(EdgeInsets())
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await model.refreshNow() }
    }

    // MARK: Status

    private func statusSection(_ status: NexusStatus) -> some View {
        let pill: (String, Color) = status.isBuilding ? ("Building", DS.Palette.info) : (status.showBuilt ? ("Ready", DS.Palette.success) : ("Idle", .secondary))
        return DSSection(footer: "Knowledge graph builder — S&P 500 company relationships.") {
            LabeledContent("Status") {
                StatusDot(pill.0, color: pill.1, pulsing: status.isBuilding)
            }
        }
    }

    // MARK: Auto-update

    private func autoUpdateSection(_ status: NexusStatus) -> some View {
        let c = status.control
        let range = NexusFormat.rangeLabels(c)
        return Section("Auto-update") {
            Text(NexusFormat.autoUpdateSummary(c))
            LabeledContent("Next", value: c.nextAutoUpdateAt != nil ? fmtDateTime(c.nextAutoUpdateAt) : "not scheduled")
            LabeledContent("Range", value: "\(range.start) → \(range.end)")
            LabeledContent("13F history", value: "\(c.phase7HistoryQuarters) quarter\(c.phase7HistoryQuarters != 1 ? "s" : "")")
            InlineActionRow("Configure", systemImage: Symbol.named("tune")) { sheet = .autoUpdate }
        }
    }

    // MARK: Bootstrap

    private func bootstrapSection(_ status: NexusStatus) -> some View {
        let b = status.bootstrap
        let s = b?.status ?? "disabled"
        let pill = NexusFormat.bootstrapPill(s)
        return Section {
            Text(NexusFormat.coverageSummary(status))
                .foregroundStyle(.secondary)
            if let b, b.status == "completed", b.completedPhases != nil {
                StatGrid(columns: 2) {
                    if let st = b.startDate, let end = b.coverageEnd {
                        StatCell(label: "Coverage", value: "\(st) → \(end)")
                    }
                    if let d = b.durationSec {
                        StatCell(label: "Duration", value: NexusFormat.fmtDuration(d))
                    }
                    if let done = b.completedPhases, let total = b.totalPhases {
                        StatCell(label: "Phases", value: "\(done)/\(total)")
                    }
                    if let at = b.completedAt {
                        StatCell(label: "Completed", value: fmtRelative(at))
                    }
                }
                .padding(.vertical, 4)
            }
        } header: {
            DSSectionHeader("Historical Bootstrap") {
                AppBadge(label: pill.text, color: pill.color)
            }
        }
    }

    // MARK: Graph counts

    private func graphCounts(_ summary: NexusGraphSummary) -> some View {
        let companies = summary.nodeCounts["companies"]
        let intervals = summary.nodeCounts["edge_intervals"]
        return Section {
            if let companies, !companies.isNull {
                LabeledContent("Companies") {
                    Text(NexusFormat.fmtNum(companies)).font(.headline.monospacedDigit()).foregroundStyle(.primary)
                }
            }
            if let intervals, !intervals.isNull {
                LabeledContent("Intervals") {
                    Text(NexusFormat.fmtNum(intervals)).font(.headline.monospacedDigit()).foregroundStyle(.primary)
                }
            }
            ForEach(Array(summary.relationshipCounts.enumerated()), id: \.offset) { _, rel in
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(rel.label)
                        Text(rel.key).font(.caption).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    if let total = rel.totalCount, total != rel.activeCount {
                        EntityRowValue(NexusFormat.fmtNum(rel.activeCount), detail: "\(NexusFormat.fmtNum(total)) total")
                    } else {
                        EntityRowValue(NexusFormat.fmtNum(rel.activeCount), detail: "active")
                    }
                }
                .accessibilityElement(children: .combine)
            }
        } header: {
            Text("Graph Counts")
        } footer: {
            Text("Current Neo4j relationship totals.")
        }
    }

    // MARK: Built

    @ViewBuilder
    private func builtSections(_ status: NexusStatus) -> some View {
        let stages = status.graphBuild?.stages ?? []
        let completed = stages.filter { $0.status == "completed" || $0.status == "skipped" }.count
        let totalRuntime = stages.reduce(0.0) { $0 + ($1.durationSec ?? 0) }
        Section {
            Label {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Knowledge Graph is Built").font(.headline)
                    Text("\(completed) of \(stages.count) stages completed · Ready for strategy use.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            } icon: {
                Image(systemName: Symbol.named("hub")).foregroundStyle(DS.Palette.success)
            }
            StatGrid(columns: 2) {
                StatCell(label: "Stages Done", value: "\(completed)/\(stages.count)")
                if let idx = status.scraper?.index {
                    StatCell(label: "SEC Tickers", value: "\(idx)\(status.scraper?.totalTickers.map { "/\($0)" } ?? "")")
                }
                if let edges = status.scraper?.edgesCount {
                    StatCell(label: "SEC Edges", value: String(edges))
                }
                if let updated = status.graphBuild?.lastUpdated {
                    StatCell(label: "Last Updated", value: fmtRelative(updated))
                }
            }
            .padding(.vertical, 4)
        }
        if !stages.isEmpty {
            Section {
                if totalRuntime > 0 {
                    LabeledContent("Total Runtime") {
                        Text(NexusFormat.fmtDuration(totalRuntime)).monospacedDigit()
                    }
                }
                ForEach(Array(stages.enumerated()), id: \.offset) { _, stage in
                    let d = NexusFormat.fmtDuration(stage.durationSec)
                    Label {
                        LabeledContent(stage.label) {
                            if !d.isEmpty { Text(d).monospacedDigit() }
                        }
                    } icon: {
                        Image(systemName: NexusFormat.stageIcon(stage.status))
                            .foregroundStyle(NexusFormat.stageColor(stage.status))
                    }
                }
            } header: {
                Text("Stage Summary")
            } footer: {
                Text("Per-stage runtime from the last completed build.")
            }
        }
    }

    // MARK: Building / idle

    @ViewBuilder
    private func buildingSections(_ status: NexusStatus) -> some View {
        let build = status.graphBuild
        let stages = build?.stages ?? []
        let pct = build?.progressPct ?? 0
        Section {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(build?.currentPhaseLabel ?? (status.isBuilding ? "Building graph…" : "Graph not yet built"))
                            .font(.headline)
                        if let message = build?.message {
                            Text(message).font(.subheadline).foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 0) {
                        Text("\(Int(pct.rounded()))%").font(.title2.bold().monospacedDigit())
                        if let eta = build?.etaFormatted {
                            Text("~\(eta) remaining").font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                }
                ProgressView(value: min(max(pct / 100, 0), 1))
                    .tint(status.isBuilding ? DS.Palette.info : Color(uiColor: .tertiaryLabel))
            }
            .padding(.vertical, 4)
        }
        Section("Build Stages") {
            if stages.isEmpty {
                VStack(spacing: 4) {
                    Text("No stage data yet.").foregroundStyle(.secondary)
                    Text("Start the Nexus engine to begin building.").font(.footnote).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
            } else {
                // One row holds the whole stepper so its connecting line
                // runs unbroken between the stages.
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(stages.enumerated()), id: \.offset) { i, stage in
                        NexusStageRow(stage: stage, isLast: i == stages.count - 1)
                    }
                }
                .padding(.vertical, 6)
            }
        }
    }
}

/// One stepper row of the build stages (`_BuildStageRow`).
private struct NexusStageRow: View {
    let stage: NexusStage
    let isLast: Bool

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var lineColor: Color {
        switch stage.status {
        case "completed", "skipped": DS.Palette.success.opacity(0.4)
        case "running": DS.Palette.info.opacity(0.5)
        case "stopped": DS.Palette.warning.opacity(0.4)
        default: Color(uiColor: .separator)
        }
    }

    private var labelColor: Color {
        switch stage.status.lowercased() {
        case "running": DS.Palette.info
        case "stopped": DS.Palette.warning
        case "completed", "skipped": .primary
        case "failed": DS.Palette.danger
        default: .secondary
        }
    }

    var body: some View {
        let color = NexusFormat.stageColor(stage.status)
        let running = stage.status.lowercased() == "running"
        let duration = NexusFormat.fmtDuration(stage.durationSec)
        HStack(alignment: .top, spacing: 12) {
            VStack(spacing: 0) {
                Group {
                    if running {
                        Image(systemName: "arrow.trianglehead.2.clockwise")
                            .symbolEffect(.rotate, options: .repeat(.continuous), isActive: !reduceMotion)
                    } else {
                        Image(systemName: NexusFormat.stageIcon(stage.status))
                    }
                }
                .font(.footnote)
                .foregroundStyle(color)
                .frame(width: 32, height: 32)
                .background(color.opacity(DS.tintFill), in: Circle())
                if !isLast {
                    Rectangle().fill(lineColor).frame(width: 1, height: 40)
                }
            }
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(stage.label).font(.subheadline.weight(.semibold)).foregroundStyle(labelColor)
                    Spacer()
                    if !duration.isEmpty {
                        Text(duration).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    } else if stage.status == "running" {
                        Text("Running…").font(.caption).italic().foregroundStyle(DS.Palette.info)
                    } else if stage.status == "pending" {
                        Text("pending").font(.caption).foregroundStyle(.secondary)
                    }
                }
                if let message = stage.message {
                    Text(message).font(.caption).foregroundStyle(.secondary)
                }
                if stage.status == "running", (stage.totalSubsteps ?? 0) > 1 {
                    HStack(spacing: 6) {
                        ProgressView(value: stage.substepFraction).tint(DS.Palette.info).frame(width: 80)
                        Text("\(stage.substepsCompleted ?? 0)/\(stage.totalSubsteps ?? 0) substeps")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(DS.Palette.info)
                    }
                    .padding(.top, 2)
                }
            }
            .padding(.bottom, isLast ? 0 : 12)
        }
        .accessibilityElement(children: .combine)
    }
}
