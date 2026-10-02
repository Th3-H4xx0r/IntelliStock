import SwiftUI

/// The self-learning subsystem (`/learning`) — `LearningScreen`: engine and
/// mode controls, pending approvals, noise floors, findings and observed
/// runs. An inset-grouped list: the overview counts are a `StatGrid`, the
/// engine is a section with its Start / Stop button and mode picker, and
/// each finding is a severity-badged row that discloses its body.
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
                    LoadingState(label: "Loading learning data…").frame(maxHeight: .infinity)
                case .failed(let e):
                    ErrorRow(message: KalshiFormat.errorText(e), onRetry: { Task { await model.load() } })
                        .padding(16)
                        .frame(maxHeight: .infinity, alignment: .top)
                case .loaded(let s):
                    content(model, s)
                }
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.canvas)
        .navigationTitle("Learning")
        .navigationBarTitleDisplayMode(.inline)
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
            overview(s)
            engine(model, s)
            if let partial = s.partialError {
                Section {
                    ErrorRow(message: partial)
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                }
            }
            Section("Pending approvals") {
                if s.approvals.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("No approvals waiting")
                        Text("The subsystem is observe-only — it records and reports, and does not yet propose changes.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    ForEach(s.approvals) { a in approvalRow(model, a) }
                }
            }
            Section("Measured noise floors") {
                if s.floors.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("No floor measured yet").foregroundStyle(DS.Palette.warning)
                        Text("Two runs of one window have differed by ~16pp here, so nothing is promotable until a target has a measured floor.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    ForEach(Array(s.floors.enumerated()), id: \.offset) { _, f in floorRow(f) }
                }
            }
            Section("Findings & reports") {
                if s.findings.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Nothing raised yet")
                        Text("Findings appear as completed runs are observed.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    ForEach(s.findings) { f in LearningFindingRow(finding: f) }
                }
            }
            Section("Observed runs") {
                if s.funnels.isEmpty {
                    Text("No runs observed yet").foregroundStyle(.secondary)
                } else {
                    ForEach(Array(s.funnels.enumerated()), id: \.offset) { _, r in funnelRow(r) }
                }
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await model.load() }
    }

    // MARK: Overview + engine

    private func overview(_ s: LearningSnapshot) -> some View {
        let ov = s.overview
        let engineOn = ov?.engineRunning ?? false
        return Section {
            StatGrid(columns: 2) {
                StatCell(label: "Open findings", value: String(ov?.openFindings ?? 0))
                StatCell(label: "Runs observed", value: String(ov?.runsObserved ?? 0))
                StatCell(label: "Decisions", value: String(ov?.decisionsObserved ?? 0))
                StatCell(label: "Refusals", value: String(ov?.refusalsObserved ?? 0))
            }
            .padding(.vertical, 4)
        } header: {
            DSSectionHeader("Overview") {
                HStack(spacing: 10) {
                    StatusBadge(label: s.observeOnly ? "Observe only" : (ov?.mode ?? "—").dsSentenceCased, color: s.observeOnly ? DS.Palette.info : DS.Palette.success, pulsing: !s.observeOnly)
                    StatusDot(engineOn ? "Engine on" : "Engine off", color: engineOn ? DS.Palette.success : .secondary, pulsing: engineOn, font: .footnote)
                }
            }
        }
    }

    private func engine(_ model: LearningModel, _ s: LearningSnapshot) -> some View {
        let running = s.engineRunning
        return Section {
            HStack {
                StatusDot(running ? "Engine running" : "Engine stopped", color: running ? DS.Palette.success : .secondary, pulsing: running, font: .body)
                Spacer()
                if running {
                    Button("Stop") { Task { show(await model.setRunning(false)) } }
                        .buttonStyle(.bordered)
                        .disabled(model.acting)
                } else {
                    Button("Start") { Task { show(await model.setRunning(true)) } }
                        .dsProminentButton()
                        .disabled(model.acting)
                }
            }
            LabeledContent("Mode") {
                Picker("Mode", selection: Binding(
                    get: { s.mode },
                    set: { mode in Task { show(await model.setMode(mode)) } }
                )) {
                    ForEach(["observe", "propose", "act"], id: \.self) { Text($0.dsSentenceCased).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(maxWidth: 240)
                .disabled(model.acting)
            }
            Button {
                targetsOpen = true
            } label: {
                HStack {
                    Text(s.targetsLabel).foregroundStyle(.primary)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.tertiary)
                        .accessibilityHidden(true)
                }
            }
            .tint(.primary)
            .disabled(s.targets == nil)
        } header: {
            Text("Engine")
        } footer: {
            Text("Budgets and the permission matrix are on the web tab — a phone is where you answer a proposal, not where you tune a matrix.")
        }
    }

    // MARK: Approvals

    /// A proposal waiting for an answer: its rung, class and document, the
    /// summary and target, then Approve and Reject side by side.
    private func approvalRow(_ model: LearningModel, _ a: LearningApproval) -> some View {
        let live = a.holdsForever
        let busy = deciding.contains(a.id) || model.acting
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                AppBadge(label: a.rung, color: live ? DS.Palette.danger : DS.Palette.info)
                Text(a.actionClass).font(.footnote).foregroundStyle(.secondary)
                Spacer()
                Text("doc \(a.documentId)").font(.footnote).foregroundStyle(.secondary)
            }
            Text(a.summary).font(.body)
            Text(live ? "\(a.target) · this one waits until you answer" : a.target)
                .font(.footnote)
                .foregroundStyle(live ? DS.Palette.danger : Color.secondary)
            HStack(spacing: 10) {
                Button {
                    decide(model, a, "approved")
                } label: {
                    Text("Approve").frame(maxWidth: .infinity)
                }
                .tint(DS.Palette.success)
                Button {
                    decide(model, a, "rejected")
                } label: {
                    Text("Reject").frame(maxWidth: .infinity)
                }
                .tint(DS.Palette.danger)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .disabled(busy)
            .padding(.top, 4)
        }
        .padding(.vertical, 4)
    }

    private func decide(_ model: LearningModel, _ a: LearningApproval, _ decision: String) {
        guard !deciding.contains(a.id) else { return }
        deciding.insert(a.id)
        Task {
            show(await model.decide(a, decision))
            deciding.remove(a.id)
        }
    }

    // MARK: Floors + runs

    private func floorRow(_ f: LearningFloor) -> some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(f.target).font(.headline)
                Text(f.windowClass).font(.subheadline).foregroundStyle(.secondary)
                if !f.measured, !f.reason.isEmpty {
                    Text(f.reason).font(.footnote).foregroundStyle(DS.Palette.warning)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(f.measured ? "\(dartToStringAsFixed(f.floorPp, 2))pp" : "—")
                .monospacedDigit()
                .foregroundStyle(f.measured ? Color.primary : DS.Palette.warning)
        }
        .accessibilityElement(children: .combine)
    }

    private func funnelRow(_ r: LearningFunnel) -> some View {
        let pct = r.buyConversionPct
        return HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Run \(r.runId)").font(.headline)
                Text(r.target).font(.subheadline).foregroundStyle(.secondary)
                Text("\(r.decided) decided · \(r.executed) executed · \(r.refused) refused")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            EntityRowValue(
                pct.map { "\(dartToStringAsFixed($0, 1))%" } ?? "—",
                color: pct.map { $0 < 25 } == true ? DS.Palette.danger : nil,
                detail: "buy conv."
            )
        }
        .accessibilityElement(children: .combine)
    }
}

