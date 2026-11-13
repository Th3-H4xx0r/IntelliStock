import SwiftUI

// The Nexus control modals (nexus_screen.dart's `_StartModal`,
// `_AutoUpdateModal`, `_RebuildModal`, `_DeleteModal`) as sheets drawn like
// the web's modals (`ModalSheet`: head, body, foot), in the web's words.
// Each closes before its request, as the Dart dialogs did; the screen's busy
// flag covers the request.

private func phaseOptions(_ c: NexusControl) -> [NexusPhaseOption] {
    c.phaseOptions.isEmpty ? kFallbackPhaseOptions : c.phaseOptions
}

/// The footer's Cancel and the sheet's action: the primary button, or a
/// secondary one in loss red for a destructive action (the web's
/// `ui-btn text-loss`).
private struct NexusSheetActions: View {
    let confirmLabel: String
    var destructive = false
    let confirmEnabled: Bool
    var cancelLabel = "Cancel"
    let onConfirm: (() -> Void)?

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Button(cancelLabel) { dismiss() }
            .buttonStyle(.dsGhost(size: .medium))
        if let onConfirm {
            if destructive {
                Button(confirmLabel, role: .destructive) {
                    dismiss()
                    onConfirm()
                }
                .buttonStyle(.dsSecondary(size: .medium))
                .disabled(!confirmEnabled)
            } else {
                Button(confirmLabel) {
                    dismiss()
                    onConfirm()
                }
                .buttonStyle(.dsPrimary)
                .disabled(!confirmEnabled)
            }
        }
    }
}

/// A phase checklist (the web's "Phase selection"): the label and its help
/// with Select all / Deselect all, the checkbox rows, and an error when
/// nothing is checked.
private struct NexusPhaseChecklist: View {
    let options: [NexusPhaseOption]
    @Binding var selected: [Int]
    var help = "Only the checked phases will run for this manual start"
    var emptyError = "Select at least one phase to start Nexus"

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .bottom, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Phase selection")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.label)
                    Text(help)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack(spacing: 4) {
                Button("Select all") { selected = options.map(\.value) }
                Button("Deselect all") { selected = [] }
            }
            .buttonStyle(.dsGhost)
            .padding(.leading, -12)
            OptionList {
                ForEach(options, id: \.value) { opt in
                    CheckboxRow(isOn: selected.contains(opt.value), action: { toggle(opt.value) }) {
                        Text(opt.label)
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.soft)
                    }
                }
            }
            if selected.isEmpty {
                Text(emptyError)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Palette.loss)
            }
        }
    }

    private func toggle(_ value: Int) {
        if selected.contains(value) { selected.removeAll { $0 == value } } else { selected.append(value) }
    }
}

/// A switch with its title and a quieter line under it (the web's checkbox
/// labels), in a hairline box when `boxed`.
private struct NexusSwitchRow: View {
    let title: String
    let detail: String
    @Binding var isOn: Bool

