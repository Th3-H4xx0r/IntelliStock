import CoreGraphics
import Foundation
import Testing
@testable import IntelliStock

/// The Sector3DChart maths: the painter's projection, the faces, wedge hit
/// testing, nearest-sector snapping, the curves, the controllers and every
/// gesture path of `_Sector3DChartState`.
///
/// test/features/dashboard/sector_3d_chart_golden_test.dart holds only two
/// pixel goldens: five slices at 360 wide with `debugDrill` 0 and 1. Its
/// harness becomes the fixtures here, and what those goldens pin down
/// (heights, ring sizes, tilt, wall heights, the raised focus block) is
/// asserted as numbers. `Sector3DChartRenderTests` renders the same two
/// states.
struct Sector3DGeometryTests {
    /// The golden test's slices.
    static let slices: [SectorSlice] = [
        SectorSlice(sector: "Technology", value: 4000, pct: 40),
        SectorSlice(sector: "Healthcare", value: 2500, pct: 25),
        SectorSlice(sector: "Financials", value: 1500, pct: 15),
        SectorSlice(sector: "Energy", value: 1200, pct: 12),
        SectorSlice(sector: "Consumer", value: 800, pct: 8),
    ]
    private var slices: [SectorSlice] { Self.slices }

    private func eq(_ a: Double, _ b: Double, _ tol: Double = 1e-9) -> Bool { abs(a - b) <= tol }

    private func flatScene(selected: Int = 0) -> Sector3DScene {
        Sector3DScene(slices: slices, selected: selected, drill: 0, ringRot: 0, size: CGSize(width: 360, height: 280))
    }

    private func drilledScene(selected: Int = 0, ringRot: Double = 0) -> Sector3DScene {
        Sector3DScene(
            slices: slices, selected: selected, drill: 1, ringRot: ringRot, size: CGSize(width: 360, height: 320)
        )
    }

    /// A point on wedge `i`'s top face, halfway across the band.
    private func pointOn(_ i: Int, _ scene: Sector3DScene) -> CGPoint {
        let w = scene.wedges.first { $0.index == i }!
        let l = scene.layout
        return l.onOval((l.ro + l.ri) / 2, w.mid, w.segWall)
    }

    // MARK: Constants

    @Test func sizesTimingsAndSensitivityMatchTheDart() {
        #expect(Sector3DGeometry.flatHeight == 280)
        #expect(Sector3DGeometry.drillHeight == 320)
        #expect(Sector3DGeometry.drillDuration == 0.620)
        #expect(Sector3DGeometry.rotationDuration == 0.380)
        #expect(Sector3DGeometry.rotPerPx == 0.009)
        #expect(Sector3DGeometry.swipeStep == 44)
        #expect(Sector3DGeometry.gap == 0.05)
        #expect(Sector3DGeometry.tapThreshold == 0.05)
        #expect(Sector3DGeometry.dragRotateThreshold == 0.5)
    }

    @Test func heightLerpsFromFlatToDrilled() {
        #expect(Sector3DGeometry.height(drill: 0) == 280)
        #expect(Sector3DGeometry.height(drill: 1) == 320)
        #expect(Sector3DGeometry.height(drill: 0.5) == 300)
    }

    @Test func dartModuloIsAlwaysNonNegative() {
        #expect(eq(Sector3DGeometry.dartMod(-1, 2 * .pi), 2 * .pi - 1))
        #expect(eq(Sector3DGeometry.dartMod(7, 2 * .pi), 7 - 2 * .pi))
        #expect(Sector3DGeometry.dartMod(0, 2 * .pi) == 0)
    }

    // MARK: Curves

    /// The exact cubic Bézier y at x, by Newton's method (the reference
    /// Flutter's 0.001 bisection approximates).
    private func bezier(_ c: Sector3DCurve, _ x: Double) -> Double {
        func f(_ a: Double, _ b: Double, _ m: Double) -> Double {
            3 * a * (1 - m) * (1 - m) * m + 3 * b * (1 - m) * m * m + m * m * m
        }
        var m = x
        for _ in 0..<50 {
            let fx = f(c.a, c.c, m) - x
            let h = 1e-7
            let dfx = (f(c.a, c.c, m + h) - f(c.a, c.c, m - h)) / (2 * h)
            if abs(dfx) < 1e-12 { break }
            m -= fx / dfx
        }
        return f(c.b, c.d, m)
    }

    @Test func curvesAreFlutterCubics() {
        #expect(Sector3DCurve.easeInOut == Sector3DCurve(a: 0.42, b: 0, c: 0.58, d: 1))
        #expect(Sector3DCurve.easeInOutCubic == Sector3DCurve(a: 0.645, b: 0.045, c: 0.355, d: 1))
        for curve in [Sector3DCurve.easeInOut, .easeInOutCubic] {
            #expect(curve.transform(0) == 0)
            #expect(curve.transform(1) == 1)
            var last = 0.0
            for k in 1..<100 {
                let t = Double(k) / 100
                let y = curve.transform(t)
                #expect(abs(y - bezier(curve, t)) < 0.004)
                #expect(y >= last - 1e-3)
                last = y
            }
        }
    }

