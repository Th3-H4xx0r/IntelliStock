import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/network/session.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/confirm_dialog.dart';
import '../application/chatbot_controller.dart';
import '../data/models/chat.dart';
import 'chat_composer.dart';
import 'chat_message.dart';
import 'chat_model_picker.dart';
import 'chat_tool_call.dart';

/// Global chatbot overlay widget.
///
/// Mount this as the `floatingActionButton` in `AppShell` (replacing
/// `ChatbotFabSlot`).  It renders nothing when the user is not authenticated.
///
/// Collapsed state: a violet pulsing FAB (smart_toy icon).
/// Expanded state:  a near-fullscreen bottom-sheet-style panel containing:
///   - [_ChatHeader] (title, model name, clear, minimise)
///   - optional conversation list drawer
///   - body: first-run model picker / empty state / message list
///   - [ChatComposer]
class ChatbotDock extends ConsumerStatefulWidget {
  const ChatbotDock({super.key});

  @override
  ConsumerState<ChatbotDock> createState() => _ChatbotDockState();
}

class _ChatbotDockState extends ConsumerState<ChatbotDock>
    with TickerProviderStateMixin {
  final _scrollCtrl = ScrollController();
  final _navigateHandled = <String>{};

  // Glow pulse animation for the FAB
  late final AnimationController _glowAnim;
  late final Animation<double> _glowOpacity;

  @override
  void initState() {
    super.initState();
    _glowAnim = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 2400),
    )..repeat(reverse: true);
    _glowOpacity =
        Tween<double>(begin: 0.0, end: 1.0).animate(_glowAnim);
  }

  @override
  void dispose() {
    _glowAnim.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollCtrl.hasClients) {
        _scrollCtrl.animateTo(
          _scrollCtrl.position.maxScrollExtent,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        );
      }
    });
  }

  /// Parse and fire navigate directives from assistant messages.
  void _handleNavigates(List<ChatMessage> messages) {
    for (final msg in messages) {
      for (final b in msg.blocks) {
        final type = b['type']?.toString() ?? '';
        final route = b['route']?.toString() ?? '';
        if (type == 'navigate' && route.isNotEmpty) {
          final key = '${msg.id}:$route';
          if (!_navigateHandled.contains(key)) {
            _navigateHandled.add(key);
            WidgetsBinding.instance.addPostFrameCallback((_) {
              try {
                context.go(route);
              } catch (_) {}
            });
          }
        }
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(sessionProvider);
    if (!session.isAuthenticated) return const SizedBox.shrink();

    final st = ref.watch(chatbotProvider);

    // Handle navigate directives whenever messages change.
    _handleNavigates(st.messages);

    // Auto-scroll when messages grow.
    if (st.isOpen && st.messages.isNotEmpty) {
      _scrollToBottom();
    }

    return Stack(
      children: [
        // Collapsed FAB
        AnimatedSwitcher(
          duration: const Duration(milliseconds: 200),
          child: st.isOpen
              ? const SizedBox.shrink()
              : _CollapsedFab(
                  glowOpacity: _glowOpacity,
                  onTap: () =>
                      ref.read(chatbotProvider.notifier).open(),
                ),
        ),

        // Expanded panel
        if (st.isOpen) _ExpandedPanel(scrollCtrl: _scrollCtrl),
      ],
    );
  }
}

// ── Collapsed FAB ─────────────────────────────────────────────────────────────

class _CollapsedFab extends StatelessWidget {
  const _CollapsedFab({
    required this.glowOpacity,
    required this.onTap,
  });

  final Animation<double> glowOpacity;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Positioned(
      right: 16,
      bottom: 16,
      child: AnimatedBuilder(
        animation: glowOpacity,
        builder: (context, child) => GestureDetector(
          onTap: onTap,
          child: Container(
            width: 56,
            height: 56,
            decoration: BoxDecoration(
              color: AppColors.primary,
              shape: BoxShape.circle,
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.4),
                  blurRadius: 16,
                  offset: const Offset(0, 6),
                ),
                BoxShadow(
                  color: AppColors.primary.withValues(
                      alpha: 0.55 * glowOpacity.value),
                  blurRadius: 24,
                  spreadRadius: 0,
                ),
              ],
            ),
            child: Icon(
              Icons.smart_toy,
              color: AppColors.onPrimary,
              size: 26,
            ),
          ),
        ),
      ),
    );
  }
}

