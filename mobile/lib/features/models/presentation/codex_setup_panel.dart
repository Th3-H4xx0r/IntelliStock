import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';
import '../data/model_repository.dart';

/// Codex CLI setup panel — mirrors CodexCliSetupPanel.vue.
/// Shows install status, one-click install (with log tail), device-code login
/// (pairing URL + big code + copy), and logout.
class CodexCliSetupPanel extends ConsumerStatefulWidget {
  const CodexCliSetupPanel({super.key, this.cliPath = 'codex'});

  final String cliPath;

  @override
  ConsumerState<CodexCliSetupPanel> createState() => _CodexCliSetupPanelState();
}

class _CodexCliSetupPanelState extends ConsumerState<CodexCliSetupPanel> {
  // Status
  bool _statusLoading = true;
  String _statusError = '';
  bool _installed = false;
  String? _version;
  bool _authenticated = false;
  String _authMessage = '';
  String _installMethod = 'unknown';

  // Install job
  String? _installJobId;
  String _installState = ''; // running | success | failed | ''
  int? _installExitCode;
  List<String> _installLog = [];
  String _installError = '';
  Timer? _installTimer;

  // Login job
  String? _loginJobId;
  String _loginState = ''; // pending | parsed | success | failed | expired | cancelled | ''
  String _loginPairingUrl = '';
  String _loginPairingCode = '';
  String _loginError = '';
  Timer? _loginTimer;

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
    _installTimer?.cancel();
    _loginTimer?.cancel();
    super.dispose();
  }

  // ── Status ───────────────────────────────────────────────────────────────────

  Future<void> _fetchStatus() async {
    if (!_mounted) return;
    setState(() { _statusLoading = true; _statusError = ''; });
    try {
      final s = await _repo.codexStatus();
      if (!_mounted) return;
      setState(() {
        _installed = s.installed;
        _version = s.version;
        _authenticated = s.authenticated;
        _authMessage = s.authMessage ?? '';
        _installMethod = s.installMethod;
        _statusLoading = false;
      });
    } catch (e) {
      if (!_mounted) return;
      setState(() { _statusLoading = false; _statusError = e.toString(); });
    }
  }

  // ── Install ──────────────────────────────────────────────────────────────────

  Future<void> _startInstall() async {
    if (!_mounted) return;
    setState(() {
      _installJobId = null;
      _installState = 'running';
      _installExitCode = null;
      _installLog = [];
      _installError = '';
    });
    try {
      final data = await _repo.codexInstall();
      if (!_mounted) return;
      setState(() {
        _installJobId = data['job_id'] as String?;
        _installState = (data['state'] as String?) ?? 'running';
      });
      _scheduleInstallPoll();
    } catch (e) {
      if (!_mounted) return;
      setState(() { _installState = 'failed'; _installError = e.toString(); });
    }
  }

  void _scheduleInstallPoll() {
    if (!_mounted) return;
    _installTimer?.cancel();
    _installTimer = Timer(const Duration(milliseconds: 1500), _pollInstall);
  }

  Future<void> _pollInstall() async {
    final jobId = _installJobId;
    if (jobId == null || !_mounted) return;
    try {
      final data = await _repo.codexInstallJob(jobId);
      if (!_mounted) return;
      setState(() {
        _installState = (data['state'] as String?) ?? _installState;
        _installExitCode = (data['exit_code'] as num?)?.toInt();
        _installLog = (data['log_tail'] as List?)?.map((e) => e.toString()).toList() ?? _installLog;
        _installError = (data['error'] as String?) ?? '';
      });
      if (_installState == 'running') {
        _scheduleInstallPoll();
      } else {
        await _fetchStatus();
      }
    } catch (e) {
      if (!_mounted) return;
      setState(() { _installState = 'failed'; _installError = e.toString(); });
    }
  }

  // ── Login ────────────────────────────────────────────────────────────────────

  static const _kAllowedPairingHosts = {'chatgpt.com', 'platform.openai.com', 'auth.openai.com'};

  bool _isSafePairingUrl(String url) {
    if (url.isEmpty) return false;
    try {
      final u = Uri.parse(url);
      if (!['http', 'https'].contains(u.scheme)) return false;
      if ((u.userInfo).isNotEmpty) return false;
      return _kAllowedPairingHosts.contains(u.host.toLowerCase());
    } catch (_) {
      return false;
    }
  }

  Future<void> _startLogin() async {
    if (!_mounted) return;
    setState(() {
      _loginJobId = null;
      _loginState = 'pending';
      _loginPairingUrl = '';
      _loginPairingCode = '';
      _loginError = '';
    });
    try {
      final data = await _repo.codexLoginStart({'cli_path': widget.cliPath.isNotEmpty ? widget.cliPath : 'codex'});
      if (!_mounted) return;
      final incomingUrl = (data['pairing_url'] as String?) ?? '';
      final safeUrl = _isSafePairingUrl(incomingUrl) ? incomingUrl : '';
      setState(() {
        _loginJobId = data['job_id'] as String?;
        _loginState = (data['state'] as String?) ?? 'pending';
        _loginPairingUrl = safeUrl;
        _loginPairingCode = (data['pairing_code'] as String?) ?? '';
        if (incomingUrl.isNotEmpty && safeUrl.isEmpty) {
          _loginError = 'codex returned a non-OpenAI pairing URL; ignoring for safety';
        } else {
          _loginError = (data['error'] as String?) ?? '';
        }
      });
      _scheduleLoginPoll();
    } catch (e) {
      if (!_mounted) return;
      setState(() { _loginState = 'failed'; _loginError = e.toString(); });
    }
  }

  void _scheduleLoginPoll() {
    if (!_mounted) return;
    _loginTimer?.cancel();
    _loginTimer = Timer(const Duration(milliseconds: 2000), _pollLogin);
  }

  Future<void> _pollLogin() async {
    final jobId = _loginJobId;
    if (jobId == null || !_mounted) return;
    try {
      final data = await _repo.codexLoginStatus(jobId);
      if (!_mounted) return;
      final loginState = (data['state'] as String?) ?? _loginState;
      final incomingUrl = (data['pairing_url'] as String?) ?? '';
      setState(() {
        _loginState = loginState;
        if (incomingUrl.isNotEmpty && _isSafePairingUrl(incomingUrl)) {
          _loginPairingUrl = incomingUrl;
        }
        _loginPairingCode = (data['pairing_code'] as String?) ?? _loginPairingCode;
        _loginError = (data['error'] as String?) ?? '';
      });
      if (loginState == 'pending' || loginState == 'parsed') {
        _scheduleLoginPoll();
      } else {
        await _fetchStatus();
      }
    } catch (e) {
      if (!_mounted) return;
      setState(() { _loginState = 'failed'; _loginError = e.toString(); });
    }
  }

  Future<void> _logout() async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.panel,
        title: Text('Sign out of OpenAI?', style: AppTextStyles.h3),
        content: Text(
          'All strategies using codex-cli will need to re-authenticate.',
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
      await _repo.codexLogout();
    } catch (_) {}
    await _fetchStatus();
  }

  void _copyToClipboard(String text) {
    Clipboard.setData(ClipboardData(text: text));
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Copied to clipboard'), duration: Duration(seconds: 1)),
      );
    }
  }

  Color _stateColor(String s) {
    if (s == 'success') return AppColors.success;
    if (s == 'failed' || s == 'expired' || s == 'cancelled') return AppColors.danger;
    return AppColors.warning;
  }

  @override
  Widget build(BuildContext context) {
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
              Text('OpenAI Codex CLI setup',
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
            Text('Probing codex CLI status...', style: AppTextStyles.nano.copyWith(color: AppColors.textDim))
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
            ]),

          // Install panel
          if (!_statusLoading && !_statusError.isNotEmpty && !_installed) ...[
            const SizedBox(height: 12),
            if (_installMethod == 'unknown')
              Text(
                'The backend has neither npm nor brew available. '
                'Rebuild the backend image with INSTALL_CODEX_CLI=1.',
                style: AppTextStyles.nano.copyWith(color: AppColors.warning),
              )
            else ...[
              Text(
                'Codex CLI is not installed. Click to install via '
                '${_installMethod == 'brew' ? 'brew install codex' : 'npm install -g @openai/codex'}.',
                style: AppTextStyles.nano.copyWith(color: AppColors.textMuted),
              ),
              const SizedBox(height: 8),
              AppButton.primary(
                label: _installState == 'running' ? 'Installing...' : 'Install Codex CLI',
                busy: _installState == 'running',
                onPressed: _installState == 'running' ? null : _startInstall,
                dense: true,
              ),
            ],
            if (_installState.isNotEmpty) ...[
              const SizedBox(height: 8),
              Row(children: [
                Text('Install state: ', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                Text(_installState, style: AppTextStyles.nano.copyWith(color: _stateColor(_installState))),
                if (_installExitCode != null)
                  Text(' (exit $_installExitCode)', style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
              ]),
              if (_installLog.isNotEmpty) ...[
                const SizedBox(height: 4),
                Container(
                  constraints: const BoxConstraints(maxHeight: 100),
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: AppColors.panelAlt,
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: SingleChildScrollView(
                    reverse: true,
                    child: Text(
                      _installLog.join('\n'),
                      style: AppTextStyles.mono(10, color: AppColors.textMd),
                    ),
                  ),
                ),
              ],
              if (_installError.isNotEmpty)
                Text(_installError, style: AppTextStyles.nano.copyWith(color: AppColors.danger)),
            ],
          ],

          // Login panel
          if (!_statusLoading && !_statusError.isNotEmpty && _installed && !_authenticated) ...[
            const SizedBox(height: 12),
            Text(
              'Codex is installed but not authenticated. Start the OpenAI device-code login.',
              style: AppTextStyles.nano.copyWith(color: AppColors.textMuted),
            ),
            const SizedBox(height: 8),
            Row(children: [
              AppButton.primary(
                label: (_loginState == 'pending' || _loginState == 'parsed')
                    ? 'Waiting for sign-in...'
                    : 'Sign in with OpenAI',
                busy: _loginState == 'pending' || _loginState == 'parsed',
                onPressed: (_loginState == 'pending' || _loginState == 'parsed') ? null : _startLogin,
                dense: true,
              ),
              if (_loginState == 'pending' || _loginState == 'parsed') ...[
                const SizedBox(width: 8),
                TextButton(
                  onPressed: () async {
                    _loginTimer?.cancel();
                    final jobId = _loginJobId;
                    if (jobId != null) {
                      try { await _repo.codexLoginCancel(jobId); } catch (_) {}
                    }
                    if (_mounted) setState(() => _loginState = 'cancelled');
                  },
                  child: Text('Cancel', style: AppTextStyles.meta.copyWith(color: AppColors.textMuted)),
                ),
              ],
            ]),
            if (_loginState.isNotEmpty && _loginState != 'pending') ...[
              const SizedBox(height: 8),
              Row(children: [
                Text('Login state: ', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                Text(_loginState, style: AppTextStyles.nano.copyWith(color: _stateColor(_loginState))),
              ]),
              if (_loginPairingUrl.isNotEmpty) ...[
                const SizedBox(height: 8),
                Row(children: [
                  Expanded(
                    child: Text.rich(TextSpan(
                      children: [
                        TextSpan(text: 'Open URL: ', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                        TextSpan(
                          text: _loginPairingUrl,
                          style: AppTextStyles.mono(10, color: AppColors.info),
                        ),
                      ],
                    )),
                  ),
                  IconButton(
                    icon: Icon(Icons.copy, size: 14, color: AppColors.textMuted),
                    onPressed: () => _copyToClipboard(_loginPairingUrl),
                    padding: EdgeInsets.zero,
                    constraints: const BoxConstraints(),
                  ),
                ]),
              ],
              if (_loginPairingCode.isNotEmpty) ...[
                const SizedBox(height: 8),
                Row(children: [
                  Text('Code: ', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                  Expanded(
                    child: Text(
                      _loginPairingCode,
                      style: AppTextStyles.mono(22, color: AppColors.success,
                          weight: FontWeight.w700).copyWith(letterSpacing: 8),
                    ),
                  ),
                  IconButton(
                    icon: Icon(Icons.copy, size: 14, color: AppColors.textMuted),
                    onPressed: () => _copyToClipboard(_loginPairingCode),
                    padding: EdgeInsets.zero,
                    constraints: const BoxConstraints(),
                  ),
                ]),
              ],
              if (_loginError.isNotEmpty)
                Text(_loginError, style: AppTextStyles.nano.copyWith(color: AppColors.danger)),
            ],
          ],

          // Authenticated → logout
          if (!_statusLoading && !_statusError.isNotEmpty && _installed && _authenticated) ...[
            const SizedBox(height: 8),
            Text(
              '✓ Codex CLI is installed and authenticated. Strategies can now select codex-cli.',
              style: AppTextStyles.nano.copyWith(color: AppColors.success),
            ),
            if (_authMessage.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(_authMessage, style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
            ],
            const SizedBox(height: 8),
            TextButton(
              onPressed: _logout,
              child: Text('Sign out of OpenAI',
                  style: AppTextStyles.micro.copyWith(color: AppColors.textMuted)),
            ),
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