    @Test func easeInOutIsSymmetric() {
        #expect(eq(Sector3DCurve.easeInOut.transform(0.5), 0.5, 0.002))
        let a = Sector3DCurve.easeInOut.transform(0.25)
        let b = Sector3DCurve.easeInOut.transform(0.75)
        #expect(eq(a + b, 1, 0.004))
    }

    // MARK: AnimationController

    @Test func tweenRunsLinearlyOverItsDuration() {
        var t = Sector3DTween(duration: 0.62)
        #expect(t.value(at: 0) == 0)
        t.forward(at: 10)
        #expect(t.target == 1)
        #expect(eq(t.endTime!, 10.62))
        #expect(eq(t.value(at: 10.31), 0.5))
        #expect(eq(t.value(at: 10.62), 1))
        #expect(t.value(at: 10.7) == 1)
        #expect(t.value(at: 99) == 1)
        #expect(t.isAnimating(at: 10.3))
        #expect(!t.isAnimating(at: 10.63))
    }

    @Test func reversingMidRunTakesTheRemainingShare() {
        var t = Sector3DTween(duration: 0.62)
        t.forward(at: 0)
        t.reverse(at: 0.31) // at 0.5, so 0.31 s back to 0
        #expect(t.target == 0)
        #expect(eq(t.endTime!, 0.62))
        #expect(eq(t.value(at: 0.465), 0.25))
        #expect(t.value(at: 0.7) == 0)
    }

    @Test func forwardFromAnEndIsANoOpAndSetStops() {
        var t = Sector3DTween(duration: 0.38, value: 1)
        t.forward(at: 0)
        #expect(t.endTime == nil)
        #expect(t.value(at: 0) == 1)
        t.forward(from: 0, at: 5)
        #expect(eq(t.endTime!, 5.38))
        t.set(1)
        #expect(t.endTime == nil)
        #expect(t.value(at: 5.1) == 1)
    }

    @Test func settleDropsOnlyAFinishedRun() {
        var t = Sector3DTween(duration: 0.62)
        t.forward(at: 0)
        t.settle(at: 0.3)
        #expect(t.endTime != nil)
        t.settle(at: 0.7)
        #expect(t.endTime == nil)
        #expect(t.value(at: 0) == 1)
    }

    // MARK: Angles and snapping

    @Test func naturalMidIsTheMidAngleFromTheTop() {
        #expect(eq(Sector3DGeometry.naturalMid(0, slices), 0.4 * .pi))
        #expect(eq(Sector3DGeometry.naturalMid(1, slices), 0.8 * .pi + 0.25 * .pi))
        #expect(eq(Sector3DGeometry.naturalMid(4, slices), 1.84 * .pi + 0.08 * .pi))
        #expect(Sector3DGeometry.naturalMid(0, [SectorSlice(sector: "x", value: 0, pct: 0)]) == 0)
    }

