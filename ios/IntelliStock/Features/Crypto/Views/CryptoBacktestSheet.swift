import SwiftUI

/// Configure and launch a backtest of a crypto instance's allocation —
/// `CryptoBacktestSheet`. With `onCreated` the caller refreshes in place;
/// otherwise the result opens (`/backtests/:id`, or `/backtests`).
struct CryptoBacktestSheet: View {
    let onCreated: (() -> Void)?

    @Environment(AppServices.self) private var services
    @Environment(\.dismiss) private var dismiss
    @State private var model: CryptoBacktestFormModel

    init(inst: Instance, repository: @escaping () -> CryptoRepository, onCreated: (() -> Void)?) {
        self.onCreated = onCreated
        _model = State(initialValue: CryptoBacktestFormModel(inst: inst, repository: repository))
    }

    var body: some View {
        @Bindable var m = model
        let name = model.inst.name.isEmpty ? model.inst.id : model.inst.name
        let earliest = Calendar.current.date(from: DateComponents(year: 2018, month: 1, day: 1))!
        FormSheet(
            "Backtest crypto instance",
            subtitle: "Uses the instance's configured allocation",
            closeDisabled: model.busy,
            onClose: { dismiss() }
        ) {
            Text("Simulate \(Text(name).foregroundStyle(DS.Text.soft))'s current allocation over a historical window. Crypto fills include the taker fee.")
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.secondary)
                .fixedSize(horizontal: false, vertical: true)

            DSField("Allocation under test", help: model.tickers.isEmpty ? "100% dynamic — the backtest auto-discovers its own universe." : nil) {
                if !model.tickers.isEmpty {
                    MarketsFlowLayout(spacing: 6) {
                        ForEach(Array(model.tickers.enumerated()), id: \.offset) { _, t in MarketsChip(text: t) }
                    }
                }
            }

            FormGrid {
                DSField("Start date") {
                    DatePicker("Start date", selection: $m.start, in: earliest...Date(), displayedComponents: .date)
                        .labelsHidden()
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                DSField("End date") {
                    DatePicker("End date", selection: $m.end, in: earliest...Date(), displayedComponents: .date)
                        .labelsHidden()
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }

            // Cadence comes from the instance's band: read-only.
            DSField("Cadence", help: "From the band. Change it in Edit") {
                HStack(spacing: 8) {
                    Image(systemName: Symbol.named("schedule"))
                        .font(.system(size: 14))
                        .foregroundStyle(DS.Text.muted)
                    Text(model.cadenceLabel)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.soft)
                        .lineLimit(1)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 12)
                .frame(minHeight: 36)
                .overlay {
                    RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                        .strokeBorder(DS.Line.divider, lineWidth: DS.Line.width)
                }
                .accessibilityElement(children: .combine)
            }

            DSField("Emulate fees", help: model.feeCaption) {
                FormSelect(
                    "Emulate fees",
                    display: CryptoBacktestFormModel.feeVenues.first { $0.key == model.feeVenue }?.label ?? model.feeVenue,
                    selection: $m.feeVenue
                ) {
                    ForEach(CryptoBacktestFormModel.feeVenues, id: \.key) { v in Text(v.label).tag(v.key) }
                }
            }

            DSField("Initial cash") {
                FormTextField("10000", text: $m.cash, keyboard: .numberPad, prefix: "$")
                    .monospacedDigit()
            }

            if let err = model.err {
                FormMessage(err, tone: .error)
            }
        } footer: {
            FormSheetActions(
                primary: "Run backtest",
                busyLabel: "Queuing…",
                busy: model.busy,
                cancelDisabled: model.busy,
                onPrimary: { Task { await submit() } },
                onCancel: { dismiss() }
            )
        }
        .presentationDetents([.large])
    }

    private func submit() async {
        guard let id = await model.submit() else { return }
        dismiss()
        if let onCreated {
            onCreated()
        } else {
            services.router.push(id.isEmpty ? .backtests : .backtest(id))
        }
    }
}
