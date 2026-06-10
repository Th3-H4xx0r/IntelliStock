import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../data/model_repository.dart';
import 'claude_setup_panel.dart';
import 'codex_setup_panel.dart';

// ── Value object ───────────────────────────────────────────────────────────────

/// Mutable value object bound to [LlmConfigForm].
class LlmConfigDraft {
  LlmConfigDraft({
    this.provider = 'gemini',
    this.model = '',
    this.reasoningEffort = '',
    this.apiKey = '',
    this.openaiBaseUrl = '',
    this.nvidiaBaseUrl = '',
    this.azureOpenaiEndpoint = '',
    this.azureOpenaiApiVersion = '2024-10-21',
    this.cliPath = '',
    this.extraArgs = '',
    this.ollamaBaseUrl = 'http://localhost:11434',
    this.ollamaKeepAlive = '',
    this.ollamaThink = '',
    this.bedrockRegion = 'us-east-1',
    this.bedrockReasoning = 'off',
    this.modelCacheFamily = '',
  });

  String provider;
  String model;
  String reasoningEffort;
  String apiKey;
  String openaiBaseUrl;
  String nvidiaBaseUrl;
  String azureOpenaiEndpoint;
  String azureOpenaiApiVersion;
  String cliPath;
  String extraArgs;
  String ollamaBaseUrl;
  String ollamaKeepAlive;
  String ollamaThink;
  String bedrockRegion;
  String bedrockReasoning;
  String modelCacheFamily;

  LlmConfigDraft copyWith({
    String? provider,
    String? model,
    String? reasoningEffort,
    String? apiKey,
    String? openaiBaseUrl,
    String? nvidiaBaseUrl,
    String? azureOpenaiEndpoint,
    String? azureOpenaiApiVersion,
    String? cliPath,
    String? extraArgs,
    String? ollamaBaseUrl,
    String? ollamaKeepAlive,
    String? ollamaThink,
    String? bedrockRegion,
    String? bedrockReasoning,
    String? modelCacheFamily,
  }) {
    return LlmConfigDraft(
      provider: provider ?? this.provider,
      model: model ?? this.model,
      reasoningEffort: reasoningEffort ?? this.reasoningEffort,
      apiKey: apiKey ?? this.apiKey,
      openaiBaseUrl: openaiBaseUrl ?? this.openaiBaseUrl,
      nvidiaBaseUrl: nvidiaBaseUrl ?? this.nvidiaBaseUrl,
      azureOpenaiEndpoint: azureOpenaiEndpoint ?? this.azureOpenaiEndpoint,
      azureOpenaiApiVersion: azureOpenaiApiVersion ?? this.azureOpenaiApiVersion,
      cliPath: cliPath ?? this.cliPath,
      extraArgs: extraArgs ?? this.extraArgs,
      ollamaBaseUrl: ollamaBaseUrl ?? this.ollamaBaseUrl,
      ollamaKeepAlive: ollamaKeepAlive ?? this.ollamaKeepAlive,
      ollamaThink: ollamaThink ?? this.ollamaThink,
      bedrockRegion: bedrockRegion ?? this.bedrockRegion,
      bedrockReasoning: bedrockReasoning ?? this.bedrockReasoning,
      modelCacheFamily: modelCacheFamily ?? this.modelCacheFamily,
    );
  }

