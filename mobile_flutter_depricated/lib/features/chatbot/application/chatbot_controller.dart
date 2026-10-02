import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/chatbot_repository.dart';
import '../data/models/chat.dart';
import '../../../core/network/session.dart';

// ── State ─────────────────────────────────────────────────────────────────────

class ChatbotState {
  const ChatbotState({
    this.isOpen = false,
    this.isFullscreen = false,
    this.conversations = const [],
    this.activeConversation,
    this.busy = false,
    this.error,
    this.conversationListOpen = false,
    this.models = const [],
    this.modelsLoaded = false,
    this.toolCatalog = const [],
    this.lastModelId,
    this.bootstrapped = false,
  });

  /// Whether the chatbot panel is expanded (false = FAB only).
  final bool isOpen;

  /// Mobile: fullscreen means the panel takes the whole viewport.
  final bool isFullscreen;

  final List<Conversation> conversations;

  /// The currently loaded conversation (with full messages).
  final Conversation? activeConversation;

  /// True while a network call is in flight (turn / confirmTool / load).
  final bool busy;

  final String? error;

  final bool conversationListOpen;

  final List<ChatModel> models;
  final bool modelsLoaded;

  final List<Map<String, dynamic>> toolCatalog;

  /// Last-used model id — remembered across conversations.
  final String? lastModelId;

  /// True after the first bootstrap() completes (avoids duplicate bootstraps).
  final bool bootstrapped;

  // Derived ──────────────────────────────────────────────────────────────────

  List<ChatMessage> get messages =>
      activeConversation?.messages ?? const [];

  /// True when the active conversation has no model set (first-run picker).
  bool get needsModel =>
      activeConversation == null ||
      (activeConversation!.modelId == null ||
          activeConversation!.modelId!.isEmpty);

  ChatMessage? get pendingConfirmationMessage {
    for (var i = messages.length - 1; i >= 0; i--) {
      if (messages[i].isPendingConfirmation) return messages[i];
    }
    return null;
  }

  ChatbotState copyWith({
    bool? isOpen,
    bool? isFullscreen,
    List<Conversation>? conversations,
    Conversation? activeConversation,
    bool clearActiveConversation = false,
    bool? busy,
    String? error,
    bool clearError = false,
    bool? conversationListOpen,
    List<ChatModel>? models,
    bool? modelsLoaded,
    List<Map<String, dynamic>>? toolCatalog,
    String? lastModelId,
    bool? bootstrapped,
  }) =>
      ChatbotState(
        isOpen: isOpen ?? this.isOpen,
        isFullscreen: isFullscreen ?? this.isFullscreen,
        conversations: conversations ?? this.conversations,
        activeConversation: clearActiveConversation
            ? null
            : (activeConversation ?? this.activeConversation),
        busy: busy ?? this.busy,
        error: clearError ? null : (error ?? this.error),
        conversationListOpen:
            conversationListOpen ?? this.conversationListOpen,
        models: models ?? this.models,
        modelsLoaded: modelsLoaded ?? this.modelsLoaded,
        toolCatalog: toolCatalog ?? this.toolCatalog,
        lastModelId: lastModelId ?? this.lastModelId,
        bootstrapped: bootstrapped ?? this.bootstrapped,
      );
}

// ── Notifier ──────────────────────────────────────────────────────────────────

class ChatbotNotifier extends Notifier<ChatbotState> {
  @override
  ChatbotState build() {
    // Re-bootstrap whenever the JWT changes (login / logout).
    ref.listen<SessionStore>(
      sessionProvider,
      (prev, next) {
        final wasAuth = prev?.isAuthenticated ?? false;
        final isAuth = next.isAuthenticated;
        if (isAuth && !wasAuth) {
          // Logged in: bootstrap fresh.
          _bootstrap();
        } else if (!isAuth && wasAuth) {
          // Logged out: reset to blank state.
          state = const ChatbotState();
        }
      },
    );

    // Kick bootstrap immediately if already authenticated.
    final session = ref.read(sessionProvider);
    if (session.isAuthenticated) {
      Future.microtask(_bootstrap);
    }

    return const ChatbotState();
  }

  ChatbotRepository get _repo => ref.read(chatbotRepositoryProvider);

  // ── Bootstrap ─────────────────────────────────────────────────────────────

  Future<void> _bootstrap() async {
    if (state.bootstrapped) return;
    state = state.copyWith(bootstrapped: true);
    await _refreshConversations();
    // Reload the last active conversation if one was remembered.
    if (state.activeConversation == null && state.conversations.isNotEmpty) {
      try {
        await _loadConversation(state.conversations.first.id);
      } catch (_) {}
    }
    _loadToolCatalog();
  }

  // ── Conversations ─────────────────────────────────────────────────────────

