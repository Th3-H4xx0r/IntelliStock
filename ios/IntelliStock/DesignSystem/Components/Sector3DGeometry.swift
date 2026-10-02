import CoreGraphics
import Foundation

// The pure maths behind `Sector3DChart`, ported 1:1 from
// mobile/lib/features/dashboard/presentation/sector_3d_chart.dart: the
// painter's lerped projection, the extruded faces, wedge hit-testing,
// nearest-sector snapping, Flutter's easing curves and the two
// `AnimationController`s. Nothing here touches SwiftUI, so all of it is
// unit-tested (`Sector3DGeometryTests`).

// MARK: - Constants and the state helpers (`_Sector3DChartState`)

nonisolated enum Sector3DGeometry {
    /// `_flatH`, `_drillH`: the chart's height flat and drilled in.
    static let flatHeight: Double = 280
    static let drillHeight: Double = 320
    /// The drill controller (`_drill`, 620 ms) and the drilled focus-change
    /// rotation (`_rotCtrl`, 380 ms).
    static let drillDuration: TimeInterval = 0.620
    static let rotationDuration: TimeInterval = 0.380
    /// `_rotPerPx`: drag sensitivity while drilled, in radians per point.
    static let rotPerPx: Double = 0.009
    /// The flat view's swipe step (`const step = 44.0`).
    static let swipeStep: Double = 44
    /// `_RingPainter._gap`: the angular gap between sectors, in radians.
    static let gap: Double = 0.05
    /// Taps only drill in while the drill controller is at or below this
    /// (`if (_drill.value > 0.05) return`); the painter records wedge hits
    /// only below it too.
    static let tapThreshold: Double = 0.05
    /// A drag rotates the ring only once the drill controller passes this
    /// (`if (_drill.value > 0.5)`).
    static let dragRotateThreshold: Double = 0.5
    /// A slice at or above this share is drawn as one solid disc.
    static let fullDiscShare: Double = 0.995
    /// Flutter's touch slop (`kTouchSlop`): how far a finger travels before a
    /// horizontal drag starts.
    static let touchSlop: Double = 18
    /// How far the drilled ring may draw past the chart's frame. The ring is
    /// 1.16x the chart's width when drilled and its front hangs below the
    /// frame; the Dart card clipped it at its padding, so the chart clips at
    /// the native `Card`'s 20 pt padding.
    static let overflowAllowance: Double = 20

    /// The chart's height for an eased drill value `d`.
    static func height(drill d: Double) -> Double {
        flatHeight + (drillHeight - flatHeight) * d
    }

    /// Dart's `%` on doubles: the result always has the divisor's sign.
    static func dartMod(_ x: Double, _ m: Double) -> Double {
        let r = x.truncatingRemainder(dividingBy: m)
        return r < 0 ? r + m : r
    }

    static func total(_ slices: [SectorSlice]) -> Double {
        slices.reduce(0) { $0 + $1.pct }
    }

    /// `_natMid`: the un-rotated mid-angle offset of sector `i` from the
    /// ring's start (-π/2).
    static func naturalMid(_ i: Int, _ slices: [SectorSlice]) -> Double {
        let total = total(slices)
        if total <= 0 { return 0 }
        var off = 0.0
        for j in 0..<i {
            off += slices[j].pct / total * 2 * .pi
        }
        return off + slices[i].pct / total * .pi
    }

    /// `_targetRot`: the rotation that brings sector `i` to the top.
    static func targetRotation(_ i: Int, _ slices: [SectorSlice]) -> Double {
        -naturalMid(i, slices)
    }

    /// `_nearestTop`: the sector whose mid-angle is nearest the top at `rot`.
    static func nearestTop(rotation rot: Double, _ slices: [SectorSlice]) -> Int {
        var best = 0
        var bestD = Double.infinity
        for i in slices.indices {
            var d = dartMod(naturalMid(i, slices) + rot, 2 * .pi)
            if d > .pi { d -= 2 * .pi }
            if d < -.pi { d += 2 * .pi }
            if abs(d) < bestD {
                bestD = abs(d)
                best = i
            }
        }
        return best
    }

    /// `_shortestTo`: the angle equivalent to `target` nearest `from`.
    static func shortest(to target: Double, from: Double) -> Double {
        var t = target
        while t - from > .pi { t -= 2 * .pi }
        while t - from < -.pi { t += 2 * .pi }
        return t
    }

    /// `_advanceFlat`'s index step, wrapping. Nil when it would not move.
    static func advanced(_ selected: Int, by delta: Int, count n: Int) -> Int? {
        if n == 0 { return nil }
        var next = (selected + delta) % n
        if next < 0 { next += n }
        return next == selected ? nil : next
    }

    /// The brightness ramp, from 0 (deep) to 1 (bright). Every wedge is
    /// violet. Brightness falls with index, so the larger sectors are
    /// brighter and neighbours read apart, and the selected wedge is
    /// brightest.
    static func brightness(index i: Int, selected: Bool, count n: Int) -> Double {
        if selected { return 1 }
        return n <= 1 ? 0.58 : 0.30 + 0.42 * (1 - Double(i) / Double(n - 1))
    }

    /// The index of the largest slice; the first one wins ties.
    static func dominantIndex(_ slices: [SectorSlice]) -> Int {
        var dom = 0
        for k in slices.indices.dropFirst() where slices[k].pct > slices[dom].pct {
            dom = k
        }
        return dom
    }

    /// One slice holds about 100%, so it draws as a solid disc rather than a
    /// ring.
    static func isFullDisc(_ slices: [SectorSlice]) -> Bool {
        let total = total(slices)
        guard total > 0, !slices.isEmpty else { return false }
        return slices[dominantIndex(slices)].pct / total >= fullDiscShare
    }

    /// VoiceOver's name for a sector: "Energy, 34 percent".
    static func accessibilityLabel(_ slice: SectorSlice) -> String {
        "\(slice.sector), \(Int(slice.pct.rounded())) percent"
    }

    /// The angular parts of `[a0, a1]` that face the viewer (`front`, where
    /// sin θ > 0) or face away from it (sin θ < 0). The outer wall shows only
    /// its front-facing part and the inner wall only its back-facing part,
    /// because those are the only parts a viewer above and in front can see.
    static func facingRanges(_ a0: Double, _ a1: Double, front: Bool) -> [ClosedRange<Double>] {
        guard a1 > a0 else { return [] }
        let lo = front ? 0.0 : -Double.pi
        let hi = front ? Double.pi : 0.0
        let twoPi = 2 * Double.pi
        var out: [ClosedRange<Double>] = []
        var k = (((a0 - hi) / twoPi).rounded(.down))
        while lo + k * twoPi < a1 {
            let s = max(a0, lo + k * twoPi)
            let e = min(a1, hi + k * twoPi)
            if e - s > 1e-9 { out.append(s...e) }
            k += 1
        }
        return out
    }
}