// ── Expanded panel ────────────────────────────────────────────────────────────

class _ExpandedPanel extends ConsumerWidget {
  const _ExpandedPanel({required this.scrollCtrl});
  final ScrollController scrollCtrl;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final st = ref.watch(chatbotProvider);
    final notifier = ref.read(chatbotProvider.notifier);

    return Positioned(
      left: 0,
      right: 0,
      top: MediaQuery.of(context).padding.top + 8,
      bottom: 0,
      child: Material(
        color: Colors.transparent,
        child: Container(
          margin: const EdgeInsets.fromLTRB(8, 0, 8, 8),
          decoration: BoxDecoration(
            color: const Color(0xFF0a0716).withValues(alpha: 0.97),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: AppColors.border),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.5),
                blurRadius: 40,
                offset: const Offset(0, 8),
              ),
            ],
          ),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(20),
            child: Column(
              children: [
                // Header
                _ChatHeader(
                  title: st.activeConversation?.title ?? 'Assistant',
                  modelName:
                      st.activeConversation?.modelName ?? '',
                  onClear: () async {
                    final ok = await showConfirmDialog(
                      context,
                      title: 'Clear conversation',
                      body:
                          'This will delete all messages. This cannot be undone.',
                      confirmLabel: 'Clear',
                      confirmColor: AppColors.danger,
                      icon: Icons.delete_sweep,
                    );
                    if (ok) notifier.clearConversation();
                  },
                  onMinimise: () => notifier.minimise(),
                ),

                // Conversation switcher (shown when >1 conversation)
                if (st.conversations.length > 1)
                  _ConversationBar(st: st, notifier: notifier),

                // Body (flex fills remaining space)
                Expanded(
                  child: _Body(
                    st: st,
                    notifier: notifier,
                    scrollCtrl: scrollCtrl,
                  ),
                ),

                // Composer
                ChatComposer(
                  busy: st.busy,
                  disabled: st.needsModel,
                  placeholder: st.needsModel
                      ? 'Pick a model first…'
                      : 'Ask me anything…',
                  onSend: (text) async {
                    await notifier.send(text);
                  },
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ── Header ────────────────────────────────────────────────────────────────────

class _ChatHeader extends StatelessWidget {
  const _ChatHeader({
    required this.title,
    required this.modelName,
    required this.onClear,
    required this.onMinimise,
  });

  final String title;
  final String modelName;
  final VoidCallback onClear;
  final VoidCallback onMinimise;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 12, 8, 12),
      decoration: BoxDecoration(
        color: const Color(0xFF0a0f13).withValues(alpha: 0.85),
        border: Border(bottom: BorderSide(color: AppColors.border)),
      ),
      child: Row(
        children: [
          // Bot icon badge
          Container(
            width: 28,
            height: 28,
            decoration: BoxDecoration(
              color: AppColors.primary.withValues(alpha: 0.15),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(
                  color: AppColors.primary.withValues(alpha: 0.3)),
            ),
            child: Icon(Icons.smart_toy,
                color: AppColors.primary, size: 16),
          ),
          const SizedBox(width: 8),

          // Title + model
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: AppTextStyles.bodyHi
                      .copyWith(color: AppColors.textHi),
                  overflow: TextOverflow.ellipsis,
                ),
                if (modelName.isNotEmpty)
                  Text(
                    modelName.toUpperCase(),
                    style: AppTextStyles.nano.copyWith(
                      color: AppColors.textDim,
                      letterSpacing: 1.2,
                      fontFamily: 'monospace',
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
              ],
            ),
          ),

          // Clear
          _HeaderBtn(
            icon: Icons.delete_sweep,
            tooltip: 'Clear conversation',
            onTap: onClear,
            hoverColor: AppColors.danger,
          ),

          // Minimise
          _HeaderBtn(
            icon: Icons.expand_more,
            tooltip: 'Minimise',
            onTap: onMinimise,
          ),
        ],
      ),
    );
  }
}

