import SwiftUI

/// The self-learning subsystem (`/learning`) — `LearningScreen`: engine and
/// mode controls, pending approvals, noise floors, findings and observed
/// runs.
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
                let m = LearningModel(repository: { [services] in services.learningRepository })
                model = m
                await m.load()
            }
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
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                header(s)
                controls(model, s)
                if let partial = s.partialError {
                    ErrorRow(message: partial)
                }
                SectionHeader(title: "Pending approvals").padding(.top, 8)
                if s.approvals.isEmpty {
                    Card {
                        VStack(spacing: 6) {
                            Text("No approvals waiting").font(.subheadline.weight(.semibold))
                            Text("The subsystem is observe-only — it records and reports, and does not yet propose changes.")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                                .multilineTextAlignment(.center)
                        }
                        .frame(maxWidth: .infinity)
                    }
                } else {
                    ForEach(s.approvals) { a in approvalCard(model, a) }
                }
                SectionHeader(title: "Measured noise floors").padding(.top, 8)
                if s.floors.isEmpty {
                    Card(padding: 16) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("No floor measured yet").font(.subheadline.weight(.semibold)).foregroundStyle(DS.Palette.warning)
                            Text("Two runs of one window have differed by ~16pp here, so nothing is promotable until a target has a measured floor.")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                } else {
                    ForEach(Array(s.floors.enumerated()), id: \.offset) { _, f in floorRow(f) }
                }
                SectionHeader(title: "Findings & reports").padding(.top, 8)
                if s.findings.isEmpty {
                    EmptyState(systemImage: Symbol.named("lightbulb"), title: "Nothing raised yet", subtitle: "Findings appear as completed runs are observed.")
                } else {
                    ForEach(s.findings) { f in LearningFindingCard(finding: f) }
                }
                SectionHeader(title: "Observed runs").padding(.top, 8)
                if s.funnels.isEmpty {
                    EmptyState(systemImage: Symbol.named("analytics"), title: "No runs observed yet")
                } else {
                    ForEach(Array(s.funnels.enumerated()), id: \.offset) { _, r in funnelRow(r) }
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 32)
        }
        .refreshable { await model.load() }
    }

    // MARK: Header + controls

    private func header(_ s: LearningSnapshot) -> some View {
        let ov = s.overview
        let engineOn = ov?.engineRunning ?? false
        return VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                StatusBadge(label: s.observeOnly ? "Observe only" : (ov?.mode ?? "—"), color: s.observeOnly ? DS.Palette.info : DS.Palette.success, pulsing: !s.observeOnly)
                StatusBadge(label: engineOn ? "Engine on" : "Engine off", color: engineOn ? DS.Palette.success : .secondary, pulsing: engineOn)
            }
            Grid(horizontalSpacing: 8, verticalSpacing: 8) {
                GridRow {
                    StatTile(label: "Open findings", value: String(ov?.openFindings ?? 0))
                    StatTile(label: "Runs observed", value: String(ov?.runsObserved ?? 0))
                }
                GridRow {
                    StatTile(label: "Decisions", value: String(ov?.decisionsObserved ?? 0))
                    StatTile(label: "Refusals", value: String(ov?.refusalsObserved ?? 0))
                }
            }
        }
    }

    private func controls(_ model: LearningModel, _ s: LearningSnapshot) -> some View {
        let running = s.engineRunning
        return Card(padding: 14) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text(running ? "Engine running" : "Engine stopped")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(running ? DS.Palette.success : Color.primary)
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
                VStack(alignment: .leading, spacing: 6) {
                    Text("Mode").font(.caption2).foregroundStyle(.secondary)
                    Picker("Mode", selection: Binding(
                        get: { s.mode },
                        set: { mode in Task { show(await model.setMode(mode)) } }
                    )) {
                        ForEach(["observe", "propose", "act"], id: \.self) { Text($0).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .disabled(model.acting)
                }
                Button {
                    targetsOpen = true
                } label: {
                    Text(s.targetsLabel).frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(s.targets == nil)
                Text("Budgets and the permission matrix are on the web tab — a phone is where you answer a proposal, not where you tune a matrix.")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
    }

    // MARK: Approvals

    private func approvalCard(_ model: LearningModel, _ a: LearningApproval) -> some View {
        let live = a.holdsForever
        let busy = deciding.contains(a.id) || model.acting
        return Card(padding: 14) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    AppBadge(label: a.rung, color: live ? DS.Palette.danger : DS.Palette.info)
                    Text(a.actionClass).font(.caption2).foregroundStyle(.secondary)
                    Spacer()
                    Text("doc \(a.documentId)").font(.caption2).foregroundStyle(.tertiary)
                }
                Text(a.summary).font(.body)
                Text(live ? "\(a.target) · this one waits until you answer" : a.target)
                    .font(.caption2)
                    .foregroundStyle(live ? DS.Palette.danger : Color(uiColor: .tertiaryLabel))
                HStack(spacing: 8) {
                    Button {
                        decide(model, a, "approved")
                    } label: {
                        Text("Approve").frame(maxWidth: .infinity)
                    }
                    .dsProminentButton()
                    Button {
                        decide(model, a, "rejected")
                    } label: {
                        Text("Reject").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                }
                .disabled(busy)
                .padding(.top, 4)
            }
        }
        .overlay {
            if live {
                RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous)
                    .strokeBorder(DS.Palette.danger, lineWidth: 1)
            }
        }
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
        Card(padding: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 0) {
                    Text(f.target).font(.subheadline.weight(.semibold))
                    Text(f.windowClass).font(.caption2).foregroundStyle(.secondary)
                    if !f.measured, !f.reason.isEmpty {
                        Text(f.reason).font(.caption2).foregroundStyle(DS.Palette.warning)
                    }
                }
                Spacer()
                Text(f.measured ? "\(dartToStringAsFixed(f.floorPp, 2))pp" : "—")
                    .font(.headline.monospacedDigit())
                    .foregroundStyle(f.measured ? Color.primary : DS.Palette.warning)
            }
        }
    }

    private func funnelRow(_ r: LearningFunnel) -> some View {
        let pct = r.buyConversionPct
        return Card(padding: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 0) {
                    Text("Run \(r.runId)").font(.subheadline.weight(.semibold))
                    Text(r.target).font(.caption2).foregroundStyle(.secondary)
                    Text("\(r.decided) decided · \(r.executed) executed · \(r.refused) refused")
                        .font(.footnote)
                        .padding(.top, 4)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 0) {
                    Text(pct.map { "\(dartToStringAsFixed($0, 1))%" } ?? "—")
                        .font(.headline.monospacedDigit())
                        .foregroundStyle(pct.map { $0 < 25 } == true ? DS.Palette.danger : Color.primary)
                    Text("buy conv.").font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
    }
}

