import SwiftUI

/// A menu picker drawn as the web's `select.ui-input`: the chosen value in
/// 14 pt zinc-200 and a muted chevron inside the input frame (`dsInput`);
/// tapping lists the options with a check on the chosen one. The system's
/// `.pickerStyle(.menu)` label ignores the input's font and padding, so the
/// options sit in a `Menu` with this label instead. Used by onboarding and
/// the live manual order; a candidate for the design system beside
/// `DSTextField`.
///
///     SelectInput(label: "Provider", selection: $provider, valueText: provider) {
///         ForEach(providers, id: \.self) { Text($0).tag($0) }
///     }
struct SelectInput<Value: Hashable, Options: View>: View {
    let label: String
    @Binding var selection: Value
    let valueText: String
    @ViewBuilder let options: Options

    var body: some View {
        Menu {
            Picker(label, selection: $selection) { options }
        } label: {
            HStack(spacing: 8) {
                Text(valueText)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.soft)
                    .lineLimit(1)
                Spacer(minLength: 8)
                Image(systemName: "chevron.up.chevron.down")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(DS.Text.muted)
            }
            .contentShape(Rectangle())
            .dsInput()
        }
        .accessibilityLabel(label)
        .accessibilityValue(valueText)
    }
}