// MARK: - Flutter curves

/// Flutter's `Cubic` curve, ported exactly: it bisects the x polynomial to
/// within 0.001, then evaluates y.
nonisolated struct Sector3DCurve: Equatable, Sendable {
    let a: Double
    let b: Double
    let c: Double
    let d: Double

    /// `Curves.easeInOut`, for the drilled focus-change rotation.
    static let easeInOut = Sector3DCurve(a: 0.42, b: 0.0, c: 0.58, d: 1.0)
    /// `Curves.easeInOutCubic`, for the drill.
    static let easeInOutCubic = Sector3DCurve(a: 0.645, b: 0.045, c: 0.355, d: 1.0)

    private static func evaluate(_ a: Double, _ b: Double, _ m: Double) -> Double {
        3 * a * (1 - m) * (1 - m) * m + 3 * b * (1 - m) * m * m + m * m * m
    }

    func transform(_ t: Double) -> Double {
        if t <= 0 { return 0 }
        if t >= 1 { return 1 }
        var start = 0.0
        var end = 1.0
        while true {
            let midpoint = (start + end) / 2
            let estimate = Self.evaluate(a, c, midpoint)
            if abs(t - estimate) < 0.001 {
                return Self.evaluate(b, d, midpoint)
            }
            if estimate < t {
                start = midpoint
            } else {
                end = midpoint
            }
        }
    }
}

// MARK: - AnimationController

