import SwiftUI

/// The self-learning subsystem (`/learning`) — `LearningScreen`, drawn as the
/// web's `LearningView.vue`: the engine's status and Start / Stop engine,
/// the counters, the engine's mode and targets, then pending approvals,
/// noise floors, findings and observed runs, each a section on one card.
/// Each finding opens in place to show its ladder.
struct LearningView: View {
    @Environment(AppServices.self) private var services
    @State private var model: LearningModel?
    @State private var toast: Toast?
    @State private var targetsOpen = false
    /// Approvals with a decision in flight.
    @State private var deciding: Set<String> = []

    var body: some View {
        Group {
            if let model {
                switch model.state {
                case .loading:
                    LearningSkeleton()
                case .failed(let e):
                    List {
                        Section {
                            ErrorRow(message: KalshiFormat.errorText(e), onRetry: { Task { await model.load() } })
                                .dsClearRow(top: 8)
                        }
                    }
                    .dsList()
                    .refreshable { await model.load() }
                case .loaded(let s):
                    content(model, s)
                }
            } else {
                LearningSkeleton()
            }
        }
        .navigationTitle("Learning")
        .navigationBarTitleDisplayMode(.large)
        .toast($toast)
        .task {
            if model == nil {
                model = LearningModel(repository: { [services] in services.learningRepository })
            }
            if let model, model.state.needsLoad { await model.load() }
        }
        .sheet(isPresented: $targetsOpen, onDismiss: { Task { await model?.load() } }) {
            if let model, let targets = model.state.value?.targets {
                LearningTargetsSheet(targets: targets, model: model)
            }
        }
    }

    private func show(_ error: String?) {
        if let error { toast = Toast(error, style: .error) }
    }

    private func content(_ model: LearningModel, _ s: LearningSnapshot) -> some View {
        List {
            Section {
                header(model, s)
                    .dsClearRow(top: 4)
            }
            if s.overview != nil {
                Section {
                    counters(s)
                        .dsClearRow()
                }
            }
            engine(model, s)
            if let partial = s.partialError {
                Section {
                    ErrorRow(message: partial)
                        .dsClearRow()
                }
            }
            approvals(model, s)
            floors(s)
            findings(s)
            runs(s)
        }
        .dsList()
        .refreshable { await model.load() }
    }

    // MARK: Header, counters, engine

