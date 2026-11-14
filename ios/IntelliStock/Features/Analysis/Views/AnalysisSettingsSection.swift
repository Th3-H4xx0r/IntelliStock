import SwiftUI

/// Settings › AI analysis: the model that explains moves (any registered
/// model, or none), and the two thresholds that decide which days stand
/// out. Each change saves on its own.
struct AnalysisSettingsSection: View {
    @State private var model: AnalysisSettingsModel

    init(services: AppServices) {
        _model = State(initialValue: AnalysisSettingsModel(
            repository: { [unowned services] in services.analysisRepository }
        ))
    }

    var body: some View {
        DSSection("AI analysis", subtitle: "Explains why a stock moved on the AI analysis screen") {
            switch model.settings {
            case .loading:
                SettingsRow(icon: "psychology", tile: "text.magnifyingglass", title: "Model", subtitle: "Loading…") {
                    ProgressView()
                        .controlSize(.small)
                }
                .task { await model.load() }
            case .failed(let error):
                SettingsRow(
                    icon: "psychology",
                    tile: "text.magnifyingglass",
                    title: "AI analysis settings",
                    subtitle: model.isUnsupported ? AnalysisFormat.missingRouteMessage : settingsErrorText(error)
                ) {
                    // Retrying an older server or a bad request changes nothing.
                    if !model.isUnsupported, AnalysisFormat.isRetryable(error) {
                        Button("Retry") { Task { await model.retry() } }
                            .buttonStyle(.dsSecondary)
                    }
                }
            case .loaded(let s):
                modelRow(s)
                    .onDisappear { Task { await model.flush() } }
                sigmaRow(s)
                minMoveRow(s)
                if let error = model.error {
                    Text(error)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Palette.loss)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    private func modelRow(_ s: AnalysisSettings) -> some View {
        SettingsRow(
            icon: "psychology",
            tile: "text.magnifyingglass",
            title: "Model",
            subtitle: s.models.isEmpty ? "Add one under More › Models first" : "Names the cause from news it must cite"
        ) {
            Menu {
                Picker("Model", selection: Binding(
                    get: { s.modelId },
                    set: { id in Task { await model.setModel(id) } }
                )) {
                    Text("None").tag(String?.none)
                    ForEach(s.models) { option in
                        Text(option.displayName).tag(Optional(option.id))
                    }
                }
                .pickerStyle(.inline)
            } label: {
                HStack(spacing: 6) {
                    Text(s.modelLabel)
                        .lineLimit(1)
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.system(size: 12))
                        .foregroundStyle(DS.Text.muted)
                }
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.secondary)
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .accessibilityLabel("Model, \(s.modelLabel)")
        }
    }

    private func sigmaRow(_ s: AnalysisSettings) -> some View {
        let v = dartToStringAsFixed(s.sigma, 1)
        // The stepper sits beside the row, not in it: the row combines its
        // children for VoiceOver, which would swallow the stepper's adjust.
        return HStack(spacing: 12) {
            SettingsRow(
                icon: "tune",
                tile: "waveform.path.ecg",
                title: "Unusual move",
                subtitle: "Flag at \(v)× the stock's usual move"
            ) { EmptyView() }
            stepper(
                "Unusual move",
                value: "\(v)×",
                binding: Binding(get: { s.sigma }, set: { model.setSigma($0) }),
                range: AnalysisSettings.sigmaRange
            )
        }
    }

    private func minMoveRow(_ s: AnalysisSettings) -> some View {
        let v = dartToStringAsFixed(s.minMovePct, 1)
        let any = dartToStringAsFixed(s.minMovePct * 2, 1)
        return HStack(spacing: 12) {
            SettingsRow(
                icon: "tune",
                tile: "arrow.up.and.down",
                title: "Minimum move",
                subtitle: "Ignore moves under \(v)%; flag any \(any)% day"
            ) { EmptyView() }
            stepper(
                "Minimum move",
                value: "\(v)%",
                binding: Binding(get: { s.minMovePct }, set: { model.setMinMovePct($0) }),
                range: AnalysisSettings.minMoveRange
            )
        }
    }

    /// The value is in the row's subtitle; the stepper reads it to VoiceOver.
    private func stepper(_ label: String, value: String, binding: Binding<Double>, range: ClosedRange<Double>) -> some View {
        Stepper(label, value: binding, in: range, step: AnalysisSettingsModel.step)
            .labelsHidden()
            .accessibilityValue(value)
    }
}