    var body: some View {
        Toggle(isOn: $isOn) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.soft)
                Text(detail)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
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
        let invalidDate = historicalMode && historicalStart.isEmpty
        ModalSheet(title, subtitle: "Choose the phases to run for this manual execution") {
            NexusPhaseChecklist(options: phaseOptions(status.control), selected: $selected)
            DSField(
                "13F history (quarters)",
                help: "Larger values load more quarterly 13F history for backtests and make Phase 7 slower"
            ) {
                DSTextField("1", text: $quarters)
                    .keyboardType(.numberPad)
            }
            OptionList {
                VStack(alignment: .leading, spacing: 16) {
                    NexusSwitchRow(
                        title: "Historical bootstrap",
                        detail: "Backfill supported temporal phases from a chosen start date through today",
                        isOn: $historicalMode
                    )
                    if historicalMode {
                        DSField(
                            "Historical start date",
                            help: "Phase 3, Phase 7 and Phase 11 use this date to build historical intervals"
                        ) {
                            DSTextField("YYYY-MM-DD", text: $historicalStart)
                                .keyboardType(.numbersAndPunctuation)
                        }
                        if invalidDate {
                            Text("Historical bootstrap needs a valid start date")
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Palette.loss)
                        }
                    }
                }
                .padding(16)
                NexusSwitchRow(
                    title: "Force bootstrap rebuild",
                    detail: "Reset historical bootstrap coverage and rebuild the temporal backfill from the start date for this manual run",
                    isOn: $forceBootstrap
                )
                .padding(16)
            }
            Text("Phase selection only affects this manual run. Auto-update scheduling still uses its own start and end phases")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
        } footer: {
            NexusSheetActions(
                confirmLabel: title,
                confirmEnabled: !selected.isEmpty && !invalidDate,
                onConfirm: submit
            )
        }
        .presentationDetents([.large])
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
        ModalSheet("Nexus auto-update", subtitle: "Keep Nexus online and rerun a chosen phase range on a schedule") {
            NexusSwitchRow(
                title: "Enable auto-update",
                detail: "Nexus will stay running and queue the next refresh automatically",
                isOn: $enabled
            )
            DSField("Interval (hours)") {
                DSTextField("168", text: $hours)
                    .keyboardType(.numberPad)
            }
            phasePicker("From phase", $startPhase, options)
            phasePicker("To phase", $endPhase, options)
            VStack(alignment: .leading, spacing: 4) {
                Text("Historical bootstrap coverage")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.soft)
                Text("Scheduled auto-updates reuse existing bootstrap coverage and fetch only newer source windows")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
                Text("Current coverage: \(NexusFormat.coverageSummary(status))")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
                    .monospacedDigit()
                    .padding(.top, 4)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .dsCardSurface(radius: DS.Radius.control)
        } footer: {
            NexusSheetActions(confirmLabel: "Save schedule", confirmEnabled: true, onConfirm: save)
        }
        .presentationDetents([.large])
    }

    /// `_PhaseDropdown` as the web's select: a value missing from the
    /// options shows the first.
    private func phasePicker(_ label: String, _ value: Binding<Int>, _ options: [NexusPhaseOption]) -> some View {
        let safe = options.isEmpty || options.contains { $0.value == value.wrappedValue } ? value.wrappedValue : options[0].value
        let current = options.first { $0.value == safe }?.label ?? "—"
        return DSField(label) {
            Menu {
                Picker(label, selection: Binding(get: { safe }, set: { value.wrappedValue = $0 })) {
                    ForEach(options, id: \.value) { Text($0.label).tag($0.value) }
                }
            } label: {
                HStack(spacing: 8) {
                    Text(current)
                        .foregroundStyle(DS.Text.soft)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.system(size: 12))
                        .foregroundStyle(DS.Text.muted)
                }
                .contentShape(Rectangle())
                .dsInput()
            }
            .accessibilityLabel(label)
            .accessibilityValue(current)
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
        ModalSheet(
            "Full rebuild",
            subtitle: destructive
                ? "Destructive mode clears Neo4j data and Nexus progress before restarting from phase 1"
                : "In-place mode reruns from phase 1, optionally clears selected cache entries, and keeps the existing graph online"
        ) {
            VStack(alignment: .leading, spacing: 12) {
                Text("Rebuild mode")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.label)
                modeCard(
                    "In-place rebuild",
                    "Recommended. Keeps current graph data online while Nexus reruns and swaps in refreshed intervals",
                    selected: !destructive,
                    danger: false
                ) { destructive = false }
                modeCard(
                    "Destructive rebuild",
                    "Clears Neo4j graph data and resets Nexus progress before phase 1 starts again",
                    selected: destructive,
                    danger: true
                ) { destructive = true }
            }
            Text(destructive
                 ? "This will take Nexus graph data offline until the rebuild repopulates Neo4j. Use this only when you want the legacy wipe-and-rebuild behavior"
                 : "Nexus will rerun from phase 1 and preserve historical edge intervals so strategies can keep reading the current graph while the refresh is in progress")
                .font(DS.Font.body)
                .foregroundStyle(destructive ? DS.Palette.loss : DS.Text.secondary)
                .fixedSize(horizontal: false, vertical: true)
            NexusSwitchRow(
                title: "Force bootstrap rebuild",
                detail: "Also clear historical bootstrap checkpoints so temporal phases rebuild from scratch",
                isOn: $forceBootstrap
            )
            .padding(16)
            .dsCardSurface(radius: DS.Radius.control)
            cacheBox
            TypedConfirmField(phrase: "confirm", label: "Type confirm to proceed") { confirmMatch = $0 }
        } footer: {
            NexusSheetActions(
                confirmLabel: destructive ? "Confirm destructive rebuild" : "Confirm rebuild",
                destructive: destructive,
                confirmEnabled: confirmMatch,
                onConfirm: submit
            )
        }
        .presentationDetents([.large])
        .task {
            cacheInfo = await model.fetchCache()
            cacheLoading = false
        }
    }

    /// A mode choice (the web's radio cards): raised when selected, with a
    /// loss-tinted hairline for the destructive one.
    private func modeCard(_ title: String, _ detail: String, selected: Bool, danger: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                    .font(.system(size: 16))
                    .foregroundStyle(selected ? DS.Text.soft : DS.Text.faint)
                    .padding(.top, 1)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.primary)
                    Text(detail)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(16)
            .dsCardSurface(
                fill: selected ? DS.Surface.raised : DS.Surface.card,
                radius: DS.Radius.control,
                border: selected ? (danger ? DS.Palette.loss.opacity(0.3) : Color.white.opacity(0.16)) : DS.Line.card
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    /// The optional cache cleanup (the web's box): the entries as checkbox
    /// rows with Select all / Clear selection.
    private var cacheBox: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Optional cache cleanup")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.soft)
                    Text("Selected entries are deleted before the rebuild starts. If the Nexus container is running, it is restarted afterward")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if let info = cacheInfo, info.available, !info.entries.isEmpty {
                    let all = selectedPaths.count == info.entries.count
                    Button(all ? "Clear selection" : "Select all") {
                        selectedPaths = all ? [] : info.entries.map(\.path)
                    }
                    .buttonStyle(.dsSecondary)
                }
            }
            if cacheLoading {
                Text("Loading Nexus cache entries…")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
            } else if let info = cacheInfo, info.available, !info.entries.isEmpty {
                OptionList {
                    ForEach(info.entries, id: \.path) { entry in
                        CheckboxRow(isOn: selectedPaths.contains(entry.path), action: { toggle(entry.path) }) {
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Image(systemName: entry.isDir ? "folder" : "doc.text")
                                    .font(.system(size: 14))
                                    .foregroundStyle(DS.Text.muted)
                                    .accessibilityHidden(true)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(entry.path)
                                        .font(DS.Font.body)
                                        .foregroundStyle(DS.Text.soft)
                                    Text(entry.isDir ? "Folder" : "File\(entry.sizeBytes.map { " · \($0) bytes" } ?? "")")
                                        .font(DS.Font.caption)
                                        .foregroundStyle(DS.Text.muted)
                                        .monospacedDigit()
                                }
                            }
                        }
                    }
                }
            } else {
                Text(cacheInfo == nil
                     ? "Cache selection is available when the API can reach the Nexus cache root or the Nexus container is running"
                     : "No cache entries were found")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16)
        .dsCardSurface(radius: DS.Radius.control)
    }

    private func toggle(_ path: String) {
        if selectedPaths.contains(path) { selectedPaths.removeAll { $0 == path } } else { selectedPaths.append(path) }
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
        return ModalSheet(
            inProgress ? "Deleting Nexus edges" : "Delete Nexus edges",
            subtitle: inProgress
                ? (c.deleteOperationMessage ?? "Deleting the selected phases' graph output")
                : "Select the phases whose graph output should be deleted from Neo4j"
        ) {
            if inProgress {
                progress(c)
            } else {
                NexusPhaseChecklist(
                    options: options,
                    selected: $selected,
                    help: "Only the checked phases will be deleted",
                    emptyError: "Select at least one phase to delete"
                )
            }
        } footer: {
            NexusSheetActions(
                confirmLabel: "Delete selected edges",
                destructive: true,
                confirmEnabled: !selected.isEmpty,
                cancelLabel: inProgress ? "Close" : "Cancel",
                onConfirm: confirm
            )
        }
        .presentationDetents([.large])
    }

    private func submit() {
        guard !selected.isEmpty else { return }
        Task { await model.deleteEdges(["selected_phases": .array(selected.sorted().map(JSON.int))]) }
    }

    @ViewBuilder
    private func progress(_ c: NexusControl) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Overall progress")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text("\(c.deleteOperationCurrent?.int ?? 0)")
                    .font(DS.Font.figureLarge)
                    .foregroundStyle(DS.Text.primary)
                Text("/ \(c.deleteOperationTotal?.int ?? 0) \(c.deleteOperationUnit ?? "phases")")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
            }
            .monospacedDigit()
            if let step = c.deleteOperationStep {
                Text(step)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
                    .padding(.top, 4)
            }
            if let err = c.deleteOperationError {
                Text(err)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Palette.loss)
                    .padding(.top, 4)
            }
        }
        OptionList {
            ForEach(Array(c.deleteOperationPhaseRows.enumerated()), id: \.offset) { _, row in
                deleteRow(row)
            }
        }
    }

    private func deleteRow(_ row: JSONObject) -> some View {
        let rowStatus = KalshiFormat.firstNonNull(row["status"], .string("pending"))
        let tone: DSStatusTone = switch rowStatus {
        case "failed": .error
        case "completed": .neutral
        case "running": .live
        default: .off
        }
        let barColor: Color = switch rowStatus {
        case "failed": DS.Palette.loss
        case "completed": DS.Text.label
        case "running": DS.Text.soft
        default: DS.Text.faint
        }
        let pct = row["progress_pct"]?.double ?? 0
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(KalshiPregame.str(row["label"]))
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.primary)
                    Text(KalshiFormat.firstNonNull(row["message"], .string("Queued")))
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                StatusDot(rowStatus.dsSentenceCased, tone: tone, font: DS.Font.caption)
            }
            OpsProgressBar(fraction: pct / 100, color: barColor)
                .padding(.top, 4)
            HStack {
                Text("\(row["current"]?.double.map { Int($0) } ?? 0) / \(row["total"]?.double.map { Int($0) } ?? 0) \(KalshiFormat.firstNonNull(row["unit"], .string("records")))")
                Spacer()
                Text("\(row["deleted_count"]?.double.map { Int($0) } ?? 0) deleted")
            }
            .font(DS.Font.caption)
            .foregroundStyle(DS.Text.muted)
            .monospacedDigit()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .accessibilityElement(children: .combine)
    }
}
