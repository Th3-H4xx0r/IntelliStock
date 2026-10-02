import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';

/// Tolerant string coercion — the LLM-test endpoint sometimes returns object
/// values (e.g. structured `smoke_response`/`message`) where a string is
/// expected; stringify instead of crashing on an `as String?` cast.
String? _asStr(dynamic v) {
  if (v == null) return null;
  if (v is String) return v;
  if (v is Map || v is List) return jsonEncode(v);
  return v.toString();
}

// ── Data model ─────────────────────────────────────────────────────────────────

class LlmModel {
  const LlmModel({
    required this.id,
    required this.name,
    required this.provider,
    required this.model,
    this.reasoningEffort,
    this.apiKey,
    this.cliPath,
    this.extraArgs,
    this.openaiBaseUrl,
    this.nvidiaBaseUrl,
    this.azureOpenaiEndpoint,
    this.azureOpenaiApiVersion,
    this.ollamaBaseUrl,
    this.ollamaKeepAlive,
    this.ollamaThink,
    this.bedrockRegion,
    this.bedrockReasoning,
    this.openrouterBaseUrl,
    this.openrouterReferer,
    this.openrouterTitle,
    this.modelCacheFamily,
    this.inputCostPer1m,
    this.outputCostPer1m,
    this.cacheCreationCostPer1m,
    this.cacheReadCostPer1m,
    this.createdAt,
  });

  final String id;
  final String name;
  final String provider;
  final String model;
  final String? reasoningEffort;
  final String? apiKey; // masked on read
  final String? cliPath;
  final String? extraArgs;
  final String? openaiBaseUrl;
  final String? nvidiaBaseUrl;
  final String? azureOpenaiEndpoint;
  final String? azureOpenaiApiVersion;
  final String? ollamaBaseUrl;
  final String? ollamaKeepAlive;
  final String? ollamaThink;
  final String? bedrockRegion;
  final String? bedrockReasoning;
  final String? openrouterBaseUrl;
  final String? openrouterReferer;
  final String? openrouterTitle;
  final String? modelCacheFamily;
  final double? inputCostPer1m;
  final double? outputCostPer1m;
  final double? cacheCreationCostPer1m;
  final double? cacheReadCostPer1m;
  final String? createdAt;

  factory LlmModel.fromJson(Map<String, dynamic> j) {
    return LlmModel(
      id: _asStr(j['id']) ?? '',
      name: _asStr(j['name']) ?? '',
      provider: _asStr(j['provider']) ?? '',
      model: _asStr(j['model']) ?? '',
      reasoningEffort: _asStr(j['reasoning_effort']),
      apiKey: _asStr(j['api_key']),
      cliPath: _asStr(j['cli_path']),
      extraArgs: _asStr(j['extra_args']),
      openaiBaseUrl: _asStr(j['openai_base_url']),
      nvidiaBaseUrl: _asStr(j['nvidia_base_url']),
      azureOpenaiEndpoint: _asStr(j['azure_openai_endpoint']),
      azureOpenaiApiVersion: _asStr(j['azure_openai_api_version']),
      ollamaBaseUrl: _asStr(j['ollama_base_url']),
      ollamaKeepAlive: _asStr(j['ollama_keep_alive']),
      ollamaThink: _asStr(j['ollama_think']),
      bedrockRegion: _asStr(j['bedrock_region']),
      bedrockReasoning: _asStr(j['bedrock_reasoning']),
      openrouterBaseUrl: _asStr(j['openrouter_base_url']),
      openrouterReferer: _asStr(j['openrouter_referer']),
      openrouterTitle: _asStr(j['openrouter_title']),
      modelCacheFamily: _asStr(j['model_cache_family']),
      inputCostPer1m: (j['input_cost_per_1m'] as num?)?.toDouble(),
      outputCostPer1m: (j['output_cost_per_1m'] as num?)?.toDouble(),
      cacheCreationCostPer1m: (j['cache_creation_cost_per_1m'] as num?)?.toDouble(),
      cacheReadCostPer1m: (j['cache_read_cost_per_1m'] as num?)?.toDouble(),
      createdAt: _asStr(j['created_at']),
    );
  }
}

/// One entry from `GET /claude/models`. The `requires_credits` flag marks
/// variants (e.g. the `[1m]` context models) that fail unless the Claude
/// subscription has usage credits enabled.
class ClaudeModelOption {
  const ClaudeModelOption({
    required this.value,
    required this.label,
    this.description,
    this.requiresCredits = false,
  });

  final String value;
  final String label;
  final String? description;
  final bool requiresCredits;

