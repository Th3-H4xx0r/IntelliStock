import SwiftUI

// The instances screens' modal sheets: create instance, add stock, link
// brokerage/strategy, create backtest, and clear state. Dart's bottom
// sheets become `.sheet`s with a `Form`, a title, Cancel and the submit
// action in the toolbar (busy while in flight).

/// The shared sheet chrome (`_ModalSheet` / `_SimpleSheet`): a titled form,
/// Cancel (disabled while busy) and a prominent submit with a spinner.
private struct InstanceFormSheet<Content: View>: View {
    let title: String
    let submitLabel: String
    let busy: Bool
    let onSubmit: () -> Void
    @ViewBuilder let content: () -> Content

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form { content() }
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { dismiss() }
                            .disabled(busy)
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        if busy {
                            ProgressView()
                        } else {
                            Button(submitLabel, action: onSubmit)
                        }
                    }
                }
                .interactiveDismissDisabled(busy)
        }
        .presentationDragIndicator(.visible)
    }
}

/// The error row inside a sheet (`ErrorBanner`).
private struct InstanceSheetError: View {
    let message: String?

    var body: some View {
        if let message {
            Section {
                ErrorRow(message: message)
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
            }
        }
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

// MARK: - New Instance

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
        InstanceFormSheet(title: "New Instance", submitLabel: "Create", busy: busy, onSubmit: submit) {
            Section {
                LabeledContent("Instance ID *") {
                    TextField("my-instance", text: $id)
                        .multilineTextAlignment(.trailing)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                LabeledContent("Name") {
                    TextField("Optional display name", text: $name)
                        .multilineTextAlignment(.trailing)
                }
            }
            Section("Granularity") {
                Picker("Granularity", selection: $granularity) {
                    ForEach(instanceGranularities, id: \.value) { Text($0.label).tag($0.value) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                Toggle("Start after creation", isOn: $runCommand)
            }
            Section("Brokerage (optional)") {
                selector(brokerages, failure: "Failed to load brokerages", selection: $brokerageId, label: instanceBrokerageLabel)
            }
            Section {
                LabeledContent("Max Usage ($)") {
                    TextField("e.g. 1000", text: $maxUsage)
                        .keyboardType(.decimalPad)
                        .multilineTextAlignment(.trailing)
                }
            }
            Section("Strategy (optional)") {
                selector(strategies, failure: "Failed to load strategies", selection: $strategyId, label: instanceStrategyLabel)
            }
            InstanceSheetError(message: error)
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
            LoadingState()
        case .failed:
            Text(failure)
                .font(.footnote)
                .foregroundStyle(DS.Palette.danger)
        case .loaded(let items):
            Picker("Select", selection: selection) {
                Text("— None —").tag("")
                ForEach(items.indices, id: \.self) { i in
                    Text(label(items[i])).tag(instanceSelectorId(items[i]))
                }
            }
            .labelsHidden()
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
                if !tradingIsCancellation(error) { self.error = instanceErrorText(error) }
            }
        }
    }
}

// MARK: - Add Stock

/// `_AddStockSheet` / `_AddStockDetailSheet`: one upper-cased symbol.
struct InstanceAddStockSheet: View {
    let onAdd: (String) async throws -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var symbol = ""
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        InstanceFormSheet(title: "Add Stock", submitLabel: "Add", busy: busy, onSubmit: submit) {
            Section {
                LabeledContent("Symbol *") {
                    TextField("e.g. AAPL", text: $symbol)
                        .multilineTextAlignment(.trailing)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .onSubmit(submit)
                }
            }
            InstanceSheetError(message: error)
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
                if !tradingIsCancellation(error) { self.error = instanceErrorText(error) }
            }
        }
    }
}

// MARK: - Link Brokerage / Strategy

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
            title: kind == .brokerage ? "Link Brokerage" : "Link Strategy",
            submitLabel: "Link",
            busy: busy,
            onSubmit: submit
        ) {
            Section {
                switch items {
                case .loading:
                    LoadingState()
                case .failed:
                    ErrorRow(message: items.errorMessage ?? "")
                case .loaded(let list):
                    Picker(kind == .brokerage ? "Brokerage" : "Strategy", selection: $selection) {
                        Text(kind == .brokerage ? "Select a brokerage" : "Select a strategy").tag("")
                        ForEach(list.indices, id: \.self) { i in
                            Text(kind == .brokerage ? instanceBrokerageLabel(list[i]) : instanceStrategyLabel(list[i]))
                                .tag(instanceSelectorId(list[i]))
                        }
                    }
                }
            }
            InstanceSheetError(message: error)
        }
        .presentationDetents([.medium])
        .task {
            if selection.isEmpty, let currentId { selection = currentId }
            items = kind == .brokerage
                ? await InstanceSelectors.brokerages(services)
                : await InstanceSelectors.strategies(services)
        }
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
                if !tradingIsCancellation(error) { self.error = instanceErrorText(error) }
            }
        }
    }
}

