import SwiftUI

/// The AI backtest agent's runs — `AgentRunsScreen` in
/// `agent_runs_screen.dart`: status, controls (start / pause / unpause with a
/// scheduled resume / stop), runs grouped by cycle with their stage steppers,
/// and pagination. Polls every 5 s. Native form: an inset-grouped list; the
/// controls are a section of button rows, each cycle a section of runs, and
/// the page size a toolbar menu. Pull to refresh replaces the refresh button.
struct AgentRunsView: View {
    @Environment(AppServices.self) private var services
    @State private var model: AgentRunsModel?
    @State private var startOpen = false
    @State private var resumeOpen = false

    var body: some View {
        Group {
            switch model?.state ?? .loading {
            case .loading:
                AgentRunsList(model: nil, state: AgentRunsState.placeholder, onStart: {}, onResume: {})
                    .redacted(reason: .placeholder)
                    .allowsHitTesting(false)
            case .failed(let error):
                List {
                    Section {
                        ErrorRow(message: (error as? ApiError)?.message ?? error.localizedDescription) {
                            Task { await model?.refreshNow() }
                        }
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                    }
                }
                .listStyle(.insetGrouped)
                .refreshable { await model?.refreshNow() }
            case .loaded(let state):
                AgentRunsList(model: model, state: state, onStart: { startOpen = true }, onResume: { resumeOpen = true })
            }
        }
        .navigationTitle("Agent Runs")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $startOpen) {
            AgentStartSheet { request in
                Task { await model?.startAgent(specialRequest: request) }
            }
        }
        .sheet(isPresented: $resumeOpen) {
            AgentResumeSheet { minutes in
                Task { await model?.scheduleResume(minutes) }
            }
        }
        .onAppear {
            if model == nil {
                let services = services
                model = AgentRunsModel(repository: { services.agentRepository })
            }
        }
        .task(id: model == nil) {
            await model?.poll(lifecycle: services.lifecycle)
        }
    }
}

private extension AgentRunsState {
    static var placeholder: AgentRunsState {
        var s = AgentRunsState()
        s.runs = (0..<3).map { i in
            AgentRun(json: [
                "id": .string("p\(i)"), "status": "passed", "name": "Strategy attempt", "cycle_id": "c",
                "stages": [["label": "Backtest", "status": "passed"], ["label": "Evaluate", "status": "passed"]],
            ])
        }
        return s
    }
}

private struct AgentRunsList: View {
    let model: AgentRunsModel?
    let state: AgentRunsState
    let onStart: () -> Void
    let onResume: () -> Void

    var body: some View {
        let cycles = AgentRunCycle.group(state.runs)
        List {
            AgentRunsControls(model: model, state: state, onStart: onStart, onResume: onResume)

            if let error = state.errorMessage {
                Section {
                    ErrorRow(message: error)
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                }
            }

            if state.busy && state.runs.isEmpty {
                Section {
                    ForEach(0..<3, id: \.self) { _ in
                        AgentRunRow(run: AgentRunsState.placeholder.runs[0], control: state.control)
                    }
                }
                .redacted(reason: .placeholder)
            } else if state.runs.isEmpty {
                Section {
                    EmptyState(
                        systemImage: Symbol.named("smart_toy"),
                        title: "No agent runs yet",
                        subtitle: "Start the AI Backtest Agent to see strategy attempts here."
                    )
                    .listRowBackground(Color.clear)
                }
            } else {
                // One section per cycle, headed by its start time.
                ForEach(cycles) { cycle in
                    Section(cycle.startedAt.map { fmtDateTime($0) } ?? "—") {
                        ForEach(cycle.runs) { run in
                            AgentRunRow(run: run, control: state.control)
                            if AgentRunRow.showsMarkStopped(run, state.control) {
                                InlineActionRow("Mark Stopped", systemImage: Symbol.named("close"), role: .destructive) {
                                    Task { await model?.forceStop(run.id) }
                                }
                                .tint(DS.Palette.danger) // the glyph red too, not just the title
                                .disabled(state.busy)
                            }
                        }
                    }
                }
            }

            if state.totalPages > 1 {
                AgentRunsPager(state: state) { page in
                    Task { await model?.goToPage(page) }
                }
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await model?.refreshNow() }
        .toolbar {
            if let model {
                ToolbarItem(placement: .topBarTrailing) {
                    ToolbarMenu("Per Page") {
                        Picker("Per page", selection: Binding(
                            get: { state.perPage },
                            set: { value in Task { await model.setPerPage(value) } }
                        )) {
                            ForEach(AgentRunsModel.perPageOptions, id: \.self) { Text("\($0)/page").tag($0) }
                        }
                    }
                }
            }
        }
    }
}

// MARK: - Controls

/// The agent's state and its controls — `_Header` and `_Controls`: a status
/// row, then one button row per action that applies (start, pause, unpause
/// or the scheduled-resume countdown, stop).
private struct AgentRunsControls: View {
    let model: AgentRunsModel?
    let state: AgentRunsState
    let onStart: () -> Void
    let onResume: () -> Void