  factory ClaudeModelOption.fromJson(Map<String, dynamic> j) {
    final value = _asStr(j['value']) ?? '';
    final label = _asStr(j['label']);
    return ClaudeModelOption(
      value: value,
      label: (label != null && label.isNotEmpty) ? label : value,
      description: _asStr(j['description']),
      requiresCredits: (j['requires_credits'] as bool?) ?? false,
    );
  }
}

class LlmTestResult {
  const LlmTestResult({
    this.provider,
    this.model,
    this.effectiveModel,
    this.latencyMs,
    this.result,
    this.providerMeta,
    this.smokePrompt,
    this.smokeResponse,
    this.smokeThinking,
    this.smokeContentChars,
    this.smokeThinkingChars,
    this.smokeLatencyMs,
    this.smokeError,
    this.message,
  });

  final String? provider;
  final String? model;
  final String? effectiveModel;
  final int? latencyMs;
  final dynamic result;
  final dynamic providerMeta;
  final String? smokePrompt;
  final String? smokeResponse;
  final String? smokeThinking;
  final int? smokeContentChars;
  final int? smokeThinkingChars;
  final int? smokeLatencyMs;
  final String? smokeError;
  final String? message;

  factory LlmTestResult.fromJson(Map<String, dynamic> j) {
    return LlmTestResult(
      provider: _asStr(j['provider']),
      model: _asStr(j['model']),
      effectiveModel: _asStr(j['effective_model']),
      latencyMs: (j['latency_ms'] as num?)?.toInt(),
      result: j['result'],
      providerMeta: j['provider_meta'],
      smokePrompt: _asStr(j['smoke_prompt']),
      smokeResponse: _asStr(j['smoke_response']),
      smokeThinking: _asStr(j['smoke_thinking']),
      smokeContentChars: (j['smoke_content_chars'] as num?)?.toInt(),
      smokeThinkingChars: (j['smoke_thinking_chars'] as num?)?.toInt(),
      smokeLatencyMs: (j['smoke_latency_ms'] as num?)?.toInt(),
      smokeError: _asStr(j['smoke_error']),
      message: _asStr(j['message']),
    );
  }
}

class CodexStatus {
  const CodexStatus({
    this.installed = false,
    this.version,
    this.authenticated = false,
    this.authMessage,
    this.installMethod = 'unknown',
  });

  final bool installed;
  final String? version;
  final bool authenticated;
  final String? authMessage;
  final String installMethod;

  factory CodexStatus.fromJson(Map<String, dynamic> j) {
    return CodexStatus(
      installed: (j['installed'] as bool?) ?? false,
      version: _asStr(j['version']),
      authenticated: (j['authenticated'] as bool?) ?? false,
      authMessage: _asStr(j['auth_message']),
      installMethod: _asStr(j['install_method']) ?? 'unknown',
    );
  }
}

// ── Repository ─────────────────────────────────────────────────────────────────

class ModelRepository {
  const ModelRepository(this._client);

  final ApiClient _client;

  /// GET /models — returns `{models: [...]}` or bare `[...]`.
  Future<List<LlmModel>> list() async {
    final raw = await _client.get<dynamic>('/models');
    List<dynamic> items;
    if (raw is List) {
      items = raw;
    } else if (raw is Map<String, dynamic>) {
      final v = raw['models'];
      items = v is List ? v : [];
    } else {
      items = [];
    }
    return items
        .whereType<Map<String, dynamic>>()
        .map(LlmModel.fromJson)
        .toList();
  }

  /// POST /models
  ///
  /// The backend wraps the created doc as `{"created": true, "model": {…}}`,
  /// so unwrap `model` before parsing (fall back to the bare body for
  /// forward-compat). Without this the returned [LlmModel.id] is empty.
  Future<LlmModel> create(Map<String, dynamic> body) async {
    final data = await _client.post<Map<String, dynamic>>('/models', body: body);
    return LlmModel.fromJson(_unwrapModel(data));
  }

  /// PUT /models/:id
  ///
  /// Returns `{"updated": true, "model": {…}}` — unwrap as in [create].
  Future<LlmModel> update(String id, Map<String, dynamic> body) async {
    final data = await _client.put<Map<String, dynamic>>('/models/$id', body: body);
    return LlmModel.fromJson(_unwrapModel(data));
  }

  /// Pull the inner `model` doc out of a `{created/updated, model}` envelope.
  static Map<String, dynamic> _unwrapModel(Map<String, dynamic> data) {
    final inner = data['model'];
    if (inner is Map) return inner.cast<String, dynamic>();
    return data;
  }

  /// DELETE /models/:id?force=true
  Future<void> delete(String id) =>
      _client.delete<dynamic>('/models/$id', query: {'force': 'true'});

