import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_3d_controller/flutter_3d_controller.dart';

import '../../../core/theme/app_colors.dart';

/// The 3D coin above the login form.
///
/// The model carries two baked rotation clips — `Intro` (an eased three turns
/// that settles exactly face-on) and `Spin` (a seamless linear loop). Playing
/// clips instead of driving the camera from Dart matters: the viewer is a
/// WebView, so per-frame control would mean a JavaScript bridge call every
/// frame. Here the renderer animates natively and Dart only says which clip.
///
/// Behaviour:
///  • plays `Intro` once when the model finishes loading;
///  • loops `Spin` while [signingIn] is true;
///  • replays `Intro` when signing-in ends, so it settles rather than stopping
///    mid-turn.
///
/// The 3D view is best-effort scenery: if it fails to load — WebView blocked,
/// asset missing, GPU unavailable — [_Fallback] takes over and the login form
/// is untouched. Nothing here can block signing in.
/// What the coin should be doing.
enum CoinPhase {
  /// Resting after its entrance.
  idle,

  /// A request is in flight.
  working,

  /// Accepted — satellites collapse inward and the coin turns over to
  /// show the IntelliStock mark on its back.
  success,
}

class LoginCoin extends StatefulWidget {
  const LoginCoin({
    super.key,
    required this.phase,
    this.size = 322,
    this.onEntranceStarted,
  });

  final CoinPhase phase;

  /// The coin's visual size at rest. The viewer itself always renders at
  /// [_viewport] and is scaled DOWN to this — see below.
  final double size;
  final VoidCallback? onEntranceStarted;

  /// The WebView's true render size. A WebView is rasterised at its own
  /// dimensions, so scaling one UP magnifies pixels and goes soft. Render
  /// big once and scale down instead: crisp at rest, and still crisp when
  /// the success phase grows it back to full size.
  static const double _viewport = 470;

  @override
  State<LoginCoin> createState() => _LoginCoinState();
}

class _LoginCoinState extends State<LoginCoin> with WidgetsBindingObserver {
  final _controller = Flutter3DController();
  bool _loaded = false;
  bool _failed = false;
  Timer? _loadTimeout;

  /// Set the moment a clip is actually told to play. The viewer stays
  /// invisible until then — otherwise the model's default rest pose (all
  /// three coins already out) flashes before the entrance begins.
  bool _started = false;
  bool _entranceNotified = false;
  Timer? _idle;

  /// The entrance is a one-time event. Returning to idle after a REJECTED
  /// sign-in must not replay it — that made a wrong password look like the
  /// screen had just opened.
  bool _introDone = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // Never leave a blank WebView while iOS initializes the 3D renderer.
    // The fallback is visible immediately; this timeout only decides when to
    // keep that deliberate fallback if the bridge never becomes ready.
    _loadTimeout = Timer(const Duration(milliseconds: 1800), () {
      if (!mounted || _loaded || _failed) return;
      _onError('coin viewer timed out while loading');
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _loadTimeout?.cancel();
    _idle?.cancel();
    super.dispose();
  }

  void _play() {
    _idle?.cancel();
    if (!_started && mounted) {
      setState(() => _started = true);
      _notifyEntranceStarted();
    }
    switch (widget.phase) {
      case CoinPhase.idle:
        if (_introDone) {
          // Back from a failed attempt: settle straight into the bob.
          _controller.playAnimation(animationName: 'Idle');
          return;
        }
        _introDone = true;
        _controller.playAnimation(animationName: 'Intro', loopCount: 1);
        // The player reports no completion event, so hand over to the
        // looping bob on a timer matched to the Intro clip's length.
        _idle = Timer(const Duration(milliseconds: 2400), () {
          if (!mounted || widget.phase != CoinPhase.idle) return;
          _controller.playAnimation(animationName: 'Idle');
        });
      case CoinPhase.working:
        // loopCount 0 loops until the outcome is known.
        _controller.playAnimation(animationName: 'Spin');
      case CoinPhase.success:
        _controller.playAnimation(animationName: 'Success', loopCount: 1);
    }
  }

  void _notifyEntranceStarted() {
    if (_entranceNotified) return;
    _entranceNotified = true;
    widget.onEntranceStarted?.call();
  }

  @override
  void didUpdateWidget(covariant LoginCoin old) {
    super.didUpdateWidget(old);
    // Deliberately NOT gated on _loaded: if onLoad stayed silent the model
    // may still be live, and the clip call is harmless if it isn't.
    if (widget.phase == old.phase) return;
    _play();
  }

  /// The viewer is a WebView running a render loop. Backgrounded it keeps
  /// drawing frames nobody sees, so pause the clip and pick it up on return.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (!_started) return;
    if (state == AppLifecycleState.resumed) {
      _play();
    } else {
      _controller.pauseAnimation();
    }
  }

