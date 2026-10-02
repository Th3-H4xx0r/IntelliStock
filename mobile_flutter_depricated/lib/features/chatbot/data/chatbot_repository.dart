import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';
import 'models/chat.dart';

/// HTTP wrapper for all `/chatbot/*` and `/models` endpoints.
///
/// Every method returns a decoded plain-Dart model; callers never touch raw
/// JSON.  Uses the same [ApiClient] pattern as other repositories.
class ChatbotRepository {
  const ChatbotRepository(this._client);

  final ApiClient _client;

  // ── Conversations ────────────────────────────────────────────────────────

  /// GET /chatbot/conversations → `{conversations: [...]}`
  Future<List<Conversation>> conversations() async {
    final data =
        await _client.get<Map<String, dynamic>>('/chatbot/conversations');
    final raw = data['conversations'];
    if (raw is! List) return const [];
    return raw
        .whereType<Map<String, dynamic>>()
        .map(Conversation.fromJson)
        .toList();
  }

  /// POST /chatbot/conversations  body: `{model_id?, title?}`
  Future<Conversation> createConversation({
    String? modelId,
    String? title,
  }) async {
    final body = <String, dynamic>{};
    if (modelId != null) body['model_id'] = modelId;
    if (title != null) body['title'] = title;
    final data = await _client.post<Map<String, dynamic>>(
      '/chatbot/conversations',
      body: body,
    );
    return Conversation.fromJson(data);
  }

  /// GET /chatbot/conversations/:id
  Future<Conversation> conversation(String id) async {
    final data = await _client
        .get<Map<String, dynamic>>('/chatbot/conversations/$id');
    return Conversation.fromJson(data);
  }

  /// PATCH /chatbot/conversations/:id  body: `{model_id?}` or `{auto_confirm_safe_tools}`
  Future<Conversation> patchConversation(
    String id,
    Map<String, dynamic> body,
  ) async {
    final data = await _client.patch<Map<String, dynamic>>(
      '/chatbot/conversations/$id',
      body: body,
    );
    return Conversation.fromJson(data);
  }

  /// DELETE /chatbot/conversations/:id
  Future<void> deleteConversation(String id) =>
      _client.delete<dynamic>('/chatbot/conversations/$id');

  /// POST /chatbot/conversations/:id/clear
  Future<Conversation> clear(String id) async {
    final data = await _client.post<Map<String, dynamic>>(
      '/chatbot/conversations/$id/clear',
    );
    return Conversation.fromJson(data);
  }

  // ── Messaging ────────────────────────────────────────────────────────────

  /// POST /chatbot/conversations/:id/turn  body: `{content}`
  ///
  /// Synchronous (no streaming) — returns `{messages: [...]}` once the full
  /// turn is complete (user echo + assistant reply + optional tool messages).
  Future<List<ChatMessage>> turn(String id, String content) async {
    final data = await _client.post<Map<String, dynamic>>(
      '/chatbot/conversations/$id/turn',
      body: {'content': content},
    );
    final raw = data['messages'];
    if (raw is! List) return const [];
    return raw
        .whereType<Map<String, dynamic>>()
        .map(ChatMessage.fromJson)
        .toList();
  }

  /// POST /chatbot/conversations/:id/confirm-tool
  /// body: `{message_id, approved}`
  Future<Map<String, dynamic>> confirmTool(
    String conversationId,
    String messageId,
    bool approved,
  ) =>
      _client.post<Map<String, dynamic>>(
        '/chatbot/conversations/$conversationId/confirm-tool',
        body: {'message_id': messageId, 'approved': approved},
      );

  // ── Catalog ──────────────────────────────────────────────────────────────

  /// GET /chatbot/tools → `{tools: [...]}`
  Future<List<Map<String, dynamic>>> tools() async {
    final data =
        await _client.get<Map<String, dynamic>>('/chatbot/tools');
    final raw = data['tools'];
    if (raw is! List) return const [];
    return raw.whereType<Map<String, dynamic>>().toList();
  }

  // ── Model picker ─────────────────────────────────────────────────────────

  /// GET /models — reused for the model picker.
  Future<List<ChatModel>> models() async {
    final data = await _client.get<dynamic>('/models');
    List<dynamic>? raw;
    if (data is Map<String, dynamic>) {
      raw = (data['models'] ?? data['data']) as List<dynamic>?;
    } else if (data is List) {
      raw = data;
    }
    if (raw == null) return const [];
    return raw
        .whereType<Map<String, dynamic>>()
        .map(ChatModel.fromJson)
        .toList();
  }
}

final chatbotRepositoryProvider = Provider<ChatbotRepository>(
  (ref) => ChatbotRepository(ref.watch(apiClientProvider)),
);
