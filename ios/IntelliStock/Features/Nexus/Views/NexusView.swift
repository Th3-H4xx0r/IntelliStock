import SwiftUI

/// The Nexus knowledge graph (`/nexus`) — `NexusScreen`: build status, the
/// auto-update schedule, historical bootstrap, graph counts, stages, build
/// logs, and the Start / Stop / Rebuild / Delete-edges controls.
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

    private func content(_ model: NexusModel, _ status: NexusStatus) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                header(model, status)
                if let message = model.errorMessage {
                    ErrorRow(message: message)
                }
                autoUpdateCard(status)
                bootstrapCard(status)
                if let summary = status.graphSummary, !summary.relationshipCounts.isEmpty || !summary.nodeCounts.isEmpty {
                    graphCounts(summary)
                }
                if status.showBuilt {
                    builtSection(status)
                } else {
                    buildingSection(status)
                }
                NexusLogsPanel()
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .refreshable { await model.refreshNow() }
    }

    // MARK: Header

    private func header(_ model: NexusModel, _ status: NexusStatus) -> some View {
        let pill: (String, Color) = status.isBuilding ? ("Building", DS.Palette.info) : (status.showBuilt ? ("Ready", DS.Palette.success) : ("Idle", .secondary))
        let c = status.control
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                Text("Knowledge graph builder — S&P 500 company relationships.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Spacer(minLength: 12)
                StatusBadge(label: pill.0, color: pill.1, pulsing: status.isBuilding)
            }
            MarketsFlowLayout(spacing: 8, runSpacing: 8) {
                if !status.isBuilding {
                    actionButton(status.showBuilt && status.serviceRunning ? "Re-run" : "Start", "play_arrow", DS.Palette.success, busy: model.busy) {
                        sheet = .start
                    }
                }
                if status.serviceRunning {
                    actionButton("Stop", "stop", DS.Palette.danger, busy: model.busy) {
                        Task { await model.postControl(["running": false]) }
                    }
                }
                ghostButton("Auto-update", "schedule") { sheet = .autoUpdate }
                ghostButton("Full Rebuild", "reset_wrench") { sheet = .rebuild }
                    .disabled(model.busy)
                ghostButton("Delete Edges", "delete_sweep") { sheet = .delete }
                    .disabled(model.busy || status.serviceRunning || c.rebuildOperationActive)
            }
        }
        .padding(.top, 4)
    }

    private func actionButton(_ label: String, _ icon: String, _ color: Color, busy: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if busy { ProgressView().controlSize(.mini) } else { Image(systemName: Symbol.named(icon)) }
                Text(label)
            }
            .font(.subheadline.weight(.semibold))
        }
        .buttonStyle(.bordered)
        .tint(color)
        .disabled(busy)
    }

    private func ghostButton(_ label: String, _ icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(label, systemImage: Symbol.named(icon)).font(.subheadline)
        }
        .buttonStyle(.bordered)
        .tint(.secondary)
    }

    private func eyebrow(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.footnote.weight(.semibold))
            .foregroundStyle(.secondary)
            .accessibilityAddTraits(.isHeader)
    }

    // MARK: Auto-update

    private func autoUpdateCard(_ status: NexusStatus) -> some View {
        let c = status.control
        let range = NexusFormat.rangeLabels(c)
        return Card(padding: 16) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    eyebrow("Auto-update")
                    Text(NexusFormat.autoUpdateSummary(c)).font(.subheadline.weight(.semibold))
                    Text("Next: \(c.nextAutoUpdateAt != nil ? fmtDateTime(c.nextAutoUpdateAt) : "not scheduled")")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("Range: \(range.start) → \(range.end)").font(.caption2).foregroundStyle(.tertiary)
                    Text("13F history: \(c.phase7HistoryQuarters) quarter\(c.phase7HistoryQuarters != 1 ? "s" : "")")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
                Spacer(minLength: 0)
                Button {
                    sheet = .autoUpdate
                } label: {
                    Label("Configure", systemImage: Symbol.named("tune")).font(.footnote)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
        }
    }

    // MARK: Bootstrap

    private func bootstrapCard(_ status: NexusStatus) -> some View {
        let b = status.bootstrap
        let s = b?.status ?? "disabled"
        let pill = NexusFormat.bootstrapPill(s)
        return Card(padding: 16) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Image(systemName: Symbol.named("history")).foregroundStyle(DS.Palette.warning)
                    Text("Historical Bootstrap").font(.subheadline.weight(.semibold))
                    Spacer()
                    AppBadge(label: pill.text, color: pill.color)
                }
                Text(NexusFormat.coverageSummary(status)).font(.footnote).foregroundStyle(.secondary)
                if let b, b.status == "completed", b.completedPhases != nil {
                    LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
                        if let st = b.startDate, let end = b.coverageEnd {
                            StatTile(label: "Coverage", value: "\(st) → \(end)")
                        }
                        if let d = b.durationSec {
                            StatTile(label: "Duration", value: NexusFormat.fmtDuration(d))
                        }
                        if let done = b.completedPhases, let total = b.totalPhases {
                            StatTile(label: "Phases", value: "\(done)/\(total)")
                        }
                        if let at = b.completedAt {
                            StatTile(label: "Completed", value: fmtRelative(at))
                        }
                    }
                    .padding(.top, 2)
                }
            }
        }
    }

    // MARK: Graph counts

    private func graphCounts(_ summary: NexusGraphSummary) -> some View {
        let companies = summary.nodeCounts["companies"]
        let intervals = summary.nodeCounts["edge_intervals"]
        return Card(padding: 16) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 0) {
                        eyebrow("Graph Counts")
                        Text("Current Neo4j relationship totals.").font(.caption2).foregroundStyle(.tertiary)
                    }
                    Spacer()
                    if let companies, !companies.isNull {
                        bigCount("Companies", NexusFormat.fmtNum(companies))
                    }
                    if let intervals, !intervals.isNull {
                        bigCount("Intervals", NexusFormat.fmtNum(intervals)).padding(.leading, 16)
                    }
                }
                ForEach(Array(summary.relationshipCounts.enumerated()), id: \.offset) { _, rel in
                    HStack {
                        VStack(alignment: .leading, spacing: 0) {
                            Text(rel.label).font(.footnote)
                            Text(rel.key).font(.caption2.monospaced()).foregroundStyle(.tertiary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 0) {
                            Text(NexusFormat.fmtNum(rel.activeCount)).font(.headline.monospacedDigit())
                            if let total = rel.totalCount, total != rel.activeCount {
                                Text("\(NexusFormat.fmtNum(total)) total").font(.caption2).foregroundStyle(.secondary)
                            } else {
                                Text("active").font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .background(DS.Surface.inset, in: .rect(cornerRadius: 10, style: .continuous))
                }
            }
        }
    }

    private func bigCount(_ label: String, _ value: String) -> some View {
        VStack(alignment: .trailing, spacing: 0) {
            Text(label).font(.caption2).foregroundStyle(.tertiary)
            Text(value).font(.title2.bold().monospacedDigit())
        }
    }

    // MARK: Built

    private func builtSection(_ status: NexusStatus) -> some View {
        let stages = status.graphBuild?.stages ?? []
        let completed = stages.filter { $0.status == "completed" || $0.status == "skipped" }.count
        let totalRuntime = stages.reduce(0.0) { $0 + ($1.durationSec ?? 0) }
        return VStack(alignment: .leading, spacing: 12) {
            Card(padding: 16) {
                HStack(spacing: 12) {
                    IconTile(systemImage: Symbol.named("hub"), color: DS.Palette.success, size: 44)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Knowledge Graph is Built").font(.subheadline.weight(.semibold)).foregroundStyle(DS.Palette.success)
                        Text("\(completed) of \(stages.count) stages completed · Ready for strategy use.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                    VStack(spacing: 4) {
                        ghostButton("Rebuild", "reset_wrench") { sheet = .rebuild }
                        ghostButton("Delete Edges", "delete_sweep") { sheet = .delete }
                    }
                    .controlSize(.small)
                }
            }
            HStack(spacing: 8) {
                StatTile(label: "Stages Done", value: "\(completed)/\(stages.count)")
                if let idx = status.scraper?.index {
                    StatTile(label: "SEC Tickers", value: "\(idx)\(status.scraper?.totalTickers.map { "/\($0)" } ?? "")")
                }
                if let edges = status.scraper?.edgesCount {
                    StatTile(label: "SEC Edges", value: String(edges))
                }
                if let updated = status.graphBuild?.lastUpdated {
                    StatTile(label: "Last Updated", value: fmtRelative(updated))
                }
            }
            if !stages.isEmpty {
                Card(padding: 16) {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 0) {
                                eyebrow("Stage Summary")
                                Text("Per-stage runtime from the last completed build.").font(.caption2).foregroundStyle(.tertiary)
                            }
                            Spacer()
                            if totalRuntime > 0 {
                                VStack(alignment: .trailing, spacing: 0) {
                                    Text("Total Runtime").font(.caption2).foregroundStyle(.tertiary)
                                    Text(NexusFormat.fmtDuration(totalRuntime)).font(.headline.monospacedDigit())
                                }
                            }
                        }
                        .padding(.bottom, 6)
                        ForEach(Array(stages.enumerated()), id: \.offset) { _, stage in
                            let color = NexusFormat.stageColor(stage.status)
                            let fill: Color = switch stage.status {
                            case "completed", "skipped": DS.Palette.success.opacity(DS.tintFill)
                            case "failed": DS.Palette.danger.opacity(DS.tintFill)
                            case "stopped": DS.Palette.warning.opacity(DS.tintFill)
                            default: DS.Surface.inset
                            }
                            HStack(spacing: 10) {
                                Image(systemName: NexusFormat.stageIcon(stage.status)).foregroundStyle(color)
                                VStack(alignment: .leading, spacing: 0) {
                                    Text(stage.label).font(.footnote)
                                    let d = NexusFormat.fmtDuration(stage.durationSec)
                                    if !d.isEmpty { Text(d).font(.caption2).foregroundStyle(.tertiary) }
                                }
                                Spacer()
                            }
                            .padding(.horizontal, 12)
                            .padding(.vertical, 10)
                            .background(fill, in: .rect(cornerRadius: 10, style: .continuous))
                        }
                    }
                }
            }
        }
    }

    // MARK: Building / idle

    private func buildingSection(_ status: NexusStatus) -> some View {
        let build = status.graphBuild
        let stages = build?.stages ?? []
        let pct = build?.progressPct ?? 0
        return VStack(alignment: .leading, spacing: 12) {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(build?.currentPhaseLabel ?? (status.isBuilding ? "Building graph…" : "Graph not yet built"))
                                .font(.subheadline.weight(.semibold))
                            if let message = build?.message {
                                Text(message).font(.footnote).foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 0) {
                            Text("\(Int(pct.rounded()))%").font(.title2.bold().monospacedDigit())
                            if let eta = build?.etaFormatted {
                                Text("~\(eta) remaining").font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }
                    ProgressView(value: min(max(pct / 100, 0), 1))
                        .tint(status.isBuilding ? DS.Palette.info : Color(uiColor: .tertiaryLabel))
                }
            }
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 0) {
                    eyebrow("Build Stages").padding(.bottom, 16)
                    if stages.isEmpty {
                        VStack(spacing: 4) {
                            Image(systemName: Symbol.named("hub")).font(.largeTitle).foregroundStyle(.tertiary).padding(.bottom, 8)
                            Text("No stage data yet.").font(.footnote).foregroundStyle(.secondary)
                            Text("Start the Nexus engine to begin building.").font(.caption2).foregroundStyle(.tertiary)
                        }
                        .frame(maxWidth: .infinity)
                    } else {
                        ForEach(Array(stages.enumerated()), id: \.offset) { i, stage in
                            NexusStageRow(stage: stage, isLast: i == stages.count - 1)
                        }
                    }
                }
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
                        Text(duration).font(.caption2.monospaced()).foregroundStyle(.tertiary)
                    } else if stage.status == "running" {
                        Text("Running…").font(.caption2).italic().foregroundStyle(DS.Palette.info)
                    } else if stage.status == "pending" {
                        Text("pending").font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                if let message = stage.message {
                    Text(message).font(.caption2).foregroundStyle(.secondary)
                }
                if stage.status == "running", (stage.totalSubsteps ?? 0) > 1 {
                    HStack(spacing: 6) {
                        ProgressView(value: stage.substepFraction).tint(DS.Palette.info).frame(width: 80)
                        Text("\(stage.substepsCompleted ?? 0)/\(stage.totalSubsteps ?? 0) substeps")
                            .font(.caption2.monospaced())
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

/// The loading placeholder (`_NexusSkeleton`).
private struct NexusSkeleton: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Skeleton.line(width: 280, height: 11)
                HStack(spacing: 8) {
                    Skeleton(width: 72, height: 32, radius: 8)
                    Skeleton(width: 60, height: 32, radius: 8)
                    Skeleton(width: 90, height: 32, radius: 8)
                }
                Skeleton(height: 96, radius: DS.Radius.card)
                Skeleton(height: 64, radius: DS.Radius.card)
                Skeleton(height: 180, radius: DS.Radius.card)
                Skeleton(height: 240, radius: DS.Radius.card)
            }
            .padding(16)
        }
        .accessibilityLabel("Loading")
    }
}