    /// The web's header: the page's one-line purpose, the engine's state and
    /// mode as a quiet line, and Start engine / Stop engine.
    private func header(_ model: LearningModel, _ s: LearningSnapshot) -> some View {
        let running = s.engineRunning
        let modeLabel = s.observeOnly ? "Observe only" : (s.overview?.mode ?? s.mode).dsSentenceCased
        return VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 8) {
                Text("What the system decided, what it declined to do, and what that says about it")
                    .font(DS.Font.pageSub)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    StatusDot(running ? "Engine running" : "Engine stopped", tone: running ? .live : .off, font: DS.Font.caption)
                    Text("·")
                        .foregroundStyle(DS.Text.faint)
                        .accessibilityHidden(true)
                    Text(modeLabel)
                        .foregroundStyle(s.observeOnly ? DS.Text.secondary : DS.Text.soft)
                }
                .font(DS.Font.caption)
            }
            HStack(spacing: 8) {
                if running {
                    Button("Stop engine", role: .destructive) { Task { show(await model.setRunning(false)) } }
                        .buttonStyle(.dsSecondary(size: .medium))
                } else {
                    Button("Start engine") { Task { show(await model.setRunning(true)) } }
                        .buttonStyle(.dsSecondary(size: .medium))
                }
                if model.acting {
                    ProgressView()
                        .controlSize(.small)
                        .padding(.leading, 4)
                }
            }
            .disabled(model.acting)
        }
    }

    /// The counters card (the web's): four figures in two columns.
    private func counters(_ s: LearningSnapshot) -> some View {
        let ov = s.overview
        return Card(padding: EdgeInsets(top: 16, leading: DS.cardPadding, bottom: 16, trailing: DS.cardPadding)) {
            StatGrid(columns: 2, verticalSpacing: 16) {
                StatCell(label: "Open findings", value: String(ov?.openFindings ?? 0))
                StatCell(label: "Runs observed", value: String(ov?.runsObserved ?? 0))
                StatCell(label: "Decisions", value: String(ov?.decisionsObserved ?? 0))
                StatCell(label: "Refusals", value: String(ov?.refusalsObserved ?? 0))
            }
            .statValueStyle(.figure)
        }
    }

    /// The engine's mode and what it may touch.
    private func engine(_ model: LearningModel, _ s: LearningSnapshot) -> some View {
        DSSection(
            "Engine",
            subtitle: "Nothing is promotable until a target has a measured noise floor, whatever these say",
            footer: "Budgets and the permission matrix are on the web — a phone is where you answer a proposal, not where you tune a matrix"
        ) {
            HStack(spacing: 12) {
                Text("Mode")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.secondary)
                Spacer(minLength: 8)
                DSSegmentedControl(
                    "Mode",
                    selection: Binding(
                        get: { s.mode },
                        set: { mode in Task { show(await model.setMode(mode)) } }
                    ),
                    options: ["observe", "propose", "act"],
                    isDisabled: { _ in model.acting }
                ) { $0.dsSentenceCased }
            }
            Button {
                targetsOpen = true
            } label: {
                HStack(spacing: 12) {
                    Text(s.targetsLabel)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.primary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 13))
                        .foregroundStyle(DS.Text.faint)
                        .accessibilityHidden(true)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(s.targets == nil)
        }
    }

    // MARK: Approvals

    private func approvals(_ model: LearningModel, _ s: LearningSnapshot) -> some View {
        let live = s.liveApprovals.count
        return DSSection("Pending approvals") {
            if s.approvals.isEmpty {
                EmptyState(
                    title: "No approvals waiting",
                    subtitle: s.observeOnly
                        ? "The subsystem is in observe mode — it records and reports, and does not propose changes"
                        : "Nothing is waiting on you right now"
                )
            } else {
                ForEach(s.approvals) { a in approvalRow(model, a) }
            }
        } accessory: {
            if live > 0 {
                StatusDot("\(live) live — waits indefinitely", tone: .warn, font: DS.Font.caption)
            }
        }
    }

    /// A proposal waiting for an answer (the web's row): its rung, class
    /// and document; the summary; the target and when it was asked; then
    /// Approve and Reject.
    private func approvalRow(_ model: LearningModel, _ a: LearningApproval) -> some View {
        let live = a.holdsForever
        let busy = deciding.contains(a.id) || model.acting
        let requested = fmtDateTime(a.requestedAt)
        var meta = a.target
        if requested != "—", !requested.isEmpty { meta += " · requested \(requested)" }
        if live { meta += " · this one waits until you answer" }
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 12) {
                HStack(spacing: 6) {
                    if live { DSStatusDot(.warn) }
                    Text(a.rung).foregroundStyle(DS.Text.label)
                }
                Text(a.actionClass).foregroundStyle(DS.Text.muted)
                Text("doc \(a.documentId.isEmpty ? "—" : a.documentId)")
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
            }
            .font(DS.Font.caption)
            .lineLimit(1)
            Text(a.summary)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.primary)
                .fixedSize(horizontal: false, vertical: true)
            Text(meta)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Button("Approve") { decide(model, a, "approved") }
                Button("Reject") { decide(model, a, "rejected") }
            }
            .buttonStyle(.dsSecondary)
            .disabled(busy)
            .padding(.top, 6)
        }
        .padding(.vertical, 8)
    }

    private func decide(_ model: LearningModel, _ a: LearningApproval, _ decision: String) {
        guard !deciding.contains(a.id) else { return }
        deciding.insert(a.id)
        Task {
            show(await model.decide(a, decision))
            deciding.remove(a.id)
        }
    }

    // MARK: Floors

    private func floors(_ s: LearningSnapshot) -> some View {
        DSSection(
            "Measured noise floors",
            subtitle: "A target with no measured floor cannot promote anything — two runs of one window have differed by ~16pp here, so an unmeasured target's results are not attributable"
        ) {
            if s.floors.isEmpty {
                VStack(spacing: 4) {
                    HStack(spacing: 8) {
                        DSStatusDot(.warn)
                        Text("No floor has been measured yet")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.label)
                    }
                    Text("Nothing is promotable until one is")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 40)
                .accessibilityElement(children: .combine)
            } else {
                ForEach(Array(s.floors.enumerated()), id: \.offset) { _, f in floorRow(f) }
            }
        }
    }

    /// A target's floor (the web's table row): the target and its window
    /// class, the floor and its repeats; an unmeasured one says why in amber.
    private func floorRow(_ f: LearningFloor) -> some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(f.target)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.primary)
                Text(f.windowClass)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                if !f.measured {
                    Text(f.reason.isEmpty ? "Not measured" : f.reason)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Palette.amber)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            EntityRowValue(
                f.measured ? "\(dartToStringAsFixed(f.floorPp, 2))pp" : "—",
                color: f.measured ? DS.Text.label : DS.Palette.amber,
                detail: "\(f.n) repeat\(f.n == 1 ? "" : "s")"
            )
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: Findings

    private func findings(_ s: LearningSnapshot) -> some View {
        DSSection("Findings & reports") {
            if s.findings.isEmpty {
                EmptyState(
                    title: "Nothing raised yet",
                    subtitle: s.overview.map { !$0.engineRunning } == true
                        ? "The self-learning engine is stopped — start it to begin observing"
                        : "Findings appear as completed runs are observed"
                )
            } else {
                ForEach(s.findings) { f in LearningFindingRow(finding: f) }
            }
        }
    }

    // MARK: Runs

    private func runs(_ s: LearningSnapshot) -> some View {
        DSSection("Observed runs") {
            if s.funnels.isEmpty {
                EmptyState(title: "No runs observed yet")
            } else {
                ForEach(Array(s.funnels.enumerated()), id: \.offset) { _, r in funnelRow(r) }
            }
        }
    }

    /// One observed run (the web's table row): the run and its target with
    /// decided / executed / refused under them, and the buy conversion on
    /// the right (amber under 25%).
    private func funnelRow(_ r: LearningFunnel) -> some View {
        let pct = r.buyConversionPct
        return HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Run \(r.runId)")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.primary)
                    .monospacedDigit()
                Text(r.target)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                Text("\(r.decided) decided · \(r.executed) executed · \(r.refused) refused")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            EntityRowValue(
                pct.map { "\(dartToStringAsFixed($0, 1))%" } ?? "—",
                color: pct.map { $0 < 25 } == true ? DS.Palette.amber : DS.Text.label,
                detail: "Buy conv."
            )
        }
        .accessibilityElement(children: .combine)
    }
}

