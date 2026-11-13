import SwiftUI

/// The AI backtest agent's runs — `AgentRunsScreen` in
/// `agent_runs_screen.dart`, drawn as the web's `AgentRunsView.vue`: the
/// agent's status as a dot and a word, its controls as a row of buttons
/// (start / pause / unpause with a scheduled-resume ring / stop), then the
/// runs grouped by cycle — each cycle under its start time and a hairline,
/// each run a card with its stage stepper and outcome — and pagination.
/// Polls every 5 s. The page size is a toolbar menu; pull to refresh
/// replaces the refresh button.
struct AgentRunsView: View {
    @Environment(AppServices.self) private var services
    @State private var model: AgentRunsModel?
    @State private var startOpen = false
    @State private var resumeOpen = false

    var body: some View {
        Group {
            switch model?.state ?? .loading {
            case .loading:
                AgentRunsSkeleton()
            case .failed(let error):
                List {
                    Section {
                        ErrorRow(message: (error as? ApiError)?.message ?? error.localizedDescription) {
                            Task { await model?.refreshNow() }
                        }
                        .dsClearRow(top: 8)
                    }
                }
                .dsList()
                .refreshable { await model?.refreshNow() }
            case .loaded(let state):
                AgentRunsList(model: model, state: state, onStart: { startOpen = true }, onResume: { resumeOpen = true })
            }
        }
        .navigationTitle("Agent runs")
        .navigationBarTitleDisplayMode(.large)
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

private struct AgentRunsList: View {
    let model: AgentRunsModel?
    let state: AgentRunsState
    let onStart: () -> Void
    let onResume: () -> Void

    var body: some View {
        let cycles = AgentRunCycle.group(state.runs)
        List {
            Section {
                AgentRunsHeader(model: model, state: state, onStart: onStart, onResume: onResume)
                    .dsClearRow(top: 4)
            }

            if let error = state.errorMessage {
                Section {
                    ErrorRow(message: error)
                        .dsClearRow()
                }
            }

            if state.busy && state.runs.isEmpty {
                Section {
                    ForEach(0..<3, id: \.self) { _ in
                        AgentRunCardSkeleton()
                            .dsClearRow(bottom: 12)
                    }
                }
            } else if state.runs.isEmpty {
                Section {
                    Card(padding: 0) {
                        EmptyState(
                            title: "No agent runs yet",
                            subtitle: "Start the AI backtest agent to see strategy attempts here"
                        )
                    }
                    .dsClearRow()
                }
            } else {
                // One section per cycle, headed by its start time and a hairline.
                ForEach(cycles) { cycle in
                    Section {
                        ForEach(cycle.runs) { run in
                            AgentRunCard(run: run, control: state.control, busy: state.busy) {
                                Task { await model?.forceStop(run.id) }
                            }
                            .dsClearRow(bottom: 12)
                        }
                    } header: {
                        AgentCycleHeader(startedAt: cycle.startedAt)
                    }
                }
            }

            if state.totalPages > 1 {
                Section {
                    AgentRunsPager(state: state) { page in
                        Task { await model?.goToPage(page) }
                    }
                    .dsClearRow(bottom: 24)
                }
            }
        }
        .dsList()
        .refreshable { await model?.refreshNow() }
        .toolbar {
            if let model {
                ToolbarItem(placement: .topBarTrailing) {
                    ToolbarMenu("Runs per page") {
                        Picker("Runs per page", selection: Binding(
                            get: { state.perPage },
                            set: { value in Task { await model.setPerPage(value) } }
                        )) {
                            ForEach(AgentRunsModel.perPageOptions, id: \.self) { Text("\($0) per page").tag($0) }
                        }
                    }
                }
            }
        }
    }
}

// MARK: - Header and controls

/// The page's header (the web's): one quiet line on what the page holds
/// with the agent's status at its end, then the controls that apply — Start
/// (primary), Pause, Unpause or the scheduled-resume ring, and Stop.
private struct AgentRunsHeader: View {
    let model: AgentRunsModel?
    let state: AgentRunsState
    let onStart: () -> Void
    let onResume: () -> Void

    var body: some View {
        let control = state.control
        let hasCountdown = state.scheduledResumeAt != nil
        let status: (String, DSStatusTone) = control.isRunning
            ? ("Running", .live)
            : (control.isPaused ? ("Paused", .warn) : ("Stopped", .off))
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text("Strategy attempts by the AI backtest agent")
                    .font(DS.Font.pageSub)
                    .foregroundStyle(DS.Text.muted)
                    .frame(maxWidth: .infinity, alignment: .leading)
                StatusDot(status.0, tone: status.1)
                    .accessibilityLabel("Agent \(status.0)")
            }
            HStack(spacing: 8) {
                if control.isStopped {
                    Button("Start", action: onStart)
                        .buttonStyle(.dsPrimary)
                }
                if control.isRunning {
                    Button("Pause") { Task { await model?.pauseAgent() } }
                        .buttonStyle(.dsSecondary(size: .medium))
                }
                if control.isPaused && !hasCountdown {
                    Button("Unpause", action: onResume)
                        .buttonStyle(.dsSecondary(size: .medium))
                }
                if hasCountdown, let model {
                    AgentCountdownRing(model: model, state: state)
                }
                if !control.isStopped {
                    Button("Stop", role: .destructive) { Task { await model?.stopAgent() } }
                        .buttonStyle(.dsSecondary(size: .medium))
                }
                if state.busy {
                    ProgressView()
                        .controlSize(.small)
                        .padding(.leading, 4)
                }
            }
            .disabled(state.busy)
        }
    }
}

