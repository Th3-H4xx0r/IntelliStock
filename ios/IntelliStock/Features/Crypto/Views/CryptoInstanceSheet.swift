import SwiftUI

/// The unified create / edit crypto instance sheet — `CryptoInstanceSheet` in
/// crypto_instance_sheet.dart, shaped like the web's modal: identity and
/// brokerage, band, dynamic strategy and a fixed + dynamic allocation editor
/// (% or $), then Cancel and Create instance / Save changes.
struct CryptoInstanceSheet: View {
    let onSaved: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var model: CryptoInstanceFormModel
    @State private var addSelection: String?

    init(request: CryptoInstanceSheetRequest, repository: @escaping () -> CryptoRepository, onSaved: @escaping () -> Void) {
        self.onSaved = onSaved
        _model = State(initialValue: CryptoInstanceFormModel(
            editInstanceId: request.editInstanceId,
            editName: request.editName,
            editBrokerageId: request.editBrokerageId,
            editConfig: request.editConfig,
            editStocks: request.editStocks,
            repository: repository
        ))
    }

    var body: some View {
        FormSheet(
            model.isEdit ? "Edit crypto instance" : "New crypto instance",
            closeDisabled: model.saving,
            onClose: { dismiss() }
        ) {
            identitySection
            bandSection
            strategySection
            allocationSection
            if !model.err.isEmpty {
                FormMessage(model.err, tone: .error)
            }
        } footer: {
            FormSheetActions(
                primary: model.isEdit ? "Save changes" : "Create instance",
                busyLabel: model.isEdit ? "Saving…" : "Creating…",
                busy: model.saving,
                cancelDisabled: model.saving,
                onPrimary: submit,
                onCancel: { dismiss() }
            )
        }
        .task { await model.loadSelectors() }
        .presentationDetents([.large])
    }

    // MARK: Identity + brokerage

    @ViewBuilder
    private var identitySection: some View {
        @Bindable var m = model
        if !model.isEdit {
            DSField("Instance ID") {
                FormTextField("e.g. crypto-main", text: $m.instanceIdText, mono: true)
            }
        }
        DSField("Name") {
            FormTextField("Optional display name", text: $m.name)
        }
        if !model.isEdit {
            let brokerages = CryptoInstanceFormModel.cryptoBrokerages(model.brokerages)
            let selected = brokerages.first { KalshiPregame.str($0["id"]) == model.brokerageId }
            FormField("Brokerage", info: "Which account this bot trades on (Alpaca).") {
                FormSelect(
                    "Brokerage",
                    display: selected.map(Self.brokerageLabel) ?? "Select a brokerage",
                    selection: Binding(
                        get: { model.brokerageId },
                        set: { id in Task { await model.selectBrokerage(id) } }
                    )
                ) {
                    if model.brokerageId.isEmpty { Text("Select a brokerage").tag("") }
                    ForEach(brokerages.indices, id: \.self) { i in
                        Text(Self.brokerageLabel(brokerages[i])).tag(KalshiPregame.str(brokerages[i]["id"]))
                    }
                }
            }
        }
        if model.equity > 0 || model.loadingEquity {
            Text(equityLine)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
                .padding(.top, -12)
        } else {
            FormNote(equityLine)
                .padding(.top, -12)
        }
    }

    private static func brokerageLabel(_ b: JSONObject) -> String {
        "\(KalshiPregame.str(b["account_name"])) (\(KalshiPregame.str(b["brokerage_type"])))".trimmingCharacters(in: .whitespaces)
    }

    private var equityLine: String {
        if model.loadingEquity { return "Account equity…" }
        guard model.equity > 0 else { return "Account equity unavailable — % still works" }
        let name = model.selectedBrokerage.map { " · \(($0["account_name"] ?? .null).dartDescription)" } ?? ""
        return "Account equity \(CryptoInstanceFormModel.fmtUsd(model.equity))\(name)"
    }

    // MARK: Band + strategy

    private var bandSection: some View {
        @Bindable var m = model
        let rec = CryptoCatalog.recommendedBand(for: model.strategy)
        return VStack(alignment: .leading, spacing: 4) {
            FormField(
                "Volatility band",
                info: "Sets the 24/7 monitor cadence (High = every 5m, Medium = 15m, Low = 60m).",
                help: CryptoCatalog.bandBlurb[model.band]
            ) {
                DSSegmentedControl("Volatility band", selection: $m.band, options: CryptoCatalog.bands.map(\.key), fullWidth: true, segmentHeight: 32) { key in
                    CryptoCatalog.bands.first { $0.key == key }?.label ?? key
                }
            }
            if let rec, rec != model.band {
                Button("Use \(CryptoCatalog.capitalized(rec)), recommended for \(model.strategy)") {
                    model.band = rec
                }
                .buttonStyle(.dsGhost)
                .padding(.leading, -12)
            }
        }
    }