  Future<void> _refreshConversations() async {
    try {
      final list = await _repo.conversations();
      state = state.copyWith(conversations: list);
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> _loadConversation(String id) async {
    try {
      final convo = await _repo.conversation(id);
      state = state.copyWith(
        activeConversation: convo,
        lastModelId:
            convo.modelId?.isNotEmpty == true ? convo.modelId : state.lastModelId,
      );
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  // ── Public API ────────────────────────────────────────────────────────────

  void open() => state = state.copyWith(isOpen: true);
  void close() => state = state.copyWith(isOpen: false, isFullscreen: false);
  void toggleFullscreen() =>
      state = state.copyWith(isFullscreen: !state.isFullscreen);
  void minimise() => state = state.copyWith(isOpen: false);
  void clearError() => state = state.copyWith(clearError: true);

  void toggleConversationList() => state = state.copyWith(
        conversationListOpen: !state.conversationListOpen,
      );

  /// Switches to the given conversation (loads it from the API).
  Future<void> selectConversation(String id) async {
    state = state.copyWith(conversationListOpen: false);
    await _loadConversation(id);
  }

  Future<void> refresh() => _refreshConversations();

  Future<Conversation> startNewConversation({
    String? modelId,
    String? title,
  }) async {
    state = state.copyWith(clearError: true);
    final useModel = modelId ?? state.lastModelId;
    final convo =
        await _repo.createConversation(modelId: useModel, title: title);
    state = state.copyWith(
      activeConversation: convo,
      lastModelId: convo.modelId ?? state.lastModelId,
    );
    await _refreshConversations();
    return convo;
  }

  Future<void> setModel(String modelId) async {
    final convo = state.activeConversation;
    if (convo == null) {
      await startNewConversation(modelId: modelId);
      return;
    }
    try {
      final updated = await _repo.patchConversation(
        convo.id,
        {'model_id': modelId},
      );
      state = state.copyWith(
        activeConversation: updated,
        lastModelId: modelId,
      );
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> setAutoConfirmSafe({required bool value}) async {
    final convo = state.activeConversation;
    if (convo == null) return;
    try {
      final updated = await _repo.patchConversation(
        convo.id,
        {'auto_confirm_safe_tools': value},
      );
      state = state.copyWith(activeConversation: updated);
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> clearConversation() async {
    final convo = state.activeConversation;
    if (convo == null) return;
    state = state.copyWith(busy: true, clearError: true);
    try {
      final updated = await _repo.clear(convo.id);
      state = state.copyWith(
        activeConversation: updated,
        busy: false,
      );
      await _refreshConversations();
    } catch (e) {
      state = state.copyWith(busy: false, error: e.toString());
    }
  }

  Future<void> deleteConversation() async {
    final convo = state.activeConversation;
    if (convo == null) return;
    state = state.copyWith(busy: true, clearError: true);
    try {
      await _repo.deleteConversation(convo.id);
      state = state.copyWith(
        clearActiveConversation: true,
        busy: false,
      );
      await _refreshConversations();
      // Auto-open first remaining conversation.
      if (state.conversations.isNotEmpty) {
        await _loadConversation(state.conversations.first.id);
      }
    } catch (e) {
      state = state.copyWith(busy: false, error: e.toString());
    }
  }

  // ── Messaging ─────────────────────────────────────────────────────────────

  Future<void> send(String text) async {
    final trimmed = text.trim();
    if (trimmed.isEmpty) return;

    var convo = state.activeConversation;
    convo ??= await startNewConversation();

    // Optimistic append so the user sees their message immediately.
    final tempId = 'temp-${DateTime.now().millisecondsSinceEpoch}';
    final optimistic = ChatMessage(
      id: tempId,
      role: 'user',
      content: trimmed,
      createdAt: DateTime.now(),
      status: 'sending',
    );
    final optimisticConvo = convo.copyWith(
      messages: [...convo.messages, optimistic],
    );
    state = state.copyWith(
      activeConversation: optimisticConvo,
      busy: true,
      clearError: true,
    );

    try {
      final newMessages = await _repo.turn(convo.id, trimmed);

      // Replace optimistic message with server messages, then refresh.
      final base = (state.activeConversation?.messages ?? const [])
          .where((m) => m.id != tempId)
          .toList();
      final merged = [...base, ...newMessages];
      state = state.copyWith(
        activeConversation: (state.activeConversation ?? convo)
            .copyWith(messages: merged),
        busy: false,
      );

      // Refresh to pick up server-assigned title + updated message count.
      try {
        final fresh = await _repo.conversation(convo.id);
        state = state.copyWith(activeConversation: fresh);
        await _refreshConversations();
      } catch (_) {}
    } catch (e) {
      // Mark the optimistic message as failed.
      final msgs = (state.activeConversation?.messages ?? const []).map((m) {
        if (m.id == tempId) {
          return ChatMessage(
            id: m.id,
            role: m.role,
            content: m.content,
            createdAt: m.createdAt,
            status: 'failed',
            toolCalls: m.toolCalls,
            pendingTool: m.pendingTool,
            blocks: m.blocks,
          );
        }
        return m;
      }).toList();
      state = state.copyWith(
        activeConversation:
            (state.activeConversation ?? convo).copyWith(messages: msgs),
        busy: false,
        error: e.toString(),
      );
    }
  }

  Future<void> confirmTool(String messageId, {required bool approved}) async {
    final convo = state.activeConversation;
    if (convo == null) return;
    state = state.copyWith(busy: true, clearError: true);
    try {
      await _repo.confirmTool(convo.id, messageId, approved);
      final fresh = await _repo.conversation(convo.id);
      state = state.copyWith(activeConversation: fresh, busy: false);
    } catch (e) {
      state = state.copyWith(busy: false, error: e.toString());
    }
  }

  // ── Model picker ──────────────────────────────────────────────────────────

  Future<void> loadModels() async {
    if (state.modelsLoaded) return;
    try {
      final list = await _repo.models();
      state = state.copyWith(models: list, modelsLoaded: true);
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  // ── Tool catalog ──────────────────────────────────────────────────────────

  Future<void> _loadToolCatalog() async {
    if (state.toolCatalog.isNotEmpty) return;
    try {
      final tools = await _repo.tools();
      state = state.copyWith(toolCatalog: tools);
    } catch (_) {
      // Not fatal — tool catalog is informational.
    }
  }
}

final chatbotProvider = NotifierProvider<ChatbotNotifier, ChatbotState>(
  ChatbotNotifier.new,
);
