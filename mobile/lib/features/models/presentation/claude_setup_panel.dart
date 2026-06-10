import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';
import '../data/model_repository.dart';

/// Tolerant string coercion — some JSON fields can come back as Map/String;
/// stringify instead of crashing on an `as String?` cast (mirrors the
/// `_asStr` helper in model_repository.dart, which is private to that file).
String? _asStr(dynamic v) {
  if (v == null) return null;
  if (v is String) return v;
  if (v is Map || v is List) return jsonEncode(v);
  return v.toString();
}

/// Claude Code CLI setup panel — mirrors CodexCliSetupPanel but for Claude's
/// two-step paste-back OAuth flow:
///   1. start → backend returns a sign-in URL
///   2. user opens it, signs in, copies the authorization code, pastes it here
///   3. submit the code → backend exchanges it for a subscription token
/// Shows install/auth status, "Re-authenticate Claude", the login URL (with
/// host allowlist), a code field, and sign-out.
class ClaudeCliSetupPanel extends ConsumerStatefulWidget {
  const ClaudeCliSetupPanel({super.key, this.cliPath = 'claude'});

  final String cliPath;

  @override
  ConsumerState<ClaudeCliSetupPanel> createState() => _ClaudeCliSetupPanelState();
}

class _ClaudeCliSetupPanelState extends ConsumerState<ClaudeCliSetupPanel> {
  // Status
  bool _statusLoading = true;
  String _statusError = '';
  bool _installed = false;
  String? _version;
  bool _authenticated = false;
  String? _account;
  String _authMessage = '';

  // Login job
  String? _loginJobId;
  String _loginState = ''; // parsed | success | failed | cancelled | ''
  String _loginUrl = '';
  String _loginError = '';
  bool _loginStarting = false;

  // Code submission
  final _codeController = TextEditingController();
  bool _submitting = false;
  String _submitMessage = '';
  bool _submitOk = false;

  bool _mounted = true;

  ModelRepository get _repo => ref.read(modelRepositoryProvider);

  @override
  void initState() {
    super.initState();
    _fetchStatus();
  }

  @override
  void dispose() {
    _mounted = false;
    _codeController.dispose();
    super.dispose();
  }

  // ── Status ───────────────────────────────────────────────────────────────────

  Future<void> _fetchStatus() async {
    if (!_mounted) return;
    setState(() { _statusLoading = true; _statusError = ''; });
    try {
      final s = await _repo.claudeAuthStatus();
      if (!_mounted) return;
      setState(() {
        _installed = (s['installed'] as bool?) ?? false;
        _version = _asStr(s['version']);
        _authenticated = (s['authenticated'] as bool?) ?? false;
        _account = _asStr(s['account']);
        _authMessage = _asStr(s['auth_message']) ?? '';
        _statusLoading = false;
      });
    } catch (e) {
      if (!_mounted) return;
      setState(() { _statusLoading = false; _statusError = e.toString(); });
    }
  }

  // ── Login ────────────────────────────────────────────────────────────────────

  static const _kAllowedLoginHosts = {
    'claude.ai', 'www.claude.ai',
    'claude.com', 'www.claude.com',
    'console.anthropic.com', 'anthropic.com',
  };

  bool _isSafeLoginUrl(String url) {
    if (url.isEmpty) return false;
    try {
      final u = Uri.parse(url);
      if (!['http', 'https'].contains(u.scheme)) return false;
      if ((u.userInfo).isNotEmpty) return false;
      return _kAllowedLoginHosts.contains(u.host.toLowerCase());
    } catch (_) {
      return false;
    }
  }

  Future<void> _startLogin() async {
    if (!_mounted) return;
    setState(() {
      _loginStarting = true;
      _loginJobId = null;
      _loginState = '';
      _loginUrl = '';
      _loginError = '';
      _codeController.clear();
      _submitMessage = '';
      _submitOk = false;
    });
    try {
      final data = await _repo.claudeLoginStart({
        'cli_path': widget.cliPath.isNotEmpty ? widget.cliPath : 'claude',
      });
      if (!_mounted) return;
      final incomingUrl = _asStr(data['login_url']) ?? '';
      final safeUrl = _isSafeLoginUrl(incomingUrl) ? incomingUrl : '';
      setState(() {
        _loginStarting = false;
        _loginJobId = _asStr(data['job_id']);
        _loginState = _asStr(data['state']) ?? '';
        _loginUrl = safeUrl;
        if (incomingUrl.isNotEmpty && safeUrl.isEmpty) {
          _loginError = 'claude returned a non-Anthropic login URL; ignoring for safety';
        } else {
          _loginError = _asStr(data['error']) ?? '';
        }
      });
    } catch (e) {
      if (!_mounted) return;
      setState(() { _loginStarting = false; _loginState = 'failed'; _loginError = e.toString(); });
    }
  }