/// A port of Flutter's `AnimationController` for a 0...1 range. The value
/// moves linearly towards its target, and a run's length is the full
/// duration scaled by the distance left: `forward()` from 0.4 takes 60% of
/// it. Time is passed in, so tests drive it directly.
nonisolated struct Sector3DTween: Equatable, Sendable {
    private struct Run: Equatable, Sendable {
        let from: Double
        let to: Double
        let start: TimeInterval
        let length: TimeInterval
    }

    let duration: TimeInterval
    private var resting: Double
    private var run: Run?

    init(duration: TimeInterval, value: Double = 0) {
        self.duration = duration
        self.resting = value
    }

    func value(at now: TimeInterval) -> Double {
        guard let run else { return resting }
        if run.length <= 0 { return run.to }
        let p = min(max((now - run.start) / run.length, 0), 1)
        return run.from + (run.to - run.from) * p
    }

    /// Where the value is heading: the run's end, or the resting value.
    var target: Double { run?.to ?? resting }

    /// When the current run ends, or nil when at rest.
    var endTime: TimeInterval? { run.map { $0.start + $0.length } }

    func isAnimating(at now: TimeInterval) -> Bool {
        guard let end = endTime else { return false }
        return now < end
    }

    /// `controller.value = v`: jump there and stop.
    mutating func set(_ v: Double) {
        resting = min(max(v, 0), 1)
        run = nil
    }

    /// `forward()` / `reverse()`: run from the current value to `target`.
    mutating func animate(to target: Double, at now: TimeInterval) {
        let current = value(at: now)
        let length = duration * abs(target - current)
        if length <= 0 {
            set(target)
            return
        }
        run = Run(from: current, to: target, start: now, length: length)
    }

    mutating func forward(at now: TimeInterval) { animate(to: 1, at: now) }

    /// `forward(from: v)`.
    mutating func forward(from v: Double, at now: TimeInterval) {
        set(v)
        forward(at: now)
    }

    mutating func reverse(at now: TimeInterval) { animate(to: 0, at: now) }

    /// Drops a finished run, so the value rests at its target.
    mutating func settle(at now: TimeInterval) {
        guard let run, now >= run.start + run.length else { return }
        resting = run.to
        self.run = nil
    }
}

// MARK: - Interaction state (`_Sector3DChartState`)