// MARK: - Create Backtest

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
        InstanceFormSheet(title: "Create Backtest", submitLabel: "Create", busy: busy, onSubmit: submit) {
            Section("Stocks (comma-separated)") {
                TextField("AAPL, TSLA", text: $stocks)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
            }
            Section {
                LabeledContent("Start *") {
                    TextField("YYYY-MM-DD", text: $start)
                        .multilineTextAlignment(.trailing)
                        .keyboardType(.numbersAndPunctuation)
                }
                LabeledContent("End *") {
                    TextField("YYYY-MM-DD", text: $end)
                        .multilineTextAlignment(.trailing)
                        .keyboardType(.numbersAndPunctuation)
                }
            }
            Section("Granularity") {
                Picker("Granularity", selection: $granularity) {
                    ForEach(instanceGranularities, id: \.value) { Text($0.label).tag($0.value) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
            }
            Section {
                LabeledContent("Initial Cash ($)") {
                    TextField("100000", text: $cash)
                        .keyboardType(.decimalPad)
                        .multilineTextAlignment(.trailing)
                }
            }
            InstanceSheetError(message: error)
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
                if !tradingIsCancellation(error) { self.error = instanceErrorText(error) }
            }
        }
    }
}

// MARK: - Clear State

/// `_ClearStateSheet`: pick a scope, preview the dry run, type the
/// instance id, then Confirm and Clear. Destructive: the confirm stays
/// disabled until the typed id matches exactly.
struct InstanceClearStateSheet: View {
    let instanceId: String
    let model: InstanceDetailModel

    @Environment(\.dismiss) private var dismiss
    @State private var scope = "lookback_only"
    @State private var preview: JSONObject?
    @State private var previewing = false
    @State private var applying = false
    @State private var confirmed = false
    @State private var error: String?
    @State private var success: String?

    private var locked: Bool { applying || previewing }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Scope", selection: $scope) {
                        ForEach(InstanceClearScope.all) { opt in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(opt.label)
                                    .font(.subheadline.weight(.semibold))
                                Text(opt.blurb)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .tag(opt.value)
                        }
                    }
                    .pickerStyle(.inline)
                    .labelsHidden()
                    .disabled(locked)
                }
                Section {
                    Button {
                        runPreview()
                    } label: {
                        HStack {
                            Text("Preview Dry Run")
                            if previewing {
                                Spacer()
                                ProgressView()
                            }
                        }
                    }
                    .disabled(locked)
                }
                if let preview {
                    Section("Preview Results") {
                        Text(InstanceClearScope.previewTotal(preview))
                            .font(.footnote)
                        ForEach(Array((preview["tables"]?.array ?? []).prefix(10).enumerated()), id: \.offset) { _, t in
                            Text("• \(t.dartDescription)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Section {
                        TypedConfirmField(
                            phrase: instanceId,
                            label: "Type \"\(instanceId)\" to enable destructive confirm"
                        ) { confirmed = $0 }
                    }
                }
                if let error {
                    Section {
                        ErrorRow(message: error)
                            .listRowInsets(EdgeInsets())
                            .listRowBackground(Color.clear)
                    }
                }
                if let success {
                    Section {
                        Text(success)
                            .font(.footnote)
                            .foregroundStyle(DS.Palette.success)
                    }
                }
                if preview != nil {
                    Section {
                        Button(role: .destructive) {
                            apply()
                        } label: {
                            HStack {
                                Label("Confirm and Clear", systemImage: Symbol.named("delete"))
                                if applying {
                                    Spacer()
                                    ProgressView()
                                }
                            }
                        }
                        .disabled(!confirmed || applying)
                    }
                }
            }
            .navigationTitle("Clear Instance State")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                        .disabled(locked)
                }
            }
            .interactiveDismissDisabled(locked)
        }
        .presentationDragIndicator(.visible)
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
                if !tradingIsCancellation(error) { self.error = "Preview failed: \(instanceErrorText(error))" }
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
                if !tradingIsCancellation(error) { self.error = "Clear failed: \(instanceErrorText(error))" }
            }
            applying = false
        }
    }
}