  Future<void> _submitCode() async {
    final jobId = _loginJobId;
    final code = _codeController.text.trim();
    if (jobId == null) return;
    if (code.isEmpty) {
      setState(() { _submitOk = false; _submitMessage = 'Paste the authorization code first.'; });
      return;
    }
    if (!_mounted) return;
    setState(() { _submitting = true; _submitMessage = 'Exchanging code…'; _submitOk = false; });
    try {
      final data = await _repo.claudeLoginSubmit(jobId, code);
      if (!_mounted) return;
      final state = _asStr(data['state']) ?? '';
      setState(() {
        _submitting = false;
        _loginState = state;
        _loginError = _asStr(data['error']) ?? '';
      });
      if (state == 'success') {
        setState(() {
          _submitOk = true;
          _submitMessage = '✓ Claude re-authenticated';
          _loginUrl = '';
          _loginJobId = null;
        });
        _codeController.clear();
        await _fetchStatus();
      } else {
        final tail = _asStr(data['output_tail']);
        setState(() {
          _submitOk = false;
          _submitMessage = (_loginError.isNotEmpty ? _loginError : 'Login failed')
              + (tail != null && tail.isNotEmpty ? '\n$tail' : '');
        });
      }
    } catch (e) {
      if (!_mounted) return;
      setState(() { _submitting = false; _submitOk = false; _submitMessage = e.toString(); });
    }
  }

  Future<void> _cancelLogin() async {
    final jobId = _loginJobId;
    if (jobId != null) {
      try { await _repo.claudeLoginCancel(jobId); } catch (_) {}
    }
    if (!_mounted) return;
    setState(() {
      _loginState = 'cancelled';
      _loginUrl = '';
      _loginJobId = null;
      _submitMessage = '';
    });
  }

