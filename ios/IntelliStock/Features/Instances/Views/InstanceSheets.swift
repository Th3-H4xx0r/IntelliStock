import SwiftUI

// The instances screens' modal sheets: new instance, add stock, link
// brokerage/strategy, create backtest, and clear data. Each is the web's
// modal (`ui-modal`) as a sheet: a header (title, optional subline, close),
// labelled fields, and a footer with Cancel and the action (busy while in
// flight), like the trade ticket.

/// The shared sheet chrome (`ui-modal-head` / `ui-modal-foot`): the title,
/// an optional subline and a close button over the fields, Cancel and the
/// submit under them. Cancel and close are disabled while busy.
private struct InstanceFormSheet<Content: View>: View {
    let title: String
    var subtitle: String?
    let submitLabel: String
    var busyLabel: String?
    var destructive = false
    var submitDisabled = false
    let busy: Bool
    /// Close, Cancel and the swipe-down stay disabled; `busy` by default.
    var locked: Bool?
    let onSubmit: () -> Void
    @ViewBuilder let content: () -> Content

    @Environment(\.dismiss) private var dismiss

    private var isLocked: Bool { locked ?? busy }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    content()
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 24)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(DS.Surface.card)
            .safeAreaInset(edge: .top, spacing: 0) { header }
            .safeAreaInset(edge: .bottom, spacing: 0) { footer }
            .toolbar(.hidden, for: .navigationBar)
            .navigationTitle(title)
            .interactiveDismissDisabled(isLocked)
        }
        .presentationBackground(DS.Surface.card)
        .presentationDragIndicator(.visible)
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(DS.Font.sectionTitle)
                    .foregroundStyle(DS.Text.primary)
                    .accessibilityAddTraits(.isHeader)
                if let subtitle {
                    Text(subtitle)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Button { dismiss() } label: {
                Image(systemName: "xmark")
            }
            .buttonStyle(.dsIcon)
            .disabled(isLocked)
            .accessibilityLabel("Close")
        }
        .padding(.horizontal, 24)
        .padding(.top, 20)
        .padding(.bottom, 16)
        .background(DS.Surface.card)
    }

    private var footer: some View {
        HStack(spacing: 8) {
            Spacer()
            Button("Cancel") { dismiss() }
                .buttonStyle(.dsGhost(size: .medium))
                .disabled(isLocked)
            if destructive {
                Button(role: .destructive, action: onSubmit) { submitText }
                    .buttonStyle(.dsSecondary(size: .medium))
                    .disabled(busy || submitDisabled)
            } else {
                Button(action: onSubmit) { submitText }
                    .buttonStyle(.dsPrimary)
                    .disabled(busy || submitDisabled)
            }
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 16)
        .background {
            DS.Surface.card
                .overlay(alignment: .top) { DSDivider() }
                .ignoresSafeArea(edges: .bottom)
        }
    }

    @ViewBuilder
    private var submitText: some View {
        if busy {
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.mini)
                if let busyLabel { Text(busyLabel) }
            }
        } else {
            Text(submitLabel)
        }
    }
}

/// A field label with a quieter note after it: "Instance ID *",
/// "Brokerage (optional)" (the web's `ui-field-label` with a zinc-500 span).
private func instanceFieldLabel(_ label: String, note: String? = nil) -> some View {
    var text = Text(label).foregroundStyle(DS.Text.label)
    if let note { text = text + Text(" \(note)").foregroundStyle(DS.Text.muted) }
    return text.font(DS.Font.body)
}

