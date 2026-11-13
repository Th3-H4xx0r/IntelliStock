import SwiftUI

/// The Kalshi backtest launcher — `KalshiBacktestScreen`, as the web's
/// Backtest page: the settings card (seeded from the instance config: a risk
/// preset, the model, dates, leagues and the numbers), Run backtest, and the
/// list of this brokerage's backtests (refreshed every 3 s). Each backtest
/// row opens its results, with Stop and Delete on swipe and in the context
/// menu.
struct KalshiBacktestView: View {
    let instanceId: String

    @Environment(AppServices.self) private var services
    @State private var model: KalshiBacktestModel?
    @State private var picking: DateTarget?
    @State private var toast: Toast?

    private enum DateTarget: String, Identifiable {
        case start, end
        var id: String { rawValue }
    }

    var body: some View {
        Group {
            if let model {
                form(model)
            } else {
                Color.clear
            }
        }
        .navigationTitle("Backtest")
        .navigationBarTitleDisplayMode(.large)
        .toast($toast)
        .task(id: instanceId) {
            // Reused on reappear (after Run Backtest or a result), so the
            // dates, leagues, model and numbers survive; only the poll restarts.
            if model?.instanceId != instanceId {
                model = KalshiBacktestModel(instanceId: instanceId, repository: { [services] in services.kalshiRepository })
            }
            await model?.poll(lifecycle: services.lifecycle)
        }
        .sheet(item: $picking) { target in
            if let model {
                let now = Date()
                let cal = Calendar.current
                let first = cal.date(from: DateComponents(year: 2024, month: 1, day: 1))!
                let last = cal.date(from: DateComponents(year: cal.component(.year, from: now) + 1, month: 1, day: 1))!
                MarketsDatePickerSheet(
                    title: target == .start ? "Start" : "End",
                    initial: (target == .start ? model.start : model.end) ?? now,
                    range: first...last
                ) { d in
                    if target == .start { model.start = d } else { model.end = d }
                }
            }
        }
    }

    static let subtitle = "Run this instance's settings over past fixtures"

