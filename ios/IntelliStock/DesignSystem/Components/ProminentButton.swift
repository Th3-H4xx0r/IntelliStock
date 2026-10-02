import SwiftUI

extension View {
    /// The primary action style (`AppButton.primary`): `.borderedProminent`
    /// in the accent, with `DS.Palette.onAccent` labels so the text keeps
    /// 4.5:1 on the light dark-mode accent. Use instead of a bare
    /// `.buttonStyle(.borderedProminent)`.
    func dsProminentButton() -> some View {
        buttonStyle(.borderedProminent)
            .foregroundStyle(DS.Palette.onAccent)
    }

    /// The floating Liquid Glass primary action (`AuthPillButton`, floating
    /// CTAs): `.glassProminent` with `DS.Palette.onAccent` labels.
    func dsGlassProminentButton() -> some View {
        buttonStyle(.glassProminent)
            .foregroundStyle(DS.Palette.onAccent)
    }
}