/// A finding (the web's row): the severity as a dot and a word with the
/// target, the title and its detail, and a chevron. Tap it to open the
/// thread: when it was detected, then the ladder stepper — Detected with
/// its evidence, and each rung not reached yet.
struct LearningFindingRow: View {
    let finding: LearningFinding

    @State private var open: Bool

    /// `open` starts the row opened (a deep link, a preview).
    init(finding: LearningFinding, open: Bool = false) {
        self.finding = finding
        _open = State(initialValue: open)
    }
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let f = finding
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(DSMotion.standard(reduceMotion: reduceMotion)) { open.toggle() }
            } label: {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 12) {
                            StatusDot(f.severity.dsSentenceCased, tone: Self.severityTone(f.severity), font: DS.Font.caption)
                            Text(f.target)
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                                .lineLimit(1)
                        }
                        Text(f.title)
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.primary)
                            .fixedSize(horizontal: false, vertical: true)
                        if !f.detail.isEmpty {
                            Text(f.detail)
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                                .lineLimit(open ? nil : 2)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: open ? "chevron.up" : "chevron.down")
                        .font(.system(size: 14))
                        .foregroundStyle(DS.Text.muted)
                        .padding(.top, 2)
                        .accessibilityHidden(true)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint(open ? "Collapses the ladder" : "Shows the ladder")

            if open {
                thread(f)
                    .padding(.top, 16)
            }
        }
        .padding(.vertical, 8)
    }

    private func thread(_ f: LearningFinding) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Detected \(fmtDateTime(f.detectedAt)) · run \(f.runId.isEmpty ? "—" : f.runId)")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
            VStack(alignment: .leading, spacing: 0) {
                step(isFirst: true, isLast: false, reached: true) {
                    Text("Detected")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.soft)
                    Text("\(f.kind) · \(f.target)")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                    if !f.evidence.isEmpty {
                        Text(f.evidence.entries.map { "\($0.key): \($0.value.dartDescription)" }.joined(separator: "\n"))
                            .font(DS.Font.mono)
                            .foregroundStyle(DS.Text.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12)
                            .background(DS.Surface.page, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
                            .overlay {
                                RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                                    .strokeBorder(DS.Line.divider, lineWidth: DS.Line.width)
                            }
                            .padding(.top, 8)
                    }
                }
                ForEach(Array(LearningModel.ladder.enumerated()), id: \.element.name) { i, rung in
                    step(isFirst: false, isLast: i == LearningModel.ladder.count - 1, reached: false) {
                        Text(rung.name)
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.muted)
                        Text(rung.detail)
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.faint)
                        Text("Not reached yet")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.faint)
                            .padding(.top, 2)
                    }
                }
            }
        }
    }

    /// One rung: a 10 pt dot on the stepper's hairline (zinc-200 once
    /// reached, zinc-700 before), its lines beside it.
    private func step<C: View>(isFirst: Bool, isLast: Bool, reached: Bool, @ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            content()
        }
        .padding(.leading, 20)
        .padding(.bottom, isLast ? 0 : 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(alignment: .topLeading) {
            // The hairline runs from this dot to the next; the last rung's
            // only reaches its own dot.
            Rectangle()
                .fill(DS.Line.input)
                .frame(width: DS.Line.width, height: isLast ? 9 : nil)
                .frame(maxHeight: isLast ? nil : .infinity, alignment: .top)
                .padding(.top, isFirst ? 9 : 0)
                .padding(.leading, 4.5)
        }
        .overlay(alignment: .topLeading) {
            Circle()
                .fill(reached ? DS.Text.soft : DS.Chart.series[2])
                .frame(width: 10, height: 10)
                .padding(.top, 4)
        }
        .accessibilityElement(children: .combine)
    }

    /// The web's severity dot: high loss, medium amber, anything else off.
    static func severityTone(_ severity: String) -> DSStatusTone {
        switch severity.lowercased() {
        case "high": .error
        case "medium": .warn
        default: .off
        }
    }
}

