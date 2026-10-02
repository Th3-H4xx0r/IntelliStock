import SwiftUI

/// The AI backtest agent's runs — `AgentRunsScreen` in
/// `agent_runs_screen.dart`: status, controls (start / pause / unpause with a
/// scheduled resume / stop), runs grouped by cycle with their stage steppers,
/// and pagination. Polls every 5 s.
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
                VStack {
                    ErrorRow(message: (error as? ApiError)?.message ?? error.localizedDescription) {
                        Task { await model?.refreshNow() }
                    }
                    .padding(16)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .loaded(let state):
                AgentRunsList(model: model, state: state, onStart: { startOpen = true }, onResume: { resumeOpen = true })
            }
        }
        .background(DS.Surface.canvas)
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
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                AgentRunsHeader(control: state.control)
                AgentRunsControls(model: model, state: state, onStart: onStart, onResume: onResume)
                    .padding(.bottom, 4)

                if let error = state.errorMessage {
                    ErrorRow(message: error)
                }

                if state.busy && state.runs.isEmpty {
                    ForEach(0..<3, id: \.self) { _ in
                        AgentRunCard(run: AgentRunsState.placeholder.runs[0], control: state.control, busy: false, onForceStop: {})
                            .redacted(reason: .placeholder)
                    }
                } else if state.runs.isEmpty {
                    EmptyState(
                        systemImage: Symbol.named("smart_toy"),
                        title: "No agent runs yet",
                        subtitle: "Start the AI Backtest Agent to see strategy attempts here."
                    )
                    .padding(.vertical, 24)
                } else {
                    ForEach(cycles) { cycle in
                        AgentCycleDivider(date: cycle.startedAt)
                        ForEach(cycle.runs) { run in
                            AgentRunCard(run: run, control: state.control, busy: state.busy) {
                                Task { await model?.forceStop(run.id) }
                            }
                        }
                    }
                }

                if state.totalPages > 1 {
                    AgentRunsPager(state: state) { page in
                        Task { await model?.goToPage(page) }
                    }
                    .padding(.top, 8)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 24)
        }
        .refreshable { await model?.refreshNow() }
    }
}

// MARK: - Header and controls

private struct AgentRunsHeader: View {
    let control: AgentControl

    var body: some View {
        let (label, color): (String, Color) = control.isRunning
            ? ("Running", DS.Palette.success)
            : (control.isPaused ? ("Paused", DS.Palette.warning) : ("Stopped", .secondary))
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("AI Agent Runs")
                    .font(.title2.bold())
                    .accessibilityAddTraits(.isHeader)
                Text("Strategy attempts by the AI Backtest Agent.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            StatusBadge(label: label, color: color, pulsing: control.isRunning)
        }
    }
}

private struct AgentRunsControls: View {
    let model: AgentRunsModel?
    let state: AgentRunsState
    let onStart: () -> Void
    let onResume: () -> Void