/// A labelled field whose label carries a note (`instanceFieldLabel`).
private struct InstanceField<Content: View>: View {
    let label: String
    var note: String?
    var help: String?
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            instanceFieldLabel(label, note: note)
            content()
            if let help {
                Text(help)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// A pick-one field in the web input's look: the chosen item (or the
/// placeholder in zinc-600) with a chevron, opening the system menu.
private struct InstanceSelectField: View {
    let placeholder: String
    let items: [JSONObject]
    @Binding var selection: String
    let label: (JSONObject) -> String

    var body: some View {
        let current = items.first { instanceSelectorId($0) == selection }.map(label)
        Menu {
            Picker(placeholder, selection: $selection) {
                Text(placeholder).tag("")
                ForEach(items.indices, id: \.self) { i in
                    Text(label(items[i])).tag(instanceSelectorId(items[i]))
                }
            }
        } label: {
            HStack(spacing: 8) {
                Text(current ?? placeholder)
                    .foregroundStyle(current == nil ? DS.Text.faint : DS.Text.soft)
                    .lineLimit(1)
                Spacer(minLength: 8)
                Image(systemName: "chevron.up.chevron.down")
                    .font(.system(size: 12))
                    .foregroundStyle(DS.Text.muted)
            }
            .contentShape(Rectangle())
            .dsInput()
        }
        .accessibilityValue(current ?? placeholder)
    }
}

/// `brokeragesProvider` / `strategiesProvider` as sheet-local loads.
private enum InstanceSelectors {
    static func brokerages(_ services: AppServices) async -> Loadable<[JSONObject]> {
        await Loadable.capture { try await services.instanceRepository.listBrokerages() }
    }

    static func strategies(_ services: AppServices) async -> Loadable<[JSONObject]> {
        await Loadable.capture { try await services.instanceRepository.listStrategies() }
    }
}

/// The message Dart's `e.toString()` printed.
private func instanceErrorText(_ error: any Error) -> String { swingErrorText(error) }

/// The granularity choice (`ui-seg`, full width).
private struct InstanceGranularityField: View {
    @Binding var granularity: String

    var body: some View {
        DSField("Granularity") {
            DSSegmentedControl(
                "Granularity",
                selection: $granularity,
                options: instanceGranularities.map(\.value),
                fullWidth: true,
                segmentHeight: 32
            ) { value in
                instanceGranularities.first { $0.value == value }?.label ?? value
            }
        }
    }
}

// MARK: - New instance

struct InstanceCreateSheet: View {
    let model: InstancesModel

    @Environment(AppServices.self) private var services
    @Environment(\.dismiss) private var dismiss
    @State private var id = ""
    @State private var name = ""
    @State private var maxUsage = ""
    @State private var granularity = "60"
    @State private var runCommand = false
    @State private var brokerageId = ""
    @State private var strategyId = ""
    @State private var busy = false
    @State private var error: String?
    @State private var brokerages: Loadable<[JSONObject]> = .loading
    @State private var strategies: Loadable<[JSONObject]> = .loading

    var body: some View {
        InstanceFormSheet(title: "New instance", submitLabel: "Create", busyLabel: "Creating…", busy: busy, onSubmit: submit) {
            InstanceField(label: "Instance ID", note: "*", help: "Unique identifier for this instance") {
                TextField(text: $id, prompt: Text("my-instance").foregroundStyle(DS.Text.faint)) { Text("Instance ID") }
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .dsInput()
            }
            InstanceField(label: "Display name", note: "(optional)") {
                DSTextField("Optional display name", text: $name)
            }
            InstanceGranularityField(granularity: $granularity)
            Toggle(isOn: $runCommand) {
                Text("Start after creation")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.label)
            }
            InstanceField(label: "Brokerage", note: "(optional)") {
                selector(brokerages, failure: "Couldn't load brokerages", selection: $brokerageId, label: instanceBrokerageLabel)
            }
            InstanceField(label: "Max usage", note: "($, optional)", help: "Maximum capital this instance can use from the brokerage") {
                TextField(text: $maxUsage, prompt: Text("e.g. 1000").foregroundStyle(DS.Text.faint)) { Text("Max usage") }
                    .keyboardType(.decimalPad)
                    .dsInput(prefix: "$")
            }
            InstanceField(label: "Strategy", note: "(optional)") {
                selector(strategies, failure: "Couldn't load strategies", selection: $strategyId, label: instanceStrategyLabel)
            }
            if let error {
                ErrorRow(message: error)
            }
        }
        .task {
            async let b = InstanceSelectors.brokerages(services)
            async let s = InstanceSelectors.strategies(services)
            (brokerages, strategies) = await (b, s)
        }
    }

    @ViewBuilder
    private func selector(
        _ list: Loadable<[JSONObject]>,
        failure: String,
        selection: Binding<String>,
        label: @escaping (JSONObject) -> String
    ) -> some View {
        switch list {
        case .loading:
            Skeleton(height: 36, radius: DS.Radius.control)
                .accessibilityLabel("Loading")
        case .failed:
            Text(failure)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Palette.loss)
        case .loaded(let items):
            InstanceSelectField(placeholder: "None", items: items, selection: selection, label: label)
        }
    }