    private var strategySection: some View {
        FormField(
            "Dynamic strategy",
            info: "How the auto-discovered (Dynamic) portion is traded.",
            help: CryptoCatalog.strategyBlurb(model.strategy)
        ) {
            FormSelect("Dynamic strategy", display: model.strategy, selection: Binding(get: { model.strategy }, set: { model.selectStrategy($0) })) {
                ForEach(CryptoCatalog.strategies, id: \.name) { s in Text(s.name).tag(s.name) }
            }
        }
    }

    // MARK: Allocation

    private var allocationSection: some View {
        @Bindable var m = model
        let pctPrimary = model.mode == "pct"
        return VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Allocation")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.label)
                    Text("Pin fixed weights, leave the rest Dynamic. Empty means 100% dynamic")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                DSSegmentedControl("Units", selection: $m.mode, options: ["pct", "usd"]) { $0 == "pct" ? "%" : "$" }
            }

            // The 280 pt `Sector3DChart` over the legend; the drilled ring
            // reaches 20 pt past its frame.
            Sector3DChart(slices: model.slices)
                .frame(width: 280)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 20)
            legend
            if model.weightsUnknown {
                FormNote("Current weights couldn’t be loaded — showing an even split. Adjust before saving.")
            }

            // The coin table (the web's bordered table).
            VStack(spacing: 0) {
                HStack {
                    Text("Coin")
                    Spacer()
                    Text(pctPrimary ? "Weight · ≈ USD" : "USD · ≈ weight")
                }
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .padding(.horizontal, 12)
                .frame(height: 36)
                DSDivider()
                ForEach(Array(model.rows.enumerated()), id: \.element.id) { i, r in
                    coinRow(r, index: i, pctPrimary: pctPrimary)
                    DSDivider(row: true)
                }
                dynamicRow
                DSDivider(row: true)
                addBar
            }
            .overlay {
                RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                    .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
            }
            meter
        }
    }

    private var legend: some View {
        MarketsFlowLayout(spacing: 12, runSpacing: 6, alignment: .center) {
            ForEach(Array(model.rows.enumerated()), id: \.element.id) { i, r in
                if r.pct > 0 { legendChip(CryptoCatalog.color(i), "\(r.sym) \(CryptoInstanceFormModel.fmtNum(r.pct))%") }
            }
            if model.dynPct > 0 {
                legendChip(CryptoCatalog.dynamicColor, "Dynamic \(CryptoInstanceFormModel.fmtNum(model.dynPct))%")
            }
        }
        .frame(maxWidth: .infinity)
    }

    private func legendChip(_ c: Color, _ label: String) -> some View {
        HStack(spacing: 6) {
            RoundedRectangle(cornerRadius: 2, style: .continuous).fill(c).frame(width: 8, height: 8)
            Text(label)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.secondary)
                .monospacedDigit()
        }
    }

    private func coinRow(_ r: CryptoAllocRow, index: Int, pctPrimary: Bool) -> some View {
        let usd = r.pct / 100 * model.equity
        return HStack(spacing: 8) {
            RoundedRectangle(cornerRadius: 2, style: .continuous).fill(CryptoCatalog.color(index)).frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 0) {
                Text(r.sym).font(DS.Font.bodyMedium).foregroundStyle(DS.Text.primary)
                Text(r.name).font(DS.Font.caption).foregroundStyle(DS.Text.muted).lineLimit(1)
            }
            Spacer(minLength: 4)
            HStack(spacing: 4) {
                TextField(pctPrimary ? "%" : "$", text: Binding(
                    get: { pctPrimary ? (model.rows.first { $0.id == r.id }?.pctText ?? "") : (model.rows.first { $0.id == r.id }?.usdText ?? "") },
                    set: { pctPrimary ? model.onPctChanged(r.id, $0) : model.onUsdChanged(r.id, $0) }
                ))
                .keyboardType(.decimalPad)
                .multilineTextAlignment(.trailing)
                .monospacedDigit()
                if pctPrimary {
                    Text("%").font(DS.Font.caption).foregroundStyle(DS.Text.muted)
                }
            }
            .frame(width: 64)
            .dsInput(prefix: pctPrimary ? nil : "$")
            Text(pctPrimary ? "≈\(CryptoInstanceFormModel.fmtUsd(usd))" : "≈\(CryptoInstanceFormModel.fmtNum(r.pct))%")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
                .lineLimit(1)
                .frame(width: 52, alignment: .trailing)
            Button {
                model.removeCoin(r.id)
            } label: {
                Image(systemName: "xmark")
            }
            .buttonStyle(.dsIcon(side: 28))
            .accessibilityLabel("Remove \(r.sym)")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private var dynamicRow: some View {
        let usd = model.dynPct / 100 * model.equity
        return HStack(spacing: 8) {
            RoundedRectangle(cornerRadius: 2, style: .continuous).fill(CryptoCatalog.dynamicColor).frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 0) {
                Text("Dynamic").font(DS.Font.bodyMedium).foregroundStyle(DS.Text.primary)
                Text("Auto-discovered and traded").font(DS.Font.caption).foregroundStyle(DS.Text.muted)
            }
            Spacer()
            Text("\(CryptoInstanceFormModel.fmtNum(model.dynPct))%")
                .font(DS.Font.bodyMedium)
                .foregroundStyle(DS.Text.soft)
                .monospacedDigit()
            Text(CryptoInstanceFormModel.fmtUsd(usd))
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
                .frame(width: 52, alignment: .trailing)
            Color.clear.frame(width: 28, height: 1)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }

    @ViewBuilder
    private var addBar: some View {
        let remaining = model.remainingCoins
        Group {
            if remaining.isEmpty {
                Text("All catalog coins added")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                let current = remaining.contains { $0.sym == addSelection } ? addSelection! : remaining[0].sym
                HStack(spacing: 8) {
                    FormSelect("Coin", display: remaining.first { $0.sym == current }.map { "\($0.sym) · \($0.name)" } ?? current, selection: Binding(get: { current }, set: { addSelection = $0 })) {
                        ForEach(remaining, id: \.sym) { c in Text("\(c.sym) · \(c.name)").tag(c.sym) }
                    }
                    Button {
                        model.addCoin(current)
                    } label: {
                        Label("Add coin", systemImage: "plus")
                    }
                    .buttonStyle(.dsSecondary(size: .medium))
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }

    private var meter: some View {
        let fixed = model.fixedSum
        let dyn = model.dynPct
        var segments: [(Double, Color)] = []
        for (i, r) in model.rows.enumerated() where r.pct > 0 {
            segments.append((Double(min(max(Int((r.pct * 10).rounded()), 1), 1000)), CryptoCatalog.color(i)))
        }
        if dyn > 0 {
            segments.append((Double(min(max(Int((dyn * 10).rounded()), 1), 1000)), CryptoCatalog.dynamicColor))
        }
        let total = segments.reduce(0) { $0 + $1.0 }
        return VStack(alignment: .leading, spacing: 8) {
            GeometryReader { geo in
                HStack(spacing: 0) {
                    ForEach(Array(segments.enumerated()), id: \.offset) { _, s in
                        s.1.frame(width: geo.size.width * s.0 / total)
                    }
                }
            }
            .frame(height: 4)
            .background(DS.Surface.raised)
            .clipShape(Capsule())
            HStack {
                Text("Fixed \(Text("\(CryptoInstanceFormModel.fmtNum(fixed))%").foregroundStyle(DS.Text.soft)) · Dynamic \(Text("\(CryptoInstanceFormModel.fmtNum(dyn))%").foregroundStyle(DS.Text.soft))")
                    .foregroundStyle(DS.Text.muted)
                Spacer()
                Text(model.over ? "Over-allocated — reduce by \(CryptoInstanceFormModel.fmtNum(fixed - 100))%" : "\(CryptoInstanceFormModel.fmtUsd(dyn / 100 * model.equity)) flexible")
                    .foregroundStyle(model.over ? DS.Palette.loss : DS.Text.muted)
            }
            .font(DS.Font.caption)
            .monospacedDigit()
            if !model.over, dyn <= 0 {
                FormNote("Dynamic 0% — the \(model.strategy) strategy won't trade (this is buy-and-hold). Lower a fixed weight to give it a dynamic budget to trade.")
            }
        }
    }

    /// Create / Save, with the same in-flight guard.
    private func submit() {
        Task {
            if await model.submit() {
                dismiss()
                onSaved()
            }
        }
    }
}
