import RealityKit
import SwiftUI

/// The 3D coin above the login form — `LoginCoin` in `login_coin.dart`, the
/// app's signature moment.
///
/// Native form: a RealityKit `RealityView` with a virtual camera instead of a
/// WebView. The coin and its four baked clips come from
/// `Resources/Coin/coin_{intro,idle,spin,success}.usdz` (one file per clip —
/// the system converter keeps only a file's first clip). The Intro file's
/// entities play every clip. It is scenery: it never takes a touch, and if
/// RealityKit cannot load it, a flat fallback disc takes its place so nothing
/// blocks signing in. No glow under it (operator: no gradients).
struct LoginCoinView: View {
    let phase: CoinPhase
    /// The coin's visual size at rest.
    var size: CGFloat = 322
    var onEntranceStarted: () -> Void = {}

    /// The scene renders at this size and is scaled DOWN to `size` at rest,
    /// growing back to full size on success (the Dart viewport).
    static let viewport: CGFloat = 470

    @State private var model = LoginCoinModel()
    @State private var scene = LoginCoinScene()
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        ZStack {
            if model.failed {
                LoginCoinFallback(size: size)
            } else {
                RealityView { content in
                    content.camera = .virtual
                    content.add(scene.root)
                }
                .frame(width: Self.viewport, height: Self.viewport)
                .scaleEffect(phase == .success ? 1 : size / Self.viewport)
                .animation(.timingCurve(0.215, 0.61, 0.355, 1, duration: 0.9), value: phase == .success)
                .opacity(model.started ? 1 : 0)
                .animation(.easeOut(duration: 0.18), value: model.started)
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: size)
        // Scenery, not a control: taps belong to the form beneath.
        .allowsHitTesting(false)
        .accessibilityHidden(true)
        .task {
            model.player = scene
            model.onEntranceStarted = onEntranceStarted
            model.setPhase(phase)
            model.start()
            do {
                try await scene.load()
                model.didLoad()
            } catch {
                model.didFail()
            }
        }
        .onDisappear { model.stop() }
        .onChange(of: phase) { _, new in model.setPhase(new) }
        .onChange(of: scenePhase) { _, new in
            if new == .active {
                model.didBecomeActive()
            } else {
                model.didEnterBackground()
            }
        }
    }
}

/// The RealityKit side of the coin: one root entity holding the Intro file's
/// hierarchy (Root / MainCoin / SatL / SatR), a camera framing it, and every
/// clip's animation resource taken from its own file.
@MainActor
final class LoginCoinScene: LoginCoinPlaying {
    let root = Entity()
    private var coin: Entity?
    private var clips: [CoinClip: AnimationResource] = [:]
    private var playback: AnimationPlaybackController?

    init() {
        let camera = PerspectiveCamera()
        camera.camera.fieldOfViewInDegrees = 30
        camera.position = [0, 0, 9.2]
        camera.look(at: .zero, from: camera.position, relativeTo: nil)
        root.addChild(camera)

        // A soft key light from the upper left plus a fill, so the metal reads
        // as lit on the plain background.
        let key = DirectionalLight()
        key.light.intensity = 2600
        key.look(at: .zero, from: [-3, 4, 6], relativeTo: nil)
        root.addChild(key)
        let fill = DirectionalLight()
        fill.light.intensity = 900
        fill.look(at: .zero, from: [4, -1, 5], relativeTo: nil)
        root.addChild(fill)
    }

    /// The bundled USDZ for `clip` (synced folders may keep the `Coin/`
    /// subdirectory or flatten it).
    static func url(for clip: CoinClip) -> URL? {
        Bundle.main.url(forResource: clip.file, withExtension: "usdz")
            ?? Bundle.main.url(forResource: clip.file, withExtension: "usdz", subdirectory: "Coin")
    }

    /// Loads the Intro file as the coin and each clip from its own file.
    /// Throws only when the coin itself cannot load; a missing clip just never
    /// plays.
    func load() async throws {
        guard coin == nil else { return }
        guard let introURL = Self.url(for: .intro) else { throw CocoaError(.fileNoSuchFile) }
        let intro = try await Entity(contentsOf: introURL)
        if let animation = intro.availableAnimations.first {
            clips[.intro] = animation
        }
        root.addChild(intro)
        coin = intro
        for clip in CoinClip.allCases where clip != .intro {
            guard let url = Self.url(for: clip),
                  let source = try? await Entity(contentsOf: url),
                  let animation = source.availableAnimations.first
            else { continue }
            clips[clip] = animation
        }
    }

    func play(_ clip: CoinClip) -> Bool {
        guard let coin, let animation = clips[clip] else { return false }
        playback?.stop()
        playback = coin.playAnimation(clip.loops ? animation.repeat() : animation, transitionDuration: 0.2)
        return true
    }

    func pause() {
        playback?.pause()
    }
}

/// Shown when the 3D coin cannot load: a flat accent disc with the same
/// silhouette, so the layout never jumps — `_Fallback`, without its gradient.
private struct LoginCoinFallback: View {
    let size: CGFloat

    var body: some View {
        let d = size * 0.62
        Circle()
            .fill(DS.Palette.accent.opacity(DS.tintFill))
            .overlay(Circle().strokeBorder(DS.Palette.accent.opacity(0.55), lineWidth: 1.4))
            .overlay(
                Image(systemName: Symbol.named("show_chart"))
                    .font(.system(size: d * 0.45))
                    .foregroundStyle(DS.Palette.accent.opacity(0.9))
            )
            .frame(width: d, height: d)
    }
}
