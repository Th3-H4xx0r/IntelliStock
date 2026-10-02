import SwiftUI

// The Nexus control modals (nexus_screen.dart's `_StartModal`,
// `_AutoUpdateModal`, `_RebuildModal`, `_DeleteModal`) as sheets. Each
// closes before its request, as the Dart dialogs did; the screen's busy flag
// covers the request.

/// A sheet chrome: inline title, Cancel in the toolbar, the explanation as
/// the first section's footer, and the primary action as a full-width
/// prominent button in the form's last section (the action is the sheet's
/// whole purpose, and its labels are too long for a toolbar item).
private struct NexusSheetChrome<Content: View>: View {
    let title: String
    let subtitle: String?
    let confirmLabel: String
    var confirmTint: Color = DS.Palette.success
    let confirmEnabled: Bool
    var cancelLabel = "Cancel"
    let onConfirm: (() -> Void)?
    @ViewBuilder let content: () -> Content

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                if let subtitle {
                    Section {
                    } footer: {
                        Text(subtitle)
                    }
                }
                content()
                if let onConfirm {
                    Section {
                        Button {
                            dismiss()
                            onConfirm()
                        } label: {
                            Text(confirmLabel).fontWeight(.semibold).frame(maxWidth: .infinity)
                        }
                        .dsProminentButton()
                        .tint(confirmTint)
                        .controlSize(.large)
                        .disabled(!confirmEnabled)
                        .listRowBackground(Color.clear)
                        .listRowInsets(EdgeInsets())
                    }
                }
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(cancelLabel) { dismiss() }
                }
            }
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
    }
}

private func phaseOptions(_ c: NexusControl) -> [NexusPhaseOption] {
    c.phaseOptions.isEmpty ? kFallbackPhaseOptions : c.phaseOptions
}

/// A phase checklist with All / None (`Phase Selection`).
private struct NexusPhaseChecklist: View {
    let options: [NexusPhaseOption]
    @Binding var selected: [Int]

    var body: some View {
        Section {
            ForEach(options, id: \.value) { opt in
                Toggle(opt.label, isOn: Binding(
                    get: { selected.contains(opt.value) },
                    set: { on in
                        if on { selected.append(opt.value) } else { selected.removeAll { $0 == opt.value } }
                    }
                ))
                .font(.footnote)
                .toggleStyle(NexusCheckboxStyle())
            }
        } header: {
            HStack {
                Text("Phase Selection")
                Spacer()
                Button("All") { selected = options.map(\.value) }
                Button("None") { selected = [] }
            }
            .buttonStyle(.borderless)
        } footer: {
            if selected.isEmpty {
                Text("Select at least one phase.").foregroundStyle(DS.Palette.danger)
            }
        }
    }
}

/// A checkmark row toggle (Flutter's `CheckboxListTile`).
private struct NexusCheckboxStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        Button {
            configuration.isOn.toggle()
        } label: {
            HStack {
                configuration.label.foregroundStyle(.primary)
                Spacer()
                Image(systemName: configuration.isOn ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(configuration.isOn ? AnyShapeStyle(DS.Palette.accent) : AnyShapeStyle(.tertiary))
            }
        }
        .tint(.primary)
        .accessibilityAddTraits(configuration.isOn ? .isSelected : [])
    }
}

// MARK: - Start

struct NexusStartSheet: View {
    let status: NexusStatus
    let model: NexusModel

    @State private var selected: [Int]
    @State private var quarters: String
    @State private var historicalMode: Bool
    @State private var historicalStart: String
    @State private var forceBootstrap = false

    init(status: NexusStatus, model: NexusModel) {
        self.status = status
        self.model = model
        let c = status.control
        _selected = State(initialValue: c.selectedPhases.isEmpty ? phaseOptions(c).map(\.value) : c.selectedPhases)
        _quarters = State(initialValue: String(c.phase7HistoryQuarters))
        _historicalMode = State(initialValue: c.historicalModeEnabled)
        _historicalStart = State(initialValue: c.historicalStartDate ?? "")
    }

    var body: some View {
        let title = status.showBuilt && status.serviceRunning ? "Re-run Nexus" : "Start Nexus"
        NexusSheetChrome(
            title: title,
            subtitle: "Choose phases for this manual execution.",
            confirmLabel: title,
            confirmEnabled: !selected.isEmpty && !(historicalMode && historicalStart.isEmpty),
            onConfirm: submit
        ) {
            NexusPhaseChecklist(options: phaseOptions(status.control), selected: $selected)
            Section("13F History (quarters)") {
                TextField("quarters", text: $quarters).keyboardType(.numberPad)
            }
            Section {
                Toggle(isOn: $historicalMode) {
                    Text("Historical bootstrap")
                    Text("Backfill temporal phases from a start date.")
                }
                if historicalMode {
                    TextField("YYYY-MM-DD", text: $historicalStart, prompt: Text("Start date"))
                        .keyboardType(.numbersAndPunctuation)
                }
                Toggle(isOn: $forceBootstrap) {
                    Text("Force bootstrap rebuild")
                    Text("Reset historical bootstrap and rebuild from start date.")
                }
            }
            .tint(DS.Palette.success)
        }
    }