/// The scheduled-resume ring (the web's): an off-white arc that depletes
/// as time passes over a faint track, a stop square in the centre that
/// cancels, and the countdown "04:59 until resume" beside it.
private struct AgentCountdownRing: View {
    let model: AgentRunsModel
    let state: AgentRunsState

    var body: some View {
        _ = model.tick
        let fraction = state.countdownFraction()
        let secs = state.countdownSecsRemaining()
        return HStack(spacing: 8) {
            ZStack {
                Circle()
                    .stroke(DS.Line.input, lineWidth: 3)
                Circle()
                    .trim(from: 0, to: 1 - fraction)
                    .stroke(DS.Palette.accent, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                Button {
                    model.cancelCountdown()
                } label: {
                    Image(systemName: "stop.fill")
                        .font(.system(size: 10))
                }
                .buttonStyle(.dsIcon(side: 24))
                .accessibilityLabel("Cancel scheduled resume")
            }
            .frame(width: 36, height: 36)
            .frame(width: 44, height: 44)
            VStack(alignment: .leading, spacing: 0) {
                Text(agentCountdownLabel(secs))
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.soft)
                    .monospacedDigit()
                    .contentTransition(.numericText(countsDown: true))
                Text("until resume")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            }
        }
        .accessibilityElement(children: .contain)
    }
}

// MARK: - Runs

/// A run's or a stage's status as the web words and colours it.
private func agentStatusMeta(_ status: String) -> (label: String, tone: DSStatusTone) {
    switch status.lowercased() {
    case "running": ("Running", .live)
    case "passed": ("Passed", .positive)
    case "failed": ("Failed", .error)
    case "error": ("Error", .error)
    case "tossed": ("Tossed", .warn)
    case "duplicate": ("Duplicate", .off)
    case "stopped": ("Stopped", .off)
    default: (status.isEmpty ? "Stopped" : status.dsSentenceCased, .off)
    }
}

/// A cycle's header (the web's): its start time in 12 pt zinc-500, then a
/// hairline to the edge.
private struct AgentCycleHeader: View {
    let startedAt: Date?

    var body: some View {
        HStack(spacing: 12) {
            Text(startedAt.map { fmtDateTime($0) } ?? "—")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
                .fixedSize()
            DSDivider()
        }
        .textCase(nil)
        .listRowInsets(EdgeInsets(top: 0, leading: DS.pageGutter, bottom: DS.sectionHeaderGap, trailing: DS.pageGutter))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Cycle started \(startedAt.map { fmtDateTime($0) } ?? "—")")
        .accessibilityAddTraits(.isHeader)
    }
}

/// One run (the web's run card): the name and when on the left, the status
/// as a dot and a word on the right; the stage stepper; and, under a
/// hairline, the outcome — or, for a run the agent left running, the stale
/// note with Mark stopped.
private struct AgentRunCard: View {
    let run: AgentRun
    let control: AgentControl
    let busy: Bool
    let onMarkStopped: () -> Void

    /// Dart's `stale` footer: a running run once the agent has stopped, or
    /// any run with a final result.
    static func showsFooter(_ run: AgentRun, _ control: AgentControl) -> Bool {
        run.finalResult != nil || (run.status.lowercased() == "running" && control.isStopped)
    }

    /// Mark stopped: inside that footer, for a run still marked running
    /// while the agent is not.
    static func showsMarkStopped(_ run: AgentRun, _ control: AgentControl) -> Bool {
        showsFooter(run, control) && run.status.lowercased() == "running" && !control.isRunning
    }

