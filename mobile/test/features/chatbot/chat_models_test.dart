import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/chatbot/data/models/chat.dart';

void main() {
  group('ChatMessage.fromJson', () {
    test('parses basic user message', () {
      final msg = ChatMessage.fromJson({
        'id': 'abc',
        'role': 'user',
        'content': 'Hello',
        'created_at': '2026-06-01T12:00:00Z',
        'status': null,
      });

      expect(msg.id, 'abc');
      expect(msg.role, 'user');
      expect(msg.content, 'Hello');
      expect(msg.createdAt, isNotNull);
      expect(msg.isPendingConfirmation, isFalse);
    });

    test('detects pending_confirmation status', () {
      final msg = ChatMessage.fromJson({
        'id': 'tc-1',
        'role': 'assistant',
        'content': null,
        'status': 'pending_confirmation',
        'tool_calls': [
          {
            'id': 'tc-call-1',
            'name': 'list_instances',
            'arguments': {'limit': 5},
            'safety': 'safe',
          }
        ],
      });

      expect(msg.isPendingConfirmation, isTrue);
      expect(msg.pendingTool, isNotNull);
      expect(msg.pendingTool!.name, 'list_instances');
      expect(msg.pendingTool!.safety, 'safe');
      expect(msg.pendingTool!.arguments['limit'], 5);
    });

    test('pending_tool from dedicated key wins over first tool_call', () {
      final msg = ChatMessage.fromJson({
        'id': 'tc-2',
        'role': 'assistant',
        'status': 'pending_confirmation',
        'pending_tool': {
          'id': 'pt-explicit',
          'name': 'delete_backtest',
          'arguments': {'id': 'x'},
          'safety': 'destructive',
        },
        'tool_calls': [
          {'id': 'tc-fallback', 'name': 'read_only', 'arguments': {}}
        ],
      });

      expect(msg.pendingTool!.name, 'delete_backtest');
      expect(msg.pendingTool!.safety, 'destructive');
    });

    test('handles missing / null fields gracefully', () {
      final msg = ChatMessage.fromJson({});
      expect(msg.id, '');
      expect(msg.role, 'assistant');
      expect(msg.content, isNull);
      expect(msg.createdAt, isNull);
      expect(msg.toolCalls, isEmpty);
      expect(msg.blocks, isEmpty);
      expect(msg.isPendingConfirmation, isFalse);
    });

    test('parses rich blocks list', () {
      final msg = ChatMessage.fromJson({
        'id': 'blk',
        'role': 'assistant',
        'blocks': [
          {'type': 'markdown', 'content': '**hi**'},
          {'type': 'navigate', 'route': '/dashboard'},
        ],
      });

      expect(msg.blocks.length, 2);
      expect(msg.blocks[0]['type'], 'markdown');
      expect(msg.blocks[1]['type'], 'navigate');
      expect(msg.blocks[1]['route'], '/dashboard');
    });

    test('parses tool-role message with name', () {
      final msg = ChatMessage.fromJson({
        'id': 'tr-1',
        'role': 'tool',
        'name': 'list_instances',
        'content': '{"instances": []}',
      });

      expect(msg.role, 'tool');
      expect(msg.name, 'list_instances');
    });
  });

  group('Conversation.fromJson', () {
    test('parses complete conversation with messages', () {
      final conv = Conversation.fromJson({
        'id': 'conv-1',
        'title': 'My Chat',
        'model_id': 'claude-opus-4-5',
        'model_name': 'Claude Opus',
        'auto_confirm_safe_tools': true,
        'message_count': 3,
        'messages': [
          {'id': 'm1', 'role': 'user', 'content': 'Hi'},
          {'id': 'm2', 'role': 'assistant', 'content': 'Hello'},
        ],
      });

      expect(conv.id, 'conv-1');
      expect(conv.title, 'My Chat');
      expect(conv.modelId, 'claude-opus-4-5');
      expect(conv.autoConfirmSafeTools, isTrue);
      expect(conv.messages.length, 2);
      expect(conv.messageCount, 3);
    });

    test('handles empty / missing messages', () {
      final conv = Conversation.fromJson({'id': 'x'});
      expect(conv.messages, isEmpty);
      expect(conv.autoConfirmSafeTools, isFalse);
    });

    test('reads auto_confirm_safe_tools nested under settings (backend shape)',
        () {
      // The backend stores/returns the flag at settings.auto_confirm_safe_tools,
      // NOT at the top level — parsing the wrong place made the toggle revert.
      final on = Conversation.fromJson({
        'id': 'c',
        'settings': {'auto_confirm_safe_tools': true},
      });
      expect(on.autoConfirmSafeTools, isTrue);

      final off = Conversation.fromJson({
        'id': 'c',
        'settings': {'auto_confirm_safe_tools': false},
      });
      expect(off.autoConfirmSafeTools, isFalse);
    });

    test('copyWith preserves unchanged fields', () {
      const original = Conversation(id: 'x', title: 'Old', modelId: 'mod');
      final updated = original.copyWith(title: 'New');
      expect(updated.title, 'New');
      expect(updated.modelId, 'mod');
      expect(updated.id, 'x');
    });
  });

  group('ToolCall.fromJson', () {
    test('maps id variants', () {
      final tc = ToolCall.fromJson({
        'tool_call_id': 'tc-abc',
        'function': 'my_func',
        'input': {'key': 'val'},
        'safety': 'write',
        'description': 'Does stuff',
      });

      expect(tc.id, 'tc-abc');
      expect(tc.name, 'my_func');
      expect(tc.arguments['key'], 'val');
      expect(tc.safety, 'write');
      expect(tc.description, 'Does stuff');
    });

    test('defaults safety to write', () {
      final tc = ToolCall.fromJson({'id': 'x', 'name': 'foo'});
      expect(tc.safety, 'write');
    });
  });

  group('Navigate-directive parsing', () {
    test('identifies navigate blocks in messages', () {
      final msg = ChatMessage.fromJson({
        'id': 'nav-1',
        'role': 'assistant',
        'content': 'Taking you to dashboard.',
        'blocks': [
          {'type': 'navigate', 'route': '/dashboard'},
        ],
      });

      final navigates = msg.blocks
          .where((b) => b['type'] == 'navigate')
          .map((b) => b['route']?.toString() ?? '')
          .where((r) => r.isNotEmpty)
          .toList();

      expect(navigates, ['dashboard'.startsWith('/') ? '/dashboard' : '/dashboard']);
      expect(navigates.first, '/dashboard');
    });

    test('filters out navigate blocks from visible list correctly', () {
      final msg = ChatMessage.fromJson({
        'id': 'nav-2',
        'role': 'assistant',
        'blocks': [
          {'type': 'markdown', 'content': 'Hello'},
          {'type': 'navigate', 'route': '/instances'},
          {'type': 'stat', 'label': 'Count', 'value': '5'},
        ],
      });

      final visible =
          msg.blocks.where((b) => b['type'] != 'navigate').toList();
      expect(visible.length, 2);
      expect(visible.any((b) => b['type'] == 'navigate'), isFalse);
    });
  });

  group('ChatModel.fromJson', () {
    test('parses standard model entry', () {
      final m = ChatModel.fromJson({
        'id': 'claude-opus-4-5',
        'name': 'Claude Opus 4.5',
        'provider': 'anthropic',
        'model': 'claude-opus-4-5-20251201',
      });

      expect(m.id, 'claude-opus-4-5');
      expect(m.name, 'Claude Opus 4.5');
      expect(m.provider, 'anthropic');
    });

    test('falls back to id as name', () {
      final m = ChatModel.fromJson({'id': 'some-model'});
      expect(m.name, 'some-model');
    });
  });
}
