import Foundation
import SwiftUI
import Testing
import UIKit
@testable import IntelliStock

/// Renders `Sector3DChart` as the Dart golden test did: five slices, 360
/// wide, with `debugDrill` 0 and 1, in both appearances. It checks the frame
/// heights and that every face is one solid colour (no gradient). Set
/// SECTOR3D_SNAPSHOT_DIR (TEST_RUNNER_SECTOR3D_SNAPSHOT_DIR through
/// xcodebuild) to also write the PNGs there.
@MainActor
struct Sector3DChartRenderTests {
    private static let width: CGFloat = 360
    private static let margin: CGFloat = 40
    private static let scale: CGFloat = 2

    private struct Pixels {
        let width: Int
        let height: Int
        let data: [UInt8]

        init?(_ image: UIImage) {
            guard let cg = image.cgImage else { return nil }
            let w = cg.width
            let h = cg.height
            var bytes = [UInt8](repeating: 0, count: w * h * 4)
            let ok = bytes.withUnsafeMutableBytes { buf -> Bool in
                guard let ctx = CGContext(
                    data: buf.baseAddress, width: w, height: h, bitsPerComponent: 8,
                    bytesPerRow: w * 4, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
                ) else { return false }
                ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
                return true
            }
            guard ok else { return nil }
            width = w
            height = h
            data = bytes
        }

        /// The colour at a chart point (in points, from the chart's origin).
        func at(_ p: CGPoint) -> (r: Int, g: Int, b: Int) {
            let x = Int(((p.x + margin) * scale).rounded())
            let y = Int(((p.y + margin) * scale).rounded())
            let i = (y * width + x) * 4
            return (Int(data[i]), Int(data[i + 1]), Int(data[i + 2]))
        }
    }

    private func render(drill: Double, scheme: ColorScheme, slices: [SectorSlice]) -> UIImage? {
        let view = Sector3DChart(slices: slices, debugDrill: drill)
            .frame(width: Self.width)
            .padding(Self.margin)
            .background(DS.Surface.panel)
            .environment(\.colorScheme, scheme)
        let renderer = ImageRenderer(content: view)
        renderer.scale = Self.scale
        return renderer.uiImage
    }

    private func save(_ image: UIImage, _ name: String) throws {
        guard let dir = ProcessInfo.processInfo.environment["SECTOR3D_SNAPSHOT_DIR"], !dir.isEmpty else { return }
        let url = URL(fileURLWithPath: dir, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        try image.pngData()?.write(to: url.appendingPathComponent("\(name).png"))
    }

    /// Points inside wedge `i`'s top face, clear of its edges.
    private func facePoints(_ i: Int, _ scene: Sector3DScene) -> [CGPoint] {
        let w = scene.wedges.first { $0.index == i }!
        let l = scene.layout
        let band = (l.ro - l.ri) * 0.25
        let r = (l.ro + l.ri) / 2
        let half = (w.a1 - w.a0) * 0.3
        return [
            l.onOval(r, w.mid, w.segWall),
            l.onOval(r - band, w.mid - half, w.segWall),
            l.onOval(r + band, w.mid + half, w.segWall),
            l.onOval(r + band, w.mid - half, w.segWall),
        ]
    }

    private func isViolet(_ c: (r: Int, g: Int, b: Int)) -> Bool {
        c.b > c.r && c.r > c.g
    }

    @Test(arguments: [ColorScheme.light, .dark], [0.0, 1.0])
    func rendersTheGoldenStatesWithFlatFaces(scheme: ColorScheme, drill: Double) throws {
        let slices = Sector3DGeometryTests.slices
        let image = try #require(render(drill: drill, scheme: scheme, slices: slices))
        let chartHeight = Sector3DGeometry.height(drill: drill)
        #expect(abs(image.size.width - (Self.width + 2 * Self.margin)) < 0.5)
        #expect(abs(image.size.height - (chartHeight + 2 * Self.margin)) < 0.5)
        try save(image, "sector3d-\(drill == 0 ? "flat" : "drilled")-\(scheme == .dark ? "dark" : "light")")

        let px = try #require(Pixels(image))
        let scene = Sector3DScene(
            slices: slices, selected: 0, drill: drill, ringRot: 0,
            size: CGSize(width: Self.width, height: chartHeight)
        )
        var tops: [Int: (r: Int, g: Int, b: Int)] = [:]
        // Drilled, the front blocks overlap the back ones and the Back button
        // sits over the front: sample the blocks whose tops are clear of both.
        let clear = drill == 0 ? Array(slices.indices) : [0, 3, 4]
        for i in clear {
            let colors = facePoints(i, scene).map(px.at)
            let first = colors[0]
            #expect(isViolet(first), "wedge \(i) is \(first)")
            for c in colors.dropFirst() {
                // One solid colour per face: no gradient across it.
                #expect(abs(c.r - first.r) <= 2 && abs(c.g - first.g) <= 2 && abs(c.b - first.b) <= 2,
                        "wedge \(i): \(c) vs \(first)")
            }
            tops[i] = first
        }
        // Neighbours read apart: every sampled top differs from the next.
        for (i, j) in zip(clear, clear.dropFirst()) {
            let a = tops[i]!, b = tops[j]!
            #expect(abs(a.r - b.r) + abs(a.g - b.g) + abs(a.b - b.b) > 6)
        }
    }

    @Test func drilledWallsAreDarkerThanTheirTops() throws {
        let slices = Sector3DGeometryTests.slices
        let image = try #require(render(drill: 1, scheme: .dark, slices: slices))
        let px = try #require(Pixels(image))
        let scene = Sector3DScene(
            slices: slices, selected: 0, drill: 1, ringRot: 0,
            size: CGSize(width: Self.width, height: Sector3DGeometry.drillHeight)
        )
        let l = scene.layout
        // Technology, raised at the upper right: its outer wall faces the
        // viewer from 0 (the right) round to its end.
        let w = scene.wedges.first { $0.index == 0 }!
        let ang = Sector3DGeometry.facingRanges(w.a0, w.a1, front: true).first!
        let mid = (ang.lowerBound + ang.upperBound) / 2
        let wallPoint = l.onOval(l.ro - 1, mid, w.segWall / 2)
        let top = px.at(facePoints(0, scene)[0])
        let wall = px.at(wallPoint)
        #expect(top.r + top.g + top.b > wall.r + wall.g + wall.b)
        #expect(isViolet(wall))
    }

    @Test(arguments: [1, 3, 8])
    func rendersEverySliceCount(count: Int) throws {
        let all = ["Technology", "Healthcare", "Financials", "Energy", "Consumer", "Industrials", "Utilities", "Real Estate"]
        let slices = all.prefix(count).enumerated().map { i, name in
            SectorSlice(sector: name, value: Double(count - i), pct: Double(count - i) / Double(count * (count + 1) / 2) * 100)
        }
        for drill in [0.0, 1.0] {
            for scheme in [ColorScheme.light, .dark] {
                let image = try #require(render(drill: drill, scheme: scheme, slices: slices))
                #expect(abs(image.size.height - (Sector3DGeometry.height(drill: drill) + 2 * Self.margin)) < 0.5)
                try save(image, "sector3d-\(count)-\(drill == 0 ? "flat" : "drilled")-\(scheme == .dark ? "dark" : "light")")
            }
        }
    }

    @Test func noSlicesIsAnEightPointSpacer() throws {
        let image = try #require(render(drill: 0, scheme: .light, slices: []))
        #expect(abs(image.size.height - (8 + 2 * Self.margin)) < 0.5)
    }
}