/// Everything `_Sector3DChartState` tracked, with its methods, so the
/// gestures are testable without a view. `now` is in seconds on any
/// monotonic clock.
nonisolated struct Sector3DInteraction: Equatable, Sendable {
    /// The focused sector, raw. It may run past the end when the slices
    /// shrink, as in Dart; read `selected(count:)` to draw.
    private(set) var selected = 0
    private(set) var drill = Sector3DTween(duration: Sector3DGeometry.drillDuration)
    private(set) var rotation = Sector3DTween(duration: Sector3DGeometry.rotationDuration)
    private(set) var rotFrom = 0.0
    private(set) var rotTo = 0.0
    /// Finger-tracking rotation while drilled.
    private(set) var dragging = false
    private(set) var rot = 0.0
    private(set) var dragAcc = 0.0
    /// A drilled drag with Reduce Motion on steps sectors instead of turning
    /// the ring.
    private(set) var steppingDrilled = false

    /// Haptic triggers: `selectionClick`, `mediumImpact` (drill in) and
    /// `lightImpact` (Back).
    private(set) var selectionTicks = 0
    private(set) var drillTicks = 0
    private(set) var backTicks = 0

    init() {}

    func selected(count n: Int) -> Int {
        n == 0 ? 0 : min(max(selected, 0), n - 1)
    }

    /// `_rotNow`: the rotation on screen.
    func rotNow(at now: TimeInterval) -> Double {
        if dragging { return rot }
        let t = Sector3DCurve.easeInOut.transform(rotation.value(at: now))
        return rotFrom + (rotTo - rotFrom) * t
    }

    /// Drilled in, or on the way in.
    var isDrilledIn: Bool { drill.target >= 1 }

    var animationDeadline: TimeInterval? {
        [drill.endTime, rotation.endTime].compactMap { $0 }.max()
    }

    func isAnimating(at now: TimeInterval) -> Bool {
        drill.isAnimating(at: now) || rotation.isAnimating(at: now)
    }

    mutating func settle(at now: TimeInterval) {
        drill.settle(at: now)
        rotation.settle(at: now)
    }

    /// `_advanceFlat`: the flat ring does not turn; a step moves the
    /// highlight.
    mutating func advanceFlat(_ delta: Int, count n: Int) {
        guard let next = Sector3DGeometry.advanced(selected, by: delta, count: n) else { return }
        selectionTicks += 1
        selected = next
    }

    /// `_dragRotate`: turn the ring with the finger, live-selecting whatever
    /// sector is nearest the top.
    mutating func dragRotate(_ dx: Double, _ slices: [SectorSlice]) {
        rot += dx * Sector3DGeometry.rotPerPx
        let next = Sector3DGeometry.nearestTop(rotation: rot, slices)
        if next != selected {
            selected = next
            selectionTicks += 1
        }
    }

    /// `_settleRotation`: on release, ease the nearest sector exactly to the
    /// top over 380 ms. With `animated` false (Reduce Motion) it jumps.
    mutating func settleRotation(_ slices: [SectorSlice], at now: TimeInterval, animated: Bool = true) {
        let target = Sector3DGeometry.shortest(
            to: Sector3DGeometry.targetRotation(selected(count: slices.count), slices), from: rot
        )
        dragging = false
        rotFrom = animated ? rot : target
        rotTo = target
        rot = target
        if animated {
            rotation.forward(from: 0, at: now)
        } else {
            rotation.set(1)
        }
    }

    /// `_drillInto`: focus sector `i` and drill in. The rotation settles on
    /// the target at once; the drill sweeps it up as `ringRot * drill`.
    mutating func drillInto(_ i: Int, _ slices: [SectorSlice], at now: TimeInterval) {
        guard slices.indices.contains(i) else { return }
        drillTicks += 1
        let target = Sector3DGeometry.targetRotation(i, slices)
        selected = i
        rotFrom = target
        rotTo = target
        rot = target
        rotation.set(1)
        drill.forward(at: now)
    }

    /// `_back`.
    mutating func back(at now: TimeInterval) {
        backTicks += 1
        drill.reverse(at: now)
    }

    /// `_onTapDown`: a tap on a wedge drills into it, but only while flat.
    /// Returns whether it drilled.
    @discardableResult
    mutating func tap(at point: CGPoint, scene: Sector3DScene, _ slices: [SectorSlice], at now: TimeInterval) -> Bool {
        if drill.value(at: now) > Sector3DGeometry.tapThreshold { return false }
        guard let i = scene.hitTest(point) else { return false }
        drillInto(i, slices, at: now)
        return true
    }

    /// `onHorizontalDragStart`. Past half-drilled, grab the rotation on
    /// screen and follow the finger. With Reduce Motion on, a drilled drag
    /// steps sectors instead.
    mutating func dragBegan(at now: TimeInterval, reduceMotion: Bool = false) {
        dragAcc = 0
        let drilled = drill.value(at: now) > Sector3DGeometry.dragRotateThreshold
        steppingDrilled = drilled && reduceMotion
        if drilled && !reduceMotion {
            rot = rotNow(at: now)
            dragging = true
        }
    }

    /// `onHorizontalDragUpdate` with the horizontal delta since the last
    /// update.
    mutating func dragMoved(by dx: Double, _ slices: [SectorSlice], at now: TimeInterval) {
        if dragging {
            dragRotate(dx, slices)
            return
        }
        dragAcc += dx
        let step = Sector3DGeometry.swipeStep
        while abs(dragAcc) >= step {
            let dir = dragAcc > 0 ? 1 : -1
            if steppingDrilled {
                // A rightward drag turns the ring clockwise, which brings the
                // previous sector to the top.
                self.step(-dir, slices, at: now, animated: false)
            } else {
                advanceFlat(dir, count: slices.count)
            }
            dragAcc -= dragAcc > 0 ? step : -step
        }
    }

    /// `onHorizontalDragEnd`.
    mutating func dragEnded(_ slices: [SectorSlice], at now: TimeInterval) {
        if dragging { settleRotation(slices, at: now) }
        dragAcc = 0
        steppingDrilled = false
    }

    /// Steps the focus by `delta` (VoiceOver's adjustable action, and a
    /// drilled swipe with Reduce Motion). Flat, it moves the highlight.
    /// Drilled, it turns the new sector up to the top: over 380 ms, or at
    /// once when `animated` is false.
    mutating func step(_ delta: Int, _ slices: [SectorSlice], at now: TimeInterval, animated: Bool) {
        guard isDrilledIn else {
            advanceFlat(delta, count: slices.count)
            return
        }
        guard let next = Sector3DGeometry.advanced(selected(count: slices.count), by: delta, count: slices.count)
        else { return }
        selected = next
        selectionTicks += 1
        let from = rotNow(at: now)
        let target = Sector3DGeometry.shortest(to: Sector3DGeometry.targetRotation(next, slices), from: from)
        dragging = false
        rotFrom = animated ? from : target
        rotTo = target
        rot = target
        if animated {
            rotation.forward(from: 0, at: now)
        } else {
            rotation.set(1)
        }
    }
}