    @Test func targetRotationBringsASectorToTheTop() {
        for i in slices.indices {
            let rot = Sector3DGeometry.targetRotation(i, slices)
            #expect(eq(rot, -Sector3DGeometry.naturalMid(i, slices)))
            #expect(Sector3DGeometry.nearestTop(rotation: rot, slices) == i)
            // Its mid-angle lands on -π/2: straight up.
            let scene = Sector3DScene(
                slices: slices, selected: i, drill: 1, ringRot: rot, size: CGSize(width: 360, height: 320)
            )
            let mid = scene.wedges.first { $0.index == i }!.mid
            #expect(eq(Sector3DGeometry.dartMod(mid + .pi / 2, 2 * .pi), 0, 1e-9)
                || eq(Sector3DGeometry.dartMod(mid + .pi / 2, 2 * .pi), 2 * .pi, 1e-9))
        }
    }

    @Test func nearestTopAtRestIsTheSectorStraddlingTheTop() {
        // Unrotated, the ring starts at the top: Consumer's mid (-0.08π) is
        // nearer than Technology's (+0.4π).
        #expect(Sector3DGeometry.nearestTop(rotation: 0, slices) == 4)
        #expect(Sector3DGeometry.nearestTop(rotation: -0.4 * .pi, slices) == 0)
        #expect(Sector3DGeometry.nearestTop(rotation: -0.4 * .pi + 4 * .pi, slices) == 0)
    }

    @Test func shortestPicksTheNearestEquivalentAngle() {
        #expect(eq(Sector3DGeometry.shortest(to: 3 * .pi, from: 0), .pi))
        #expect(eq(Sector3DGeometry.shortest(to: -3.5 * .pi, from: 0), 0.5 * .pi))
        #expect(eq(Sector3DGeometry.shortest(to: 0.1, from: 10 * .pi), 10 * .pi + 0.1))
        for target in stride(from: -20.0, through: 20, by: 0.7) {
            let s = Sector3DGeometry.shortest(to: target, from: 1.3)
            #expect(s - 1.3 <= .pi && s - 1.3 >= -.pi)
        }
    }

    @Test func advancedWrapsAndSkipsNoMoves() {
        #expect(Sector3DGeometry.advanced(4, by: 1, count: 5) == 0)
        #expect(Sector3DGeometry.advanced(0, by: -1, count: 5) == 4)
        #expect(Sector3DGeometry.advanced(2, by: 1, count: 5) == 3)
        #expect(Sector3DGeometry.advanced(0, by: 1, count: 1) == nil)
        #expect(Sector3DGeometry.advanced(0, by: 1, count: 0) == nil)
    }

    @Test func brightnessRampMatchesTheDart() {
        #expect(Sector3DGeometry.brightness(index: 3, selected: true, count: 5) == 1)
        #expect(Sector3DGeometry.brightness(index: 0, selected: false, count: 1) == 0.58)
        #expect(eq(Sector3DGeometry.brightness(index: 0, selected: false, count: 5), 0.72))
        #expect(eq(Sector3DGeometry.brightness(index: 4, selected: false, count: 5), 0.30))
        #expect(eq(Sector3DGeometry.brightness(index: 2, selected: false, count: 5), 0.51))
    }

    @Test func aLoneFullSliceIsASolidDisc() {
        let one = [SectorSlice(sector: "Technology", value: 1, pct: 100)]
        #expect(Sector3DGeometry.isFullDisc(one))
        #expect(Sector3DGeometry.isFullDisc([
            SectorSlice(sector: "a", value: 1, pct: 99.6), SectorSlice(sector: "b", value: 1, pct: 0.4),
        ]))
        #expect(!Sector3DGeometry.isFullDisc([
            SectorSlice(sector: "a", value: 1, pct: 99), SectorSlice(sector: "b", value: 1, pct: 1),
        ]))
        #expect(!Sector3DGeometry.isFullDisc([SectorSlice(sector: "a", value: 0, pct: 0)]))
        #expect(!Sector3DGeometry.isFullDisc(slices))
        #expect(Sector3DGeometry.dominantIndex([
            SectorSlice(sector: "a", value: 1, pct: 10), SectorSlice(sector: "b", value: 1, pct: 90),
        ]) == 1)
    }

    @Test func accessibilityLabelNamesTheSectorAndItsPercent() {
        #expect(Sector3DGeometry.accessibilityLabel(SectorSlice(sector: "Energy", value: 1, pct: 34.4))
            == "Energy, 34 percent")
        #expect(Sector3DGeometry.accessibilityLabel(SectorSlice(sector: "Energy", value: 1, pct: 33.5))
            == "Energy, 34 percent")
    }

    @Test func facingRangesSplitAWedgeIntoItsVisibleParts() {
        func eqRanges(_ a: [ClosedRange<Double>], _ b: [ClosedRange<Double>]) -> Bool {
            a.count == b.count && zip(a, b).allSatisfy {
                eq($0.lowerBound, $1.lowerBound) && eq($0.upperBound, $1.upperBound)
            }
        }
        #expect(eqRanges(Sector3DGeometry.facingRanges(0.2, 1.0, front: true), [0.2...1.0]))
        #expect(Sector3DGeometry.facingRanges(0.2, 1.0, front: false).isEmpty)
        #expect(eqRanges(Sector3DGeometry.facingRanges(-0.5, 0.5, front: true), [0...0.5]))
        #expect(eqRanges(Sector3DGeometry.facingRanges(-0.5, 0.5, front: false), [-0.5...0]))
        #expect(eqRanges(Sector3DGeometry.facingRanges(3, 7, front: true), [3...Double.pi, (2 * .pi)...7]))
        #expect(eqRanges(Sector3DGeometry.facingRanges(3, 7, front: false), [Double.pi...(2 * .pi)]))
        #expect(Sector3DGeometry.facingRanges(1, 1, front: true).isEmpty)
    }

    // MARK: Projection (the golden harness: 360 wide)

    @Test func flatLayoutIsARoundTopDownRing() {
        let l = Sector3DLayout(size: CGSize(width: 360, height: 280), drill: 0)
        #expect(l.sy == 1)
        #expect(eq(l.ro, 118.8))
        #expect(eq(l.ri, 73.8))
        #expect(l.wall == 0)
        #expect(l.cx == 180)
        #expect(l.cy == 140)
    }

    @Test func drilledLayoutIsBiggerTiltedAndExtruded() {
        let l = Sector3DLayout(size: CGSize(width: 360, height: 320), drill: 1)
        #expect(eq(l.sy, 0.42))
        #expect(eq(l.ro, 208.8))
        #expect(eq(l.ri, 118.8))
        #expect(l.wall == 64)
        #expect(eq(l.cy, 275.2))
        // The drilled ring is wider than the chart (1.16x), as in the golden.
        #expect(2 * l.ro > 360)
    }

    @Test func onOvalProjectsTheTiltedRing() {
        let l = Sector3DLayout(size: CGSize(width: 360, height: 320), drill: 1)
        let top = l.onOval(100, -.pi / 2, 10)
        #expect(eq(top.x, 180, 1e-9))
        #expect(eq(top.y, 275.2 - 42 - 10, 1e-9))
        let right = l.onOval(100, 0, 0)
        #expect(eq(right.x, 280) && eq(right.y, 275.2))
        let r = l.oval(100, 10)
        #expect(eq(r.minX, 80) && eq(r.width, 200) && eq(r.height, 84) && eq(r.midY, 265.2))
    }

    @Test func arcsSampleBothEndsAboutTwoDegreesApart() {
        let l = Sector3DLayout(size: CGSize(width: 360, height: 280), drill: 0)
        // 0.1 rad is 2.9 steps of 2°, so 3 steps and 4 points.
        #expect(l.arc(100, 0, 0.1, 0).count == 4)
        let pts = l.arc(100, 0, .pi / 2, 0)
        #expect(eq(pts.first!.x, 280) && eq(pts.first!.y, 140))
        #expect(eq(pts.last!.x, 180, 1e-9) && eq(pts.last!.y, 240, 1e-9))
        for (p, q) in zip(pts, pts.dropFirst()) {
            #expect(hypot(q.x - p.x, q.y - p.y) <= 100 * .pi / 90 + 1e-9)
        }
    }

    // MARK: Scene

    @Test func wedgesPaintBackToFront() {
        for scene in [flatScene(), drilledScene(ringRot: 1.1), drilledScene(selected: 3, ringRot: -2)] {
            let sines = scene.wedges.map { sin($0.mid) }
            #expect(sines == sines.sorted())
            #expect(scene.wedges.count == 5)
        }
    }

    @Test func wedgesAreGapTrimmedAndATinySliceIsSkipped() {
        let scene = flatScene()
        let tech = scene.wedges.first { $0.index == 0 }!
        #expect(eq(tech.a0, -.pi / 2 + 0.025))
        #expect(eq(tech.a1, -.pi / 2 + 0.8 * .pi - 0.025))
        let withTiny = slices + [SectorSlice(sector: "Tiny", value: 1, pct: 0.5)]
        let tiny = Sector3DScene(
            slices: withTiny, selected: 0, drill: 0, ringRot: 0, size: CGSize(width: 360, height: 280)
        )
        #expect(tiny.wedges.count == 5)
        #expect(!tiny.wedges.contains { $0.index == 5 })
        #expect(tiny.labels.count == 6)
    }

    @Test func theFocusedBlockRisesOnlyWhenDrilled() {
        let flat = flatScene(selected: 2)
        #expect(flat.wedges.allSatisfy { $0.segWall == 0 })
        let drilled = drilledScene(selected: 2)
        for w in drilled.wedges {
            #expect(w.segWall == (w.index == 2 ? 102 : 64))
            #expect(w.isSelected == (w.index == 2))
            #expect(w.brightness == Sector3DGeometry.brightness(index: w.index, selected: w.index == 2, count: 5))
        }
    }

    @Test func rotationAppliesOnlyAsItDrillsIn() {
        let ringRot = -1.2
        #expect(Sector3DScene(slices: slices, selected: 0, drill: 0, ringRot: ringRot,
                              size: CGSize(width: 360, height: 280)).rot == 0)
        #expect(eq(Sector3DScene(slices: slices, selected: 0, drill: 0.5, ringRot: ringRot,
                                 size: CGSize(width: 360, height: 300)).rot, -0.6))
        #expect(eq(drilledScene(ringRot: ringRot).rot, ringRot))
    }

    @Test func flatChromeFadesOutByHalfDrilled() {
        #expect(flatScene().flatAlpha == 1)
        #expect(eq(Sector3DScene(slices: slices, selected: 0, drill: 0.25, ringRot: 0,
                                 size: CGSize(width: 360, height: 290)).flatAlpha, 0.5))
        #expect(drilledScene().flatAlpha == 0)
    }

    @Test func labelsSitJustOutsideTheRingAtEachMid() {
        let scene = flatScene()
        let l = scene.layout
        var s = -Double.pi / 2
        for (i, slice) in slices.enumerated() {
            let full = slice.pct / 100 * 2 * .pi
            let expected = l.onOval(l.ro + 14, s + full / 2, l.wall)
            #expect(eq(scene.labels[i].x, expected.x) && eq(scene.labels[i].y, expected.y))
            s += full
        }
        #expect(scene.centre == CGPoint(x: 180, y: 140))
        #expect(eq(drilledScene().centre.y, 275.2 - 64, 1e-9))
    }

    @Test func aZeroTotalDrawsNothing() {
        let zero = [SectorSlice(sector: "a", value: 0, pct: 0)]
        let scene = Sector3DScene(slices: zero, selected: 0, drill: 0, ringRot: 0, size: CGSize(width: 360, height: 280))
        #expect(scene.wedges.isEmpty)
        #expect(scene.faces().isEmpty)
        #expect(scene.hitTest(CGPoint(x: 180, y: 40)) == nil)
    }

    @Test func selectedClampsToTheSlices() {
        #expect(flatScene(selected: 9).selected == 4)
        #expect(flatScene(selected: -2).selected == 0)
    }

    // MARK: Hit testing

    @Test func aTapOnAWedgeHitsThatWedge() {
        let scene = flatScene()
        for i in slices.indices {
            #expect(scene.hitTest(pointOn(i, scene)) == i)
        }
    }

    @Test func holesGapsAndTheOutsideMiss() {
        let scene = flatScene()
        let l = scene.layout
        #expect(scene.hitTest(CGPoint(x: l.cx, y: l.cy)) == nil)
        #expect(scene.hitTest(CGPoint(x: 2, y: 2)) == nil)
        // The gap between Technology and Healthcare.
        let boundary = -Double.pi / 2 + 0.8 * .pi
        #expect(scene.hitTest(l.onOval((l.ro + l.ri) / 2, boundary, 0)) == nil)
        // Just inside each rim.
        #expect(scene.hitTest(l.onOval(l.ro - 0.5, -.pi / 2 + 0.5, 0)) == 0)
        #expect(scene.hitTest(l.onOval(l.ri + 0.5, -.pi / 2 + 0.5, 0)) == 0)
        #expect(scene.hitTest(l.onOval(l.ro + 0.5, -.pi / 2 + 0.5, 0)) == nil)
    }

    @Test func wedgesStopTakingHitsOnceDrilling() {
        let nearlyFlat = Sector3DScene(slices: slices, selected: 0, drill: 0.04, ringRot: 0,
                                       size: CGSize(width: 360, height: 281.6))
        #expect(nearlyFlat.hitTest(pointOn(1, nearlyFlat)) == 1)
        let drilling = Sector3DScene(slices: slices, selected: 0, drill: 0.05, ringRot: 0,
                                     size: CGSize(width: 360, height: 282))
        #expect(drilling.hitTest(pointOn(1, drilling)) == nil)
        #expect(drilledScene().hitTest(pointOn(1, drilledScene())) == nil)
    }

    @Test func aFullDiscHitsAnywhereInside() {
        let one = [SectorSlice(sector: "Technology", value: 1, pct: 100)]
        let scene = Sector3DScene(slices: one, selected: 0, drill: 0, ringRot: 0, size: CGSize(width: 360, height: 280))
        #expect(scene.fullDisc == 0)
        #expect(scene.wedges.isEmpty)
        #expect(scene.hitTest(CGPoint(x: 180, y: 140)) == 0)
        #expect(scene.hitTest(CGPoint(x: 180 + 118, y: 140)) == 0)
        #expect(scene.hitTest(CGPoint(x: 180 + 120, y: 140)) == nil)
    }

    // MARK: Faces

    @Test func flatFacesAreTopsOnly() {
        let faces = flatScene().faces()
        #expect(faces.count == 5)
        #expect(faces.allSatisfy { $0.kind == .top && $0.normal == .up })
        #expect(faces.map(\.slice) == flatScene().wedges.map { Optional($0.index) })
    }

    @Test func drilledFacesHaveAFloorThenBlocksEachEndingInItsTop() {
        let scene = drilledScene(ringRot: 0.7)
        let faces = scene.faces()
        #expect(faces.first?.kind == .floor)
        let blocks = faces.dropFirst()
        var order: [Int] = []
        for face in blocks where order.last != face.slice {
            order.append(face.slice!)
        }
        #expect(order == scene.wedges.map(\.index))
        let rank: [Sector3DFaceKind: Int] = [.innerWall: 0, .startCap: 1, .endCap: 2, .outerWall: 3, .top: 4]
        for i in order {
            let kinds = blocks.filter { $0.slice == i }.map(\.kind)
            #expect(kinds.last == .top)
            #expect(kinds.map { rank[$0]! } == kinds.map { rank[$0]! }.sorted())
        }
    }

    @Test func everySideFaceDrawnFacesTheViewer() {
        // The view direction for the painter's projection (x, z·sy − h) is
        // (0, sy, 1): a vertical face shows only when its normal has z > 0.
        for rot in stride(from: -3.0, through: 3, by: 0.5) {
            for face in drilledScene(selected: 1, ringRot: rot).faces() where face.kind != .floor && face.kind != .top {
                #expect(face.normal.z > -1e-9)
                #expect(face.normal.y == 0)
            }
        }
    }

    @Test func wallsFollowTheirVisibleArcs() {
        let scene = drilledScene(ringRot: 0)
        let faces = scene.faces()
        // Technology spans the top-right quarter and more: its outer wall
        // shows only from 0 (the right) round to its end.
        let tech = scene.wedges.first { $0.index == 0 }!
        let outer = faces.filter { $0.slice == 0 && $0.kind == .outerWall }
        #expect(outer.count == 1)
        let l = scene.layout
        #expect(eq(outer[0].points.first!.x, l.onOval(l.ro, 0, 64 + 38).x, 1e-9))
        let inner = faces.filter { $0.slice == 0 && $0.kind == .innerWall }
        #expect(inner.count == 1)
        #expect(eq(inner[0].points.first!.x, l.onOval(l.ri, tech.a0, 64 + 38).x, 1e-9))
    }

    @Test func aFullDiscIsAWallAndATop() {
        let one = [SectorSlice(sector: "Technology", value: 1, pct: 100)]
        let flat = Sector3DScene(slices: one, selected: 0, drill: 0, ringRot: 0, size: CGSize(width: 360, height: 280))
        #expect(flat.faces().map(\.kind) == [.discTop])
        let drilled = Sector3DScene(slices: one, selected: 0, drill: 1, ringRot: 0, size: CGSize(width: 360, height: 320))
        #expect(drilled.faces().map(\.kind) == [.discWall, .discTop])
    }

    // MARK: Flat shading

    @Test func topFacesAreLightestAndTheFrontDarkest() {
        let top = Sector3DPalette.intensity(.up)
        let left = Sector3DPalette.intensity(Sector3DVector(x: -1, y: 0, z: 0))
        let right = Sector3DPalette.intensity(Sector3DVector(x: 1, y: 0, z: 0))
        let front = Sector3DPalette.intensity(Sector3DVector(x: 0, y: 0, z: 1))
        #expect(top > left)
        #expect(left > right)
        #expect(right > front)
        for palette in [Sector3DPalette.dark, .light] {
            for b in [0.3, 0.6, 1.0] {
                let t = palette.color(normal: .up, brightness: b).luminance
                let s = palette.color(normal: Sector3DVector(x: -1, y: 0, z: 0), brightness: b).luminance
                let f = palette.color(normal: Sector3DVector(x: 0, y: 0, z: 1), brightness: b).luminance
                #expect(t > s && s > f)
                let top = palette.color(normal: .up, brightness: b)
                let want = palette.topColor(brightness: b)
                #expect(eq(top.r, want.r) && eq(top.g, want.g) && eq(top.b, want.b))
            }
        }
    }

    @Test func darkKeepsTheDartPaletteAndBrightensTheFocus() {
        let dark = Sector3DPalette.dark
        #expect(dark.hi.deep == Sector3DRGB(0x7C5CE6) && dark.hi.bright == Sector3DRGB(0xEBE0FF))
        #expect(dark.mid.deep == Sector3DRGB(0x4A2C9E) && dark.mid.bright == Sector3DRGB(0xB79BFF))
        #expect(dark.lo.deep == Sector3DRGB(0x24114F) && dark.lo.bright == Sector3DRGB(0x7E55E6))
        #expect(dark.topColor(brightness: 1).luminance > dark.topColor(brightness: 0.72).luminance)
    }

    @Test func lightStrengthensTheFocusAndKeepsItLegibleOnWhite() {
        let light = Sector3DPalette.light
        let focus = light.topColor(brightness: 1).luminance
        #expect(focus < light.topColor(brightness: 0.72).luminance)
        #expect(light.topColor(brightness: 0.72).luminance < light.topColor(brightness: 0.30).luminance)
        // The focused wedge against a white card: at least 3:1, the WCAG
        // non-text minimum.
        #expect((1.05) / (focus + 0.05) >= 3)
    }

    @Test func rgbLerpsPerChannel() {
        let mid = Sector3DRGB(0x000000).lerp(Sector3DRGB(0xFFFFFF), 0.5)
        #expect(eq(mid.r, 0.5) && eq(mid.g, 0.5) && eq(mid.b, 0.5))
        #expect(eq(Sector3DRGB(0xFFFFFF).luminance, 1))
        #expect(Sector3DRGB(0x6D28D9) == Sector3DRGB(r: 0x6D / 255.0, g: 0x28 / 255.0, b: 0xD9 / 255.0))
    }
}

