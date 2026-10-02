import SwiftUI

/// The Kalshi backtest launcher — `KalshiBacktestScreen`: settings seeded
/// from the instance config, a risk preset, dates, leagues, and the list of
/// this brokerage's backtests (refreshed every 3 s). A native `Form`; each
/// backtest row opens its results, with Stop and Delete on swipe and in the
/// context menu.
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
        .navigationBarTitleDisplayMode(.inline)
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

    private func form(_ model: KalshiBacktestModel) -> some View {
        @Bindable var m = model
        return Form {
            if let err = model.err {
                Section {
                    Text(err).foregroundStyle(DS.Palette.warning)
                }
            }
            Section("New backtest") {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Risk tolerance").font(.footnote).foregroundStyle(.secondary)
                    Picker("Risk tolerance", selection: Binding(get: { model.tier }, set: { model.applyPreset($0) })) {
                        ForEach(KalshiBacktestPreset.all, id: \.key) { p in Text(p.label).tag(p.key) }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                }
                Picker("Analyst LLM model", selection: Binding(get: { model.modelId }, set: { model.selectModel($0) })) {
                    Text("None (statistical model only)").tag(String?.none)
                    ForEach(model.models.indices, id: \.self) { i in
                        let row = model.models[i]
                        Text(KalshiFormat.firstNonNull(row["name"], row["model"], row["id"]))
                            .tag(Optional(KalshiFormat.firstNonNull(row["id"])))
                    }
                }
                Toggle("Use LLM analyst in this backtest", isOn: $m.useLlm)
                    .disabled(model.modelId == nil)
                dateRow("Start", model.start) { picking = .start }
                dateRow("End", model.end) { picking = .end }
                KalshiLeaguePicker(selected: $m.leagues)
            }
            Section {
                ForEach(KalshiBacktestModel.Field.allCases, id: \.self) { f in
                    MarketsNumberRow(label: f.label, text: Binding(get: { model.text(f) }, set: { model.edit(f, $0) }))
                }
                VStack(alignment: .leading, spacing: 6) {
                    Text("OddsPapi API key (saved; blank = model-only)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    TextField("OddsPapi API key (saved; blank = model-only)", text: $m.oddsKey)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
            }
            Section {
                Button {
                    Task {
                        if let id = await model.submit() {
                            services.router.push(.kalshiBacktestResult(id))
                        }
                    }
                } label: {
                    Text(model.submitting ? "Starting…" : "Run Backtest")
                        .fontWeight(.semibold)
                        .frame(maxWidth: .infinity)
                }
                .dsProminentButton()
                .controlSize(.large)
                .disabled(!model.canSubmit)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())
            }
            Section("Backtests") {
                if model.backtests.isEmpty {
                    Text("No backtests yet.").foregroundStyle(.secondary)
                } else {
                    ForEach(Array(model.backtests.enumerated()), id: \.offset) { _, b in
                        backtestRow(model, b)
                    }
                }
            }
        }
    }

    private func dateRow(_ label: String, _ date: Date?, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            LabeledContent(label) {
                Text(date.map(KalshiFormat.ymd) ?? "Not set")
                    .monospacedDigit()
                    .foregroundStyle(date == nil ? .tertiary : .secondary)
            }
        }
        .foregroundStyle(.primary)
    }

    private func statusColor(_ s: String?) -> Color {
        switch s {
        case "finished": DS.Palette.success
        case "error": DS.Palette.danger
        case "stopped": .secondary
        default: DS.Palette.warning
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
                        .monospacedDigit()
                        .foregroundStyle((pnlValue ?? 0) >= 0 ? DS.Palette.success : DS.Palette.danger)
                    StatusDot(active ? "\(status ?? "") \(progress)%" : (status ?? ""), color: statusColor(status), pulsing: active, font: .footnote)
                }
            }
        }
        // The Dart icon button deleted at once (no confirmation), so no full
        // swipe: the button has to be tapped, as before.
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button {
                delete()
            } label: {
                Label("Delete Backtest", systemImage: Symbol.named("delete"))
            }
            .tint(DS.Palette.danger)
            if active {
                Button {
                    stop()
                } label: {
                    Label("Stop Backtest", systemImage: Symbol.named("stop_circle"))
                }
                .tint(DS.Palette.warning)
            }
        }
        .contextMenu {
            Button {
                services.router.push(.kalshiBacktestResult(id))
            } label: {
                Label("View Results", systemImage: Symbol.named("visibility"))
            }
            if active {
                Button {
                    stop()
                } label: {
                    Label("Stop Backtest", systemImage: Symbol.named("stop_circle"))
                }
            }
            Divider()
            Button(role: .destructive) {
                delete()
            } label: {
                Label("Delete Backtest", systemImage: Symbol.named("delete"))
            }
        }
    }

    /// A failed stop / delete's toast.
    private func showError(_ message: String?) {
        if let message { toast = Toast(message, style: .error) }
    }
}
