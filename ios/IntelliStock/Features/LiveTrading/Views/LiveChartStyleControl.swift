import SwiftUI

/// The chart-style toggle (area / line / candles) as the web draws it: a
/// `ui-seg` whose segments are glyphs, not words. It mirrors
/// `DSSegmentedControl` (2 pt-padded surface track, 7% hairline, 8 radius;
/// 28 pt segments, the active one raised and zinc-100, the rest zinc-500),
/// which takes text labels only. A candidate for the design system as an
/// icon variant of `DSSegmentedControl`.
///
/// VoiceOver reads each segment by its name ("Area"), the active one selected.
struct LiveChartStyleControl: View {
    @Binding var selection: LiveChartStyle

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 0) {
            ForEach(LiveChartStyle.allCases, id: \.self) { style in
                let active = style == selection
                Button {
                    guard selection != style else { return }
                    withAnimation(DSMotion.standard(reduceMotion: reduceMotion)) { selection = style }
                } label: {
                    Image(systemName: Symbol.named(style.symbolName))
                        .font(.system(size: 14, weight: .regular))
                        .foregroundStyle(active ? DS.Text.primary : DS.Text.muted)
                        .frame(width: 36, height: 28)
                        .background {
                            if active {
                                RoundedRectangle(cornerRadius: DS.Radius.small, style: .continuous)
                                    .fill(DS.Surface.raised)
                                    .overlay(alignment: .top) {
                                        DSTopHighlight(color: DS.Line.highlight, radius: DS.Radius.small)
                                    }
                            }
                        }
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(style.accessibilityName)
                .accessibilityAddTraits(active ? .isSelected : [])
            }
        }
        .padding(2)
        .background(DS.Surface.card, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
        }
        .fixedSize()
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Chart style")
    }
}
