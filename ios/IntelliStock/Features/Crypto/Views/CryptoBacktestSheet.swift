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
        NavigationStack {
            Form {
                Section {
                    Text("Simulate \(name)'s current allocation over a historical window. Crypto fills include the taker fee.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .listRowBackground(Color.clear)
                        .listRowInsets(EdgeInsets())
                }
                Section("ALLOCATION UNDER TEST") {
                    if model.tickers.isEmpty {
                        Text("100% dynamic — the backtest auto-discovers its universe.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    } else {
                        MarketsFlowLayout {
                            ForEach(Array(model.tickers.enumerated()), id: \.offset) { _, t in MarketsChip(text: t) }
                        }
                    }
                }
                Section {
                    DatePicker("Start", selection: $m.start, in: earliest...Date(), displayedComponents: .date)
                    DatePicker("End", selection: $m.end, in: earliest...Date(), displayedComponents: .date)
                    LabeledContent {
                        Text("from Band").font(.caption).foregroundStyle(.secondary)
                    } label: {
                        Label {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Cadence").font(.caption).foregroundStyle(.secondary)
                                Text(model.cadenceLabel)
                            }
                        } icon: {
                            Image(systemName: Symbol.named("schedule")).foregroundStyle(.secondary)
                        }
                    }
                    LabeledContent("Initial cash ($)") {
                        HStack(spacing: 4) {
                            Text("$").foregroundStyle(.secondary)
                            TextField("10000", text: $m.cash)
                                .keyboardType(.numberPad)
                                .multilineTextAlignment(.trailing)
                                .monospacedDigit()
                        }
                    }
                }
                Section {
                    Picker("Emulate fees", selection: $m.feeVenue) {
                        ForEach(CryptoBacktestFormModel.feeVenues, id: \.key) { v in Text(v.label).tag(v.key) }
                    }
                } footer: {
                    Text(model.feeCaption)
                }
                if let err = model.err {
                    Section {
                        Text(err).font(.footnote).foregroundStyle(DS.Palette.danger)
                    }
                }
            }
            .navigationTitle("Backtest Crypto Instance")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .safeAreaInset(edge: .bottom) {
                Button {
                    Task { await submit() }
                } label: {
                    Text(model.busy ? "Queuing…" : "Run Backtest")
                        .fontWeight(.bold)
                        .frame(maxWidth: .infinity)
                }
                .dsProminentButton()
                .controlSize(.large)
                .disabled(model.busy)
                .padding(.horizontal, 20)
                .padding(.vertical, 8)
                .background(.bar)
            }
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
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
