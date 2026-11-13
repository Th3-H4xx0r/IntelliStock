import SwiftUI

/// The create / edit Kalshi instance sheet — `KalshiInstanceSheet` in
/// kalshi_screen.dart, shaped like the web's modal. Edit mode prefills from
/// the stored `kalshi_config` and PATCHes instead of creating. The paper-mode
/// default is kept, and real orders read in loss red.
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
        FormSheet(
            model.isEdit ? "Edit Kalshi instance" : "Create Kalshi instance",
            subtitle: "Soccer event contracts on a demo or live account",
            closeDisabled: model.creating,
            onClose: { dismiss() }
        ) {
            accountField
            riskField
            FormField("Analyst LLM model", info: "The model that reads injuries, lineups and form, adjusts probabilities and writes the per-bet rationale. Leave on Default to use the system model.") {
                FormSelect("Analyst LLM model", display: modelLabel, selection: $m.selectedModel) {
                    Text("Default (system model)").tag(String?.none)
                    ForEach(model.models.indices, id: \.self) { i in
                        let row = model.models[i]
                        Text(KalshiFormat.firstNonNull(row["name"], row["model"], row["id"]))
                            .tag(Optional(KalshiPregame.str(row["id"])))
                    }
                }
            }
            DSField("Instance name") {
                FormTextField("e.g. Soccer edge — demo", text: $m.name)
            }
            FormField("Leagues", info: "Which soccer leagues to scan. Thinner divisions often carry more edge than marquee games.") {
                KalshiLeaguePicker(selected: $m.leagues)
            }
            bankrollField
            numbersGrid
            togglesGroup
            oddsGroup
            paperModeBox
            if !model.err.isEmpty {
                FormMessage(model.err, tone: .error)
            }
        } footer: {
            FormSheetActions(
                primary: model.isEdit ? "Save changes" : "Create instance",
                busyLabel: model.isEdit ? "Saving…" : "Creating…",
                busy: model.creating,
                cancelDisabled: model.creating,
                onPrimary: submit,
                onCancel: { dismiss() }
            )
        }
        .task { await model.start() }
        .presentationDetents([.large])
    }

    private var modelLabel: String {
        guard let id = model.selectedModel,
              let row = model.models.first(where: { KalshiPregame.str($0["id"]) == id }) else { return "Default (system model)" }
        return KalshiFormat.firstNonNull(row["name"], row["model"], row["id"])
    }

    // MARK: Fields

    /// The account (create only — an edit keeps it) and its live balance.
    @ViewBuilder
    private var accountField: some View {
        let balance = model.loadingBalance
            ? "Balance…"
            : (model.hasBalance ? "Balance $\(dartToStringAsFixed(model.balance, 2))" : "Live balance unavailable")
        if !model.isEdit {
            FormField("Kalshi account", info: "Which Kalshi account this bot trades on. Demo is paper, live is real money.") {
                FormSelect(
                    "Kalshi account",
                    display: accounts.first { $0.id == model.brokerageId }?.accountName ?? "Select an account",
                    selection: Binding(
                        get: { model.brokerageId },
                        set: { id in Task { await model.selectBrokerage(id) } }
                    )
                ) {
                    if model.brokerageId.isEmpty { Text("Select an account").tag("") }
                    ForEach(accounts) { a in Text(a.accountName).tag(a.id) }
                }
            }
        }
        if model.hasBalance || model.loadingBalance {
            Text(balance)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
                .padding(.top, model.isEdit ? 0 : -12)
        } else {
            FormNote(balance)
                .padding(.top, model.isEdit ? 0 : -12)
        }
    }

    private var riskField: some View {
        FormField(
            "Risk tolerance",
            info: "Pick a preset and we'll tune edge, Kelly, exposure, caps, bankroll usage, cadence and the daily-loss cap. You can still tweak any value after.",
            help: model.riskBlurb
        ) {
            DSSegmentedControl(
                "Risk tolerance",
                selection: Binding(get: { model.risk }, set: { model.applyPreset($0) }),
                options: KalshiRiskPreset.all.map(\.key),
                fullWidth: true,
                segmentHeight: 32
            ) { key in KalshiRiskPreset.all.first { $0.key == key }?.label ?? key }
        }
    }

    @ViewBuilder
    private var bankrollField: some View {
        @Bindable var m = model
        FormField("Bankroll usage", info: "How much of this account the bot sizes against. The daily-loss cap scales with it.") {
            if model.hasBalance {
                HStack(spacing: 12) {
                    Slider(value: Binding(get: { model.usagePct }, set: { model.setUsagePct($0) }), in: 5...100, step: 5)
                        .accessibilityLabel("Bankroll usage")
                        .accessibilityValue("\(Int(model.usagePct.rounded()))%")
                    Text("\(Int(model.usagePct.rounded()))% · $\(Int(dartTruncating: model.effectiveBankroll.rounded()) ?? 0)")
                        .font(DS.Font.small)
                        .foregroundStyle(DS.Text.soft)
                        .monospacedDigit()
                }
            } else {
                FormTextField("Bankroll ($)", text: $m.manualBankroll, keyboard: .decimalPad, prefix: "$")
                    .monospacedDigit()
            }
        }
    }

    private var numbersGrid: some View {
        @Bindable var m = model
        return FormGrid {
            number("Edge threshold (%)", $m.edge, info: "Minimum +EV edge (fair − price − fee) needed to place a trade.")
            number("Kelly fraction", $m.kelly, info: "Fraction of full Kelly to stake. ¼ (0.25) is conservative; higher means larger bets and more variance.")
            number("Max contracts", $m.maxContracts, info: "Hard cap on contracts bought in any single market.")
            number("Max exposure (%)", $m.exposure, info: "Cap on total open exposure as a % of bankroll.")
            number("Per-league cap (%)", $m.leagueCap, info: "Cap on exposure to any one league; limits correlated risk.")
            number("Scan cadence (s)", $m.poll, info: "How often it polls odds, respecting the OddsPapi monthly budget.")
            number("Min price (¢)", $m.minPrice, info: "Lowest YES price to buy. The 15¢ floor blocks cheap longshots; lower it to allow cheaper contracts.")
            number("Max price (¢)", $m.maxPrice, info: "Highest YES price to buy. Above ~90¢ fees eat the thin upside.")
            number("Draw min edge (%)", $m.drawMinEdge, info: "Draws are model-overconfident; they need at least this much edge to trade.")
            number("No-sharp edge bar (%)", $m.noSharpEdge, info: "Higher edge required to bet a market with no sharp odds (model only).")
            number("Order size min ($)", $m.orderSizeMin, info: "Target dollar size per trade as a range: the bot sizes within it by edge conviction. Leave both 0 for auto (Kelly-sized).")
            number("Order size max ($)", $m.orderSizeMax)
            number("Market anchor (%)", $m.marketShrink, info: "With no sharp line, shrink the model this far toward the de-vigged Kalshi market.")
            // A user edit marks the cap touched, so it stops auto-scaling.
            number(
                "Daily-loss cap ($)",
                Binding(get: { model.dailyLoss }, set: { model.editDailyLoss($0) }),
                info: "The bot halts for the day if losses hit this. It auto-scales with the bankroll until you edit it."
            )
        }
    }

    private func number(_ label: String, _ text: Binding<String>, info: String? = nil) -> some View {
        FormField(label, info: info) {
            FormTextField("0", text: text, keyboard: .decimalPad)
                .monospacedDigit()
        }
    }

    @ViewBuilder
    private var togglesGroup: some View {
        @Bindable var m = model
        FormToggle(
            "Live in-match trading",
            note: "Monitor live matches and trade two-way (open, add, reduce, exit) in play.",
            isOn: $m.liveMonitoring
        )
        FormToggle(
            "One bet per fixture",
            note: "Only take one open position per match at a time.",
            isOn: $m.oneBetPerFixture
        )
    }

    @ViewBuilder
    private var oddsGroup: some View {
        @Bindable var m = model
        FormField("Odds API key — sharp anchor", info: "Anchor fair value to de-vigged sharp bookmaker odds (the-odds-api.com) so it bets where Kalshi disagrees with the books. Free key. Blank means model only (rarely trades).") {
            FormTextField("the-odds-api.com key (optional)", text: $m.oddsKey, mono: true, secure: true)
        }
        FormField("OddsPapi key", info: "Used for backtest odds only, not live trading.") {
            FormTextField("oddspapi.io key (optional)", text: $m.oddspapiKey, mono: true, secure: true)
        }
        FormField("Sharp weight", info: "How much the sharp bookmaker line anchors fair value against the in-house model.") {
            HStack(spacing: 12) {
                Slider(value: $m.sharpWeight, in: 0...100, step: 5)
                    .accessibilityLabel("Sharp weight")
                    .accessibilityValue("\(Int(model.sharpWeight.rounded()))%")
                Text("\(Int(model.sharpWeight.rounded()))%")
                    .font(DS.Font.small)
                    .foregroundStyle(DS.Text.soft)
                    .monospacedDigit()
                    .frame(width: 40, alignment: .trailing)
            }
        }
    }

    /// Paper mode (dry-run) — safe for funded live accounts. Off, the box
    /// turns loss-tinted: the instance will place real orders.
    private var paperModeBox: some View {
        @Bindable var m = model
        return FormToggle(
            model.paperMode ? "Paper mode (dry-run)" : "Real orders",
            note: model.paperMode
                ? "Reads real prices and places no real orders. Safe to test on a funded account."
                : "Places real orders with real money when started.",
            titleColor: model.paperMode ? DS.Text.label : DS.Palette.loss,
            isOn: $m.paperMode
        )
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background {
            if model.paperMode {
                RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                    .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
            } else {
                Color.clear.dsErrorSurface(radius: DS.Radius.control)
            }
        }
    }

    /// Create / Save, with the in-flight guard.
    private func submit() {
        Task {
            if let bid = await model.submit() {
                dismiss()
                onCreated(bid)
            }
        }
    }
}