class _HeaderBtn extends StatelessWidget {
  const _HeaderBtn({
    required this.icon,
    required this.onTap,
    this.tooltip,
    this.hoverColor,
  });
  final IconData icon;
  final VoidCallback onTap;
  final String? tooltip;
  final Color? hoverColor;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip ?? '',
      child: InkWell(
        borderRadius: BorderRadius.circular(8),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(6),
          child: Icon(icon, color: AppColors.textMuted, size: 20),
        ),
      ),
    );
  }
}

// ── Conversation switcher bar ─────────────────────────────────────────────────

class _ConversationBar extends ConsumerWidget {
  const _ConversationBar({required this.st, required this.notifier});
  final ChatbotState st;
  final ChatbotNotifier notifier;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          padding:
              const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          decoration: BoxDecoration(
            border: Border(
                bottom: BorderSide(color: AppColors.border)),
          ),
          child: Row(
            children: [
              GestureDetector(
                onTap: () => notifier.toggleConversationList(),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      st.conversationListOpen
                          ? Icons.expand_less
                          : Icons.history,
                      size: 14,
                      color: AppColors.textDim,
                    ),
                    const SizedBox(width: 4),
                    Text(
                      '${st.conversations.length} conversations',
                      style: AppTextStyles.micro
                          .copyWith(color: AppColors.textDim),
                    ),
                  ],
                ),
              ),
              const Spacer(),
              GestureDetector(
                onTap: () => notifier.startNewConversation(),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.add, size: 14, color: AppColors.primary),
                    const SizedBox(width: 2),
                    Text(
                      'New',
                      style: AppTextStyles.micro
                          .copyWith(color: AppColors.primary),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        if (st.conversationListOpen)
          ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 160),
            child: ListView.builder(
              shrinkWrap: true,
              padding:
                  const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              itemCount: st.conversations.length,
              itemBuilder: (context, i) {
                final c = st.conversations[i];
                final active = c.id == st.activeConversation?.id;
                return _ConvTile(
                  conv: c,
                  active: active,
                  onTap: () => notifier.selectConversation(c.id),
                );
              },
            ),
          ),
      ],
    );
  }
}