  void _onLoad(String src) {
    debugPrint('[coin] loaded: $src');
    _loadTimeout?.cancel();
    if (!mounted) return;
    setState(() {
      _loaded = true;
      _failed = false;
    });
    // Let the newly visible WebView paint before issuing its first clip.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _play();
    });
  }

  void _onError(String error) {
    debugPrint('[coin] FAILED: $error');
    _loadTimeout?.cancel();
    if (mounted) {
      setState(() => _failed = true);
      _notifyEntranceStarted();
    }
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: widget.size,
      width: double.infinity,
      child: Stack(
        alignment: Alignment.center,
        children: [
          // Violet light pooled under the coin, so it reads as lit rather than
          // pasted on. Also covers the gap before the model appears.
          IgnorePointer(
            child: AnimatedScale(
              scale: widget.phase == CoinPhase.success ? 1.25 : 1.0,
              duration: const Duration(milliseconds: 900),
              curve: Curves.easeOutCubic,
              child: Container(
                width: widget.size * 1.5,
                height: widget.size * 1.5,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: RadialGradient(
                    colors: [
                      AppColors.primary.withValues(
                        alpha: widget.phase == CoinPhase.success ? 0.15 : 0.30,
                      ),
                      AppColors.primary.withValues(alpha: 0.10),
                      AppColors.primary.withValues(alpha: 0),
                    ],
                    stops: const [0.0, 0.45, 1.0],
                  ),
                ),
              ),
            ),
          ),

          // This appears in the very first frame, giving the coin slot an
          // intentional silhouette while the WebView/GL renderer warms up.
          AnimatedOpacity(
            opacity: _loaded && !_failed ? 0 : 1,
            duration: const Duration(milliseconds: 220),
            child: _Fallback(size: widget.size),
          ),
          if (!_failed) ...[
            // OverflowBox lets the oversized viewer live inside the
            // smaller slot the layout reserves for it.
            OverflowBox(
              maxWidth: LoginCoin._viewport,
              maxHeight: LoginCoin._viewport,
              child: AnimatedOpacity(
                opacity: _loaded && _started ? 1 : 0,
                duration: const Duration(milliseconds: 180),
                curve: Curves.easeOut,
                child: AnimatedScale(
                  scale: widget.phase == CoinPhase.success
                      ? 1.0
                      : widget.size / LoginCoin._viewport,
                  duration: const Duration(milliseconds: 900),
                  curve: Curves.easeOutCubic,
                  child: SizedBox(
                    width: LoginCoin._viewport,
                    height: LoginCoin._viewport,
                    child: Flutter3DViewer(
                      src: 'assets/models/coin.glb',
                      controller: _controller,
                      // Scenery, not a control: taps belong to the form beneath.
                      enableTouch: false,
                      activeGestureInterceptor: false,
                      progressBarColor: Colors.transparent,
                      onLoad: _onLoad,
                      onError: _onError,
                    ),
                  ),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Shown when the 3D view cannot start: a flat violet disc with the same
/// silhouette, so the layout never jumps and the page still looks deliberate.
class _Fallback extends StatelessWidget {
  const _Fallback({required this.size});

  final double size;

  @override
  Widget build(BuildContext context) {
    final d = size * 0.62;
    return Container(
      width: d,
      height: d,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF2A1B4E), Color(0xFF130C24)],
        ),
        border: Border.all(
          color: const Color(0xFFB9A5F5).withValues(alpha: 0.55),
          width: 1.4,
        ),
      ),
      child: Icon(
        Icons.show_chart,
        size: d * 0.45,
        color: const Color(0xFFB9A5F5).withValues(alpha: 0.9),
      ),
    );
  }
}