/// The gesture and animation paths of `_Sector3DChartState`.
struct Sector3DInteractionTests {
    private var slices: [SectorSlice] { Sector3DGeometryTests.slices }

    private func eq(_ a: Double, _ b: Double, _ tol: Double = 1e-9) -> Bool { abs(a - b) <= tol }

    private func flatScene(_ m: Sector3DInteraction) -> Sector3DScene {
        Sector3DScene(slices: slices, selected: m.selected(count: 5), drill: 0, ringRot: 0,
                      size: CGSize(width: 360, height: 280))
    }

    private func point(on i: Int, _ scene: Sector3DScene) -> CGPoint {
        let w = scene.wedges.first { $0.index == i }!
        let l = scene.layout
        return l.onOval((l.ro + l.ri) / 2, w.mid, w.segWall)
    }

    /// Tapped into sector `i` at t = 0, and settled at t = 1.
    private func drilled(into i: Int) -> Sector3DInteraction {
        var m = Sector3DInteraction()
        m.drillInto(i, slices, at: 0)
        m.settle(at: 1)
        return m
    }

    @Test func startsFlatOnTheLargestSector() {
        let m = Sector3DInteraction()
        #expect(m.selected(count: 5) == 0)
        #expect(m.drill.value(at: 0) == 0)
        #expect(m.rotNow(at: 0) == 0)
        #expect(!m.isDrilledIn)
        #expect(m.animationDeadline == nil)
    }

