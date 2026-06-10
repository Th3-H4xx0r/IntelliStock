import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';

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
  final String? modelCacheFamily;
  final double? inputCostPer1m;
  final double? outputCostPer1m;
  final double? cacheCreationCostPer1m;
  final double? cacheReadCostPer1m;
  final String? createdAt;

  factory LlmModel.fromJson(Map<String, dynamic> j) {
    return LlmModel(
      id: (j['id'] as String?) ?? '',
      name: (j['name'] as String?) ?? '',
      provider: (j['provider'] as String?) ?? '',
      model: (j['model'] as String?) ?? '',
      reasoningEffort: j['reasoning_effort'] as String?,
      apiKey: j['api_key'] as String?,
      cliPath: j['cli_path'] as String?,
      extraArgs: j['extra_args'] as String?,
      openaiBaseUrl: j['openai_base_url'] as String?,
      nvidiaBaseUrl: j['nvidia_base_url'] as String?,
      azureOpenaiEndpoint: j['azure_openai_endpoint'] as String?,
      azureOpenaiApiVersion: j['azure_openai_api_version'] as String?,
      ollamaBaseUrl: j['ollama_base_url'] as String?,
      ollamaKeepAlive: j['ollama_keep_alive'] as String?,
      ollamaThink: j['ollama_think'] as String?,
      bedrockRegion: j['bedrock_region'] as String?,
      bedrockReasoning: j['bedrock_reasoning'] as String?,
      modelCacheFamily: j['model_cache_family'] as String?,
      inputCostPer1m: (j['input_cost_per_1m'] as num?)?.toDouble(),
      outputCostPer1m: (j['output_cost_per_1m'] as num?)?.toDouble(),
      cacheCreationCostPer1m: (j['cache_creation_cost_per_1m'] as num?)?.toDouble(),
      cacheReadCostPer1m: (j['cache_read_cost_per_1m'] as num?)?.toDouble(),
      createdAt: j['created_at'] as String?,
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
      provider: j['provider'] as String?,
      model: j['model'] as String?,
      effectiveModel: j['effective_model'] as String?,
      latencyMs: (j['latency_ms'] as num?)?.toInt(),
      result: j['result'],
      providerMeta: j['provider_meta'],
      smokePrompt: j['smoke_prompt'] as String?,
      smokeResponse: j['smoke_response'] as String?,
      smokeThinking: j['smoke_thinking'] as String?,
      smokeContentChars: (j['smoke_content_chars'] as num?)?.toInt(),
      smokeThinkingChars: (j['smoke_thinking_chars'] as num?)?.toInt(),
      smokeLatencyMs: (j['smoke_latency_ms'] as num?)?.toInt(),
      smokeError: j['smoke_error'] as String?,
      message: j['message'] as String?,
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
      version: j['version'] as String?,
      authenticated: (j['authenticated'] as bool?) ?? false,
      authMessage: j['auth_message'] as String?,
      installMethod: (j['install_method'] as String?) ?? 'unknown',
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
  Future<LlmModel> create(Map<String, dynamic> body) async {
    final data = await _client.post<Map<String, dynamic>>('/models', body: body);
    return LlmModel.fromJson(data);
  }

  /// PUT /models/:id
  Future<LlmModel> update(String id, Map<String, dynamic> body) async {
    final data = await _client.put<Map<String, dynamic>>('/models/$id', body: body);
    return LlmModel.fromJson(data);
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
}

final modelRepositoryProvider = Provider<ModelRepository>(
  (ref) => ModelRepository(ref.watch(apiClientProvider)),
);