// MARK: - Projection (`_RingPainter`)

/// A direction in the ring's world: `x` to the right, `y` up (height) and
/// `z` towards the viewer. A point at angle θ on the ring sits at
/// (r cos θ, h, r sin θ).
nonisolated struct Sector3DVector: Equatable, Sendable {
    let x: Double
    let y: Double
    let z: Double

    static let up = Sector3DVector(x: 0, y: 1, z: 0)

    func dot(_ o: Sector3DVector) -> Double { x * o.x + y * o.y + z * o.z }

    var normalized: Sector3DVector {
        let l = (x * x + y * y + z * z).squareRoot()
        return l == 0 ? self : Sector3DVector(x: x / l, y: y / l, z: z / l)
    }
}

/// The painter's lerped geometry for one drill value: a flat top-down ring
/// at 0, and at 1 a bigger, lower-tilted ring with tall walls, its centre
/// pushed down so the raised focused block sits up top.
nonisolated struct Sector3DLayout: Equatable, Sendable {
    let size: CGSize
    let drill: Double
    /// The vertical squash: 1 is round (top-down), 0.42 is tilted.
    let sy: Double
    let ro: Double
    let ri: Double
    /// The wall height: 0 flat, 64 drilled.
    let wall: Double
    let cx: Double
    let cy: Double

    init(size: CGSize, drill: Double) {
        func lerp(_ a: Double, _ b: Double) -> Double { a + (b - a) * drill }
        self.size = size
        self.drill = drill
        sy = lerp(1.0, 0.42)
        ro = size.width * lerp(0.33, 0.58)
        ri = size.width * lerp(0.205, 0.33)
        wall = lerp(0, 64)
        cx = size.width / 2
        cy = size.height * lerp(0.5, 0.86)
    }

    func lerp(_ a: Double, _ b: Double) -> Double { a + (b - a) * drill }

    /// `onOval`: the point at angle `ang` on the ring of radius `r`, raised by
    /// `yOff`.
    func onOval(_ r: Double, _ ang: Double, _ yOff: Double) -> CGPoint {
        CGPoint(x: cx + r * cos(ang), y: cy + r * sy * sin(ang) - yOff)
    }

    /// `oval`: the bounds of the ring of radius `r` raised by `yOff`.
    func oval(_ r: Double, _ yOff: Double) -> CGRect {
        CGRect(x: cx - r, y: cy - yOff - r * sy, width: 2 * r, height: 2 * r * sy)
    }

    /// The arc from `a0` to `a1` on the ring of radius `r`, as points about
    /// 2° apart (both ends included).
    func arc(_ r: Double, _ a0: Double, _ a1: Double, _ yOff: Double) -> [CGPoint] {
        let steps = max(1, Int((abs(a1 - a0) / (Double.pi / 90)).rounded(.up)))
        return (0...steps).map { k in
            onOval(r, a0 + (a1 - a0) * Double(k) / Double(steps), yOff)
        }
    }
}

/// One sector as the painter lays it out.
nonisolated struct Sector3DWedge: Equatable, Sendable {
    let index: Int
    /// The gap-trimmed start and end angles, rotated.
    let a0: Double
    let a1: Double
    /// The block's wall height; the focused block rises 38 higher drilled.
    let segWall: Double
    let isSelected: Bool
    let brightness: Double

    var mid: Double { (a0 + a1) / 2 }
}

nonisolated enum Sector3DFaceKind: Equatable, Sendable {
    /// The recessed floor inside the hole, drilled only.
    case floor
    /// A lone ~100% slice: the front half of its cylinder wall, and its top.
    case discWall
    case discTop
    /// A block's faces, in the order the painter draws them.
    case innerWall
    case startCap
    case endCap
    case outerWall
    case top
}