    @Test func aTapOnAWedgeDrillsIntoIt() {
        var m = Sector3DInteraction()
        let hit = m.tap(at: point(on: 2, flatScene(m)), scene: flatScene(m), slices, at: 10)
        #expect(hit)
        #expect(m.selected(count: 5) == 2)
        #expect(m.drillTicks == 1)
        #expect(m.selectionTicks == 0)
        #expect(m.isDrilledIn)
        #expect(eq(m.drill.endTime!, 10.62))
        // The rotation is already on its target; the drill sweeps it up.
        #expect(m.rotation.value(at: 10) == 1)
        #expect(eq(m.rotNow(at: 10), Sector3DGeometry.targetRotation(2, slices)))
        #expect(m.isAnimating(at: 10.3))
    }

    @Test func tapsMissingAWedgeOrPastFivePercentDoNothing() {
        var m = Sector3DInteraction()
        let scene = flatScene(m)
        let hole = m.tap(at: CGPoint(x: 180, y: 140), scene: scene, slices, at: 0)
        #expect(!hole)
        #expect(m.drillTicks == 0)
        m.drillInto(0, slices, at: 0)
        // 0.1 s in, the controller is at 0.16: drilled taps do nothing.
        let late = m.tap(at: point(on: 3, scene), scene: scene, slices, at: 0.1)
        #expect(!late)
        #expect(m.selected(count: 5) == 0)
        #expect(m.drillTicks == 1)
    }