    private func form(_ model: KalshiBacktestModel) -> some View {
        @Bindable var m = model
        return List {
            Section {
                Text(Self.subtitle)
                    .font(DS.Font.pageSub)
                    .foregroundStyle(DS.Text.muted)
                    .dsClearRow(top: 0)
                if let err = model.err {
                    ErrorRow(message: err)
                        .dsClearRow(top: 16)
                }
            }

            DSSection("New backtest", subtitle: "Prefilled from the instance config", rowMinHeight: 44) {
                DSField("Risk tolerance") {
                    DSSegmentedControl(
                        "Risk tolerance",
                        selection: Binding(get: { model.tier }, set: { model.applyPreset($0) }),
                        options: KalshiBacktestPreset.all.map(\.key),
                        fullWidth: true,
                        segmentHeight: 32
                    ) { key in KalshiBacktestPreset.all.first { $0.key == key }?.label ?? key }
                }
                .padding(.vertical, 8)
                DSField("Analyst LLM model") {
                    FormSelect("Analyst LLM model", display: modelLabel(model), selection: Binding(get: { model.modelId }, set: { model.selectModel($0) })) {
                        Text("None (statistical model only)").tag(String?.none)
                        ForEach(model.models.indices, id: \.self) { i in
                            let row = model.models[i]
                            Text(KalshiFormat.firstNonNull(row["name"], row["model"], row["id"]))
                                .tag(Optional(KalshiFormat.firstNonNull(row["id"])))
                        }
                    }
                }
                .padding(.vertical, 8)
                FormToggle("Use the LLM analyst in this backtest", isOn: $m.useLlm)
                    .disabled(model.modelId == nil)
                FormGrid {
                    dateField("Start date", model.start) { picking = .start }
                    dateField("End date", model.end) { picking = .end }
                }
                .padding(.vertical, 8)
                KalshiLeaguePicker(selected: $m.leagues, asRow: true)
                FormGrid {
                    ForEach(KalshiBacktestModel.Field.allCases, id: \.self) { f in
                        DSField(f.label) {
                            FormTextField("0", text: Binding(get: { model.text(f) }, set: { model.edit(f, $0) }), keyboard: .decimalPad)
                                .monospacedDigit()
                        }
                    }
                }
                .padding(.vertical, 12)
                DSField("OddsPapi API key", help: "Sharp line, free tier. Saved for next time; blank means model only") {
                    FormTextField("oddspapi apiKey", text: $m.oddsKey, mono: true)
                }
                .padding(.vertical, 8)
            }

            Section {
                Button {
                    Task {
                        if let id = await model.submit() {
                            services.router.push(.kalshiBacktestResult(id))
                        }
                    }
                } label: {
                    HStack(spacing: 6) {
                        if model.submitting { ProgressView().controlSize(.mini).tint(DS.Palette.onAccent) }
                        Text(model.submitting ? "Starting…" : "Run backtest")
                    }
                }
                .buttonStyle(.dsPrimary(size: .large, fullWidth: true))
                .disabled(!model.canSubmit)
                .dsClearRow()
            }

            DSSection("Backtests", subtitle: "For this Kalshi account") {
                if model.backtests.isEmpty {
                    Text("No backtests yet")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                } else {
                    ForEach(Array(model.backtests.enumerated()), id: \.offset) { _, b in
                        backtestRow(model, b)
                    }
                }
            }
        }
        .dsList()
        .scrollDismissesKeyboard(.interactively)
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") { formEndEditing() }
            }
        }
    }

    private func modelLabel(_ model: KalshiBacktestModel) -> String {
        guard let id = model.modelId,
              let row = model.models.first(where: { KalshiFormat.firstNonNull($0["id"]) == id }) else { return "None (statistical model only)" }
        return KalshiFormat.firstNonNull(row["name"], row["model"], row["id"])
    }

    /// A date as the web's date input: the value (or "Not set") in a
    /// `ui-input`; tapping opens the picker sheet.
    private func dateField(_ label: String, _ date: Date?, action: @escaping () -> Void) -> some View {
        DSField(label) {
            Button(action: action) {
                HStack {
                    Text(date.map(KalshiFormat.ymd) ?? "Not set")
                        .foregroundStyle(date == nil ? DS.Text.faint : DS.Text.soft)
                        .monospacedDigit()
                    Spacer(minLength: 4)
                    Image(systemName: "calendar")
                        .font(.system(size: 13))
                        .foregroundStyle(DS.Text.muted)
                }
                .contentShape(Rectangle())
                .dsInput()
            }
            .buttonStyle(.plain)
            .accessibilityLabel(label)
            .accessibilityValue(date.map(KalshiFormat.ymd) ?? "Not set")
        }
    }

    /// One backtest: the dates over the short id, the P&L over the status.
    /// The row opens the results; Stop (while active) and Delete are swipe
    /// actions and context-menu items, where the Dart had icon buttons.
    private func backtestRow(_ model: KalshiBacktestModel, _ b: JSONObject) -> some View {
        let id = KalshiPregame.str(b["id"])
        let status = b["status"].flatMap { $0.isNull ? nil : $0.dartDescription }
        let summary = b["summary"]?.orderedObject ?? JSONObject()
        let pnl = summary["pnl_cents"]
        let active = status == "running" || status == "pending"
        let progress = Int((b["progress"]?.double ?? 0).rounded())
        let pnlValue = (pnl?.isNull ?? true) ? nil : pnl?.double
        let stop: () -> Void = {
            Task { showError(await model.stopBacktest(id)) }
        }
        let delete: () -> Void = {
            Task { showError(await model.deleteBacktest(id)) }
        }

        return NavigationLink(value: Route.kalshiBacktestResult(id)) {
            EntityRow(
                "\((b["start_date"] ?? .null).dartDescription) → \((b["end_date"] ?? .null).dartDescription)",
                subtitle: String(id.prefix(8))
            ) {
                VStack(alignment: .trailing, spacing: 2) {
                    Text(pnlValue.map { KalshiFormat.money(cents: $0) } ?? "—")
                        .font(DS.Font.body)
                        .monospacedDigit()
                        .foregroundStyle(pnlValue == nil ? DS.Text.muted : ((pnlValue ?? 0) >= 0 ? DS.Palette.gain : DS.Palette.loss))
                    StatusDot(
                        (active ? "\(status ?? "") \(progress)%" : (status ?? "")).dsSentenceCased,
                        status: status,
                        pulsing: active,
                        font: DS.Font.caption
                    )
                }
            }
        }
        // The Dart icon button deleted at once (no confirmation), so no full
        // swipe: the button has to be tapped, as before.
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button {
                delete()
            } label: {
                Label("Delete backtest", systemImage: Symbol.named("delete"))
            }
            .tint(DS.Swipe.destructive)
            if active {
                Button {
                    stop()
                } label: {
                    Label("Stop backtest", systemImage: Symbol.named("stop_circle"))
                }
                .tint(DS.Swipe.neutral)
            }
        }
        .contextMenu {
            Button {
                services.router.push(.kalshiBacktestResult(id))
            } label: {
                Label("View results", systemImage: Symbol.named("visibility"))
            }
            if active {
                Button {
                    stop()
                } label: {
                    Label("Stop backtest", systemImage: Symbol.named("stop_circle"))
                }
            }
            Divider()
            Button(role: .destructive) {
                delete()
            } label: {
                Label("Delete backtest", systemImage: Symbol.named("delete"))
            }
        }
    }

    /// A failed stop / delete's toast.
    private func showError(_ message: String?) {
        if let message { toast = Toast(message, style: .error) }
    }
}