    var body: some View {
        let control = state.control
        let hasCountdown = state.scheduledResumeAt != nil
        let (label, color): (String, Color) = control.isRunning
            ? ("Running", DS.Palette.success)
            : (control.isPaused ? ("Paused", DS.Palette.warning) : ("Stopped", .secondary))
        Section {
            LabeledContent("AI Backtest Agent") {
                StatusDot(label, color: color, pulsing: control.isRunning)
            }
            if control.isStopped {
                InlineActionRow("Start", systemImage: Symbol.named("play_arrow"), isBusy: state.busy, action: onStart)
            }
            if control.isRunning {
                InlineActionRow("Pause", systemImage: Symbol.named("pause"), isBusy: state.busy) {
                    Task { await model?.pauseAgent() }
                }
            }
            if control.isPaused && !hasCountdown {
                InlineActionRow("Unpause", systemImage: Symbol.named("play_arrow"), isBusy: state.busy, action: onResume)
            }
            if hasCountdown, let model {
                AgentCountdownRing(model: model, state: state)
            }
            if !control.isStopped {
                InlineActionRow("Stop", systemImage: Symbol.named("stop"), role: .destructive, isBusy: state.busy) {
                    Task { await model?.stopAgent() }
                }
                .tint(DS.Palette.danger) // the glyph red too, not just the title
            }
        } footer: {
            Text("Strategy attempts by the AI Backtest Agent.")
        }
    }
}

/// The scheduled-resume ring — `_CountdownRing`: depletes as time passes;
/// the stop square cancels.
private struct AgentCountdownRing: View {
    let model: AgentRunsModel
    let state: AgentRunsState