    private func submit() {
        let trimmedId = id.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmedId.isEmpty {
            error = "Instance ID is required"
            return
        }
        busy = true
        error = nil
        Task {
            do {
                try await model.createInstance(
                    id: trimmedId,
                    name: name.trimmingCharacters(in: .whitespacesAndNewlines),
                    granularity: granularity,
                    runCommand: runCommand,
                    brokerageId: brokerageId.isEmpty ? nil : brokerageId,
                    maxUsage: maxUsage.isEmpty ? nil : JSON.parseDouble(maxUsage),
                    strategyId: strategyId.isEmpty ? nil : strategyId
                )
                dismiss()
            } catch {
                busy = false
                if !error.isCancellationOrTaskCancelled { self.error = instanceErrorText(error) }
            }
        }
    }
}

// MARK: - Add stock

/// `_AddStockSheet` / `_AddStockDetailSheet`: one upper-cased symbol.
struct InstanceAddStockSheet: View {
    let onAdd: (String) async throws -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var symbol = ""
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        InstanceFormSheet(title: "Add stock", submitLabel: "Add", busy: busy, onSubmit: submit) {
            InstanceField(label: "Ticker symbol", note: "*") {
                TextField(text: $symbol, prompt: Text("e.g. AAPL").foregroundStyle(DS.Text.faint)) { Text("Ticker symbol") }
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .onSubmit(submit)
                    .dsInput()
            }
            if let error {
                ErrorRow(message: error)
            }
        }
        .presentationDetents([.medium])
    }

    private func submit() {
        let sym = symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        if sym.isEmpty {
            error = "Symbol is required"
            return
        }
        busy = true
        error = nil
        Task {
            do {
                try await onAdd(sym)
                dismiss()
            } catch {
                busy = false
                if !error.isCancellationOrTaskCancelled { self.error = instanceErrorText(error) }
            }
        }
    }
}

// MARK: - Link brokerage / strategy

/// `_LinkBrokerageSheet` and `_LinkStrategyDetailSheet`.
struct InstanceLinkSheet: View {
    enum Kind {
        case brokerage, strategy
    }

    let kind: Kind
    var currentId: String?
    let onLink: (String) async throws -> Void

    @Environment(AppServices.self) private var services
    @Environment(\.dismiss) private var dismiss
    @State private var selection = ""
    @State private var busy = false
    @State private var error: String?
    @State private var items: Loadable<[JSONObject]> = .loading

    var body: some View {
        InstanceFormSheet(
            title: kind == .brokerage ? (currentId == nil ? "Link brokerage" : "Change brokerage") : "Link strategy",
            submitLabel: "Link",
            busy: busy,
            onSubmit: submit
        ) {
            InstanceField(label: kind == .brokerage ? "Brokerage" : "Strategy") {
                picker
            }
            if let error {
                ErrorRow(message: error)
            }
        }
        .presentationDetents([.medium])
        .task {
            if selection.isEmpty, let currentId { selection = currentId }
            items = kind == .brokerage
                ? await InstanceSelectors.brokerages(services)
                : await InstanceSelectors.strategies(services)
        }
    }