    private func submit() {
        guard !selected.isEmpty, !(historicalMode && historicalStart.isEmpty) else { return }
        let body = NexusFormat.startBody(
            selectedPhases: selected,
            historyQuarters: JSON.parseInt(quarters) ?? 1,
            historicalMode: historicalMode,
            historicalStartDate: historicalStart,
            forceBootstrapRebuild: forceBootstrap
        )
        Task { await model.postControl(body) }
    }
}

// MARK: - Auto-update

struct NexusAutoUpdateSheet: View {
    let status: NexusStatus
    let model: NexusModel

    @State private var enabled: Bool
    @State private var hours: String
    @State private var startPhase: Int
    @State private var endPhase: Int

    init(status: NexusStatus, model: NexusModel) {
        self.status = status
        self.model = model
        let c = status.control
        _enabled = State(initialValue: c.autoUpdateEnabled)
        _hours = State(initialValue: String(c.autoUpdateIntervalHours))
        _startPhase = State(initialValue: c.autoUpdateStartPhase)
        _endPhase = State(initialValue: c.autoUpdateEndPhase)
    }

    var body: some View {
        let options = phaseOptions(status.control)
        NexusSheetChrome(
            title: "Nexus Auto-update",
            subtitle: "Keep Nexus online and rerun on a schedule.",
            confirmLabel: "Save Schedule",
            confirmTint: DS.Palette.info,
            confirmEnabled: true,
            onConfirm: save
        ) {
            Section {
                Toggle(isOn: $enabled) {
                    Text("Enable auto-update")
                    Text("Nexus will queue the next refresh automatically.")
                }
                .tint(DS.Palette.info)
            }
            Section("Interval (hours)") {
                TextField("hours", text: $hours).keyboardType(.numberPad)
            }
            Section {
                phasePicker("From phase", $startPhase, options)
                phasePicker("To phase", $endPhase, options)
            }
        }
    }

    /// `_PhaseDropdown`: a value missing from the options shows the first.
    private func phasePicker(_ label: String, _ value: Binding<Int>, _ options: [NexusPhaseOption]) -> some View {
        let safe = options.isEmpty || options.contains { $0.value == value.wrappedValue } ? value.wrappedValue : options[0].value
        return Picker(label, selection: Binding(get: { safe }, set: { value.wrappedValue = $0 })) {
            ForEach(options, id: \.value) { Text($0.label).tag($0.value) }
        }
    }

    private func save() {
        let body = NexusFormat.autoUpdateBody(
            enabled: enabled,
            intervalHours: JSON.parseInt(hours) ?? 168,
            startPhase: startPhase,
            endPhase: endPhase
        )
        Task { await model.postControl(body) }
    }
}

// MARK: - Full rebuild

struct NexusRebuildSheet: View {
    let model: NexusModel

    @State private var destructive = false
    @State private var forceBootstrap = false
    @State private var confirmMatch = false
    @State private var cacheInfo: NexusCacheInfo?
    @State private var cacheLoading = true
    @State private var selectedPaths: [String] = []

    var body: some View {
        NexusSheetChrome(
            title: "Full Rebuild",
            subtitle: destructive ? "Clears Neo4j graph data and resets Nexus progress." : "Reruns from phase 1, keeps existing graph online.",
            confirmLabel: destructive ? "Confirm Destructive Rebuild" : "Confirm Rebuild",
            confirmTint: destructive ? DS.Palette.danger : DS.Palette.warning,
            confirmEnabled: confirmMatch,
            onConfirm: submit
        ) {
            Section("Rebuild mode") {
                modeRow("In-place rebuild", "Recommended. Keeps current graph data online while Nexus reruns.", DS.Palette.success, selected: !destructive) { destructive = false }
                modeRow("Destructive rebuild", "Clears Neo4j graph data and resets Nexus progress before phase 1.", DS.Palette.danger, selected: destructive) { destructive = true }
            }
            Section {
                Toggle(isOn: $forceBootstrap) {
                    Text("Force bootstrap rebuild")
                    Text("Clear historical bootstrap checkpoints so temporal phases rebuild from scratch.")
                }
                .tint(DS.Palette.danger)
            }
            cacheSection
            Section("Type confirm to proceed") {
                TypedConfirmField(phrase: "confirm", label: "confirm") { confirmMatch = $0 }
            }
        }
        .task {
            cacheInfo = await model.fetchCache()
            cacheLoading = false
        }
    }