    var body: some View {
        _ = model.tick
        let fraction = state.countdownFraction()
        let secs = state.countdownSecsRemaining()
        return HStack(spacing: 6) {
            ZStack {
                Circle()
                    .stroke(Color(uiColor: .systemFill), lineWidth: 3.5)
                Circle()
                    .trim(from: 0, to: 1 - fraction)
                    .stroke(DS.Palette.info, style: StrokeStyle(lineWidth: 3.5, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                Button {
                    model.cancelCountdown()
                } label: {
                    Image(systemName: Symbol.named("stop"))
                        .font(.caption2)
                        .foregroundStyle(DS.Palette.danger)
                        .frame(width: 22, height: 22)
                        .background(DS.Palette.danger.opacity(DS.tintFill), in: .rect(cornerRadius: 4))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Cancel scheduled resume")
            }
            .frame(width: 36, height: 36)
            .frame(width: 44, height: 44)
            VStack(alignment: .leading, spacing: 0) {
                Text(agentCountdownLabel(secs))
                    .font(.body.monospacedDigit().weight(.semibold))
                    .foregroundStyle(DS.Palette.info)
                    .contentTransition(.numericText(countsDown: true))
                Text("resuming").font(.footnote).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
    }
}

// MARK: - Runs

/// `_statusColor` for runs and stages.
private func agentStatusColor(_ status: String) -> Color {
    switch status.lowercased() {
    case "running": DS.Palette.info
    case "passed": DS.Palette.success
    case "failed", "error": DS.Palette.danger
    case "tossed": DS.Palette.warning
    default: Color(uiColor: .tertiaryLabel)
    }
}

/// `_statusIcon`.
private func agentStatusSymbol(_ status: String) -> String {
    switch status.lowercased() {
    case "passed": Symbol.named("check_circle")
    case "failed", "error": Symbol.named("cancel")
    case "tossed": Symbol.named("do_not_disturb_on")
    case "duplicate": Symbol.named("content_copy")
    case "stopped": Symbol.named("stop_circle")
    default: Symbol.named("radio_button_unchecked")
    }
}

/// One run — `_RunCard`, as a list row: the name, when, and its status;
/// the stage stepper; the final result; and, for a run the stopped agent
/// left running, the stale note (Mark Stopped is the row below it).
private struct AgentRunRow: View {
    let run: AgentRun
    let control: AgentControl

    /// Dart's `stale` footer: a running run once the agent has stopped, or
    /// any run with a final result.
    static func showsFooter(_ run: AgentRun, _ control: AgentControl) -> Bool {
        run.finalResult != nil || (run.status.lowercased() == "running" && control.isStopped)
    }

    /// Mark Stopped: inside that footer, for a run still marked running
    /// while the agent is not.
    static func showsMarkStopped(_ run: AgentRun, _ control: AgentControl) -> Bool {
        showsFooter(run, control) && run.status.lowercased() == "running" && !control.isRunning
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            EntityRow(run.name ?? "Unnamed Strategy", subtitle: fmtDateTime(run.createdAt),
                      systemImage: Symbol.named("smart_toy"), tint: DS.Palette.warning) {
                AppBadge(label: run.status, color: agentStatusColor(run.status))
            }

            if run.stages.isEmpty {
                HStack(spacing: 10) {
                    Image(systemName: Symbol.named("hourglass_empty"))
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .frame(width: 28, height: 28)
                        .background(Color(uiColor: .tertiarySystemFill), in: Circle())
                    Text("Queued…").font(.subheadline.italic()).foregroundStyle(.secondary)
                }
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(run.stages.enumerated()), id: \.offset) { index, stage in
                        AgentStageRow(stage: stage, isLast: index == run.stages.count - 1, parentRunning: control.isRunning)
                    }
                }
            }

            if Self.showsFooter(run, control) {
                if let result = run.finalResult {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(run.status == "passed" ? "✓" : (run.status == "failed" ? "✗" : "○"))
                            .font(.body.weight(.semibold))
                            .foregroundStyle(agentStatusColor(run.status))
                        Text(result).font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                if Self.showsMarkStopped(run, control) {
                    Text("Agent stopped — run may be stale")
                        .font(.footnote.italic())
                        .foregroundStyle(DS.Palette.warning)
                }
            }
        }
        .padding(.vertical, 4)
    }
}

private struct AgentStageRow: View {
    let stage: AgentStage
    let isLast: Bool
    let parentRunning: Bool

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let color = agentStatusColor(stage.status)
        let spinning = stage.status.lowercased() == "running" && parentRunning
        let lineColor: Color = switch stage.status.lowercased() {
        case "running": DS.Palette.info.opacity(0.4)
        case "passed": DS.Palette.success.opacity(0.4)
        default: Color(uiColor: .separator)
        }
        HStack(alignment: .top, spacing: 10) {
            VStack(spacing: 0) {
                Group {
                    if spinning {
                        Image(systemName: "progress.indicator")
                            .symbolEffect(.variableColor.iterative, isActive: !reduceMotion)
                    } else {
                        Image(systemName: agentStatusSymbol(stage.status))
                    }
                }
                .font(.caption)
                .foregroundStyle(color)
                .frame(width: 28, height: 28)
                .background(color.opacity(DS.tintFill), in: Circle())
                if !isLast {
                    Rectangle().fill(lineColor).frame(width: 1, height: 32)
                }
            }
            .frame(width: 28)

            VStack(alignment: .leading, spacing: 2) {
                Text(stage.label).font(.subheadline.weight(.medium))
                if !stage.stocks.isEmpty {
                    Text(stage.stocks.joined(separator: ", "))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                if let pnl = stage.pnl {
                    HStack(spacing: 8) {
                        Text(fmtPnl(pnl.double))
                            .font(.footnote.monospacedDigit().weight(.semibold))
                            .foregroundStyle(pnlColor(pnl.double))
                        if let pct = stage.pnlPct {
                            Text(fmtPct(pct.double))
                                .font(.footnote.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.top, 2)
                }
                if let details = stage.details {
                    Text(details).font(.footnote).foregroundStyle(.secondary).lineLimit(1)
                }
                if stage.details == nil, stage.status.lowercased() == "running", parentRunning {
                    Text("In progress…").font(.footnote.italic()).foregroundStyle(DS.Palette.info)
                }
            }
            .padding(.bottom, isLast ? 0 : 8)
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
    }
}

private struct AgentRunsPager: View {
    let state: AgentRunsState
    let onPage: (Int) -> Void

    var body: some View {
        let page = state.page
        let total = state.totalPages
        Section {
            HStack(spacing: 2) {
                pageButton("‹", enabled: page > 1, active: false) { onPage(page - 1) }
                    .accessibilityLabel("Previous page")
                Spacer(minLength: 0)
                ForEach(Array(AgentRunsPagination.items(page: page, total: total).enumerated()), id: \.offset) { _, item in
                    if let n = item {
                        pageButton("\(n)", enabled: true, active: n == page) { onPage(n) }
                    } else {
                        Text("…").foregroundStyle(.secondary).padding(.horizontal, 4)
                    }
                }
                Spacer(minLength: 0)
                pageButton("›", enabled: page < total, active: false) { onPage(page + 1) }
                    .accessibilityLabel("Next page")
            }
        } footer: {
            Text("\(state.total) runs · page \(page) of \(total)")
                .frame(maxWidth: .infinity)
        }
    }

    /// A page number as plain text: the current page bold in the primary
    /// colour, the others in the accent.
    private func pageButton(_ label: String, enabled: Bool, active: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(active ? .body.weight(.semibold) : .body)
                .monospacedDigit()
                .foregroundStyle(active ? AnyShapeStyle(Color.primary) : AnyShapeStyle(.tint))
                .frame(minWidth: 36, minHeight: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .disabled(!enabled)
        .accessibilityAddTraits(active ? .isSelected : [])
    }
}

// MARK: - Sheets

/// `_StartAgentModal` as a sheet.
private struct AgentStartSheet: View {
    let onConfirm: (String?) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var request = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Special Request", text: $request,
                              prompt: Text("e.g. Focus on high-volatility tech stocks…"), axis: .vertical)
                        .lineLimit(4, reservesSpace: true)
                } header: {
                    Text("Special Request")
                } footer: {
                    Text("Optionally provide a special instruction.")
                }
            }
            .navigationTitle("Start Agent")
            .navigationSubtitle("Start AI Backtest Agent")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        let trimmed = request.trimmingCharacters(in: .whitespacesAndNewlines)
                        dismiss()
                        onConfirm(trimmed.isEmpty ? nil : trimmed)
                    } label: {
                        Label("Start Agent", systemImage: Symbol.named("play_arrow"))
                            .labelStyle(.titleAndIcon)
                    }
                    .tint(DS.Palette.success)
                }
            }
        }
        .presentationDetents([.medium])
        .presentationDragIndicator(.visible)
    }
}

