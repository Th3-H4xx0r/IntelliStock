import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/network/api_base_url.dart';
import '../../../core/network/session.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';

/// First-run + Settings "edit instance" screen. Captures the backend base URL,
/// probes `GET {url}/health`, and saves it. When reached via the router gate
/// (unconfigured) there is no back affordance; when pushed from Settings the
/// system back works.
class ConnectScreen extends ConsumerStatefulWidget {
  const ConnectScreen({super.key});

  @override
  ConsumerState<ConnectScreen> createState() => _ConnectScreenState();
}

class _ConnectScreenState extends ConsumerState<ConnectScreen> {
  late final TextEditingController _controller;
  String? _error;
  bool _probing = false;
  bool _probeFailed = false; // when true, the button becomes "Save anyway"

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(
      text: ref.read(apiBaseUrlProvider).baseUrl,
    );
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _save(String url) async {
    if (!isValidBaseUrl(url)) {
      setState(() {
        _error = 'Enter a valid URL, e.g. https://your-instance.example.com';
        _probing = false;
        _probeFailed = false;
      });
      return;
    }
    final store = ref.read(apiBaseUrlProvider);
    final wasConfigured = store.isConfigured;
    final previous = store.baseUrl;
    final next = normalizeBaseUrl(url);
    final changed = next != previous;
    await store.set(next);
    // Changing to a different instance invalidates the existing session.
    if (wasConfigured && changed) {
      await ref.read(sessionProvider).clear();
    }
    if (!mounted) return;
    if (Navigator.of(context).canPop()) {
      // Edited from Settings: re-login only if the instance actually changed;
      // an unchanged save just returns the user to Settings.
      changed ? context.go('/login') : context.pop();
    }
    // First-run (can't pop): the merged refreshListenable re-runs the router
    // redirect, which moves us off /connect to /login now that a URL is set.
  }

  Future<void> _testAndConnect() async {
    final raw = _controller.text;
    if (!isValidBaseUrl(raw)) {
      setState(() {
        _error = 'Enter a valid URL, e.g. https://your-instance.example.com';
        _probeFailed = false;
      });
      return;
    }
    final url = normalizeBaseUrl(raw);
    setState(() {
      _error = null;
      _probing = true;
      _probeFailed = false;
    });
    final reachable = await _probe(url);
    if (!mounted) return;
    if (reachable) {
      await _save(url);
      return;
    }
    setState(() {
      _probing = false;
      _probeFailed = true;
      _error = "Couldn't reach $url/health. Check the URL, or save anyway.";
    });
  }

  Future<bool> _probe(String url) async {
    final dio = Dio(
      BaseOptions(
        baseUrl: url,
        connectTimeout: const Duration(seconds: 4),
        receiveTimeout: const Duration(seconds: 4),
        headers: {'Accept': 'application/json'},
      ),
    );
    try {
      final res = await dio.get<dynamic>('/health');
      return res.statusCode == 200;
    } catch (_) {
      return false;
    } finally {
      dio.close(force: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final canPop = Navigator.of(context).canPop();
    return PopScope(
      // First-run gate (nothing to pop): swallow hardware back so the user
      // can't escape into an unconfigured blank state. Edit-from-Settings
      // (canPop) keeps normal back behaviour.
      canPop: canPop,
      child: Scaffold(
        backgroundColor: AppColors.canvas,
        appBar: canPop
            ? AppBar(backgroundColor: Colors.transparent, elevation: 0)
            : null,
        body: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Connect to your instance', style: AppTextStyles.h2),
                  const SizedBox(height: 8),
                  Text(
                    'Enter the URL of your IntelliStock backend. You can change '
                    'this later in Settings.',
                    style: AppTextStyles.body.copyWith(
                      color: AppColors.textMuted,
                    ),
                  ),
                  const SizedBox(height: 20),
                  TextField(
                    controller: _controller,
                    autocorrect: false,
                    enableSuggestions: false,
                    keyboardType: TextInputType.url,
                    style: AppTextStyles.body.copyWith(color: AppColors.textHi),
                    decoration: InputDecoration(
                      hintText: 'https://your-instance.example.com',
                      hintStyle: AppTextStyles.body.copyWith(
                        color: AppColors.textDim,
                      ),
                      errorText: _error,
                      filled: true,
                      fillColor: AppColors.surface,
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide: BorderSide(color: AppColors.border),
                      ),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide: BorderSide(color: AppColors.border),
                      ),
                    ),
                    onChanged: (_) {
                      if (_error != null || _probeFailed) {
                        setState(() {
                          _error = null;
                          _probeFailed = false;
                        });
                      }
                    },
                    onSubmitted: (_) => _testAndConnect(),
                  ),
                  const SizedBox(height: 20),
                  SizedBox(
                    width: double.infinity,
                    child: AppButton.primary(
                      label: _probeFailed ? 'Save anyway' : 'Test & Connect',
                      busy: _probing,
                      onPressed: _probing
                          ? null
                          : (_probeFailed
                                ? () => _save(_controller.text)
                                : _testAndConnect),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
