import SwiftUI

/// The unified create / edit crypto instance sheet — `CryptoInstanceSheet` in
/// crypto_instance_sheet.dart: band, dynamic strategy and a fixed + dynamic
/// allocation editor (% or $), as a native Form.
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
        @Bindable var m = model
        NavigationStack {
            Form {
                identitySection
                bandSection
                strategySection
                allocationSection
                if !model.err.isEmpty {
                    Section {
                        Text(model.err).font(.footnote).foregroundStyle(DS.Palette.danger)
                    }
                }
            }
            .navigationTitle(model.isEdit ? "Edit Crypto Instance" : "New Crypto Instance")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) { submitButton }
            }
        }
        .task { await model.loadSelectors() }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
    }

    // MARK: Identity + brokerage

    private var identitySection: some View {
        @Bindable var m = model
        return Section {
            if !model.isEdit {
                labelled("Instance ID *") {
                    TextField("Instance ID *", text: $m.instanceIdText, prompt: Text("e.g. crypto-main"))
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
            }
            labelled("Name") {
                TextField("Name", text: $m.name, prompt: Text("Optional display name"))
            }
            if !model.isEdit {
                Picker(selection: Binding(
                    get: { model.brokerageId },
                    set: { id in Task { await model.selectBrokerage(id) } }
                )) {
                    if model.brokerageId.isEmpty { Text("Select a brokerage").tag("") }
                    ForEach(CryptoInstanceFormModel.cryptoBrokerages(model.brokerages).indices, id: \.self) { i in
                        let b = CryptoInstanceFormModel.cryptoBrokerages(model.brokerages)[i]
                        Text("\(KalshiPregame.str(b["account_name"])) (\(KalshiPregame.str(b["brokerage_type"])))".trimmingCharacters(in: .whitespaces))
                            .tag(KalshiPregame.str(b["id"]))
                    }
                } label: {
                    MarketsInfoLabel(text: "Brokerage", info: "Which account this bot trades on (Alpaca).")
                }
            }
        } footer: {
            Text(equityLine)
                .foregroundStyle(model.equity > 0 ? Color.secondary : DS.Palette.warning)
        }
    }

    private var equityLine: String {
        if model.loadingEquity { return "Account equity …" }
        guard model.equity > 0 else { return "Account equity unavailable — % still works" }
        let name = model.selectedBrokerage.map { " · \(($0["account_name"] ?? .null).dartDescription)" } ?? ""
        return "Account equity \(CryptoInstanceFormModel.fmtUsd(model.equity))\(name)"
    }

    private func labelled<F: View>(_ label: String, @ViewBuilder field: () -> F) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            field()
        }
    }

    // MARK: Band + strategy

    private var bandSection: some View {
        @Bindable var m = model
        let rec = CryptoCatalog.recommendedBand(for: model.strategy)
        return Section {
            Picker("Volatility band", selection: $m.band) {
                ForEach(CryptoCatalog.bands, id: \.key) { b in Text(b.label).tag(b.key) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets())
        } header: {
            MarketsInfoLabel(text: "Volatility band", info: "Sets the 24/7 monitor cadence (High = every 5m, Medium = 15m, Low = 60m).")
        } footer: {
            VStack(alignment: .leading, spacing: 4) {
                Text(CryptoCatalog.bandBlurb[model.band] ?? "")
                if let rec, rec != model.band {
                    Button("Recommended \(CryptoCatalog.capitalized(rec)) for \(model.strategy) — tap to use") {
                        model.band = rec
                    }
                    .font(.footnote.weight(.semibold))
                    .buttonStyle(.borderless)
                }
            }
        }
    }

    private var strategySection: some View {
        Section {
            Picker(selection: Binding(get: { model.strategy }, set: { model.selectStrategy($0) })) {
                ForEach(CryptoCatalog.strategies, id: \.name) { s in Text(s.name).tag(s.name) }
            } label: {
                MarketsInfoLabel(text: "Dynamic strategy", info: "How the auto-discovered (Dynamic) portion is traded.")
            }
        } footer: {
            Text(CryptoCatalog.strategyBlurb(model.strategy))
        }
    }

    // MARK: Allocation

    private var allocationSection: some View {
        @Bindable var m = model
        let pctPrimary = model.mode == "pct"
        return Group {
            Section {
                // The Dart sheet's 280 pt `Sector3DChart` over the legend. The
                // drilled ring reaches 20 pt past its frame, so the legend
                // sits 20 pt below it.
                Sector3DChart(slices: model.slices)
                    .frame(width: 280)
                    .frame(maxWidth: .infinity)
                    .padding(.top, 20)
                    .padding(.bottom, 20)
                legend
                if model.weightsUnknown {
                    Text("Current weights couldn’t be loaded — showing an even split. Adjust before saving.")
                        .font(.footnote)
                        .foregroundStyle(DS.Palette.warning)
                }
            } header: {
                DSSectionHeader("Allocation") {
                    Picker("Units", selection: $m.mode) {
                        Text("%").tag("pct")
                        Text("$").tag("usd")
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .frame(width: 110)
                }
            } footer: {
                Text("Pin fixed weights for the coins you want, and leave the rest Dynamic — auto-discovered and traded for you. Empty means 100% dynamic.")
            }
            Section {
                ForEach(Array(model.rows.enumerated()), id: \.element.id) { i, r in
                    coinRow(r, index: i, pctPrimary: pctPrimary)
                }
                dynamicRow
                addBar
            } header: {
                HStack {
                    Text("Coin")
                    Spacer()
                    Text(pctPrimary ? "Weight · ≈USD" : "USD · ≈Weight")
                }
                .textCase(nil)
            } footer: {
                meter
            }
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
            RoundedRectangle(cornerRadius: 3).fill(c).frame(width: 9, height: 9)
            Text(label).font(.caption2.monospacedDigit())
        }
    }

    private func coinRow(_ r: CryptoAllocRow, index: Int, pctPrimary: Bool) -> some View {
        let usd = r.pct / 100 * model.equity
        return HStack(spacing: 8) {
            RoundedRectangle(cornerRadius: 3).fill(CryptoCatalog.color(index)).frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 0) {
                Text(r.sym).font(.body.weight(.semibold))
                Text(r.name).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 4)
            HStack(spacing: 2) {
                if !pctPrimary { Text("$").font(.caption).foregroundStyle(.secondary) }
                TextField(pctPrimary ? "%" : "$", text: Binding(
                    get: { pctPrimary ? (model.rows.first { $0.id == r.id }?.pctText ?? "") : (model.rows.first { $0.id == r.id }?.usdText ?? "") },
                    set: { pctPrimary ? model.onPctChanged(r.id, $0) : model.onUsdChanged(r.id, $0) }
                ))
                .keyboardType(.decimalPad)
                .multilineTextAlignment(.trailing)
                .monospacedDigit()
                .frame(width: 64)
                if pctPrimary { Text("%").font(.caption).foregroundStyle(.secondary) }
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
            .background(DS.Surface.inset, in: .rect(cornerRadius: 9, style: .continuous))
            Text(pctPrimary ? "≈\(CryptoInstanceFormModel.fmtUsd(usd))" : "≈\(CryptoInstanceFormModel.fmtNum(r.pct))%")
                .font(.caption2.monospacedDigit())
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .frame(width: 52, alignment: .trailing)
            Button {
                model.removeCoin(r.id)
            } label: {
                Image(systemName: Symbol.named("close"))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .frame(width: 28, height: 28)
            }
            .buttonStyle(.borderless)
            .accessibilityLabel("Remove \(r.sym)")
        }
    }

    private var dynamicRow: some View {
        let usd = model.dynPct / 100 * model.equity
        return HStack(spacing: 8) {
            RoundedRectangle(cornerRadius: 3).fill(CryptoCatalog.dynamicColor).frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 0) {
                Text("Dynamic").font(.body.weight(.semibold))
                Text("auto-discover & trade").font(.caption2).foregroundStyle(.tint)
            }
            Spacer()
            Text("\(CryptoInstanceFormModel.fmtNum(model.dynPct))%")
                .font(.subheadline.weight(.semibold).monospacedDigit())
                .foregroundStyle(.tint)
            Text(CryptoInstanceFormModel.fmtUsd(usd))
                .font(.caption2.monospacedDigit())
                .foregroundStyle(.secondary)
                .frame(width: 52, alignment: .trailing)
            Color.clear.frame(width: 28, height: 1)
        }
    }

    @ViewBuilder
    private var addBar: some View {
        let remaining = model.remainingCoins
        if remaining.isEmpty {
            Text("All catalog coins added.").font(.footnote).foregroundStyle(.secondary)
        } else {
            let current = remaining.contains { $0.sym == addSelection } ? addSelection! : remaining[0].sym
            HStack {
                Picker("Coin", selection: Binding(get: { current }, set: { addSelection = $0 })) {
                    ForEach(remaining, id: \.sym) { c in Text("\(c.sym) · \(c.name)").tag(c.sym) }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                Spacer()
                Button {
                    model.addCoin(current)
                } label: {
                    Label("Add Coin", systemImage: Symbol.named("add"))
                }
                .buttonStyle(.borderless)
            }
        }
    }

    private var meter: some View {
        let fixed = model.fixedSum
        let dyn = model.dynPct
        var segments: [(Double, Color)] = []
        for (i, r) in model.rows.enumerated() where r.pct > 0 {
            segments.append((Double(min(max(Int((r.pct * 10).rounded()), 1), 1000)), CryptoCatalog.color(i)))
        }
        if dyn > 0 {
            segments.append((Double(min(max(Int((dyn * 10).rounded()), 1), 1000)), CryptoCatalog.dynamicColor.opacity(0.55)))
        }
        let total = segments.reduce(0) { $0 + $1.0 }
        return VStack(alignment: .leading, spacing: 9) {
            GeometryReader { geo in
                HStack(spacing: 0) {
                    if segments.isEmpty {
                        Color(uiColor: .systemFill)
                    } else {
                        ForEach(Array(segments.enumerated()), id: \.offset) { _, s in
                            s.1.frame(width: geo.size.width * s.0 / total)
                        }
                    }
                }
            }
            .frame(height: 8)
            .background(Color(uiColor: .systemFill))
            .clipShape(Capsule())
            HStack {
                Text("Fixed \(Text("\(CryptoInstanceFormModel.fmtNum(fixed))%").foregroundStyle(.primary).fontWeight(.semibold))  ·  Dynamic \(Text("\(CryptoInstanceFormModel.fmtNum(dyn))%").foregroundStyle(.tint).fontWeight(.semibold))")
                Spacer()
                Text(model.over ? "Over by \(CryptoInstanceFormModel.fmtNum(fixed - 100))%" : "\(CryptoInstanceFormModel.fmtUsd(dyn / 100 * model.equity)) flexible")
                    .foregroundStyle(model.over ? DS.Palette.danger : Color.secondary)
            }
            .font(.footnote)
            if !model.over, dyn <= 0 {
                Text("⚠ Dynamic 0% — the \(model.strategy) strategy won't trade (buy-and-hold). Lower a fixed weight to give it a dynamic budget to trade.")
                    .font(.caption)
                    .foregroundStyle(DS.Palette.warning)
            }
        }
        .padding(.top, 8)
    }

    /// The confirm action, in the toolbar (Form rule), with the Dart
    /// bottom button's labels and in-flight guard.
    private var submitButton: some View {
        Button {
            Task {
                if await model.submit() {
                    dismiss()
                    onSaved()
                }
            }
        } label: {
            Text(model.saving ? "Saving…" : (model.isEdit ? "Save Changes" : "Create Instance"))
        }
        .disabled(model.saving)
    }
}