/// A finding (`_FindingCard`): the severity badge, title and target; expand
/// it for the body and its ladder stepper.
private struct LearningFindingRow: View {
    let finding: LearningFinding

    @State private var open = false

    var body: some View {
        let f = finding
        DisclosureGroup(isExpanded: $open) {
            VStack(alignment: .leading, spacing: 0) {
                Text(f.detail)
                    .font(.subheadline)
                    .padding(.bottom, 12)
                step("Detected", "\(f.kind) · run \(f.runId.isEmpty ? "—" : f.runId)", DS.Palette.danger, reached: true)
                if !f.evidence.isEmpty {
                    Text(f.evidence.entries.map { "\($0.key): \($0.value.dartDescription)" }.joined(separator: "\n"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.leading, 20)
                        .padding(.bottom, 12)
                }
                ForEach(LearningModel.ladder, id: \.name) { rung in
                    step(rung.name, rung.detail, Color(uiColor: .tertiaryLabel), reached: false)
                }
            }
            .padding(.vertical, 4)
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    StatusBadge(label: f.severity.dsSentenceCased, color: LearningModel.severityColor(f.severity))
                    Text(f.target).font(.footnote).foregroundStyle(.secondary).lineLimit(1)
                }
                Text(f.title).font(.headline).foregroundStyle(.primary)
            }
        }
        .accessibilityHint(open ? "Collapses the ladder" : "Shows the ladder")
    }

    private func step(_ label: String, _ detail: String, _ color: Color, reached: Bool) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(color).frame(width: 10, height: 10).padding(.top, 4)
            VStack(alignment: .leading, spacing: 0) {
                Text(label).font(.footnote.weight(.semibold)).foregroundStyle(reached ? Color.primary : Color.secondary)
                Text(detail).font(.caption).foregroundStyle(.secondary)
                if !reached {
                    Text("not reached — the subsystem observes only").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .padding(.bottom, 12)
    }
}