/// A finding, tappable into its ladder stepper (`_FindingCard`).
private struct LearningFindingCard: View {
    let finding: LearningFinding

    @State private var open = false

    var body: some View {
        let f = finding
        Button {
            withAnimation(.snappy) { open.toggle() }
        } label: {
            Card(padding: 14) {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        AppBadge(label: f.severity, color: LearningModel.severityColor(f.severity))
                        Text(f.target).font(.caption2).foregroundStyle(.secondary)
                        Spacer()
                        Image(systemName: Symbol.named(open ? "expand_less" : "expand_more"))
                            .font(.footnote)
                            .foregroundStyle(.tertiary)
                    }
                    Text(f.title).font(.subheadline.weight(.semibold)).foregroundStyle(.primary)
                    Text(f.detail).font(.footnote).foregroundStyle(.primary)
                    if open {
                        Divider().padding(.vertical, 6)
                        step("Detected", "\(f.kind) · run \(f.runId.isEmpty ? "—" : f.runId)", DS.Palette.danger, reached: true)
                        if !f.evidence.isEmpty {
                            Text(f.evidence.entries.map { "\($0.key): \($0.value.dartDescription)" }.joined(separator: "\n"))
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
                                .padding(.leading, 20)
                                .padding(.bottom, 12)
                        }
                        ForEach(LearningModel.ladder, id: \.name) { rung in
                            step(rung.name, rung.detail, Color(uiColor: .tertiaryLabel), reached: false)
                        }
                    }
                }
                .multilineTextAlignment(.leading)
            }
        }
        .buttonStyle(.plain)
        .accessibilityHint(open ? "Collapses the ladder" : "Shows the ladder")
    }

    private func step(_ label: String, _ detail: String, _ color: Color, reached: Bool) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(color).frame(width: 10, height: 10).padding(.top, 4)
            VStack(alignment: .leading, spacing: 0) {
                Text(label).font(.caption.weight(.semibold)).foregroundStyle(reached ? Color.primary : Color.secondary)
                Text(detail).font(.caption2).foregroundStyle(.tertiary)
                if !reached {
                    Text("not reached — the subsystem observes only").font(.caption2).foregroundStyle(.tertiary)
                }
            }
        }
        .padding(.bottom, 12)
    }
}

/// Which strategy documents the subsystem may write to and which instances it
/// watches (`_TargetsSheet`). The two lists mean opposite things when empty,
/// and each says so.
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
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
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
                            Text("\(inst.kind) · doc #\(inst.strategyId ?? "—")").font(.caption2).foregroundStyle(.tertiary)
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
            }
            .safeAreaInset(edge: .bottom) {
                Button {
                    Task { await save() }
                } label: {
                    Text(saving ? "Saving…" : "Save").fontWeight(.semibold).frame(maxWidth: .infinity)
                }
                .dsProminentButton()
                .controlSize(.large)
                .disabled(saving)
                .padding(.horizontal, 20)
                .padding(.vertical, 8)
                .background(.bar)
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
                    .foregroundStyle(on ? AnyShapeStyle(.tint) : AnyShapeStyle(.tertiary))
            }
        }
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
            if !marketsIsCancellation(error) { self.error = KalshiFormat.errorText(error) }
            saving = false
        }
    }
}