    @ViewBuilder
    private var picker: some View {
        switch items {
        case .loading:
            Skeleton(height: 36, radius: DS.Radius.control)
                .accessibilityLabel("Loading")
        case .failed:
            ErrorRow(message: items.errorMessage ?? "")
        case .loaded(let list):
            InstanceSelectField(
                placeholder: kind == .brokerage ? "Select a brokerage" : "Select a strategy",
                items: list,
                selection: $selection,
                label: itemLabel
            )
        }
    }

    private func itemLabel(_ item: JSONObject) -> String {
        kind == .brokerage ? instanceBrokerageLabel(item) : instanceStrategyLabel(item)
    }

    private func submit() {
        if selection.isEmpty {
            error = kind == .brokerage ? "Select a brokerage" : "Select a strategy"
            return
        }
        busy = true
        error = nil
        Task {
            do {
                try await onLink(selection)
                dismiss()
            } catch {
                busy = false
                if !error.isCancellationOrTaskCancelled { self.error = instanceErrorText(error) }
            }
        }
    }
}

// MARK: - Create backtest

/// `_CreateBacktestDetailSheet`.
struct InstanceCreateBacktestSheet: View {
    let model: InstanceDetailModel

    @Environment(\.dismiss) private var dismiss
    @State private var stocks = ""
    @State private var start = ""
    @State private var end = ""
    @State private var cash = "100000"
    @State private var granularity = "60"
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        InstanceFormSheet(title: "Create backtest", submitLabel: "Create", busyLabel: "Creating…", busy: busy, onSubmit: submit) {
            InstanceField(label: "Stocks", note: "*", help: "Comma-separated ticker symbols") {
                TextField(text: $stocks, prompt: Text("AAPL, TSLA").foregroundStyle(DS.Text.faint)) { Text("Stocks") }
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .dsInput()
            }
            HStack(alignment: .top, spacing: 12) {
                InstanceField(label: "Start date", note: "*") {
                    TextField(text: $start, prompt: Text("YYYY-MM-DD").foregroundStyle(DS.Text.faint)) { Text("Start date") }
                        .keyboardType(.numbersAndPunctuation)
                        .monospacedDigit()
                        .dsInput()
                }
                InstanceField(label: "End date", note: "*") {
                    TextField(text: $end, prompt: Text("YYYY-MM-DD").foregroundStyle(DS.Text.faint)) { Text("End date") }
                        .keyboardType(.numbersAndPunctuation)
                        .monospacedDigit()
                        .dsInput()
                }
            }
            InstanceGranularityField(granularity: $granularity)
            InstanceField(label: "Initial cash", note: "($)") {
                TextField(text: $cash, prompt: Text("100000").foregroundStyle(DS.Text.faint)) { Text("Initial cash") }
                    .keyboardType(.decimalPad)
                    .monospacedDigit()
                    .dsInput(prefix: "$")
            }
            if let error {
                ErrorRow(message: error)
            }
        }
    }

    private func submit() {
        if let message = InstanceBacktestForm.validate(start: start, end: end) {
            error = message
            return
        }
        busy = true
        error = nil
        Task {
            do {
                try await model.createBacktest(
                    stocks: InstanceBacktestForm.stocks(stocks),
                    startDate: start,
                    endDate: end,
                    granularity: granularity,
                    initialCash: InstanceBacktestForm.cash(cash)
                )
                dismiss()
            } catch {
                busy = false
                if !error.isCancellationOrTaskCancelled { self.error = instanceErrorText(error) }
            }
        }
    }
}

// MARK: - Clear data

/// `_ClearStateSheet` (the web's "Clear instance data"): pick a scope,
/// preview the dry run, type the instance id, then Confirm and clear.
/// Destructive: the confirm stays disabled until the typed id matches.
struct InstanceClearStateSheet: View {
    let instanceId: String
    let model: InstanceDetailModel