/// The first load (the web's): a header line and blocks where the counters
/// and the first section will be.
private struct LearningSkeleton: View {
    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 16) {
                    Skeleton(width: 280, height: 14)
                    Skeleton(width: 160, height: 12)
                    Skeleton(width: 112, height: 36, radius: DS.Radius.control)
                }
                .dsClearRow(top: 4)
            }
            Section {
                Skeleton(height: 112, radius: DS.Radius.card)
                    .dsClearRow()
            }
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    Skeleton(width: 144, height: 16)
                    Skeleton(height: 112, radius: DS.Radius.card)
                }
                .dsClearRow()
            }
        }
        .dsList()
        .disabled(true)
        .accessibilityLabel("Loading")
    }
}

/// Which strategy documents the subsystem may write to and which instances it
/// watches (`_TargetsSheet`), in the web's words. The two lists mean opposite
/// things when empty, and each says so.
struct LearningTargetsSheet: View {
    let targets: LearningTargets
    let model: LearningModel

    @Environment(\.dismiss) private var dismiss
    @State private var armed: [String]
    @State private var watched: [String]
    @State private var saving = false
    @State private var error: String?

    init(targets: LearningTargets, model: LearningModel) {
        self.targets = targets
        self.model = model
        var a: [String] = []
        for id in targets.documentAllowlist where !a.contains(id) { a.append(id) }
        var w: [String] = []
        for id in targets.watchedInstances where !w.contains(id) { w.append(id) }
        _armed = State(initialValue: a)
        _watched = State(initialValue: w)
    }