    var body: some View {
        let control = state.control
        let hasCountdown = state.scheduledResumeAt != nil
        ChatFlowLayout(spacing: 8) {
            if control.isStopped {
                AgentControlButton(label: "Start", icon: "play_arrow", color: DS.Palette.success, busy: state.busy, action: onStart)
            }
            if control.isRunning {
                AgentControlButton(label: "Pause", icon: "pause", color: DS.Palette.warning, busy: state.busy) {
                    Task { await model?.pauseAgent() }
                }
            }
            if control.isPaused && !hasCountdown {
                AgentControlButton(label: "Unpause", icon: "play_arrow", color: DS.Palette.info, busy: state.busy, action: onResume)
            }
            if hasCountdown, let model {
                AgentCountdownRing(model: model, state: state)
            }
            if !control.isStopped {
                AgentControlButton(label: "Stop", icon: "stop", color: DS.Palette.danger, busy: state.busy) {
                    Task { await model?.stopAgent() }
                }
            }
            Menu {
                Picker("Per page", selection: Binding(
                    get: { state.perPage },
                    set: { value in Task { await model?.setPerPage(value) } }
                )) {
                    ForEach(AgentRunsModel.perPageOptions, id: \.self) { Text("\($0)/page").tag($0) }
                }
            } label: {
                Text("\(state.perPage)/page")
                    .font(.footnote)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
                    .background(DS.Surface.panel, in: .capsule)
            }
            Button {
                Task { await model?.refreshNow() }
            } label: {
                Group {
                    if state.busy { ProgressView() } else { Image(systemName: Symbol.named("refresh")) }
                }
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .accessibilityLabel("Refresh")
        }
    }
}

private struct AgentControlButton: View {
    let label: String
    let icon: String
    let color: Color
    let busy: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if busy { ProgressView() } else { Image(systemName: Symbol.named(icon)) }
                Text(label)
            }
            .font(.subheadline.weight(.semibold))
        }
        .buttonStyle(.bordered)
        .tint(color)
        .disabled(busy)
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
                    .font(.system(.caption, design: .monospaced).weight(.semibold))
                    .foregroundStyle(DS.Palette.info)
                    .contentTransition(.numericText(countsDown: true))
                Text("resuming").font(.caption2).foregroundStyle(.tertiary)
            }
        }
    }
}

// MARK: - Runs

private struct AgentCycleDivider: View {
    let date: Date?

    var body: some View {
        HStack(spacing: 8) {
            Rectangle().fill(Color(uiColor: .separator)).frame(height: 0.5)
            Text(date.map { fmtDateTime($0) } ?? "—")
                .font(.system(.caption2, design: .monospaced))
                .tracking(0.5)
                .foregroundStyle(.tertiary)
                .fixedSize()
            Rectangle().fill(Color(uiColor: .separator)).frame(height: 0.5)
        }
        .padding(.top, 4)
    }
}

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

/// `_statusIcon` (SF Symbols where the Material name has no map entry).
private func agentStatusSymbol(_ status: String) -> String {
    switch status.lowercased() {
    case "passed": Symbol.named("check_circle")
    case "failed", "error": Symbol.named("cancel")
    case "tossed": "minus.circle"          // do_not_disturb_on
    case "duplicate": Symbol.named("content_copy")
    case "stopped": Symbol.named("stop_circle")
    default: "circle"                      // radio_button_unchecked
    }
}

private struct AgentRunCard: View {
    let run: AgentRun
    let control: AgentControl
    let busy: Bool
    let onForceStop: () -> Void