    @Test func backReversesTheDrillAndKeepsTheFocus() {
        var m = drilled(into: 3)
        m.back(at: 5)
        #expect(m.backTicks == 1)
        #expect(!m.isDrilledIn)
        #expect(eq(m.drill.value(at: 5.31), 0.5))
        #expect(m.drill.value(at: 5.7) == 0)
        #expect(m.selected(count: 5) == 3)
    }

    @Test func backHalfwayInTakesHalfTheTime() {
        var m = Sector3DInteraction()
        m.drillInto(1, slices, at: 0)
        m.back(at: 0.31)
        #expect(eq(m.drill.endTime!, 0.62))
    }

    @Test func aFlatSwipeStepsEvery44Points() {
        var m = Sector3DInteraction()
        m.dragBegan(at: 0)
        #expect(!m.dragging)
        m.dragMoved(by: 30, slices, at: 0)
        #expect(m.selected(count: 5) == 0)
        m.dragMoved(by: 14, slices, at: 0)
        #expect(m.selected(count: 5) == 1)
        #expect(m.selectionTicks == 1)
        m.dragMoved(by: -88, slices, at: 0)
        #expect(m.selected(count: 5) == 4)
        #expect(m.selectionTicks == 3)
        m.dragEnded(slices, at: 0)
        #expect(m.dragAcc == 0)
        // The flat ring itself never turns.
        #expect(m.rotNow(at: 0) == 0)
    }