  /// POST /models/:id/test-cli
  Future<Map<String, dynamic>> testCli(String id) =>
      _client.post<Map<String, dynamic>>('/models/$id/test-cli');

  /// POST /llm/test
  Future<LlmTestResult> testLlm(Map<String, dynamic> body) async {
    final data = await _client.post<Map<String, dynamic>>('/llm/test', body: body);
    return LlmTestResult.fromJson(data);
  }

  /// POST /ollama/list-models
  Future<List<Map<String, dynamic>>> ollamaModels(Map<String, dynamic> body) async {
    final raw = await _client.post<dynamic>('/ollama/list-models', body: body);
    if (raw is List) return raw.whereType<Map<String, dynamic>>().toList();
    if (raw is Map<String, dynamic>) {
      final v = raw['models'];
      if (v is List) return v.whereType<Map<String, dynamic>>().toList();
    }
    return [];
  }

  /// POST /bedrock/list-models
  Future<List<Map<String, dynamic>>> bedrockModels(Map<String, dynamic> body) async {
    final raw = await _client.post<dynamic>('/bedrock/list-models', body: body);
    if (raw is List) return raw.whereType<Map<String, dynamic>>().toList();
    if (raw is Map<String, dynamic>) {
      final v = raw['models'];
      if (v is List) return v.whereType<Map<String, dynamic>>().toList();
    }
    return [];
  }

  // ── Codex ────────────────────────────────────────────────────────────────────

  /// GET /codex/status
  Future<CodexStatus> codexStatus() async {
    final data = await _client.get<Map<String, dynamic>>('/codex/status');
    return CodexStatus.fromJson(data);
  }

  /// POST /codex/install
  Future<Map<String, dynamic>> codexInstall() =>
      _client.post<Map<String, dynamic>>('/codex/install', body: {});

  /// GET /codex/install/:jobId
  Future<Map<String, dynamic>> codexInstallJob(String jobId) =>
      _client.get<Map<String, dynamic>>('/codex/install/$jobId');

  /// POST /codex/login/start
  Future<Map<String, dynamic>> codexLoginStart(Map<String, dynamic> body) =>
      _client.post<Map<String, dynamic>>('/codex/login/start', body: body);

  /// GET /codex/login/:jobId/status
  Future<Map<String, dynamic>> codexLoginStatus(String jobId) =>
      _client.get<Map<String, dynamic>>('/codex/login/$jobId/status');

  /// POST /codex/login/:jobId/cancel
  Future<void> codexLoginCancel(String jobId) =>
      _client.post<dynamic>('/codex/login/$jobId/cancel');

  /// POST /codex/logout
  Future<void> codexLogout() =>
      _client.post<dynamic>('/codex/logout');

  // ── Claude ───────────────────────────────────────────────────────────────────

  /// GET /claude/models — returns `{models: [...], error?: String}`.
  ///
  /// The model list is dynamic (depends on the server's claude binary), so the
  /// form always fetches it rather than hardcoding. Pass [cliPath] to probe a
  /// specific binary.
  Future<Map<String, dynamic>> claudeModels({String? cliPath}) =>
      _client.get<Map<String, dynamic>>(
        '/claude/models',
        query: (cliPath != null && cliPath.isNotEmpty) ? {'cli_path': cliPath} : null,
      );

  /// GET /claude/auth/status
  Future<Map<String, dynamic>> claudeAuthStatus() =>
      _client.get<Map<String, dynamic>>('/claude/auth/status');

  /// POST /claude/login/start
  Future<Map<String, dynamic>> claudeLoginStart(Map<String, dynamic> body) =>
      _client.post<Map<String, dynamic>>('/claude/login/start', body: body);

  /// POST /claude/login/:jobId/submit
  Future<Map<String, dynamic>> claudeLoginSubmit(String jobId, String code) =>
      _client.post<Map<String, dynamic>>('/claude/login/$jobId/submit', body: {'code': code});

  /// GET /claude/login/:jobId/status
  Future<Map<String, dynamic>> claudeLoginStatus(String jobId) =>
      _client.get<Map<String, dynamic>>('/claude/login/$jobId/status');

  /// POST /claude/login/:jobId/cancel
  Future<void> claudeLoginCancel(String jobId) =>
      _client.post<dynamic>('/claude/login/$jobId/cancel');

  /// POST /claude/logout
  Future<void> claudeLogout() =>
      _client.post<dynamic>('/claude/logout');
}

final modelRepositoryProvider = Provider<ModelRepository>(
  (ref) => ModelRepository(ref.watch(apiClientProvider)),
);