/// `_ResumeModal` as a sheet: presets or a custom delay.
private struct AgentResumeSheet: View {
    let onConfirm: (Int) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var custom = ""

    private static let presets: [(label: String, minutes: Int)] = [
        ("Now", 0), ("5 min", 5), ("15 min", 15), ("30 min", 30), ("1 hr", 60),
    ]

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    ForEach(Self.presets, id: \.minutes) { preset in
                        InlineActionRow(preset.label,
                                        systemImage: preset.minutes == 0 ? Symbol.named("play_arrow") : Symbol.named("schedule")) {
                            pick(preset.minutes)
                        }
                    }
                } header: {
                    Text("Resume in")
                } footer: {
                    Text("Resume now or schedule automatic resume.")
                }
                Section("Custom delay") {
                    HStack(spacing: 8) {
                        TextField("0", text: $custom)
                            .keyboardType(.numberPad)
                            .frame(width: 80)
                        Text("minutes").foregroundStyle(.secondary)
                        Spacer()
                        Button("Schedule") {
                            let minutes = JSON.parseInt(custom) ?? 0
                            if minutes > 0 { pick(minutes) }
                        }
                        .buttonStyle(.borderless)
                    }
                }
            }
            .navigationTitle("Resume Agent")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    private func pick(_ minutes: Int) {
        dismiss()
        onConfirm(minutes)
    }
}
