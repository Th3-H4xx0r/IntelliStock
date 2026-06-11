/// Plain Dart models for the chatbot feature.
/// No freezed / json_serializable — hand-written fromJson, nullable-tolerant.
library;

// ── ToolCall ─────────────────────────────────────────────────────────────────

class ToolCall {
  const ToolCall({
    required this.id,
    required this.name,
    this.arguments = const {},
    this.description,
    this.safety = 'write',
  });

  final String id;
  final String name;
  final Map<String, dynamic> arguments;
  final String? description;

  /// 'safe' | 'write' | 'destructive'
  final String safety;

  factory ToolCall.fromJson(Map<String, dynamic> j) => ToolCall(
        id: (j['id'] ?? j['tool_call_id'] ?? '').toString(),
        name: (j['name'] ?? j['function'] ?? '').toString(),
        arguments: _asMap(j['arguments'] ?? j['input'] ?? {}),
        description: j['description']?.toString(),
        safety: (j['safety'] ?? 'write').toString(),
      );
}

// ── ChatMessage ───────────────────────────────────────────────────────────────

/// A single message in a conversation.
///
/// [status] is normally `null` or `'sent'`.  When the backend is waiting for
/// the operator to approve a tool call it emits `'pending_confirmation'`.
/// The presentation layer uses this flag to swap in the [ChatToolCallCard]
/// instead of a normal bubble.
class ChatMessage {
  const ChatMessage({
    required this.id,
    required this.role,
    this.content,
    this.createdAt,
    this.status,
    this.toolCalls = const [],
    this.pendingTool,
    this.blocks = const [],
    this.name,
  });

  final String id;

  /// 'user' | 'assistant' | 'tool'
  final String role;

  final String? content;
  final DateTime? createdAt;

  /// 'pending_confirmation' | 'sending' | 'failed' | null
  final String? status;

  /// Tool-call chips shown on an assistant message that triggered tools.
  final List<ToolCall> toolCalls;

  /// Populated when [status] == 'pending_confirmation'.
  final ToolCall? pendingTool;

  /// Rich rendered blocks (markdown / table / chart / navigate / stat).
  final List<Map<String, dynamic>> blocks;

  /// Tool-role message source name.
  final String? name;

  bool get isPendingConfirmation => status == 'pending_confirmation';

  factory ChatMessage.fromJson(Map<String, dynamic> j) {
    final tcRaw = j['tool_calls'];
    final List<ToolCall> toolCalls = tcRaw is List
        ? tcRaw
            .whereType<Map<String, dynamic>>()
            .map(ToolCall.fromJson)
            .toList()
        : const [];

    // pending_tool is either nested under 'pending_tool' key or is the first
    // tool_call when status == 'pending_confirmation'.
    final ptRaw = j['pending_tool'];
    ToolCall? pendingTool;
    if (ptRaw is Map<String, dynamic>) {
      pendingTool = ToolCall.fromJson(ptRaw);
    } else if ((j['status'] ?? '').toString() == 'pending_confirmation' &&
        toolCalls.isNotEmpty) {
      pendingTool = toolCalls.first;
    }

    final blocksRaw = j['blocks'];
    final List<Map<String, dynamic>> blocks = blocksRaw is List
        ? blocksRaw.whereType<Map<String, dynamic>>().toList()
        : const [];

    return ChatMessage(
      id: (j['id'] ?? '').toString(),
      role: (j['role'] ?? 'assistant').toString(),
      content: j['content']?.toString(),
      createdAt: _parseDate(j['created_at']),
      status: j['status']?.toString(),
      toolCalls: toolCalls,
      pendingTool: pendingTool,
      blocks: blocks,
      name: j['name']?.toString(),
    );
  }
}

// ── Conversation ──────────────────────────────────────────────────────────────

class Conversation {
  const Conversation({
    required this.id,
    this.title,
    this.modelId,
    this.modelName,
    this.autoConfirmSafeTools = false,
    this.messages = const [],
    this.messageCount = 0,
  });

  final String id;
  final String? title;

  /// Claude model id (e.g. `claude-opus-4-5`).
  final String? modelId;

  /// Display name of the model (may be absent on list endpoints).
  final String? modelName;

  final bool autoConfirmSafeTools;
  final List<ChatMessage> messages;
  final int messageCount;

  Conversation copyWith({
    String? title,
    String? modelId,
    String? modelName,
    bool? autoConfirmSafeTools,
    List<ChatMessage>? messages,
    int? messageCount,
  }) =>
      Conversation(
        id: id,
        title: title ?? this.title,
        modelId: modelId ?? this.modelId,
        modelName: modelName ?? this.modelName,
        autoConfirmSafeTools:
            autoConfirmSafeTools ?? this.autoConfirmSafeTools,
        messages: messages ?? this.messages,
        messageCount: messageCount ?? this.messageCount,
      );

  factory Conversation.fromJson(Map<String, dynamic> j) {
    final msgsRaw = j['messages'];
    final List<ChatMessage> messages = msgsRaw is List
        ? msgsRaw
            .whereType<Map<String, dynamic>>()
            .map(ChatMessage.fromJson)
            .toList()
        : const [];

    // The backend stores this under `settings.auto_confirm_safe_tools`; some
    // responses may also carry it at the top level. Check both.
    final settings = j['settings'];
    final autoConfirm = j['auto_confirm_safe_tools'] is bool
        ? j['auto_confirm_safe_tools'] as bool
        : (settings is Map && settings['auto_confirm_safe_tools'] is bool
            ? settings['auto_confirm_safe_tools'] as bool
            : false);

    return Conversation(
      id: (j['id'] ?? '').toString(),
      title: j['title']?.toString(),
      modelId: j['model_id']?.toString(),
      modelName: j['model_name']?.toString(),
      autoConfirmSafeTools: autoConfirm,
      messages: messages,
      messageCount: (j['message_count'] as num?)?.toInt() ?? messages.length,
    );
  }
}

// ── Model (for the model picker) ─────────────────────────────────────────────

class ChatModel {
  const ChatModel({
    required this.id,
    required this.name,
    this.provider = '',
    this.model = '',
  });

  final String id;
  final String name;
  final String provider;
  final String model;

  factory ChatModel.fromJson(Map<String, dynamic> j) => ChatModel(
        id: (j['id'] ?? '').toString(),
        name: (j['name'] ?? j['id'] ?? '').toString(),
        provider: (j['provider'] ?? '').toString(),
        model: (j['model'] ?? '').toString(),
      );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

DateTime? _parseDate(dynamic v) {
  if (v == null) return null;
  try {
    return DateTime.parse(v.toString());
  } catch (_) {
    return null;
  }
}

Map<String, dynamic> _asMap(dynamic v) {
  if (v is Map<String, dynamic>) return v;
  if (v is Map) return v.cast<String, dynamic>();
  return const {};
}