    var body: some View {
        let meta = agentStatusMeta(run.status)
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(run.name ?? "Unnamed strategy")
                        .font(DS.Font.bodyMedium)
                        .foregroundStyle(DS.Text.primary)
                        .lineLimit(1)
                    Text(fmtDateTime(run.createdAt))
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                StatusDot(meta.label, tone: meta.tone, font: DS.Font.caption)
            }

            VStack(alignment: .leading, spacing: 0) {
                if run.stages.isEmpty {
                    AgentStepRow(tone: .off, isLast: true) {
                        Text("Queued")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.muted)
                    }
                } else {
                    ForEach(Array(run.stages.enumerated()), id: \.offset) { index, stage in
                        AgentStageRow(stage: stage, isLast: index == run.stages.count - 1, parentRunning: control.isRunning)
                    }
                }
            }

            if Self.showsFooter(run, control) {
                VStack(alignment: .leading, spacing: 12) {
                    DSDivider()
                    if let result = run.finalResult {
                        HStack(alignment: .top, spacing: 8) {
                            DSStatusDot(resultTone)
                                .padding(.top, 1.5)
                            Text(result)
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .accessibilityElement(children: .combine)
                    }
                    if Self.showsMarkStopped(run, control) {
                        HStack(spacing: 8) {
                            Text("Agent stopped — run may be stale")
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Palette.amber)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            Button("Mark stopped", role: .destructive, action: onMarkStopped)
                                .buttonStyle(.dsSecondary)
                                .disabled(busy)
                        }
                    }
                }
                .padding(.top, -4)
            }
        }
        .padding(DS.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCardSurface()
    }

    private var resultTone: DSStatusTone {
        switch run.status {
        case "passed": .positive
        case "failed": .error
        default: .off
        }
    }
}

/// One step of a stepper: the dot in a 12 pt column with a hairline down to
/// the next step, and the step's lines beside it.
private struct AgentStepRow<Content: View>: View {
    let tone: DSStatusTone
    let isLast: Bool
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            content()
        }
        .padding(.leading, 24)
        .padding(.bottom, isLast ? 0 : 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(alignment: .topLeading) {
            if !isLast {
                Rectangle()
                    .fill(DS.Line.input)
                    .frame(width: DS.Line.width)
                    .frame(maxHeight: .infinity)
                    .padding(.top, 19)
                    .padding(.bottom, 2)
                    .padding(.leading, 5.5)
            }
        }
        .overlay(alignment: .topLeading) {
            DSStatusDot(tone)
                .padding(.top, 3)
        }
    }
}

private struct AgentStageRow: View {
    let stage: AgentStage
    let isLast: Bool
    let parentRunning: Bool

    var body: some View {
        let running = stage.status.lowercased() == "running"
        // A stage left "running" by a stopped agent reads as stopped.
        let tone: DSStatusTone = running && !parentRunning ? .off : agentStatusMeta(stage.status).tone
        AgentStepRow(tone: tone, isLast: isLast) {
            Text(stage.label)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.soft)
            if !stage.stocks.isEmpty {
                Text(stage.stocks.joined(separator: ", "))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .lineLimit(1)
            }
            if let pnl = stage.pnl {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(fmtPnl(pnl.double))
                        .foregroundStyle(pnlColor(pnl.double))
                    if let pct = stage.pnlPct {
                        Text(fmtPct(pct.double))
                            .foregroundStyle(DS.Text.muted)
                    }
                }
                .font(DS.Font.caption)
                .monospacedDigit()
                .padding(.top, 2)
            }
            if let details = stage.details {
                Text(details)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .lineLimit(1)
                    .padding(.top, 2)
            } else if running, parentRunning {
                Text("In progress")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
                    .padding(.top, 2)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// A run card's loading shape (the web's skeleton cards).
private struct AgentRunCardSkeleton: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Skeleton(width: 160, height: 16)
            Skeleton(width: 96, height: 12).padding(.top, 8)
            Skeleton(width: 192, height: 12).padding(.top, 32)
            Skeleton(width: 144, height: 12).padding(.top, 16)
        }
        .frame(maxWidth: .infinity, minHeight: 152, alignment: .topLeading)
        .padding(DS.cardPadding)
        .dsCardSurface()
        .accessibilityHidden(true)
    }
}

/// The first load: the header's place and three skeleton cards.
private struct AgentRunsSkeleton: View {
    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 16) {
                    Skeleton(width: 240, height: 14)
                    Skeleton(width: 72, height: 36, radius: DS.Radius.control)
                }
                .dsClearRow(top: 4)
            }
            Section {
                ForEach(0..<3, id: \.self) { _ in
                    AgentRunCardSkeleton()
                        .dsClearRow(bottom: 12)
                }
            }
        }
        .dsList()
        .disabled(true)
        .accessibilityLabel("Loading")
    }
}

