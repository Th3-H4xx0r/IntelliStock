import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// A single scrub reading: which data point is snapped to, and the horizontal
/// fraction `[0, 1]` at which the hairline + dot should be drawn (the data
/// point's own fraction, so the hairline passes exactly through the point).
@immutable
class ScrubSample {
  const ScrubSample({required this.index, required this.fraction});

  final int index;
  final double fraction;

  @override
  bool operator ==(Object other) =>
      other is ScrubSample &&
      other.index == index &&
      other.fraction == fraction;

  @override
  int get hashCode => Object.hash(index, fraction);
}

/// Default haptic: the soft iOS "selection" tick used by brokerage charts.
/// per-data-point feel while scrubbing. Wrapped in a block body so the returned
/// Future is intentionally discarded.
void defaultScrubTick() {
  HapticFeedback.selectionClick();
}

/// Holds the active scrub sample and emits a soft haptic tick each time the
/// snapped data index changes (not on every pixel of movement).
///
/// Driving the scrubber through a [ValueNotifier] lets the chart cache its
/// expensive Syncfusion widget and rebuild only a thin overlay + the header
/// value while the finger moves — which is what removes the scrubbing jank.
class ScrubController extends ValueNotifier<ScrubSample?> {
  ScrubController({VoidCallback? onTick})
    : _onTick = onTick ?? defaultScrubTick,
      super(null);

  final VoidCallback _onTick;

  /// Update to a new sample. Fires a haptic tick only when [index] differs from
  /// the current sample's index. Always notifies listeners so the hairline can
  /// follow the finger smoothly.
  void update(int index, double fraction) {
    final prev = value;
    if (prev == null || prev.index != index) {
      _onTick();
    }
    value = ScrubSample(index: index, fraction: fraction);
  }

  /// Clear the scrub state. Only notifies if there was an active sample.
  void clear() {
    if (value != null) value = null;
  }
}