  /// Build a create/update payload from this draft.
  Map<String, dynamic> toPayload() {
    final isCli = provider == 'claude-cli' || provider == 'codex-cli';
    final m = <String, dynamic>{
      'provider': provider,
      'model': model.trim(),
    };
    if (reasoningEffort.isNotEmpty) m['reasoning_effort'] = reasoningEffort;
    if (modelCacheFamily.isNotEmpty) m['model_cache_family'] = modelCacheFamily.trim().toLowerCase();
    if (isCli) {
      if (cliPath.isNotEmpty) m['cli_path'] = cliPath.trim();
      if (extraArgs.isNotEmpty) m['extra_args'] = extraArgs.trim();
    } else {
      if (apiKey.isNotEmpty) m['api_key'] = apiKey.trim();
      if (openaiBaseUrl.isNotEmpty) m['openai_base_url'] = openaiBaseUrl.trim();
      if (nvidiaBaseUrl.isNotEmpty) m['nvidia_base_url'] = nvidiaBaseUrl.trim();
      if (azureOpenaiEndpoint.isNotEmpty) m['azure_openai_endpoint'] = azureOpenaiEndpoint.trim();
      if (azureOpenaiApiVersion.isNotEmpty) m['azure_openai_api_version'] = azureOpenaiApiVersion.trim();
    }
    if (provider == 'ollama') {
      m['ollama_base_url'] = ollamaBaseUrl.isNotEmpty ? ollamaBaseUrl.trim() : 'http://localhost:11434';
      if (ollamaKeepAlive.isNotEmpty) m['ollama_keep_alive'] = ollamaKeepAlive.trim();
      if (ollamaThink.isNotEmpty) m['ollama_think'] = ollamaThink.trim();
    }
    if (provider == 'bedrock') {
      if (bedrockRegion.isNotEmpty) m['bedrock_region'] = bedrockRegion.trim();
      if (bedrockReasoning.isNotEmpty) m['bedrock_reasoning'] = bedrockReasoning.trim().toLowerCase();
    }
    return m;
  }

  /// Build the /llm/test payload.
  Map<String, dynamic> toTestPayload() {
    final base = toPayload();
    // /llm/test expects a flat body matching the model fields; toPayload covers it.
    return base;
  }
}

// ── Constants ──────────────────────────────────────────────────────────────────

const _kProviders = [
  ('gemini', 'Google Gemini'),
  ('deepseek', 'DeepSeek'),
  ('openai', 'OpenAI Compatible'),
  ('azure', 'Azure OpenAI'),
  ('nvidia', 'NVIDIA NIM'),
  ('ollama', 'Ollama (local/cloud)'),
  ('bedrock', 'AWS Bedrock'),
  ('claude-cli', 'Claude Code CLI'),
  ('codex-cli', 'OpenAI Codex CLI'),
];

const _kReasoningEffort = [
  ('', 'Default'),
  ('low', 'Low'),
  ('medium', 'Medium'),
  ('high', 'High'),
];

const _kNvidiaEffort = [
  ('', 'Default'),
  ('none', 'None (off)'),
  ('low', 'Low'),
  ('medium', 'Medium'),
  ('high', 'High'),
];

const _kClaudeCliEffort = [
  ('', 'Default'),
  ('low', 'Low'),
  ('medium', 'Medium'),
  ('high', 'High'),
];

const _kOllamaThink = [
  ('', 'Default'),
  ('off', 'Off'),
  ('on', 'On'),
  ('low', 'Low'),
  ('medium', 'Medium'),
  ('high', 'High'),
];

const _kBedrockReasoning = [
  ('off', 'Off'),
  ('low', 'Low'),
  ('medium', 'Medium'),
  ('high', 'High'),
];

const _kBedrockRegions = [
  'us-east-1', 'us-west-2', 'us-east-2',
  'eu-central-1', 'eu-west-1', 'eu-west-3',
  'ap-southeast-1', 'ap-southeast-2', 'ap-northeast-1',
];

// ── Widget ─────────────────────────────────────────────────────────────────────

/// Reusable LLM configuration form. The parent owns a [LlmConfigDraft] and
/// passes [onChanged] to receive updates.
///
/// Export: `LlmConfigForm`, `LlmConfigDraft`.
class LlmConfigForm extends ConsumerStatefulWidget {
  const LlmConfigForm({
    super.key,
    required this.draft,
    required this.onChanged,
    this.disabled = false,
    this.repo,
  });

  final LlmConfigDraft draft;
  final ValueChanged<LlmConfigDraft> onChanged;
  final bool disabled;

  /// Injected for Ollama/Bedrock pickers. Falls back to repo from provider.
  final ModelRepository? repo;

  @override
  ConsumerState<LlmConfigForm> createState() => _LlmConfigFormState();
}