  Future<void> _logout() async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.panel,
        title: Text('Sign out of Claude?', style: AppTextStyles.h3),
        content: Text(
          'All strategies using claude-cli will need to re-authenticate.',
          style: AppTextStyles.body.copyWith(color: AppColors.textMuted),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text('Sign out', style: TextStyle(color: AppColors.danger)),
          ),
        ],
      ),
    );
    if (confirm != true) return;
    try {
      await _repo.claudeLogout();
    } catch (_) {}
    await _fetchStatus();
  }

  void _copyToClipboard(String text) {
    Clipboard.setData(ClipboardData(text: text));
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Copied'), duration: Duration(seconds: 1)),
      );
    }
  }

  Color _stateColor(String s) {
    if (s == 'success') return AppColors.success;
    if (s == 'failed' || s == 'cancelled') return AppColors.danger;
    return AppColors.warning;
  }

  @override
  Widget build(BuildContext context) {
    final loginLive = _loginUrl.isNotEmpty && _loginJobId != null && _loginState != 'success';

    return Container(
      margin: const EdgeInsets.only(top: 4, bottom: 4),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.info),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.stroke(AppColors.info)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Claude Code CLI (subscription) setup',
                  style: AppTextStyles.meta.copyWith(color: AppColors.textHi, fontWeight: FontWeight.w600)),
              IconButton(
                icon: _statusLoading
                    ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                    : Icon(Icons.refresh, size: 18, color: AppColors.textMuted),
                onPressed: _statusLoading ? null : _fetchStatus,
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
              ),
            ],
          ),
          const SizedBox(height: 8),

          // Status row
          if (_statusLoading)
            Text('Probing claude CLI status…', style: AppTextStyles.nano.copyWith(color: AppColors.textDim))
          else if (_statusError.isNotEmpty)
            Text('Status probe failed: $_statusError', style: AppTextStyles.nano.copyWith(color: AppColors.danger))
          else
            Wrap(spacing: 16, runSpacing: 4, children: [
              _statusChip('installed', _installed ? '✓ yes' : '✗ no', _installed ? AppColors.success : AppColors.warning),
              if (_installed && _version != null)
                _statusChip('version', _version!, AppColors.textMd),
              if (_installed)
                _statusChip('authenticated', _authenticated ? '✓ yes' : '✗ no',
                    _authenticated ? AppColors.success : AppColors.warning),
              if (_installed && _authenticated && _account != null && _account!.isNotEmpty)
                _statusChip('account', _account!, AppColors.textMd),
            ]),

          // Auth message
          if (!_statusLoading && _statusError.isEmpty && _authMessage.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(_authMessage, style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
          ],

          // Not installed
          if (!_statusLoading && _statusError.isEmpty && !_installed) ...[
            const SizedBox(height: 8),
            Text(
              'The claude binary is not installed on the server. '
              'Install it on the server before re-authenticating.',
              style: AppTextStyles.nano.copyWith(color: AppColors.warning),
            ),
          ],

          // Re-authenticate / login flow (installed)
          if (!_statusLoading && _statusError.isEmpty && _installed) ...[
            const SizedBox(height: 12),
            Text(
              _authenticated
                  ? 'Re-authenticate if the saved subscription token has expired '
                      '(e.g. "401 Invalid authentication credentials").'
                  : 'Claude is installed but not authenticated. Start the sign-in flow.',
              style: AppTextStyles.nano.copyWith(color: AppColors.textMuted),
            ),
            const SizedBox(height: 8),
            Row(children: [
              AppButton.primary(
                label: _loginStarting ? 'Starting…' : 'Re-authenticate Claude',
                busy: _loginStarting,
                onPressed: (_loginStarting || _submitting) ? null : _startLogin,
                dense: true,
              ),
              if (loginLive) ...[
                const SizedBox(width: 8),
                TextButton(
                  onPressed: _submitting ? null : _cancelLogin,
                  child: Text('Cancel', style: AppTextStyles.meta.copyWith(color: AppColors.textMuted)),
                ),
              ],
            ]),

            // Login state line
            if (_loginState.isNotEmpty && _loginState != 'parsed') ...[
              const SizedBox(height: 8),
              Row(children: [
                Text('Login state: ', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                Text(_loginState, style: AppTextStyles.nano.copyWith(color: _stateColor(_loginState))),
              ]),
            ],
            if (_loginError.isNotEmpty && !loginLive) ...[
              const SizedBox(height: 4),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: AppColors.fill(AppColors.danger),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: AppColors.stroke(AppColors.danger)),
                ),
                child: Text(_loginError, style: AppTextStyles.nano.copyWith(color: AppColors.danger)),
              ),
            ],

            // Paste-back flow
            if (loginLive) ...[
              const SizedBox(height: 10),
              Text('1. Open the link and sign in.\n'
                  '2. Copy the authorization code.\n'
                  '3. Paste it below.',
                  style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
              const SizedBox(height: 8),
              Row(children: [
                Expanded(
                  child: Text.rich(TextSpan(
                    children: [
                      TextSpan(text: 'Open URL: ', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                      TextSpan(
                        text: _loginUrl,
                        style: AppTextStyles.mono(10, color: AppColors.info),
                      ),
                    ],
                  )),
                ),
                IconButton(
                  icon: Icon(Icons.copy, size: 14, color: AppColors.textMuted),
                  onPressed: () => _copyToClipboard(_loginUrl),
                  padding: EdgeInsets.zero,
                  constraints: const BoxConstraints(),
                ),
              ]),
              const SizedBox(height: 10),
              TextField(
                controller: _codeController,
                enabled: !_submitting,
                style: AppTextStyles.mono(13, color: AppColors.textHi),
                decoration: InputDecoration(
                  hintText: 'Paste authorization code',
                  hintStyle: AppTextStyles.body.copyWith(color: AppColors.textFaint),
                  filled: true,
                  fillColor: AppColors.surface,
                  contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: AppColors.border)),
                  enabledBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: AppColors.border)),
                  focusedBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: AppColors.primary)),
                ),
              ),
              const SizedBox(height: 8),
              AppButton.primary(
                label: _submitting ? 'Submitting…' : 'Submit code',
                busy: _submitting,
                onPressed: _submitting ? null : _submitCode,
                dense: true,
              ),
            ],

            // Submit result
            if (_submitMessage.isNotEmpty) ...[
              const SizedBox(height: 8),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: AppColors.fill(_submitOk ? AppColors.success : AppColors.danger),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: AppColors.stroke(_submitOk ? AppColors.success : AppColors.danger)),
                ),
                child: Text(_submitMessage,
                    style: AppTextStyles.nano.copyWith(color: _submitOk ? AppColors.success : AppColors.danger)),
              ),
            ],

            // Authenticated → sign out
            if (_authenticated) ...[
              const SizedBox(height: 8),
              TextButton(
                onPressed: _logout,
                child: Text('Sign out of Claude',
                    style: AppTextStyles.micro.copyWith(color: AppColors.textMuted)),
              ),
            ],
          ],
        ],
      ),
    );
  }

  Widget _statusChip(String label, String value, Color color) {
    return RichText(
      text: TextSpan(children: [
        TextSpan(text: '$label: ', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
        TextSpan(text: value, style: AppTextStyles.nano.copyWith(color: color)),
      ]),
    );
  }
}