    @Test func aSwipeBeforeHalfDrilledStillSteps() {
        var m = Sector3DInteraction()
        m.drillInto(0, slices, at: 0)
        m.dragBegan(at: 0.2) // controller at 0.32
        #expect(!m.dragging)
        m.dragMoved(by: 44, slices, at: 0.2)
        #expect(m.selected(count: 5) == 1)
    }

    @Test func aDrilledDragTurnsTheRingWithTheFinger() {
        var m = drilled(into: 0)
        let start = Sector3DGeometry.targetRotation(0, slices)
        m.dragBegan(at: 2)
        #expect(m.dragging)
        #expect(eq(m.rot, start))
        m.dragMoved(by: 10, slices, at: 2)
        #expect(eq(m.rotNow(at: 2), start + 10 * 0.009))
        #expect(m.selected(count: 5) == 0)
        // A rightward drag of 0.48π / 0.009 brings Consumer, the sector
        // before Technology, to the top.
        m.dragMoved(by: 160, slices, at: 2)
        #expect(m.selected(count: 5) == 4)
        #expect(m.selectionTicks == 1)
    }

    @Test func releasingEasesTheNearestSectorToTheTop() {
        var m = drilled(into: 0)
        m.dragBegan(at: 2)
        m.dragMoved(by: 170, slices, at: 2)
        let released = m.rot
        m.dragEnded(slices, at: 3)
        #expect(!m.dragging)
        let target = Sector3DGeometry.shortest(to: Sector3DGeometry.targetRotation(4, slices), from: released)
        #expect(eq(m.rotNow(at: 3), released))
        #expect(eq(m.rotNow(at: 3.19), (released + target) / 2, 1e-3))
        #expect(eq(m.rotNow(at: 3.5), target))
        #expect(eq(m.rotation.endTime!, 3.38))
        #expect(Sector3DGeometry.nearestTop(rotation: target, slices) == 4)
    }