class _LlmConfigFormState extends ConsumerState<LlmConfigForm> {
  // Ollama picker
  List<Map<String, dynamic>> _ollamaModels = [];
  bool _ollamaLoading = false;
  String _ollamaError = '';

  // Bedrock picker
  List<Map<String, dynamic>> _bedrockModels = [];
  bool _bedrockLoading = false;
  String _bedrockError = '';

  ModelRepository get _repo =>
      widget.repo ?? ref.read(modelRepositoryProvider);

  @override
  void initState() {
    super.initState();
    if (widget.draft.provider == 'ollama') _fetchOllama();
    if (widget.draft.provider == 'bedrock') _fetchBedrock();
  }

  @override
  void didUpdateWidget(LlmConfigForm old) {
    super.didUpdateWidget(old);
    final p = widget.draft.provider;
    final op = old.draft.provider;
    if (p == 'ollama' && (op != 'ollama' ||
        widget.draft.ollamaBaseUrl != old.draft.ollamaBaseUrl)) {
      _fetchOllama();
    }
    if (p == 'bedrock' && (op != 'bedrock' ||
        widget.draft.bedrockRegion != old.draft.bedrockRegion ||
        widget.draft.apiKey != old.draft.apiKey)) {
      _fetchBedrock();
    }
  }

  Future<void> _fetchOllama({bool force = false}) async {
    final baseUrl = widget.draft.ollamaBaseUrl;
    if (baseUrl.isEmpty) return;
    setState(() { _ollamaLoading = true; _ollamaError = ''; });
    try {
      final models = await _repo.ollamaModels({
        'base_url': baseUrl,
        if (widget.draft.apiKey.isNotEmpty) 'api_key': widget.draft.apiKey,
        if (force) 'force': true,
      });
      if (mounted) setState(() { _ollamaModels = models; _ollamaLoading = false; });
    } catch (e) {
      if (mounted) setState(() { _ollamaError = e.toString(); _ollamaLoading = false; });
    }
  }

  Future<void> _fetchBedrock({bool force = false}) async {
    final region = widget.draft.bedrockRegion;
    final key = widget.draft.apiKey;
    if (region.isEmpty || key.isEmpty) return;
    setState(() { _bedrockLoading = true; _bedrockError = ''; });
    try {
      final models = await _repo.bedrockModels({
        'region': region,
        'api_key': key,
        if (force) 'force': true,
      });
      if (mounted) setState(() { _bedrockModels = models; _bedrockLoading = false; });
    } catch (e) {
      if (mounted) setState(() { _bedrockError = e.toString(); _bedrockLoading = false; });
    }
  }

  void _update(LlmConfigDraft next) => widget.onChanged(next);

  void _onProviderChanged(String value) {
    final isCli = value == 'claude-cli' || value == 'codex-cli';
    var next = widget.draft.copyWith(provider: value);
    if (isCli) {
      next = next.copyWith(apiKey: '', openaiBaseUrl: '', nvidiaBaseUrl: '',
          azureOpenaiEndpoint: '', azureOpenaiApiVersion: '2024-10-21', reasoningEffort: '');
    } else {
      next = next.copyWith(cliPath: '', extraArgs: '');
    }
    if (value == 'ollama') {
      if (next.ollamaBaseUrl.isEmpty) next = next.copyWith(ollamaBaseUrl: 'http://localhost:11434');
      next = next.copyWith(reasoningEffort: '');
    } else {
      next = next.copyWith(ollamaBaseUrl: '', ollamaKeepAlive: '', ollamaThink: '');
    }
    if (value == 'bedrock') {
      if (next.bedrockRegion.isEmpty) next = next.copyWith(bedrockRegion: 'us-east-1');
      if (next.bedrockReasoning.isEmpty) next = next.copyWith(bedrockReasoning: 'off');
      next = next.copyWith(reasoningEffort: '');
    } else {
      next = next.copyWith(bedrockRegion: '', bedrockReasoning: '');
    }
    _update(next);
  }