/// One flat polygon to fill, with the world normal its shade comes from.
nonisolated struct Sector3DFace: Equatable, Sendable {
    let kind: Sector3DFaceKind
    /// The slice it belongs to; nil for the floor.
    let slice: Int?
    let points: [CGPoint]
    let normal: Sector3DVector
    let isSelected: Bool
    let brightness: Double
}

/// Everything `_RingPainter.paint` computes for one frame, in paint order.
nonisolated struct Sector3DScene: Equatable, Sendable {
    let layout: Sector3DLayout
    /// `ringRot * drill`: the rotation only applies as the ring drills in.
    let rot: Double
    let total: Double
    let selected: Int
    /// The lone slice drawn as a solid disc, when one holds ~100%.
    let fullDisc: Int?
    /// The blocks, back to front.
    let wedges: [Sector3DWedge]
    /// The % label positions, by slice.
    let labels: [CGPoint]
    /// The flat-only chrome's opacity: labels and the centre readout.
    let flatAlpha: Double

    var drill: Double { layout.drill }

    init(slices: [SectorSlice], selected: Int, drill: Double, ringRot: Double, size: CGSize) {
        let layout = Sector3DLayout(size: size, drill: drill)
        self.layout = layout
        let total = Sector3DGeometry.total(slices)
        self.total = total
        let n = slices.count
        self.selected = n == 0 ? 0 : min(max(selected, 0), n - 1)
        let rot = ringRot * drill
        self.rot = rot
        self.flatAlpha = min(max(1 - drill * 2, 0), 1)

        guard total > 0 else {
            fullDisc = nil
            wedges = []
            labels = []
            return
        }
        let full = Sector3DGeometry.isFullDisc(slices)
        fullDisc = full ? Sector3DGeometry.dominantIndex(slices) : nil

        // Start angles, rotated.
        var starts: [Double] = []
        var s = -Double.pi / 2 + rot
        for slice in slices {
            starts.append(s)
            s += slice.pct / total * 2 * .pi
        }
        let spans = slices.map { $0.pct / total * 2 * Double.pi }

        // Back to front: the front (sin near +1) draws last. Ties keep index
        // order.
        let order = slices.indices.sorted { a, b in
            let sa = sin(starts[a] + spans[a] / 2)
            let sb = sin(starts[b] + spans[b] / 2)
            return sa == sb ? a < b : sa < sb
        }
        var wedges: [Sector3DWedge] = []
        if !full {
            for i in order {
                let a0 = starts[i] + Sector3DGeometry.gap / 2
                let a1 = starts[i] + spans[i] - Sector3DGeometry.gap / 2
                if a1 <= a0 { continue }
                let isSel = i == self.selected
                wedges.append(Sector3DWedge(
                    index: i,
                    a0: a0,
                    a1: a1,
                    segWall: layout.wall + (isSel ? layout.lerp(0, 38) : 0),
                    isSelected: isSel,
                    brightness: Sector3DGeometry.brightness(index: i, selected: isSel, count: n)
                ))
            }
        }
        self.wedges = wedges

        let lro = layout.ro + 14
        labels = slices.indices.map { i in
            layout.onOval(lro, starts[i] + spans[i] / 2, layout.wall)
        }
    }

    /// Where the flat centre readout sits: its percentage is centred 9 above
    /// this point and the sector name 13 below it.
    var centre: CGPoint { CGPoint(x: layout.cx, y: layout.cy - layout.wall) }

    /// `_onTapDown`'s wedge hit test, against the top faces. The painter
    /// records hits only while nearly flat; it returns the first wedge in
    /// paint order (back to front) whose top face holds `p`.
    func hitTest(_ p: CGPoint) -> Int? {
        guard drill < Sector3DGeometry.tapThreshold, total > 0 else { return nil }
        let l = layout
        if let dom = fullDisc {
            let dx = p.x - l.cx
            let dy = (p.y - (l.cy - l.wall)) / l.sy
            return dx * dx + dy * dy <= l.ro * l.ro ? dom : nil
        }
        for w in wedges {
            let dx = p.x - l.cx
            let dy = (p.y - (l.cy - w.segWall)) / l.sy
            let r = (dx * dx + dy * dy).squareRoot()
            guard r >= l.ri, r <= l.ro else { continue }
            let t = Sector3DGeometry.dartMod(atan2(dy, dx) - w.a0, 2 * .pi)
            if t <= w.a1 - w.a0 { return w.index }
        }
        return nil
    }

    /// The polygons to fill, back to front. The Dart filled every face with
    /// a gradient and let later faces paint over hidden ones; flat fills
    /// would show those overpaints, so each face is culled to the part a
    /// viewer can see.
    func faces() -> [Sector3DFace] {
        let l = layout
        var out: [Sector3DFace] = []
        guard total > 0 else { return out }

        if let dom = fullDisc {
            if l.wall > 1 {
                let wall = l.arc(l.ro, 0, .pi, l.wall) + l.arc(l.ro, .pi, 0, 0)
                out.append(Sector3DFace(
                    kind: .discWall, slice: dom, points: wall,
                    normal: Sector3DVector(x: 0, y: 0, z: 1), isSelected: true, brightness: 1
                ))
            }
            out.append(Sector3DFace(
                kind: .discTop, slice: dom, points: l.arc(l.ro, 0, 2 * .pi, l.wall),
                normal: .up, isSelected: true, brightness: 1
            ))
            return out
        }

        if drill > 0.02 {
            out.append(Sector3DFace(
                kind: .floor, slice: nil, points: l.arc(l.ri * 0.985, 0, 2 * .pi, 2),
                normal: .up, isSelected: false, brightness: 0
            ))
        }

        for w in wedges {
            func face(_ kind: Sector3DFaceKind, _ points: [CGPoint], _ normal: Sector3DVector) {
                out.append(Sector3DFace(
                    kind: kind, slice: w.index, points: points, normal: normal,
                    isSelected: w.isSelected, brightness: w.brightness
                ))
            }
            if w.segWall > 1 {
                // The inner wall (the hole side), where it faces the viewer.
                for r in Sector3DGeometry.facingRanges(w.a0, w.a1, front: false) {
                    let m = (r.lowerBound + r.upperBound) / 2
                    face(
                        .innerWall,
                        l.arc(l.ri, r.lowerBound, r.upperBound, w.segWall)
                            + l.arc(l.ri, r.upperBound, r.lowerBound, 0),
                        Sector3DVector(x: -cos(m), y: 0, z: -sin(m))
                    )
                }
                // The radial cut faces at each end, where they face the
                // viewer: they close the block so a gap shows no open cut.
                func cap(_ a: Double) -> [CGPoint] {
                    [l.onOval(l.ro, a, w.segWall), l.onOval(l.ri, a, w.segWall),
                     l.onOval(l.ri, a, 0), l.onOval(l.ro, a, 0)]
                }
                if cos(w.a0) < 0 {
                    face(.startCap, cap(w.a0), Sector3DVector(x: sin(w.a0), y: 0, z: -cos(w.a0)))
                }
                if cos(w.a1) > 0 {
                    face(.endCap, cap(w.a1), Sector3DVector(x: -sin(w.a1), y: 0, z: cos(w.a1)))
                }
                // The outer wall, where it faces the viewer.
                for r in Sector3DGeometry.facingRanges(w.a0, w.a1, front: true) {
                    let m = (r.lowerBound + r.upperBound) / 2
                    face(
                        .outerWall,
                        l.arc(l.ro, r.lowerBound, r.upperBound, w.segWall)
                            + l.arc(l.ro, r.upperBound, r.lowerBound, 0),
                        Sector3DVector(x: cos(m), y: 0, z: sin(m))
                    )
                }
            }
            face(.top, l.arc(l.ro, w.a0, w.a1, w.segWall) + l.arc(l.ri, w.a1, w.a0, w.segWall), .up)
        }
        return out
    }
}

