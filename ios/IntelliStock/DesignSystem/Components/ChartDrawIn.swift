import SwiftUI

/// The charts' entrance: the line draws itself in from the leading edge, as
/// `Sparkline` always has (operator, 2026-10-02: "make it animate side to side
/// like how the sparklines animate").
///
/// This half is pure, so the rules are testable; the modifier is
/// `View.chartDrawIn(trigger:duration:enabled:interacting:bleed:)`.
nonisolated enum ChartDrawIn {
    /// A full-size chart (a hero, a detail chart, a trend).
    static let chartDuration: Double = 0.9
    /// A row sparkline.
    static let sparkDuration: Double = 0.65
    /// How far the mask reaches past the chart's edges, so an end dot or its
    /// pulse ring overhanging the plot is never clipped.
    static let chartBleed: CGFloat = 16

    enum Step: Equatable {
        /// Already drawn for this trigger: a poll tick, a list row scrolling
        /// back, a tab revisited.
        case none
        /// Show the whole chart at once (Reduce Motion, or disabled).
        case show
        /// Draw it in from the leading edge.
        case animate
    }

    /// What a chart does when it appears, or when its trigger changes.
    ///
    /// `drawn` is the trigger it last drew in for (nil before it first
    /// appears). A new series (a range or account switch) is a new trigger
    /// and draws in again; a poll that appends or updates points keeps the
    /// trigger, so the chart stays still.
    static func step(drawn: AnyHashable?, trigger: AnyHashable, enabled: Bool, reduceMotion: Bool) -> Step {
        if drawn == trigger { return .none }
        return animates(enabled: enabled, reduceMotion: reduceMotion) ? .animate : .show
    }

    static func animates(enabled: Bool, reduceMotion: Bool) -> Bool {
        enabled && !reduceMotion
    }

    /// The fraction of the width the mask uncovers, 0…1.
    ///
    /// - `target` is the settled draw count and `phase` its animated value, so
    ///   `target − phase` is the distance still to draw: 1 just after a
    ///   replay starts, 0 at rest. A replay that interrupts another clamps at
    ///   0 until the new run catches up.
    /// - `pending`: the chart is about to draw in (it has not appeared yet, or
    ///   its trigger just changed): keep it hidden, so the new series never
    ///   flashes in whole for a frame first.
    /// - `complete`: a finger is (or was, during this run) on the chart, so
    ///   the scrub hairline and dot are never behind the edge.
    static func revealed(phase: Double, target: Double, pending: Bool, complete: Bool) -> Double {
        if complete { return 1 }
        if pending { return 0 }
        return min(max(1 - (target - phase), 0), 1)
    }
}

extension View {
    /// Draws a chart in from the leading edge: a mask whose width eases out
    /// from 0 to the full width over `duration`, like `Sparkline`.
    ///
    /// - It runs when the chart first appears and again whenever `trigger`
    ///   changes. Pass what names the series (its range, its account, the
    ///   range the data was loaded for), never the points themselves, so a
    ///   poll tick keeps the chart still. Leave it out to draw in on
    ///   appearance only.
    /// - Under Reduce Motion, or with `enabled: false`, the chart shows at
    ///   once.
    /// - It never blocks the scrub: a mask leaves hit-testing alone, and
    ///   `interacting: true` (a finger on the chart) uncovers the whole chart
    ///   at once, so the hairline and dot always show.
    /// - On a chart with axes, apply it inside `.chartPlotStyle` so only the
    ///   plot draws in and the axis labels and legend stay put.
    func chartDrawIn(
        trigger: AnyHashable = AnyHashable(0),
        duration: Double = ChartDrawIn.chartDuration,
        enabled: Bool = true,
        interacting: Bool = false,
        bleed: CGFloat = ChartDrawIn.chartBleed
    ) -> some View {
        modifier(ChartDrawInModifier(
            trigger: trigger,
            duration: duration,
            enabled: enabled,
            interacting: interacting,
            bleed: bleed
        ))
    }
}

private struct ChartDrawInModifier: ViewModifier {
    let trigger: AnyHashable
    let duration: Double
    let enabled: Bool
    let interacting: Bool
    let bleed: CGFloat

    /// The trigger the chart last drew in for.
    @State private var drawn: AnyHashable?
    /// Bumped once per draw-in; the mask animates its phase up to it.
    @State private var generation = 0
    /// A scrub began mid-draw: stay fully uncovered until the next draw-in.
    @State private var finishedEarly = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func body(content: Content) -> some View {
        let pending = drawn != trigger && ChartDrawIn.animates(enabled: enabled, reduceMotion: reduceMotion)
        content
            .mask {
                ChartDrawInMask(
                    phase: Double(generation),
                    target: Double(generation),
                    pending: pending,
                    complete: interacting || finishedEarly,
                    bleed: bleed
                )
            }
            .onAppear(perform: run)
            .onChange(of: trigger) { run() }
            .onChange(of: interacting) { _, now in
                if now { finishedEarly = true }
            }
    }

    private func run() {
        switch ChartDrawIn.step(drawn: drawn, trigger: trigger, enabled: enabled, reduceMotion: reduceMotion) {
        case .none:
            return
        case .show:
            drawn = trigger
        case .animate:
            finishedEarly = false
            withAnimation(.easeOut(duration: duration)) {
                drawn = trigger
                generation += 1
            }
        }
    }
}

/// The leading-edge mask. Only `phase` animates: `target` jumps to the new
/// draw count at once, so the uncovered width runs from 0 back up to 1.
nonisolated private struct ChartDrawInMask: Shape {
    var phase: Double
    let target: Double
    let pending: Bool
    let complete: Bool
    let bleed: CGFloat

    var animatableData: Double {
        get { phase }
        set { phase = newValue }
    }

    func path(in rect: CGRect) -> Path {
        let f = ChartDrawIn.revealed(phase: phase, target: target, pending: pending, complete: complete)
        // The edge runs from the chart's leading edge to `bleed` past its
        // trailing one; the bleed on the other three sides is always open.
        let width = bleed + (rect.width + bleed) * f
        return Path(CGRect(
            x: rect.minX - bleed,
            y: rect.minY - bleed,
            width: width,
            height: rect.height + 2 * bleed
        ))
    }
}