    var body: some View {
        Card(padding: 16) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 10) {
                    IconTile(systemImage: Symbol.named("smart_toy"), color: DS.Palette.warning)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(run.name ?? "Unnamed Strategy")
                            .font(.subheadline.weight(.semibold))
                            .lineLimit(1)
                        Text(fmtDateTime(run.createdAt))
                            .font(.system(.caption2, design: .monospaced))
                            .foregroundStyle(.tertiary)
                    }
                    Spacer()
                    AppBadge(label: run.status, color: agentStatusColor(run.status))
                }

                if run.stages.isEmpty {
                    HStack(spacing: 10) {
                        Image(systemName: Symbol.named("hourglass_empty"))
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                            .frame(width: 28, height: 28)
                            .background(Color(uiColor: .tertiarySystemFill), in: Circle())
                        Text("Queued…").font(.footnote.italic()).foregroundStyle(.secondary)
                    }
                } else {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(run.stages.enumerated()), id: \.offset) { index, stage in
                            AgentStageRow(stage: stage, isLast: index == run.stages.count - 1, parentRunning: control.isRunning)
                        }
                    }
                }

                let stale = run.status.lowercased() == "running" && control.isStopped
                if run.finalResult != nil || stale {
                    Divider()
                    if let result = run.finalResult {
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Text(run.status == "passed" ? "✓" : (run.status == "failed" ? "✗" : "○"))
                                .font(.body.weight(.semibold))
                                .foregroundStyle(agentStatusColor(run.status))
                            Text(result).font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                    if run.status.lowercased() == "running" && !control.isRunning {
                        HStack {
                            Text("Agent stopped — run may be stale")
                                .font(.caption2.italic())
                                .foregroundStyle(DS.Palette.warning)
                            Spacer()
                            Button(role: .destructive, action: onForceStop) {
                                Label("Mark Stopped", systemImage: Symbol.named("close"))
                                    .font(.caption.weight(.semibold))
                            }
                            .buttonStyle(.bordered)
                            .controlSize(.small)
                            .disabled(busy)
                        }
                    }
                }
            }
        }
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
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
                if let pnl = stage.pnl {
                    HStack(spacing: 8) {
                        Text(fmtPnl(pnl.double))
                            .font(.system(.caption, design: .monospaced).weight(.semibold))
                            .foregroundStyle(pnlColor(pnl.double))
                        if let pct = stage.pnlPct {
                            Text(fmtPct(pct.double))
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.top, 2)
                }
                if let details = stage.details {
                    Text(details).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
                if stage.details == nil, stage.status.lowercased() == "running", parentRunning {
                    Text("In progress…").font(.caption2.italic()).foregroundStyle(DS.Palette.info)
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
        VStack(spacing: 8) {
            Text("\(state.total) runs · page \(page) of \(total)")
                .font(.caption2)
                .foregroundStyle(.secondary)
            ChatFlowLayout(spacing: 4, centered: true) {
                pageButton("‹", enabled: page > 1, active: false) { onPage(page - 1) }
                    .accessibilityLabel("Previous page")
                ForEach(Array(AgentRunsPagination.items(page: page, total: total).enumerated()), id: \.offset) { _, item in
                    if let n = item {
                        pageButton("\(n)", enabled: true, active: n == page) { onPage(n) }
                    } else {
                        Text("…").font(.footnote).foregroundStyle(.secondary).padding(.horizontal, 4)
                    }
                }
                pageButton("›", enabled: page < total, active: false) { onPage(page + 1) }
                    .accessibilityLabel("Next page")
            }
        }
        .frame(maxWidth: .infinity)
    }

    private func pageButton(_ label: String, enabled: Bool, active: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.footnote.weight(active ? .semibold : .regular))
                .frame(minWidth: 44, minHeight: 44)
        }
        .buttonStyle(.bordered)
        // A 44 pt hit target with the bordered padding kept minimal.
        .controlSize(.mini)
        .tint(active ? DS.Palette.accent : .secondary)
        .disabled(!enabled)
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
                    HStack(spacing: 12) {
                        IconTile(systemImage: Symbol.named("smart_toy"), color: DS.Palette.warning)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Start AI Backtest Agent").font(.subheadline.weight(.semibold))
                            Text("Optionally provide a special instruction.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .listRowBackground(Color.clear)
                }
                Section("Special Request") {
                    TextField("Special Request", text: $request,
                              prompt: Text("e.g. Focus on high-volatility tech stocks…"), axis: .vertical)
                        .lineLimit(4, reservesSpace: true)
                }
            }
            .navigationTitle("Start Agent")
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
                    HStack(spacing: 12) {
                        IconTile(systemImage: Symbol.named("play_circle"), color: DS.Palette.info)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Resume Agent").font(.subheadline.weight(.semibold))
                            Text("Resume now or schedule automatic resume.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .listRowBackground(Color.clear)
                }
                Section("RESUME IN") {
                    ChatFlowLayout(spacing: 8) {
                        ForEach(Self.presets, id: \.minutes) { preset in
                            Button(preset.label) { pick(preset.minutes) }
                                .buttonStyle(.bordered)
                                .tint(preset.minutes == 0 ? DS.Palette.success : DS.Palette.info)
                        }
                    }
                    .padding(.vertical, 4)
                }
                Section("CUSTOM DELAY") {
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
                        .buttonStyle(.bordered)
                        .tint(DS.Palette.info)
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