class _ConvTile extends StatelessWidget {
  const _ConvTile({
    required this.conv,
    required this.active,
    required this.onTap,
  });
  final Conversation conv;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      borderRadius: BorderRadius.circular(8),
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        decoration: active
            ? BoxDecoration(
                color: AppColors.primary.withValues(alpha: 0.1),
                borderRadius: BorderRadius.circular(8),
              )
            : null,
        child: Row(
          children: [
            Icon(Icons.chat_bubble_outline,
                size: 14,
                color:
                    active ? AppColors.primary : AppColors.textDim),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                (conv.title?.isNotEmpty == true)
                    ? conv.title!
                    : 'Untitled',
                style: AppTextStyles.micro.copyWith(
                  color: active
                      ? AppColors.primary
                      : AppColors.textMd,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            Text(
              '${conv.messageCount} msg',
              style: AppTextStyles.nano
                  .copyWith(color: AppColors.textFaint),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Body ──────────────────────────────────────────────────────────────────────

class _Body extends ConsumerWidget {
  const _Body({
    required this.st,
    required this.notifier,
    required this.scrollCtrl,
  });

  final ChatbotState st;
  final ChatbotNotifier notifier;
  final ScrollController scrollCtrl;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // First-run model picker
    if (st.needsModel) {
      return ChatModelPicker(
        onConfirm: (model) async {
          await notifier.setModel(model.id);
        },
      );
    }

    // Empty state with suggestion pills
    if (st.messages.isEmpty) {
      return _EmptyBody(onSuggestion: (s) async {
        await notifier.send(s);
      });
    }

    // Message list
    return _MessageList(
      st: st,
      notifier: notifier,
      scrollCtrl: scrollCtrl,
    );
  }
}

class _EmptyBody extends StatelessWidget {
  const _EmptyBody({required this.onSuggestion});
  final ValueChanged<String> onSuggestion;

  static const _suggestions = [
    'List my instances',
    'Show my portfolio over the last month',
    'How many backtests have I run?',
  ];

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 48,
              height: 48,
              decoration: BoxDecoration(
                color: AppColors.primary.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(14),
                border: Border.all(
                    color: AppColors.primary.withValues(alpha: 0.3)),
              ),
              child:
                  Icon(Icons.smart_toy, color: AppColors.primary, size: 24),
            ),
            const SizedBox(height: 12),
            Text(
              'How can I help?',
              style: AppTextStyles.h3.copyWith(color: AppColors.textHi),
            ),
            const SizedBox(height: 6),
            Text(
              'Ask about your portfolio, run a backtest, link a brokerage, '
              'or just say hi. I can show charts, tables, and run actions on '
              'your behalf with your approval.',
              style: AppTextStyles.body.copyWith(color: AppColors.textDim),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              alignment: WrapAlignment.center,
              children: [
                for (final s in _suggestions)
                  _SuggestionPill(text: s, onTap: () => onSuggestion(s)),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _SuggestionPill extends StatelessWidget {
  const _SuggestionPill({required this.text, required this.onTap});
  final String text;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding:
            const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(999),
          border: Border.all(color: AppColors.border),
        ),
        child: Text(
          text,
          style: AppTextStyles.micro.copyWith(color: AppColors.textMd),
        ),
      ),
    );
  }
}

class _MessageList extends ConsumerWidget {
  const _MessageList({
    required this.st,
    required this.notifier,
    required this.scrollCtrl,
  });

  final ChatbotState st;
  final ChatbotNotifier notifier;
  final ScrollController scrollCtrl;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return ListView.builder(
      controller: scrollCtrl,
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 8),
      itemCount: st.messages.length +
          (st.busy ? 1 : 0) +
          (st.error != null ? 1 : 0),
      itemBuilder: (context, index) {
        // Thinking indicator
        if (index == st.messages.length && st.busy) {
          return const _ThinkingIndicator();
        }
        // Error banner
        if (index == st.messages.length + (st.busy ? 1 : 0) &&
            st.error != null) {
          return Padding(
            padding: const EdgeInsets.only(top: 8),
            child: ErrorBanner(
              message: st.error!,
              onRetry: () => notifier.clearError(),
            ),
          );
        }

        final msg = st.messages[index];

        // Pending tool-call card
        if (msg.isPendingConfirmation) {
          return Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: ChatToolCallCard(
              message: msg,
              busy: st.busy,
              onApprove: () =>
                  notifier.confirmTool(msg.id, approved: true),
              onDecline: () =>
                  notifier.confirmTool(msg.id, approved: false),
            ),
          );
        }

        return Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: ChatMessageBubble(message: msg),
        );
      },
    );
  }
}

// ── Thinking indicator ────────────────────────────────────────────────────────

class _ThinkingIndicator extends StatefulWidget {
  const _ThinkingIndicator();

  @override
  State<_ThinkingIndicator> createState() => _ThinkingIndicatorState();
}

class _ThinkingIndicatorState extends State<_ThinkingIndicator>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1100),
    )..repeat();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 4),
      child: Row(
        children: [
          for (var i = 0; i < 3; i++) _Dot(anim: _ctrl, delay: i * 0.15),
          const SizedBox(width: 8),
          Text(
            'Thinking…',
            style: AppTextStyles.micro.copyWith(color: AppColors.textDim),
          ),
        ],
      ),
    );
  }
}

class _Dot extends StatelessWidget {
  const _Dot({required this.anim, required this.delay});
  final AnimationController anim;
  final double delay;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: anim,
      builder: (_, _) {
        final t = ((anim.value - delay) % 1.0).clamp(0.0, 1.0);
        final scale = 0.55 + 0.45 * (1 - (2 * t - 1).abs());
        final opacity = 0.4 + 0.6 * (1 - (2 * t - 1).abs());
        return Opacity(
          opacity: opacity,
          child: Transform.scale(
            scale: scale,
            child: Container(
              width: 6,
              height: 6,
              margin: const EdgeInsets.only(right: 3),
              decoration: BoxDecoration(
                color: const Color(0xFFc98fff),
                shape: BoxShape.circle,
              ),
            ),
          ),
        );
      },
    );
  }
}