    var body: some View {
        ModalSheet("Documents & instances", subtitle: "What the subsystem may change, and whose runs it observes") {
            DSField(
                "Strategy documents the subsystem may write to",
                help: "Arming a document is what lets the subsystem change it. Empty means it writes nowhere, whatever the permission matrix says"
            ) {
                if targets.strategies.isEmpty {
                    Text("No strategy documents found")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                } else {
                    OptionList {
                        ForEach(targets.strategies) { doc in
                            CheckboxRow(isOn: armed.contains(doc.id), action: { toggle(&armed, doc.id) }) {
                                HStack(spacing: 8) {
                                    Text(doc.name)
                                        .font(DS.Font.body)
                                        .foregroundStyle(DS.Text.soft)
                                        .lineLimit(1)
                                    Text("#\(doc.id)")
                                        .font(DS.Font.caption)
                                        .foregroundStyle(DS.Text.faint)
                                        .monospacedDigit()
                                    Spacer(minLength: 4)
                                    moneyTag(doc.money, running: false)
                                }
                                Text(documentDetail(doc))
                                    .font(DS.Font.caption)
                                    .foregroundStyle(DS.Text.muted)
                            }
                        }
                    }
                }
            }
            DSField(
                "Instances to watch",
                help: "Which instances' runs the engine observes. Selecting none watches everything — observing is read-only, so narrowing is the deliberate act"
            ) {
                VStack(alignment: .leading, spacing: 8) {
                    if watched.isEmpty {
                        Text("Watching every instance")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.secondary)
                    }
                    if targets.instances.isEmpty {
                        Text("No instances found")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.muted)
                    } else {
                        OptionList {
                            ForEach(targets.instances) { inst in
                                CheckboxRow(isOn: watched.contains(inst.id), action: { toggle(&watched, inst.id) }) {
                                    HStack(spacing: 8) {
                                        Text(inst.name)
                                            .font(DS.Font.body)
                                            .foregroundStyle(DS.Text.soft)
                                            .lineLimit(1)
                                        Text(inst.kind)
                                            .font(DS.Font.caption)
                                            .foregroundStyle(DS.Text.faint)
                                        Spacer(minLength: 4)
                                        moneyTag(inst.money, running: inst.running)
                                    }
                                    Text("doc #\(inst.strategyId ?? "—")")
                                        .font(DS.Font.caption)
                                        .foregroundStyle(DS.Text.muted)
                                        .monospacedDigit()
                                }
                            }
                        }
                    }
                }
            }
            if let error {
                ErrorRow(message: error)
            }
        } footer: {
            Button("Cancel") { dismiss() }
                .buttonStyle(.dsGhost(size: .medium))
                .disabled(saving)
            Button {
                Task { await save() }
            } label: {
                Text(saving ? "Saving…" : "Save")
            }
            .buttonStyle(.dsPrimary)
            .disabled(saving)
        }
        .presentationDetents([.large])
    }

    private func documentDetail(_ doc: LearningStrategyTarget) -> String {
        let subs = "\(doc.subStrategies) sub-strateg\(doc.subStrategies == 1 ? "y" : "ies")"
        return doc.instanceNames.isEmpty
            ? "\(subs) · not attached to an instance"
            : "\(subs) · used by \(doc.instanceNames.joined(separator: ", "))"
    }

    private func toggle(_ list: inout [String], _ id: String) {
        if let i = list.firstIndex(of: id) { list.remove(at: i) } else { list.append(id) }
    }

    /// The web's money tag: real money as an amber dot and a word, unverified
    /// and paper as quiet words, else a running dot.
    @ViewBuilder
    private func moneyTag(_ money: String, running: Bool) -> some View {
        switch money {
        case "live":
            StatusDot("Real money", tone: .warn, font: DS.Font.caption)
        case "unknown":
            Text("Unverified")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.secondary)
        case "paper":
            Text("Paper")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
        default:
            if running {
                StatusDot("Running", tone: .live, font: DS.Font.caption)
            }
        }
    }

    private func save() async {
        saving = true
        error = nil
        do {
            try await model.saveTargets(armed: armed, watched: watched)
            dismiss()
        } catch {
            if !error.isCancellation { self.error = KalshiFormat.errorText(error) }
            saving = false
        }
    }
}