  @override
  Widget build(BuildContext context) {
    final d = widget.draft;
    final disabled = widget.disabled;
    final isCliProvider = d.provider == 'claude-cli' || d.provider == 'codex-cli';
    final showReasoningEffort = ['azure', 'openai', 'nvidia', 'claude-cli', 'codex-cli'].contains(d.provider);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        // Provider
        _label('Provider'),
        _dropdown(
          value: d.provider,
          items: _kProviders.map((p) => DropdownMenuItem(value: p.$1, child: Text(p.$2))).toList(),
          onChanged: disabled ? null : (v) => _onProviderChanged(v as String),
        ),
        const SizedBox(height: 16),

        // Model
        _label(d.provider == 'azure' ? 'Deployment / Model Name' : 'Model'),
        _textField(
          value: d.model,
          placeholder: _modelPlaceholder(d.provider),
          mono: true,
          disabled: disabled,
          onChanged: (v) => _update(d.copyWith(model: v)),
        ),
        const SizedBox(height: 16),

        // Cache Family
        _label('Cache Family (optional)'),
        _textField(
          value: d.modelCacheFamily,
          placeholder: 'auto (e.g. gpt-oss-120b)',
          mono: true,
          disabled: disabled,
          onChanged: (v) => _update(d.copyWith(modelCacheFamily: v)),
        ),
        _hint('Share LLM cache across same-underlying-model rows. Leave blank to auto-detect.'),
        const SizedBox(height: 16),

        // CLI block
        if (isCliProvider) ...[
          _label('CLI Path'),
          _textField(
            value: d.cliPath,
            placeholder: d.provider == 'codex-cli' ? 'codex' : 'claude',
            mono: true,
            disabled: disabled,
            onChanged: (v) => _update(d.copyWith(cliPath: v)),
          ),
          const SizedBox(height: 12),
          _label('Extra Args'),
          _textField(
            value: d.extraArgs,
            placeholder: d.provider == 'codex-cli' ? '--sandbox read-only' : '--fallback-model claude-haiku-4-5',
            mono: true,
            disabled: disabled,
            onChanged: (v) => _update(d.copyWith(extraArgs: v)),
          ),
          const SizedBox(height: 12),
          if (d.provider == 'codex-cli')
            CodexCliSetupPanel(cliPath: d.cliPath.isNotEmpty ? d.cliPath : 'codex'),
          if (d.provider == 'claude-cli') ...[
            _infoBox('Uses the locally-installed claude binary on the server '
                '(subscription auth). Tools are disabled — CC is used as a '
                'text-only LLM. Use the panel below to re-authenticate when the '
                'token expires; no SSH required.', AppColors.info),
            ClaudeCliSetupPanel(cliPath: d.cliPath.isNotEmpty ? d.cliPath : 'claude'),
          ],
          const SizedBox(height: 4),
        ],

        // Ollama block
        if (d.provider == 'ollama') ...[
          _label('Ollama Base URL'),
          _textField(
            value: d.ollamaBaseUrl,
            placeholder: 'http://localhost:11434 or https://ollama.com/v1',
            mono: true,
            disabled: disabled,
            onChanged: (v) => _update(d.copyWith(ollamaBaseUrl: v)),
          ),
          const SizedBox(height: 12),
          _label('API Key (optional — local Ollama has no auth)'),
          _passwordField(
            value: d.apiKey,
            placeholder: 'Ollama Cloud Bearer token, or leave blank',
            disabled: disabled,
            onChanged: (v) => _update(d.copyWith(apiKey: v)),
          ),
          const SizedBox(height: 12),
          _pickerLabel('Pick from installed models', onRefresh: () => _fetchOllama(force: true),
              loading: _ollamaLoading),
          if (_ollamaError.isNotEmpty)
            _infoBox("Couldn't reach Ollama at this base URL: $_ollamaError", AppColors.warning),
          if (_ollamaModels.isNotEmpty && _ollamaError.isEmpty)
            _dropdown(
              value: _ollamaModels.any((m) => m['name'] == d.model) ? d.model : null,
              hint: 'Select a model',
              items: _ollamaModels.map((m) {
                final n = m['name'] as String? ?? '';
                final ps = m['parameter_size'] as String?;
                final q = m['quantization_level'] as String?;
                final label = [n, if (ps != null) ps, if (q != null) q].join(' · ');
                return DropdownMenuItem(value: n, child: Text(label, style: AppTextStyles.mono(11)));
              }).toList(),
              onChanged: disabled ? null : (v) => _update(d.copyWith(model: v ?? '')),
            ),
          const SizedBox(height: 12),
          _label('Thinking / Effort'),
          _dropdown(
            value: d.ollamaThink,
            items: _kOllamaThink.map((o) => DropdownMenuItem(value: o.$1, child: Text(o.$2))).toList(),
            onChanged: disabled ? null : (v) => _update(d.copyWith(ollamaThink: v ?? '')),
          ),
          const SizedBox(height: 8),
          _AdvancedSection(
            label: 'Keep Alive (Advanced)',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _label('Keep Alive'),
                _textField(
                  value: d.ollamaKeepAlive,
                  placeholder: '5m',
                  mono: true,
                  disabled: disabled,
                  onChanged: (v) => _update(d.copyWith(ollamaKeepAlive: v)),
                ),
                _hint('Go duration like 5m (default), 60m, 1h. -1 = never unload.'),
              ],
            ),
          ),
          const SizedBox(height: 4),
        ],

        // Bedrock block
        if (d.provider == 'bedrock') ...[
          _label('AWS Region'),
          _autocompleteOrText(
            value: d.bedrockRegion,
            suggestions: _kBedrockRegions,
            placeholder: 'us-east-1',
            disabled: disabled,
            onChanged: (v) => _update(d.copyWith(bedrockRegion: v)),
          ),
          const SizedBox(height: 12),
          _label('API Key (required — Bedrock bearer token)'),
          _passwordField(
            value: d.apiKey,
            placeholder: 'Bedrock API key (bearer token)',
            disabled: disabled,
            onChanged: (v) => _update(d.copyWith(apiKey: v)),
          ),
          const SizedBox(height: 12),
          _pickerLabel('Pick from available models', onRefresh: () => _fetchBedrock(force: true),
              loading: _bedrockLoading),
          if (_bedrockError.isNotEmpty)
            _infoBox("Couldn't list Bedrock models: $_bedrockError\n"
                "Enter the model id manually above (e.g. us.anthropic.claude-3-5-sonnet-20241022-v2:0).",
                AppColors.warning),
          if (_bedrockModels.isNotEmpty && _bedrockError.isEmpty)
            _dropdown(
              value: _bedrockModels.any((m) => m['id'] == d.model) ? d.model : null,
              hint: 'Select a model',
              items: _bedrockModels.map((m) {
                final id = m['id'] as String? ?? '';
                final kind = m['kind'] as String?;
                final pn = m['provider_name'] as String?;
                final label = [id, if (kind == 'inference_profile') 'profile', if (pn != null) pn].join(' · ');
                return DropdownMenuItem(value: id, child: Text(label, style: AppTextStyles.mono(11)));
              }).toList(),
              onChanged: disabled ? null : (v) => _update(d.copyWith(model: v ?? '')),
            ),
          const SizedBox(height: 12),
          _label('Reasoning'),
          _dropdown(
            value: d.bedrockReasoning,
            items: _kBedrockReasoning.map((o) => DropdownMenuItem(value: o.$1, child: Text(o.$2))).toList(),
            onChanged: disabled ? null : (v) => _update(d.copyWith(bedrockReasoning: v ?? 'off')),
          ),
          const SizedBox(height: 4),
        ],

        // Reasoning effort (azure / openai / nvidia / cli)
        if (showReasoningEffort) ...[
          const SizedBox(height: 4),
          _label('Reasoning Effort'),
          _dropdown(
            value: d.reasoningEffort,
            items: _effortOptions(d.provider).map((o) => DropdownMenuItem(value: o.$1, child: Text(o.$2))).toList(),
            onChanged: disabled ? null : (v) => _update(d.copyWith(reasoningEffort: v ?? '')),
          ),
          const SizedBox(height: 4),
        ],

        // Standard API key for non-CLI, non-Ollama, non-Bedrock
        if (!isCliProvider && d.provider != 'ollama' && d.provider != 'bedrock') ...[
          const SizedBox(height: 4),
          _label(d.provider == 'azure' ? 'Azure API Key' : 'API Key'),
          _passwordField(
            value: d.apiKey,
            placeholder: d.provider == 'nvidia' ? 'NVIDIA API Key (nvapi-...)' : 'Optional if provided by environment',
            disabled: disabled,
            onChanged: (v) => _update(d.copyWith(apiKey: v)),
          ),
          const SizedBox(height: 4),
        ],

        // OpenAI base URL
        if (d.provider == 'openai') ...[
          const SizedBox(height: 4),
          _label('OpenAI Base URL'),
          _textField(
            value: d.openaiBaseUrl,
            placeholder: 'Optional custom base URL',
            mono: true,
            disabled: disabled,
            onChanged: (v) => _update(d.copyWith(openaiBaseUrl: v)),
          ),
        ],

        // NVIDIA base URL
        if (d.provider == 'nvidia') ...[
          const SizedBox(height: 4),
          _label('NVIDIA NIM Base URL'),
          _textField(
            value: d.nvidiaBaseUrl,
            placeholder: 'https://integrate.api.nvidia.com/v1',
            mono: true,
            disabled: disabled,
            onChanged: (v) => _update(d.copyWith(nvidiaBaseUrl: v)),
          ),
        ],

        // Azure
        if (d.provider == 'azure') ...[
          const SizedBox(height: 4),
          _label('Azure Endpoint'),
          _textField(
            value: d.azureOpenaiEndpoint,
            placeholder: 'https://your-resource.services.ai.azure.com',
            mono: true,
            disabled: disabled,
            onChanged: (v) => _update(d.copyWith(azureOpenaiEndpoint: v)),
          ),
          const SizedBox(height: 12),
          _label('API Version'),
          _textField(
            value: d.azureOpenaiApiVersion,
            placeholder: '2024-10-21',
            mono: true,
            disabled: disabled,
            onChanged: (v) => _update(d.copyWith(azureOpenaiApiVersion: v)),
          ),
          const SizedBox(height: 8),
          _infoBox('Use the Azure resource root plus your deployment/model name. '
              'Do not use a full /models/chat/completions or /openai/v1/ URL here.', AppColors.info),
        ],
      ],
    );
  }

  // ── Helper builders ───────────────────────────────────────────────────────────

  List<(String, String)> _effortOptions(String provider) {
    if (provider == 'nvidia') return _kNvidiaEffort;
    if (provider == 'claude-cli') return _kClaudeCliEffort;
    return _kReasoningEffort;
  }

  String _modelPlaceholder(String provider) {
    switch (provider) {
      case 'azure': return 'e.g. gpt-5.2 deployment name';
      case 'claude-cli': return 'claude-sonnet-4-6';
      case 'codex-cli': return 'gpt-5-codex';
      default: return 'e.g. gemini-3-flash-preview';
    }
  }

  Widget _label(String text) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Text(text, style: AppTextStyles.meta.copyWith(color: AppColors.textMuted, fontWeight: FontWeight.w500)),
    );
  }

  Widget _hint(String text) {
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Text(text, style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
    );
  }

  Widget _textField({
    required String value,
    required String placeholder,
    required ValueChanged<String> onChanged,
    bool mono = false,
    bool disabled = false,
  }) {
    return TextFormField(
      initialValue: value,
      onChanged: disabled ? null : onChanged,
      readOnly: disabled,
      style: mono
          ? AppTextStyles.mono(14, color: AppColors.textHi)
          : AppTextStyles.body.copyWith(color: AppColors.textHi),
      decoration: _inputDec(placeholder),
    );
  }

  Widget _passwordField({
    required String value,
    required String placeholder,
    required ValueChanged<String> onChanged,
    bool disabled = false,
  }) {
    return TextFormField(
      initialValue: value,
      onChanged: disabled ? null : onChanged,
      readOnly: disabled,
      obscureText: true,
      style: AppTextStyles.mono(14, color: AppColors.textHi),
      decoration: _inputDec(placeholder),
    );
  }

  InputDecoration _inputDec(String hint) {
    return InputDecoration(
      hintText: hint,
      hintStyle: AppTextStyles.body.copyWith(color: AppColors.textFaint),
      filled: true,
      fillColor: AppColors.surface,
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(8),
        borderSide: BorderSide(color: AppColors.border),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(8),
        borderSide: BorderSide(color: AppColors.border),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(8),
        borderSide: BorderSide(color: AppColors.primary),
      ),
    );
  }

  Widget _dropdown({
    required String? value,
    required List<DropdownMenuItem<String>> items,
    required ValueChanged<String?>? onChanged,
    String? hint,
  }) {
    return DropdownButtonFormField<String>(
      initialValue: value,
      items: items,
      onChanged: onChanged,
      hint: hint != null ? Text(hint, style: AppTextStyles.body.copyWith(color: AppColors.textFaint)) : null,
      dropdownColor: AppColors.panel,
      style: AppTextStyles.body.copyWith(color: AppColors.textHi),
      decoration: InputDecoration(
        filled: true,
        fillColor: AppColors.surface,
        contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: AppColors.border)),
        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: AppColors.border)),
        focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: AppColors.primary)),
      ),
    );
  }

  Widget _autocompleteOrText({
    required String value,
    required List<String> suggestions,
    required String placeholder,
    required ValueChanged<String> onChanged,
    bool disabled = false,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _textField(value: value, placeholder: placeholder, mono: true, disabled: disabled, onChanged: onChanged),
        const SizedBox(height: 4),
        Wrap(
          spacing: 6, runSpacing: 4,
          children: suggestions.map((s) => GestureDetector(
            onTap: disabled ? null : () => onChanged(s),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              decoration: BoxDecoration(
                color: AppColors.fill(AppColors.primary),
                borderRadius: BorderRadius.circular(4),
                border: Border.all(color: AppColors.stroke(AppColors.primary)),
              ),
              child: Text(s, style: AppTextStyles.mono(10, color: AppColors.primary)),
            ),
          )).toList(),
        ),
      ],
    );
  }

  Widget _pickerLabel(String text, {required VoidCallback onRefresh, required bool loading}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(text, style: AppTextStyles.meta.copyWith(color: AppColors.textMuted, fontWeight: FontWeight.w500)),
          GestureDetector(
            onTap: loading ? null : onRefresh,
            child: Text(
              loading ? 'Loading...' : 'Refresh',
              style: AppTextStyles.micro.copyWith(color: AppColors.primary),
            ),
          ),
        ],
      ),
    );
  }

  Widget _infoBox(String text, Color color) {
    return Container(
      margin: const EdgeInsets.only(top: 4, bottom: 4),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.fill(color),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.stroke(color)),
      ),
      child: Text(text, style: AppTextStyles.nano.copyWith(color: color)),
    );
  }
}

// ── Collapsible section ────────────────────────────────────────────────────────

class _AdvancedSection extends StatefulWidget {
  const _AdvancedSection({required this.label, required this.child});
  final String label;
  final Widget child;

  @override
  State<_AdvancedSection> createState() => _AdvancedSectionState();
}

class _AdvancedSectionState extends State<_AdvancedSection> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        GestureDetector(
          onTap: () => setState(() => _open = !_open),
          child: Row(children: [
            Icon(_open ? Icons.keyboard_arrow_up : Icons.keyboard_arrow_down,
                size: 16, color: AppColors.textDim),
            const SizedBox(width: 4),
            Text(widget.label, style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
          ]),
        ),
        if (_open) ...[const SizedBox(height: 8), widget.child],
      ],
    );
  }
}