    @State private var scope = "lookback_only"
    @State private var preview: JSONObject?
    @State private var previewing = false
    @State private var applying = false
    @State private var confirmed = false
    @State private var error: String?
    @State private var success: String?

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var locked: Bool { applying || previewing }

    var body: some View {
        InstanceFormSheet(
            title: "Clear instance data",
            subtitle: "Destructive · scoped to instance \(instanceId)",
            submitLabel: "Confirm and clear",
            busyLabel: "Clearing…",
            destructive: true,
            submitDisabled: preview == nil || !confirmed || previewing,
            busy: applying,
            locked: locked,
            onSubmit: apply
        ) {
            VStack(alignment: .leading, spacing: 8) {
                instanceFieldLabel("Scope")
                ForEach(InstanceClearScope.all) { opt in
                    scopeOption(opt)
                }
            }
            HStack(spacing: 12) {
                Button {
                    runPreview()
                } label: {
                    HStack(spacing: 8) {
                        if previewing { ProgressView().controlSize(.mini) }
                        Text(previewing ? "Counting…" : (preview == nil ? "Preview (dry run)" : "Refresh preview"))
                    }
                }
                .buttonStyle(.dsSecondary)
                .disabled(locked)
                if let preview {
                    Text(InstanceClearScope.previewTotal(preview))
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                        .monospacedDigit()
                }
            }
            if let preview {
                let tables = Array((preview["tables"]?.array ?? []).prefix(10))
                if !tables.isEmpty {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(tables.enumerated()), id: \.offset) { index, t in
                            if index > 0 { DSDivider(row: true) }
                            Text(t.dartDescription)
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.label)
                                .monospacedDigit()
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 16)
                                .padding(.vertical, 10)
                        }
                    }
                    .dsCardSurface(fill: DS.Surface.page, radius: DS.Radius.control)
                }
                TypedConfirmField(
                    phrase: instanceId,
                    label: "To confirm, type the instance id \(instanceId) below"
                ) { confirmed = $0 }
            }
            if let error {
                ErrorRow(message: error)
            }
            if let success {
                Text(success)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Palette.gain)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(DS.Palette.gain.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
                    .overlay {
                        RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                            .strokeBorder(DS.Palette.gain.opacity(DS.tintBorder), lineWidth: DS.Line.width)
                    }
            }
        }
        // A preview speaks for one scope: picking another drops it (and
        // the typed confirmation), so Confirm can never clear a scope that
        // was not previewed.
        .onChange(of: scope) {
            preview = nil
            confirmed = false
            success = nil
        }
    }

    /// One scope as the web's radio card: the label and its blurb, raised
    /// with a brighter border when chosen.
    private func scopeOption(_ opt: InstanceClearScope) -> some View {
        let selected = scope == opt.value
        return Button {
            withAnimation(DSMotion.standard(reduceMotion: reduceMotion)) { scope = opt.value }
        } label: {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                    .font(.system(size: 16))
                    .foregroundStyle(selected ? DS.Palette.accent : DS.Text.faint)
                    .padding(.top, 1)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(opt.label)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.soft)
                    Text(opt.blurb)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(selected ? DS.Surface.raised : Color.clear, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                    .strokeBorder(selected ? Color.white.opacity(0.16) : DS.Line.card, lineWidth: DS.Line.width)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(locked)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func runPreview() {
        previewing = true
        error = nil
        preview = nil
        success = nil
        confirmed = false
        Task {
            do {
                preview = try await model.previewClearState(scope)
            } catch {
                if !error.isCancellationOrTaskCancelled { self.error = "Preview failed: \(instanceErrorText(error))" }
            }
            previewing = false
        }
    }

    private func apply() {
        guard confirmed else { return }
        applying = true
        error = nil
        success = nil
        Task {
            do {
                let result = try await model.applyClearState(scope)
                success = InstanceClearScope.successMessage(result)
            } catch {
                if !error.isCancellationOrTaskCancelled { self.error = "Clear failed: \(instanceErrorText(error))" }
            }
            applying = false
        }
    }
}