// MARK: - Pagination

/// One slot of the page row: a page number, or a gap (`page == nil`).
private struct AgentPageSlot: Hashable {
    let index: Int
    let page: Int?
}

/// The web's pagination: "N runs · page X of Y", and the pages as a
/// segmented control between previous and next.
private struct AgentRunsPager: View {
    let state: AgentRunsState
    let onPage: (Int) -> Void

    var body: some View {
        let page = state.page
        let total = state.totalPages
        let slots = AgentRunsPagination.items(page: page, total: total).enumerated().map { AgentPageSlot(index: $0.offset, page: $0.element) }
        VStack(spacing: 12) {
            HStack(spacing: 4) {
                Button { onPage(page - 1) } label: { Image(systemName: "chevron.left") }
                    .buttonStyle(.dsIcon)
                    .disabled(page <= 1)
                    .accessibilityLabel("Previous page")
                DSSegmentedControl(
                    "Page",
                    selection: Binding(
                        get: { slots.first { $0.page == page } ?? slots[0] },
                        set: { slot in if let p = slot.page { onPage(p) } }
                    ),
                    options: slots,
                    isDisabled: { $0.page == nil }
                ) { slot in
                    slot.page.map(String.init) ?? "…"
                }
                Button { onPage(page + 1) } label: { Image(systemName: "chevron.right") }
                    .buttonStyle(.dsIcon)
                    .disabled(page >= total)
                    .accessibilityLabel("Next page")
            }
            Text("\(state.total) runs · page \(page) of \(total)")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - Sheets

/// The "Start AI backtest agent" sheet (the web's modal): an optional
/// special request, then Cancel or Start agent.
struct AgentStartSheet: View {
    let onConfirm: (String?) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var request = ""

    var body: some View {
        ModalSheet("Start AI backtest agent", subtitle: "Optionally give a special instruction for strategy generation") {
            DSField("Special request (optional)") {
                DSTextField("e.g. Focus on high-volatility tech stocks, avoid leveraged ETFs", text: $request, axis: .vertical)
                    .lineLimit(4, reservesSpace: true)
            }
        } footer: {
            Button("Cancel") { dismiss() }
                .buttonStyle(.dsGhost(size: .medium))
            Button("Start agent") {
                let trimmed = request.trimmingCharacters(in: .whitespacesAndNewlines)
                dismiss()
                onConfirm(trimmed.isEmpty ? nil : trimmed)
            }
            .buttonStyle(.dsPrimary)
        }
        .presentationDetents([.medium])
    }
}

/// The "Resume agent" sheet (the web's modal): resume now or after a preset
/// delay, or schedule a custom one.
struct AgentResumeSheet: View {
    let onConfirm: (Int) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var custom = ""

    private static let presets: [(label: String, minutes: Int)] = [
        ("Now", 0), ("5 min", 5), ("15 min", 15), ("30 min", 30), ("1 hr", 60),
    ]

    private var customMinutes: Int { JSON.parseInt(custom) ?? 0 }

    var body: some View {
        ModalSheet("Resume agent", subtitle: "Resume now or schedule an automatic resume") {
            DSField("Resume in") {
                FlowLayout(spacing: 8) {
                    ForEach(Self.presets, id: \.minutes) { preset in
                        if preset.minutes == 0 {
                            Button(preset.label) { pick(0) }
                                .buttonStyle(.dsPrimary)
                        } else {
                            Button(preset.label) { pick(preset.minutes) }
                                .buttonStyle(.dsSecondary(size: .medium))
                                .monospacedDigit()
                        }
                    }
                }
            }
            DSField("Custom delay") {
                HStack(spacing: 8) {
                    DSTextField("minutes", text: $custom)
                        .keyboardType(.numberPad)
                        .frame(width: 96)
                    Text("minutes")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                    Spacer(minLength: 8)
                    Button("Schedule") {
                        if customMinutes > 0 { pick(customMinutes) }
                    }
                    .buttonStyle(.dsSecondary(size: .medium))
                    .disabled(customMinutes < 1)
                }
            }
        } footer: {
            Button("Cancel") { dismiss() }
                .buttonStyle(.dsGhost(size: .medium))
        }
        .presentationDetents([.medium, .large])
    }

    private func pick(_ minutes: Int) {
        dismiss()
        onConfirm(minutes)
    }
}
