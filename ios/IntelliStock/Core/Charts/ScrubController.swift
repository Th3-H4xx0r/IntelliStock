import Foundation
import Observation
import UIKit

/// One scrub reading: the snapped data index and the horizontal fraction at
/// which the hairline and dot sit (the point's own fraction, so the hairline
/// passes through it).
nonisolated struct ScrubSample: Hashable, Sendable {
    let index: Int
    let fraction: Double
}

/// Holds the active scrub sample and ticks a selection haptic each time the
/// snapped index changes — not on every point of finger movement. Ported
/// from `core/charts/scrub_controller.dart`.
///
/// `ScrubbableAreaChart` passes a no-op tick and uses `.sensoryFeedback`
/// instead; hand-built charts can keep the default tick.
@Observable
final class ScrubController {
    /// The current sample; nil when not scrubbing. Changes only when the
    /// sample actually differs (`ValueNotifier` semantics).
    private(set) var value: ScrubSample?

    @ObservationIgnored private let onTick: () -> Void

    init(onTick: (() -> Void)? = nil) {
        self.onTick = onTick ?? ScrubController.defaultTick
    }

    /// The soft iOS selection tick brokerage charts use.
    static func defaultTick() {
        UISelectionFeedbackGenerator().selectionChanged()
    }

    /// Moves to a new sample. Ticks only when `index` differs from the
    /// current sample's index.
    func update(_ index: Int, _ fraction: Double) {
        if value?.index != index {
            onTick()
        }
        let next = ScrubSample(index: index, fraction: fraction)
        if value != next { value = next }
    }

    /// Ends the scrub. Changes nothing when there was no sample.
    func clear() {
        if value != nil { value = nil }
    }
}