    /// A mode choice: the title and detail, with a checkmark on the
    /// selected one (Settings style); the colour stays on the mark only.
    private func modeRow(_ title: String, _ detail: String, _ color: Color, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).foregroundStyle(.primary)
                    Text(detail).font(.footnote).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "checkmark")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(color)
                    .opacity(selected ? 1 : 0)
                    .accessibilityHidden(true)
            }
        }
        .tint(.primary)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    @ViewBuilder
    private var cacheSection: some View {
        Section {
            if cacheLoading {
                LoadingState(label: "Loading cache…")
            } else if let info = cacheInfo, info.available, !info.entries.isEmpty {
                ForEach(info.entries, id: \.path) { entry in
                    Toggle(isOn: Binding(
                        get: { selectedPaths.contains(entry.path) },
                        set: { on in
                            if on { selectedPaths.append(entry.path) } else { selectedPaths.removeAll { $0 == entry.path } }
                        }
                    )) {
                        Label {
                            VStack(alignment: .leading, spacing: 0) {
                                Text(entry.path).font(.footnote.monospaced())
                                if let size = entry.sizeBytes {
                                    Text("\(size) bytes").font(.caption2).foregroundStyle(.tertiary)
                                }
                            }
                        } icon: {
                            Image(systemName: entry.isDir ? "folder" : "doc.text")
                                .foregroundStyle(entry.isDir ? DS.Palette.warning : DS.Palette.info)
                        }
                    }
                    .toggleStyle(NexusCheckboxStyle())
                    .tint(DS.Palette.danger)
                }
            } else {
                Text(cacheInfo == nil ? "Cache not accessible from this context." : "No cache entries found.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        } header: {
            HStack {
                Text("Cache cleanup (optional)")
                Spacer()
                if let info = cacheInfo, info.available, !info.entries.isEmpty {
                    Button("Select All") { selectedPaths = info.entries.map(\.path) }
                    Button("Clear") { selectedPaths = [] }
                }
            }
            .buttonStyle(.borderless)
        }
    }

    private func submit() {
        guard confirmMatch else { return }
        let body = NexusFormat.rebuildBody(destructive: destructive, forceBootstrap: forceBootstrap, cachePaths: selectedPaths)
        Task { await model.rebuild(body) }
    }
}

// MARK: - Delete edges

struct NexusDeleteSheet: View {
    let status: NexusStatus
    let model: NexusModel

    @State private var selected: [Int]

    init(status: NexusStatus, model: NexusModel) {
        self.status = status
        self.model = model
        let c = status.control
        let options = c.deletePhaseOptions.isEmpty ? kFallbackDeletePhaseOptions : c.deletePhaseOptions
        _selected = State(initialValue: c.deleteOperationSelectedPhases.isEmpty ? options.map(\.value) : c.deleteOperationSelectedPhases)
    }

    var body: some View {
        let c = model.statusValue?.control ?? status.control
        let options = c.deletePhaseOptions.isEmpty ? kFallbackDeletePhaseOptions : c.deletePhaseOptions
        let inProgress = c.deleteOperationActive
        let confirm: (() -> Void)? = inProgress ? nil : { submit() }
        return NexusSheetChrome(
            title: inProgress ? "Deleting Nexus Edges" : "Delete Nexus Edges",
            subtitle: nil,
            confirmLabel: "Delete Selected Edges",
            confirmTint: DS.Palette.warning,
            confirmEnabled: !selected.isEmpty,
            cancelLabel: inProgress ? "Close" : "Cancel",
            onConfirm: confirm
        ) {
            if inProgress {
                progress(c)
            } else {
                NexusPhaseChecklist(options: options, selected: $selected)
            }
        }
    }

    private func submit() {
        guard !selected.isEmpty else { return }
        Task { await model.deleteEdges(["selected_phases": .array(selected.sorted().map(JSON.int))]) }
    }

    @ViewBuilder
    private func progress(_ c: NexusControl) -> some View {
        Section("Overall Progress") {
            Text("\(c.deleteOperationCurrent?.int ?? 0) / \(c.deleteOperationTotal?.int ?? 0) \(c.deleteOperationUnit ?? "phases")")
                .font(.title2.bold().monospacedDigit())
            if let step = c.deleteOperationStep {
                Text(step).font(.footnote).foregroundStyle(.secondary)
            }
            if let err = c.deleteOperationError {
                Text(err).font(.footnote).foregroundStyle(DS.Palette.danger)
            }
        }
        Section {
            ForEach(Array(c.deleteOperationPhaseRows.enumerated()), id: \.offset) { _, row in
                let rowStatus = KalshiFormat.firstNonNull(row["status"], .string("pending"))
                let color: Color = rowStatus == "completed" ? DS.Palette.success : (rowStatus == "running" ? DS.Palette.warning : (rowStatus == "failed" ? DS.Palette.danger : .secondary))
                let pct = row["progress_pct"]?.double ?? 0
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        VStack(alignment: .leading, spacing: 0) {
                            Text(KalshiPregame.str(row["label"])).font(.subheadline.weight(.semibold))
                            Text(KalshiFormat.firstNonNull(row["message"], .string("Queued"))).font(.footnote).foregroundStyle(.secondary)
                        }
                        Spacer()
                        AppBadge(label: rowStatus, color: color)
                    }
                    ProgressView(value: min(max(pct / 100, 0), 1)).tint(color)
                    HStack {
                        Text("\(row["current"]?.double.map { Int($0) } ?? 0) / \(row["total"]?.double.map { Int($0) } ?? 0) \(KalshiFormat.firstNonNull(row["unit"], .string("records")))")
                        Spacer()
                        Text("\(row["deleted_count"]?.double.map { Int($0) } ?? 0) deleted")
                    }
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                }
            }
        }
    }
}
