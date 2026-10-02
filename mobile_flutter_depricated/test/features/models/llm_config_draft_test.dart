import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/models/presentation/llm_config_form.dart';

void main() {
  group('LlmConfigDraft.toPayload', () {
    test('gemini provider sends api_key, not CLI fields', () {
      final d = LlmConfigDraft(
        provider: 'gemini',
        model: 'gemini-3-flash',
        apiKey: 'test-key',
        openaiBaseUrl: 'should-be-excluded-for-gemini',
      );
      final p = d.toPayload();
      expect(p['provider'], 'gemini');
      expect(p['model'], 'gemini-3-flash');
      expect(p['api_key'], 'test-key');
      expect(p.containsKey('cli_path'), isFalse);
      expect(p.containsKey('extra_args'), isFalse);
    });

    test('claude-cli sends CLI fields, no api_key', () {
      final d = LlmConfigDraft(
        provider: 'claude-cli',
        model: 'claude-sonnet-4-6',
        cliPath: '/usr/local/bin/claude',
        extraArgs: '--effort high',
        apiKey: 'should-be-ignored',
      );
      final p = d.toPayload();
      expect(p['provider'], 'claude-cli');
      expect(p['cli_path'], '/usr/local/bin/claude');
      expect(p['extra_args'], '--effort high');
      expect(p.containsKey('api_key'), isFalse);
    });

    test('codex-cli sends CLI fields, no api_key', () {
      final d = LlmConfigDraft(
        provider: 'codex-cli',
        model: 'gpt-5-codex',
        cliPath: 'codex',
        apiKey: 'ignored',
      );
      final p = d.toPayload();
      expect(p['provider'], 'codex-cli');
      expect(p['cli_path'], 'codex');
      expect(p.containsKey('api_key'), isFalse);
    });

    test('ollama sends ollama fields', () {
      final d = LlmConfigDraft(
        provider: 'ollama',
        model: 'llama3',
        ollamaBaseUrl: 'http://localhost:11434',
        ollamaThink: 'on',
        ollamaKeepAlive: '10m',
      );
      final p = d.toPayload();
      expect(p['provider'], 'ollama');
      expect(p['ollama_base_url'], 'http://localhost:11434');
      expect(p['ollama_think'], 'on');
      expect(p['ollama_keep_alive'], '10m');
    });

    test('ollama falls back to default base URL when empty', () {
      final d = LlmConfigDraft(
        provider: 'ollama',
        model: 'llama3',
        ollamaBaseUrl: '',
      );
      final p = d.toPayload();
      expect(p['ollama_base_url'], 'http://localhost:11434');
    });

    test('bedrock sends region and reasoning', () {
      final d = LlmConfigDraft(
        provider: 'bedrock',
        model: 'us.anthropic.claude-3-5-sonnet-20241022-v2:0',
        bedrockRegion: 'us-west-2',
        bedrockReasoning: 'high',
        apiKey: 'bearer-token',
      );
      final p = d.toPayload();
      expect(p['provider'], 'bedrock');
      expect(p['bedrock_region'], 'us-west-2');
      expect(p['bedrock_reasoning'], 'high');
      expect(p['api_key'], 'bearer-token');
    });

    test('openrouter sends base_url and attribution headers', () {
      final d = LlmConfigDraft(
        provider: 'openrouter',
        model: 'anthropic/claude-3.5-sonnet',
        openrouterBaseUrl: 'https://openrouter.ai/api/v1',
        openrouterReferer: 'https://intellistock.app',
        openrouterTitle: 'IntelliStock',
        apiKey: 'sk-or-key',
        reasoningEffort: 'high',
      );
      final p = d.toPayload();
      expect(p['provider'], 'openrouter');
      expect(p['openrouter_base_url'], 'https://openrouter.ai/api/v1');
      expect(p['openrouter_referer'], 'https://intellistock.app');
      expect(p['openrouter_title'], 'IntelliStock');
      expect(p['api_key'], 'sk-or-key');
      expect(p['reasoning_effort'], 'high');
    });

    test('openrouter falls back to default base URL and omits empty headers', () {
      final d = LlmConfigDraft(
        provider: 'openrouter',
        model: 'openai/gpt-4o-mini',
        openrouterBaseUrl: '',
      );
      final p = d.toPayload();
      expect(p['openrouter_base_url'], 'https://openrouter.ai/api/v1');
      expect(p.containsKey('openrouter_referer'), isFalse);
      expect(p.containsKey('openrouter_title'), isFalse);
    });

    test('openrouter copyWith round-trips the new fields', () {
      final d = LlmConfigDraft(provider: 'openrouter', model: 'x/y');
      final d2 = d.copyWith(openrouterReferer: 'https://a', openrouterTitle: 'T');
      expect(d2.openrouterReferer, 'https://a');
      expect(d2.openrouterTitle, 'T');
      expect(d2.openrouterBaseUrl, 'https://openrouter.ai/api/v1');
    });

    test('azure sends endpoint and api_version', () {
      final d = LlmConfigDraft(
        provider: 'azure',
        model: 'gpt-5-deployment',
        azureOpenaiEndpoint: 'https://my-resource.services.ai.azure.com',
        azureOpenaiApiVersion: '2024-10-21',
        apiKey: 'azure-key',
        reasoningEffort: 'medium',
      );
      final p = d.toPayload();
      expect(p['azure_openai_endpoint'], 'https://my-resource.services.ai.azure.com');
      expect(p['azure_openai_api_version'], '2024-10-21');
      expect(p['api_key'], 'azure-key');
      expect(p['reasoning_effort'], 'medium');
    });

    test('empty optional fields are excluded from payload', () {
      final d = LlmConfigDraft(
        provider: 'openai',
        model: 'gpt-4o',
        reasoningEffort: '',
        modelCacheFamily: '',
        openaiBaseUrl: '',
      );
      final p = d.toPayload();
      expect(p.containsKey('reasoning_effort'), isFalse);
      expect(p.containsKey('model_cache_family'), isFalse);
      expect(p.containsKey('openai_base_url'), isFalse);
    });

    test('modelCacheFamily is lowercased in payload', () {
      final d = LlmConfigDraft(
        provider: 'gemini',
        model: 'gemini-pro',
        modelCacheFamily: 'GPT-OSS-120B',
      );
      final p = d.toPayload();
      expect(p['model_cache_family'], 'gpt-oss-120b');
    });

    test('reasoning_effort is included when set', () {
      final d = LlmConfigDraft(
        provider: 'openai',
        model: 'o4-mini',
        reasoningEffort: 'high',
      );
      final p = d.toPayload();
      expect(p['reasoning_effort'], 'high');
    });

    test('copyWith preserves unchanged fields', () {
      final d = LlmConfigDraft(provider: 'gemini', model: 'gemini-pro', apiKey: 'k');
      final d2 = d.copyWith(model: 'gemini-flash');
      expect(d2.provider, 'gemini');
      expect(d2.model, 'gemini-flash');
      expect(d2.apiKey, 'k');
    });
  });
}
