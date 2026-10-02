import SwiftUI

/// The create / edit Kalshi instance sheet — `KalshiInstanceSheet` in
/// kalshi_screen.dart, as a native Form. Edit mode prefills from the stored
/// `kalshi_config` and PATCHes instead of creating. Paper/real copy and the
/// paper-mode default are kept exactly.
struct KalshiInstanceSheet: View {
    let accounts: [BrokerageAccount]
    let onCreated: (String) -> Void

    @Environment(AppServices.self) private var services
    @Environment(\.dismiss) private var dismiss
    @State private var model: KalshiInstanceFormModel

    init(
        accounts: [BrokerageAccount],
        initialBrokerageId: String,
        editInstanceId: String? = nil,
        editName: String? = nil,
        editConfig: JSONObject? = nil,
        repository: @escaping () -> KalshiRepository,
        onCreated: @escaping (String) -> Void
    ) {
        self.accounts = accounts
        self.onCreated = onCreated
        _model = State(initialValue: KalshiInstanceFormModel(
            initialBrokerageId: initialBrokerageId,
            editInstanceId: editInstanceId,
            editName: editName,
            editConfig: editConfig,
            repository: repository
        ))
    }

    var body: some View {
        @Bindable var m = model
        NavigationStack {
            Form {
                accountSection
                riskSection
                Section {
                    Picker("Analyst LLM model", selection: $m.selectedModel) {
                        Text("Default (system model)").tag(String?.none)
                        ForEach(model.models.indices, id: \.self) { i in
                            let row = model.models[i]
                            Text(KalshiFormat.firstNonNull(row["name"], row["model"], row["id"]))
                                .tag(Optional(KalshiPregame.str(row["id"])))
                        }
                    }
                } header: {
                    MarketsInfoLabel(text: "Analyst LLM model", info: "The model that reads injuries/lineups/form and adjusts probabilities + writes the per-bet rationale.")
                }
                togglesSection
                oddsSection
                Section {
                    TextField("Instance name", text: $m.name, prompt: Text("e.g. Soccer edge — demo"))
                } header: {
                    Text("Instance name")
                }
                Section {
                    KalshiLeaguePicker(selected: $m.leagues)
                } header: {
                    MarketsInfoLabel(text: "Leagues", info: "Which soccer leagues to scan. Thinner divisions often carry more edge.")
                }
                bankrollSection
                numbersSection
                if !model.err.isEmpty {
                    Section {
                        Text(model.err)
                            .font(.footnote)
                            .foregroundStyle(DS.Palette.danger)
                    }
                }
            }
            .navigationTitle(model.isEdit ? "Edit Kalshi Instance" : "Create Kalshi Instance")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) { submitButton }
            }
        }
        .task { await model.start() }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
    }

    // MARK: Sections

    @ViewBuilder
    private var accountSection: some View {
        // Brokerage selector + balance (create only — an edit keeps the account).
        Section {
            if !model.isEdit {
                Picker("Kalshi account", selection: Binding(
                    get: { model.brokerageId },
                    set: { id in Task { await model.selectBrokerage(id) } }
                )) {
                    if model.brokerageId.isEmpty { Text("").tag("") }
                    ForEach(accounts) { a in Text(a.accountName).tag(a.id) }
                }
            }
        } header: {
            if !model.isEdit {
                MarketsInfoLabel(text: "Kalshi account", info: "Which Kalshi account this bot trades on.")
            }
        } footer: {
            Text(model.loadingBalance ? "Balance: …" : (model.hasBalance ? "Balance: $\(dartToStringAsFixed(model.balance, 2))" : "Live balance unavailable"))
                .foregroundStyle(model.hasBalance ? Color.secondary : DS.Palette.warning)
        }
    }

    private var riskSection: some View {
        Section {
            Picker("Risk tolerance", selection: Binding(
                get: { model.risk },
                set: { model.applyPreset($0) }
            )) {
                ForEach(KalshiRiskPreset.all, id: \.key) { p in Text(p.label).tag(p.key) }
            }
            .pickerStyle(.segmented)
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets())
        } header: {
            MarketsInfoLabel(text: "Risk tolerance", info: "Pick a preset and we'll tune edge, Kelly, exposure, caps, bankroll usage, cadence, and the daily-loss cap. You can still tweak any value after.")
        } footer: {
            Text(model.riskBlurb)
        }
    }

    private var togglesSection: some View {
        @Bindable var m = model
        return Section {
            Toggle(isOn: $m.liveMonitoring) {
                Text("Live in-match trading")
                Text("Monitor live matches and trade two-way (open/add/reduce/exit) in-play.")
            }
            // Paper mode (dry-run) — safe for funded live accounts.
            Toggle(isOn: $m.paperMode) {
                Text(model.paperMode ? "Paper mode (dry-run)" : "REAL orders")
                    .fontWeight(.semibold)
                    .foregroundStyle(model.paperMode ? Color.primary : DS.Palette.danger)
                Text(model.paperMode ? "Reads real prices, places NO real orders — safe to test a funded account." : "⚠ Places REAL orders with REAL money when started.")
            }
            .listRowBackground(model.paperMode ? DS.Surface.panel : DS.Palette.danger.opacity(0.1))
            Toggle(isOn: $m.oneBetPerFixture) {
                Text("One bet per fixture")
                Text("Only take one open position per match at a time.")
            }
        }
    }

    private var oddsSection: some View {
        @Bindable var m = model
        return Group {
            Section {
                labelledSecure("The-Odds-API key (live sharp odds)", text: $m.oddsKey, prompt: "the-odds-api.com key (optional)")
            } header: {
                MarketsInfoLabel(text: "Odds API key — sharp anchor", info: "Anchor fair value to de-vig'd sharp bookmaker odds (the-odds-api.com) so it bets where Kalshi disagrees with the books. Free key. Blank = model-only (rarely trades).")
            }
            Section {
                labelledSecure("OddsPapi key (backtest odds)", text: $m.oddspapiKey, prompt: "oddspapi.io key (optional)")
                VStack(alignment: .leading, spacing: 4) {
                    Text("Sharp weight: \(Int(model.sharpWeight.rounded()))%")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Slider(value: $m.sharpWeight, in: 0...100, step: 5)
                        .accessibilityValue("\(Int(model.sharpWeight.rounded()))%")
                }
            } header: {
                MarketsInfoLabel(text: "OddsPapi key", info: "Used for backtest odds only, not live trading.")
            }
        }
    }

    /// `_field(label, hint:, obscure: true)`: the label above a secure field.
    private func labelledSecure(_ label: String, text: Binding<String>, prompt: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            SecureField(label, text: text, prompt: Text(prompt))
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
        }
    }

    private var bankrollSection: some View {
        @Bindable var m = model
        return Section {
            if model.hasBalance {
                HStack {
                    Slider(value: Binding(get: { model.usagePct }, set: { model.setUsagePct($0) }), in: 5...100, step: 5)
                        .accessibilityValue("\(Int(model.usagePct.rounded()))%")
                    Text("\(Int(model.usagePct.rounded()))% · $\(Int(dartTruncating: model.effectiveBankroll.rounded()) ?? 0)")
                        .font(.footnote.weight(.bold).monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            } else {
                MarketsNumberRow(label: "Bankroll ($)", text: $m.manualBankroll)
            }
        } header: {
            MarketsInfoLabel(text: "Bankroll usage", info: "How much of this account the bot sizes against. The daily-loss cap scales with it.")
        }
    }

    private var numbersSection: some View {
        @Bindable var m = model
        return Section {
            MarketsNumberRow(label: "Edge threshold (%)", text: $m.edge)
            MarketsNumberRow(label: "Kelly fraction", text: $m.kelly)
            MarketsNumberRow(label: "Max contracts", text: $m.maxContracts)
            MarketsNumberRow(label: "Max exposure (%)", text: $m.exposure)
            MarketsNumberRow(label: "Per-league cap (%)", text: $m.leagueCap)
            MarketsNumberRow(label: "Scan cadence (s)", text: $m.poll)
            MarketsNumberRow(label: "Min price (¢)", text: $m.minPrice)
            MarketsNumberRow(label: "Max price (¢)", text: $m.maxPrice)
            MarketsNumberRow(label: "Draw min edge (%)", text: $m.drawMinEdge)
            MarketsNumberRow(label: "Order size min ($)", text: $m.orderSizeMin)
            MarketsNumberRow(label: "Order size max ($)", text: $m.orderSizeMax)
            MarketsNumberRow(label: "No-sharp edge bar (%)", text: $m.noSharpEdge)
            MarketsNumberRow(label: "Market anchor (%)", text: $m.marketShrink)
            // A user edit marks the cap touched, so it stops auto-scaling.
            MarketsNumberRow(
                label: "Daily-loss cap ($) — auto-scales with bankroll",
                text: Binding(get: { model.dailyLoss }, set: { model.editDailyLoss($0) })
            )
        }
    }

    /// The form's confirm action, in the toolbar (Form rule: confirm and
    /// Cancel go in the toolbar): the iOS 26 prominent checkmark, with the
    /// Dart button's labels for VoiceOver, a spinner while saving, and the
    /// same in-flight guard.
    private var submitButton: some View {
        Button {
            Task {
                if let bid = await model.submit() {
                    dismiss()
                    onCreated(bid)
                }
            }
        } label: {
            if model.creating {
                ProgressView().accessibilityLabel("Saving…")
            } else {
                Label(model.isEdit ? "Save Changes" : "Create Instance", systemImage: "checkmark")
            }
        }
        .dsGlassProminentButton()
        .disabled(model.creating)
    }
}