// MARK: - Flat shading

/// An sRGB colour in 0...1, lerped per channel as Flutter's `Color.lerp` does.
nonisolated struct Sector3DRGB: Equatable, Sendable {
    let r: Double
    let g: Double
    let b: Double

    init(_ hex: UInt32) {
        r = Double((hex >> 16) & 0xFF) / 255
        g = Double((hex >> 8) & 0xFF) / 255
        b = Double(hex & 0xFF) / 255
    }

    init(r: Double, g: Double, b: Double) {
        self.r = r
        self.g = g
        self.b = b
    }

    static let black = Sector3DRGB(0x000000)
    static let white = Sector3DRGB(0xFFFFFF)

    func lerp(_ o: Sector3DRGB, _ t: Double) -> Sector3DRGB {
        Sector3DRGB(r: r + (o.r - r) * t, g: g + (o.g - g) * t, b: b + (o.b - b) * t)
    }

    /// WCAG relative luminance.
    var luminance: Double {
        func lin(_ c: Double) -> Double { c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    }
}

/// The flat replacement for the Dart's metallic gradients: one solid colour
/// per face, shaded from a fixed light above, behind and slightly left of
/// the ring. Top faces are lightest, side faces darker and the front darkest.
nonisolated struct Sector3DPalette: Equatable, Sendable {
    /// One of the Dart's violet ramps, from brightness 0 (deep) to 1.
    struct Ramp: Equatable, Sendable {
        let deep: Sector3DRGB
        let bright: Sector3DRGB

        init(_ deep: UInt32, _ bright: UInt32) {
            self.deep = Sector3DRGB(deep)
            self.bright = Sector3DRGB(bright)
        }

        func at(_ b: Double) -> Sector3DRGB { deep.lerp(bright, b) }
    }

    /// The Dart's three ramps: `hiC`, `midC` and `loC`.
    let hi: Ramp
    let mid: Ramp
    let lo: Ramp
    /// How far towards black the unlit side of a face goes.
    let shadowDepth: Double
    let floor: Sector3DRGB
    /// The 1 pt edge strokes: their colour, and their opacity on a top face,
    /// the selected top face and every other face.
    let edge: Sector3DRGB
    let edgeTop: Double
    let edgeSelected: Double
    let edgeSide: Double

    /// Dark: the Dart palette exactly, bright for emphasis. The floor is the
    /// mean of its radial gradient's two stops.
    static let dark = Sector3DPalette(
        hi: Ramp(0x7C5CE6, 0xEBE0FF),
        mid: Ramp(0x4A2C9E, 0xB79BFF),
        lo: Ramp(0x24114F, 0x7E55E6),
        shadowDepth: 0.55,
        floor: Sector3DRGB(0x120825),
        edge: .white, edgeTop: 0.10, edgeSelected: 0.22, edgeSide: 0.08
    )

    /// Light: the same violets, mapped so emphasis means stronger rather than
    /// paler. Pale lavender recedes on a white card, so the selected wedge
    /// is the light accent (#6D28D9) and the smaller sectors fade towards
    /// lavender.
    static let light = Sector3DPalette(
        hi: Ramp(0xD8CCFB, 0x8B5CF6),
        mid: Ramp(0xB9A5F7, 0x6D28D9),
        lo: Ramp(0x8E73E0, 0x4C1D95),
        shadowDepth: 0.35,
        floor: Sector3DRGB(0x2A1659),
        edge: .black, edgeTop: 0.10, edgeSelected: 0.18, edgeSide: 0.08
    )

    /// The fixed light: above, behind and a little left of the ring.
    static let lightDirection = Sector3DVector(x: -0.35, y: 1, z: -0.55).normalized

    /// Half-Lambert intensity, 0 (facing straight away) to 1 (facing the
    /// light).
    static func intensity(_ normal: Sector3DVector) -> Double {
        0.5 + 0.5 * normal.normalized.dot(lightDirection)
    }

    /// A top face's colour: the Dart top gradient's body, between its `hi`
    /// and `mid` stops.
    func topColor(brightness b: Double) -> Sector3DRGB {
        hi.at(b).lerp(mid.at(b), 0.35)
    }

    /// The unlit end of the ramp: the Dart's darkest wall stop.
    func shadowColor(brightness b: Double) -> Sector3DRGB {
        lo.at(b).lerp(.black, shadowDepth)
    }

    /// One face's solid colour: a top face gets `topColor`, and a face
    /// turned from the light falls towards `shadowColor`.
    func color(normal: Sector3DVector, brightness b: Double) -> Sector3DRGB {
        let k = min(max(Self.intensity(normal) / Self.intensity(.up), 0), 1)
        return shadowColor(brightness: b).lerp(topColor(brightness: b), k)
    }
}