    @Test func grabbingMidRotationStartsFromWhatIsOnScreen() {
        var m = drilled(into: 0)
        m.step(1, slices, at: 2, animated: true)
        let shown = m.rotNow(at: 2.19)
        m.dragBegan(at: 2.19)
        #expect(eq(m.rot, shown))
        #expect(eq(m.rotNow(at: 2.3), shown))
    }

    @Test func aDrilledStepTurnsTheNextSectorUpOver380Milliseconds() {
        var m = drilled(into: 0)
        let from = m.rotNow(at: 2)
        m.step(1, slices, at: 2, animated: true)
        #expect(m.selected(count: 5) == 1)
        #expect(m.selectionTicks == 1)
        let target = Sector3DGeometry.shortest(to: Sector3DGeometry.targetRotation(1, slices), from: from)
        #expect(eq(m.rotNow(at: 2), from))
        #expect(eq(m.rotNow(at: 2.5), target))
        #expect(Sector3DGeometry.nearestTop(rotation: target, slices) == 1)
    }

    @Test func aFlatStepMovesTheHighlightOnly() {
        var m = Sector3DInteraction()
        m.step(-1, slices, at: 0, animated: true)
        #expect(m.selected(count: 5) == 4)
        #expect(m.rotation.endTime == nil)
        #expect(m.rotNow(at: 0) == 0)
    }

    @Test func reduceMotionStepsJumpWithoutTurning() {
        var m = drilled(into: 0)
        m.step(1, slices, at: 2, animated: false)
        #expect(m.rotation.endTime == nil)
        #expect(eq(m.rotNow(at: 2), Sector3DGeometry.shortest(
            to: Sector3DGeometry.targetRotation(1, slices), from: Sector3DGeometry.targetRotation(0, slices)
        )))
    }

    @Test func reduceMotionDrilledSwipesStepInsteadOfRotating() {
        var m = drilled(into: 0)
        m.dragBegan(at: 2, reduceMotion: true)
        #expect(!m.dragging)
        m.dragMoved(by: 20, slices, at: 2)
        #expect(m.selected(count: 5) == 0)
        // Rightward brings the previous sector to the top, as the turn would.
        m.dragMoved(by: 24, slices, at: 2)
        #expect(m.selected(count: 5) == 4)
        #expect(m.rotation.endTime == nil)
        #expect(eq(m.rotNow(at: 2), Sector3DGeometry.shortest(
            to: Sector3DGeometry.targetRotation(4, slices), from: Sector3DGeometry.targetRotation(0, slices)
        )))
        m.dragEnded(slices, at: 2)
        #expect(m.rotation.endTime == nil)
    }

    @Test func aSingleSliceNeverSteps() {
        let one = [SectorSlice(sector: "Technology", value: 1, pct: 100)]
        var m = Sector3DInteraction()
        m.dragBegan(at: 0)
        m.dragMoved(by: 200, one, at: 0)
        m.step(1, one, at: 0, animated: true)
        #expect(m.selected(count: 1) == 0)
        #expect(m.selectionTicks == 0)
    }

    @Test func theFocusClampsWhenTheSlicesShrink() {
        let m = drilled(into: 4)
        #expect(m.selected(count: 5) == 4)
        #expect(m.selected(count: 3) == 2)
        #expect(m.selected(count: 0) == 0)
    }

    @Test func settlingEndsTheRuns() {
        var m = Sector3DInteraction()
        m.drillInto(0, slices, at: 0)
        #expect(eq(m.animationDeadline!, 0.62))
        m.settle(at: 0.3)
        #expect(m.animationDeadline != nil)
        m.settle(at: 0.7)
        #expect(m.animationDeadline == nil)
        #expect(!m.isAnimating(at: 0.7))
        #expect(m.drill.value(at: 0) == 1)
    }
}