/// Which strategy documents the subsystem may write to and which instances it
/// watches (`_TargetsSheet`). The two lists mean opposite things when empty,
/// and each says so. Save is the toolbar's confirm action.
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
        NavigationStack {
            Form {
                Section {
                    ForEach(targets.strategies) { doc in
                        checkRow(on: armed.contains(doc.id), toggle: { toggle(&armed, doc.id) }) {
                            HStack {
                                Text(doc.name).lineLimit(1)
                                Spacer()
                                moneyBadge(doc.money, running: false)
                            }
                            Text(doc.instanceNames.isEmpty ? "#\(doc.id) · not attached to an instance" : "#\(doc.id) · \(doc.instanceNames.joined(separator: ", "))")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                } header: {
                    Text("Documents the subsystem may write to")
                } footer: {
                    Text("Empty means it writes nowhere.")
                }
                Section {
                    ForEach(targets.instances) { inst in
                        checkRow(on: watched.contains(inst.id), toggle: { toggle(&watched, inst.id) }) {
                            HStack {
                                Text(inst.name).lineLimit(1)
                                Spacer()
                                moneyBadge(inst.money, running: inst.running)
                            }
                            Text("\(inst.kind) · doc #\(inst.strategyId ?? "—")").font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                } header: {
                    Text("Instances to watch")
                } footer: {
                    Text(watched.isEmpty ? "None selected — watching every instance." : "Only the selected instances are observed.")
                        .foregroundStyle(watched.isEmpty ? DS.Palette.info : Color.secondary)
                }
                if let error {
                    Section { ErrorRow(message: error).listRowInsets(EdgeInsets()) }
                }
            }
            .navigationTitle("Documents & instances")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }.disabled(saving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task { await save() }
                    } label: {
                        if saving {
                            ProgressView().accessibilityLabel("Saving…")
                        } else {
                            Label("Save", systemImage: "checkmark")
                        }
                    }
                    .dsGlassProminentButton()
                    .disabled(saving)
                }
            }
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
    }

    private func toggle(_ list: inout [String], _ id: String) {
        if let i = list.firstIndex(of: id) { list.remove(at: i) } else { list.append(id) }
    }

    private func checkRow<L: View>(on: Bool, toggle: @escaping () -> Void, @ViewBuilder label: () -> L) -> some View {
        Button(action: toggle) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) { label() }
                    .foregroundStyle(.primary)
                Image(systemName: on ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(on ? AnyShapeStyle(DS.Palette.accent) : AnyShapeStyle(.tertiary))
            }
        }
        .tint(.primary)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    @ViewBuilder
    private func moneyBadge(_ money: String, running: Bool) -> some View {
        if money == "live" {
            AppBadge(label: "real money", color: DS.Palette.danger)
        } else if money == "unknown" {
            AppBadge(label: "unverified", color: DS.Palette.warning)
        } else if running {
            AppBadge(label: "running", color: DS.Palette.success)
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
