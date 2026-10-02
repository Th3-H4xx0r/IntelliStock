import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../application/models_controller.dart';
import '../data/model_repository.dart';
import 'llm_config_form.dart';

// ── Helpers ────────────────────────────────────────────────────────────────────

String _providerLabel(String p) {
  const m = {
    'gemini': 'Google Gemini',
    'deepseek': 'DeepSeek',
    'openai': 'OpenAI Compatible',
    'azure': 'Azure OpenAI',
    'nvidia': 'NVIDIA NIM',
    'ollama': 'Ollama (local/cloud)',
    'bedrock': 'AWS Bedrock',
    'openrouter': 'OpenRouter',
    'claude-cli': 'Claude Code CLI',
    'codex-cli': 'OpenAI Codex CLI',
  };
  return m[p] ?? p;
}

String _reasoningCell(LlmModel m) {
  if (m.provider == 'claude-cli' || m.provider == 'codex-cli') return '—';
  if (m.provider == 'ollama') {
    final t = (m.ollamaThink ?? '').trim().toLowerCase();
    if (t.isEmpty) return 'Default';
    if (t == 'true' || t == 'on') return 'On';
    if (t == 'false' || t == 'off') return 'Off';
    return t[0].toUpperCase() + t.substring(1);
  }
  if (m.provider == 'bedrock') {
    final r = (m.bedrockReasoning ?? '').trim().toLowerCase();
    if (r.isEmpty || r == 'off') return 'Off';
    return r[0].toUpperCase() + r.substring(1);
  }
  final e = (m.reasoningEffort ?? '').trim();
  if (e.isEmpty) return 'Default';
  return e[0].toUpperCase() + e.substring(1);
}

String _keyCell(LlmModel m) {
  if (m.provider == 'claude-cli') return m.cliPath?.isNotEmpty == true ? m.cliPath! : 'claude';
  if (m.provider == 'codex-cli') return m.cliPath?.isNotEmpty == true ? m.cliPath! : 'codex';
  return m.apiKey?.isNotEmpty == true ? m.apiKey! : '—';
}

// ── Main screen ────────────────────────────────────────────────────────────────

class ModelsScreen extends ConsumerWidget {
  const ModelsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(modelsControllerProvider);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: Column(
            children: [
              // App bar
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                child: Row(children: [
                  IconButton(
                    icon: const Icon(Icons.arrow_back, size: 22),
                    color: AppColors.textMuted,
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                  const Spacer(),
                  IconButton(
                    icon: const Icon(Icons.refresh, size: 22),
                    color: AppColors.textMuted,
                    onPressed: () =>
                        ref.read(modelsControllerProvider.notifier).refresh(),
                  ),
                ]),
              ),
              Expanded(
                child: RefreshIndicator(
                  color: AppColors.primary,
                  backgroundColor: AppColors.surface,
                  onRefresh: () =>
                      ref.read(modelsControllerProvider.notifier).refresh(),
                  child: ListView(
                    padding: const EdgeInsets.symmetric(horizontal: 20),
                    children: [
                      _Header(
                        onAdd: () => _showAddEditSheet(context, ref, null),
                      ),
                      const SizedBox(height: 24),
                      state.when(
                        loading: () => const _ModelsSkeleton(),
                        error: (e, _) => ErrorBanner(
                          message: e.toString(),
                          onRetry: () =>
                              ref.read(modelsControllerProvider.notifier).refresh(),
                        ),
                        data: (models) => models.isEmpty
                            ? EmptyState(
                                icon: symbol('psychology'),
                                title: 'No models saved yet',
                                subtitle:
                                    'Add a model to start using centralized LLM configurations.',
                                actionLabel: 'Add Model',
                                onAction: () =>
                                    _showAddEditSheet(context, ref, null),
                              )
                            : _ModelList(
                                models: models,
                                onEdit: (m) =>
                                    _showAddEditSheet(context, ref, m),
                                onDelete: (m) =>
                                    _confirmDelete(context, ref, m),
                              ),
                      ),
                      const SizedBox(height: 40),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _showAddEditSheet(BuildContext context, WidgetRef ref, LlmModel? existing) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.panel,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (ctx) => _AddEditSheet(
        existing: existing,
        onSaved: () {
          Navigator.pop(ctx);
          ref.read(modelsControllerProvider.notifier).refresh();
        },
      ),
    );
  }

  Future<void> _confirmDelete(
      BuildContext context, WidgetRef ref, LlmModel m) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.panel,
        title: Text('Delete "${m.name}"?', style: AppTextStyles.h3),
        content: Text(
          'Strategies referencing this model will revert to inline credentials.',
          style: AppTextStyles.body.copyWith(color: AppColors.textMuted),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text('Delete', style: TextStyle(color: AppColors.danger)),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await ref.read(modelRepositoryProvider).delete(m.id);
      await ref.read(modelsControllerProvider.notifier).refresh();
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('Delete failed: $e'),
          backgroundColor: AppColors.danger,
        ));
      }
    }
  }
}

// ── Loading skeleton ────────────────────────────────────────────────────────────

class _ModelsSkeleton extends StatelessWidget {
  const _ModelsSkeleton();

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Header skeleton
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Skeleton(width: 60, height: 10),
              const SizedBox(height: 6),
              Skeleton(width: 130, height: 20),
              const SizedBox(height: 6),
              Skeleton(width: double.infinity, height: 11),
              const SizedBox(height: 4),
              Skeleton(width: 220, height: 11),
            ]),
          ),
          const SizedBox(width: 12),
          Skeleton(width: 100, height: 36, radius: 10),
        ]),
        const SizedBox(height: 24),
        // 4 model-row/card skeletons
        for (int i = 0; i < 4; i++) ...[
          _ModelCardSkeleton(),
          const SizedBox(height: 16),
        ],
      ],
    );
  }
}

class _ModelCardSkeleton extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Skeleton(width: 160, height: 14),
              const SizedBox(height: 6),
              Skeleton(width: 110, height: 11),
            ]),
          ),
          Row(mainAxisSize: MainAxisSize.min, children: [
            Skeleton.circle(30),
            const SizedBox(width: 6),
            Skeleton.circle(30),
          ]),
        ]),
        const SizedBox(height: 10),
        Wrap(spacing: 8, runSpacing: 4, children: [
          Skeleton(width: 90, height: 11),
          Skeleton(width: 70, height: 11),
          Skeleton(width: 110, height: 11),
        ]),
      ]),
    );
  }
}

// ── Header ─────────────────────────────────────────────────────────────────────

class _Header extends StatelessWidget {
  const _Header({required this.onAdd});
  final VoidCallback onAdd;

  @override
  Widget build(BuildContext context) {
    return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Expanded(
        child: SectionHeader(
          eyebrow: 'Models',
          title: 'LLM Models',
          subtitle: 'Centralized LLM model configurations. Strategies can reference these instead of storing credentials inline.',
        ),
      ),
      const SizedBox(width: 12),
      AppButton.primary(
        label: 'Add Model',
        icon: Icons.add,
        onPressed: onAdd,
        dense: true,
      ),
    ]);
  }
}

// ── Model list ─────────────────────────────────────────────────────────────────

class _ModelList extends StatelessWidget {
  const _ModelList({
    required this.models,
    required this.onEdit,
    required this.onDelete,
  });

  final List<LlmModel> models;
  final ValueChanged<LlmModel> onEdit;
  final ValueChanged<LlmModel> onDelete;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: models
          .map((m) => Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: _ModelCard(
                  model: m,
                  onEdit: () => onEdit(m),
                  onDelete: () => onDelete(m),
                ),
              ))
          .toList(),
    );
  }
}

// ── Model card ─────────────────────────────────────────────────────────────────

class _ModelCard extends ConsumerStatefulWidget {
  const _ModelCard({required this.model, required this.onEdit, required this.onDelete});
  final LlmModel model;
  final VoidCallback onEdit;
  final VoidCallback onDelete;

  @override
  ConsumerState<_ModelCard> createState() => _ModelCardState();
}

class _ModelCardState extends ConsumerState<_ModelCard> {
  bool _testingCli = false;
  String? _testCliMessage;
  bool? _testCliOk;

  Future<void> _testCli() async {
    setState(() { _testingCli = true; _testCliMessage = 'Testing…'; _testCliOk = null; });
    try {
      final data = await ref.read(modelRepositoryProvider).testCli(widget.model.id);
      final ok = data['ok'] as bool? ?? false;
      if (ok) {
        final v = data['version'] as String?;
        final li = data['logged_in'] as bool? ?? false;
        final resp = data['model_response'] as String?;
        setState(() {
          _testCliOk = true;
          _testCliMessage = '✓ v${v ?? '?'}, ${li ? 'logged in' : 'not logged in'}${resp != null ? ', response: $resp' : ''}';
        });
      } else {
        setState(() { _testCliOk = false; _testCliMessage = data['error'] as String? ?? 'Unknown error'; });
      }
    } catch (e) {
      setState(() { _testCliOk = false; _testCliMessage = e.toString(); });
    } finally {
      if (mounted) setState(() => _testingCli = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final m = widget.model;
    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(m.name, style: AppTextStyles.cardTitle),
                const SizedBox(height: 4),
                Text(_providerLabel(m.provider),
                    style: AppTextStyles.meta.copyWith(color: AppColors.textMuted)),
              ]),
            ),
            Row(mainAxisSize: MainAxisSize.min, children: [
              if (m.provider == 'claude-cli')
                _iconBtn(
                  icon: _testingCli ? Icons.sync : Icons.cable,
                  spinning: _testingCli,
                  onTap: _testingCli ? null : _testCli,
                  tooltip: 'Test CLI connection',
                ),
              _iconBtn(icon: Icons.edit_outlined, onTap: widget.onEdit, tooltip: 'Edit'),
              _iconBtn(
                  icon: Icons.delete_outline,
                  color: AppColors.danger,
                  onTap: widget.onDelete,
                  tooltip: 'Delete'),
            ]),
          ]),
          const SizedBox(height: 10),
          Wrap(spacing: 8, runSpacing: 4, children: [
            _chip('Model', m.model, mono: true),
            _chip('Effort', _reasoningCell(m)),
            _chip('Key/CLI', _keyCell(m), mono: true),
            if (m.createdAt != null)
              _chip('Created', fmtDate(parseDateTime(m.createdAt))),
          ]),
          if (_testCliMessage != null) ...[
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
              decoration: BoxDecoration(
                color: AppColors.fill(_testCliOk == true ? AppColors.success : AppColors.danger),
                borderRadius: BorderRadius.circular(6),
                border: Border.all(
                    color: AppColors.stroke(_testCliOk == true ? AppColors.success : AppColors.danger)),
              ),
              child: Text(
                _testCliMessage!,
                style: AppTextStyles.micro
                    .copyWith(color: _testCliOk == true ? AppColors.success : AppColors.danger),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _iconBtn({
    required IconData icon,
    VoidCallback? onTap,
    String? tooltip,
    Color color = AppColors.textMuted,
    bool spinning = false,
  }) {
    final iconWidget = spinning
        ? SizedBox(
            width: 16,
            height: 16,
            child: CircularProgressIndicator(strokeWidth: 2, color: color))
        : Icon(icon, size: 18, color: color);
    return Tooltip(
      message: tooltip ?? '',
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(6),
        child: Padding(padding: const EdgeInsets.all(6), child: iconWidget),
      ),
    );
  }

  Widget _chip(String label, String value, {bool mono = false}) {
    return RichText(
      text: TextSpan(children: [
        TextSpan(text: '$label: ', style: AppTextStyles.micro.copyWith(color: AppColors.textFaint)),
        TextSpan(
          text: value,
          style: mono
              ? AppTextStyles.mono(11, color: AppColors.textMuted)
              : AppTextStyles.micro.copyWith(color: AppColors.textMuted),
        ),
      ]),
    );
  }
}

// ── Add / Edit bottom sheet ────────────────────────────────────────────────────

class _AddEditSheet extends ConsumerStatefulWidget {
  const _AddEditSheet({this.existing, required this.onSaved});
  final LlmModel? existing;
  final VoidCallback onSaved;

  @override
  ConsumerState<_AddEditSheet> createState() => _AddEditSheetState();
}

class _AddEditSheetState extends ConsumerState<_AddEditSheet> {
  final _nameController = TextEditingController();
  late LlmConfigDraft _draft;

  // Pricing overrides
  final _inputCostCtrl = TextEditingController();
  final _outputCostCtrl = TextEditingController();
  final _cacheCreationCtrl = TextEditingController();
  final _cacheReadCtrl = TextEditingController();
  bool _pricingExpanded = false;

  // Test/save state
  bool _submitting = false;
  String _statusMsg = '';
  bool _statusOk = false;
  bool _saved = false;
  LlmTestResult? _testResult;

  @override
  void initState() {
    super.initState();
    final e = widget.existing;
    if (e != null) {
      _nameController.text = e.name;
      _draft = LlmConfigDraft(
        provider: e.provider,
        model: e.model,
        reasoningEffort: e.reasoningEffort ?? '',
        apiKey: '', // don't prefill masked
        openaiBaseUrl: e.openaiBaseUrl ?? '',
        nvidiaBaseUrl: e.nvidiaBaseUrl ?? '',
        azureOpenaiEndpoint: e.azureOpenaiEndpoint ?? '',
        azureOpenaiApiVersion: e.azureOpenaiApiVersion ?? '2024-10-21',
        cliPath: e.cliPath ?? '',
        extraArgs: e.extraArgs ?? '',
        ollamaBaseUrl: e.ollamaBaseUrl?.isNotEmpty == true ? e.ollamaBaseUrl! : 'http://localhost:11434',
        ollamaKeepAlive: e.ollamaKeepAlive ?? '',
        ollamaThink: e.ollamaThink ?? '',
        bedrockRegion: e.bedrockRegion?.isNotEmpty == true ? e.bedrockRegion! : 'us-east-1',
        bedrockReasoning: e.bedrockReasoning ?? 'off',
        openrouterBaseUrl: e.openrouterBaseUrl?.isNotEmpty == true
            ? e.openrouterBaseUrl!
            : 'https://openrouter.ai/api/v1',
        openrouterReferer: e.openrouterReferer ?? '',
        openrouterTitle: e.openrouterTitle ?? '',
        modelCacheFamily: e.modelCacheFamily ?? '',
      );
      if (e.inputCostPer1m != null) _inputCostCtrl.text = e.inputCostPer1m!.toString();
      if (e.outputCostPer1m != null) _outputCostCtrl.text = e.outputCostPer1m!.toString();
      if (e.cacheCreationCostPer1m != null) _cacheCreationCtrl.text = e.cacheCreationCostPer1m!.toString();
      if (e.cacheReadCostPer1m != null) _cacheReadCtrl.text = e.cacheReadCostPer1m!.toString();
    } else {
      _draft = LlmConfigDraft();
    }
  }

  @override
  void dispose() {
    _nameController.dispose();
    _inputCostCtrl.dispose();
    _outputCostCtrl.dispose();
    _cacheCreationCtrl.dispose();
    _cacheReadCtrl.dispose();
    super.dispose();
  }

  Map<String, dynamic> _buildPayload() {
    final p = _draft.toPayload();
    p['name'] = _nameController.text.trim();
    final inputCost = double.tryParse(_inputCostCtrl.text);
    final outputCost = double.tryParse(_outputCostCtrl.text);
    final cacheCreate = double.tryParse(_cacheCreationCtrl.text);
    final cacheRead = double.tryParse(_cacheReadCtrl.text);
    if (inputCost != null) p['input_cost_per_1m'] = inputCost;
    if (outputCost != null) p['output_cost_per_1m'] = outputCost;
    if (cacheCreate != null) p['cache_creation_cost_per_1m'] = cacheCreate;
    if (cacheRead != null) p['cache_read_cost_per_1m'] = cacheRead;
    return p;
  }

  Future<void> _testOnly() async {
    if (_draft.model.trim().isEmpty) {
      setState(() { _statusMsg = 'Model is required'; _statusOk = false; });
      return;
    }
    if (_draft.provider == 'claude-cli') {
      setState(() {
        _statusMsg = 'Use the cable icon on the saved row to test a claude-cli model.';
        _statusOk = false;
      });
      return;
    }
    setState(() { _submitting = true; _testResult = null; _statusMsg = 'Testing LLM configuration…'; _statusOk = false; });
    try {
      final result = await ref.read(modelRepositoryProvider).testLlm(_draft.toTestPayload());
      setState(() {
        _testResult = result;
        _statusMsg = 'LLM test passed (not saved).';
        _statusOk = true;
      });
    } catch (e) {
      setState(() { _statusMsg = 'LLM test failed: $e'; _statusOk = false; });
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  Future<void> _testAndSave() async {
    final name = _nameController.text.trim();
    if (name.isEmpty) {
      setState(() { _statusMsg = 'Name is required'; _statusOk = false; });
      return;
    }
    if (_draft.model.trim().isEmpty) {
      setState(() { _statusMsg = 'Model is required'; _statusOk = false; });
      return;
    }
    setState(() { _submitting = true; _testResult = null; _statusOk = false; });

    final skipTest = _draft.provider == 'claude-cli';
    final hasKey = _draft.apiKey.isNotEmpty;
    final shouldTest = !skipTest &&
        (_draft.provider == 'codex-cli' || _draft.provider == 'ollama' || hasKey);

    if (shouldTest) {
      setState(() => _statusMsg = 'Testing LLM configuration…');
      try {
        final result = await ref.read(modelRepositoryProvider).testLlm(_draft.toTestPayload());
        setState(() => _testResult = result);
      } catch (e) {
        setState(() { _statusMsg = 'LLM test failed: $e'; _statusOk = false; _submitting = false; });
        return;
      }
    }

    setState(() => _statusMsg = widget.existing != null ? 'Updating model…' : 'Saving model…');
    try {
      final payload = _buildPayload();
      final repo = ref.read(modelRepositoryProvider);
      final LlmModel savedModel;
      if (widget.existing != null) {
        savedModel = await repo.update(widget.existing!.id, payload);
      } else {
        savedModel = await repo.create(payload);
      }

      // claude-cli skips the synthetic /llm/test above. Instead of falsely
      // claiming "LLM test passed", run the REAL CLI connectivity test against
      // the now-saved model so an expired subscription token (401) surfaces.
      if (_draft.provider == 'claude-cli') {
        setState(() {
          _saved = true;
          _statusMsg = 'Testing Claude CLI connection…';
        });
        try {
          final data = await repo.testCli(savedModel.id);
          final ok = data['ok'] as bool? ?? false;
          if (ok) {
            setState(() {
              _statusOk = true;
              _statusMsg = 'Model saved. Claude CLI connection OK (logged in).';
            });
          } else {
            final err = data['error'] as String? ?? 'unknown error';
            setState(() {
              _statusOk = false;
              _statusMsg = 'Model saved, but Claude CLI test failed: $err';
            });
          }
        } catch (e) {
          // The model is already saved — don't fail the save itself.
          setState(() {
            _statusOk = false;
            _statusMsg = 'Model saved, but Claude CLI test failed: $e';
          });
        }
      } else {
        setState(() {
          _statusOk = true;
          _saved = true;
          _statusMsg = widget.existing != null
              ? 'LLM test passed. Model updated.'
              : 'LLM test passed. Model saved.';
        });
      }
    } catch (e) {
      setState(() { _statusMsg = e.toString(); _statusOk = false; });
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final isEdit = widget.existing != null;
    final bottom = MediaQuery.viewInsetsOf(context).bottom;

    return Container(
      padding: EdgeInsets.only(bottom: bottom),
      constraints: BoxConstraints(
        maxHeight: MediaQuery.sizeOf(context).height * 0.92,
      ),
      child: Column(children: [
        // Handle
        Container(
          width: 40, height: 4, margin: const EdgeInsets.only(top: 12, bottom: 8),
          decoration: BoxDecoration(
            color: AppColors.textFaint, borderRadius: BorderRadius.circular(2)),
        ),
        // Title bar
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Row(children: [
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(isEdit ? 'Edit Model' : 'Add Model', style: AppTextStyles.h3),
              const SizedBox(height: 2),
              Text('Save a reusable LLM configuration.',
                  style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
            ])),
            if (_saved)
              IconButton(
                icon: const Icon(Icons.close),
                color: AppColors.textMuted,
                onPressed: () { widget.onSaved(); },
              )
            else
              IconButton(
                icon: const Icon(Icons.close),
                color: AppColors.textMuted,
                onPressed: _submitting ? null : () => Navigator.pop(context),
              ),
          ]),
        ),
        const Divider(height: 16),
        // Scrollable body
        Expanded(child: SingleChildScrollView(
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            // Name
            Text('Name', style: AppTextStyles.meta.copyWith(color: AppColors.textMuted, fontWeight: FontWeight.w500)),
            const SizedBox(height: 6),
            TextFormField(
              controller: _nameController,
              style: AppTextStyles.body.copyWith(color: AppColors.textHi),
              decoration: InputDecoration(
                hintText: 'e.g. Gemini Flash — Main',
                hintStyle: AppTextStyles.body.copyWith(color: AppColors.textFaint),
                filled: true, fillColor: AppColors.surface,
                contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: AppColors.border)),
                enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: AppColors.border)),
                focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: AppColors.primary)),
              ),
            ),
            const SizedBox(height: 20),
            // LLM config form
            LlmConfigForm(
              draft: _draft,
              onChanged: (d) => setState(() => _draft = d),
              disabled: _submitting,
            ),
            const SizedBox(height: 20),
            // Pricing overrides
            GestureDetector(
              onTap: () => setState(() => _pricingExpanded = !_pricingExpanded),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                decoration: BoxDecoration(
                  color: AppColors.surface,
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: AppColors.border),
                ),
                child: Row(children: [
                  Expanded(child: Text('Pricing override (optional)',
                      style: AppTextStyles.meta.copyWith(color: AppColors.textMd, fontWeight: FontWeight.w500))),
                  Icon(_pricingExpanded ? Icons.expand_less : Icons.expand_more,
                      size: 18, color: AppColors.textMuted),
                ]),
              ),
            ),
            if (_pricingExpanded) ...[
              const SizedBox(height: 8),
              Text('Leave blank to use backend llm_pricing.yaml defaults. Values are \$/1M tokens.',
                  style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
              const SizedBox(height: 10),
              _pricingField('Input cost (\$/1M tokens)', _inputCostCtrl),
              const SizedBox(height: 10),
              _pricingField('Output cost (\$/1M tokens)', _outputCostCtrl),
              const SizedBox(height: 10),
              _pricingField('Cache creation cost (\$/1M tokens)', _cacheCreationCtrl),
              const SizedBox(height: 10),
              _pricingField('Cache read cost (\$/1M tokens)', _cacheReadCtrl),
            ],
            const SizedBox(height: 16),
            // Status message
            if (_statusMsg.isNotEmpty)
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: AppColors.fill(_statusOk ? AppColors.success : AppColors.danger),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: AppColors.stroke(_statusOk ? AppColors.success : AppColors.danger)),
                ),
                child: Text(_statusMsg,
                    style: AppTextStyles.nano.copyWith(
                        color: _statusOk ? AppColors.success : AppColors.danger)),
              ),
            // Test result panel
            if (_testResult != null) ...[
              const SizedBox(height: 12),
              _TestResultPanel(result: _testResult!),
            ],
            // Edit note
            if (isEdit) ...[
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: AppColors.fill(AppColors.warning),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: AppColors.stroke(AppColors.warning)),
                ),
                child: Text('Leave API Key empty to keep the existing key unchanged.',
                    style: AppTextStyles.nano.copyWith(color: AppColors.warning)),
              ),
            ],
            const SizedBox(height: 24),
          ]),
        )),
        // Action buttons
        const Divider(height: 1),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
          child: Row(children: [
            Expanded(
              child: _saved
                  ? AppButton.semantic(
                      label: 'Close',
                      color: AppColors.success,
                      onPressed: widget.onSaved,
                    )
                  : AppButton.ghost(
                      label: 'Cancel',
                      onPressed: _submitting ? null : () => Navigator.pop(context),
                    ),
            ),
            if (!_saved && _draft.provider != 'claude-cli') ...[
              const SizedBox(width: 10),
              Expanded(
                child: AppButton.ghost(
                  label: _submitting ? 'Testing…' : 'Test only',
                  onPressed: _submitting ? null : _testOnly,
                ),
              ),
            ],
            if (!_saved) ...[
              const SizedBox(width: 10),
              Expanded(
                child: AppButton.primary(
                  label: _submitting
                      ? (_draft.provider == 'claude-cli'
                          ? (isEdit ? 'Updating…' : 'Saving…')
                          : 'Testing…')
                      : (isEdit ? 'Test & Update' : 'Test & Save'),
                  busy: _submitting,
                  onPressed: _submitting ? null : _testAndSave,
                ),
              ),
            ],
          ]),
        ),
      ]),
    );
  }

  Widget _pricingField(String label, TextEditingController ctrl) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
      const SizedBox(height: 4),
      TextFormField(
        controller: ctrl,
        keyboardType: const TextInputType.numberWithOptions(decimal: true),
        style: AppTextStyles.mono(14, color: AppColors.textHi),
        decoration: InputDecoration(
          hintText: 'e.g. 3.00',
          hintStyle: AppTextStyles.body.copyWith(color: AppColors.textFaint),
          filled: true, fillColor: AppColors.surface,
          contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: AppColors.border)),
          enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: AppColors.border)),
          focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: AppColors.primary)),
        ),
      ),
    ]);
  }
}

// ── Test result panel ─────────────────────────────────────────────────────────

class _TestResultPanel extends StatelessWidget {
  const _TestResultPanel({required this.result});
  final LlmTestResult result;

  @override
  Widget build(BuildContext context) {
    String prettyJson(dynamic v) {
      if (v == null) return '';
      try { return const JsonEncoder.withIndent('  ').convert(v); } catch (_) { return v.toString(); }
    }

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.success),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.stroke(AppColors.success)),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('LLM connectivity test response',
            style: AppTextStyles.meta.copyWith(color: AppColors.success, fontWeight: FontWeight.w600)),
        const SizedBox(height: 6),
        Wrap(spacing: 12, runSpacing: 2, children: [
          if (result.provider != null) _kv('provider', result.provider!),
          if (result.model != null) _kv('model', result.model!),
          if (result.effectiveModel != null && result.effectiveModel != result.model)
            _kv('effective', result.effectiveModel!),
          if (result.latencyMs != null) _kv('latency', '${result.latencyMs}ms'),
        ]),
        if (result.result != null) ...[
          const SizedBox(height: 8),
          Text('structured connectivity probe:',
              style: AppTextStyles.nano.copyWith(color: AppColors.success.withValues(alpha: 0.7))),
          const SizedBox(height: 4),
          Container(
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(color: Colors.black38, borderRadius: BorderRadius.circular(4)),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Text(prettyJson(result.result), style: AppTextStyles.mono(10, color: AppColors.textMd)),
            ),
          ),
        ],
        if (result.smokePrompt != null || result.smokeResponse != null ||
            result.smokeThinking != null || result.smokeError != null) ...[
          const SizedBox(height: 8),
          Row(children: [
            Text('real-generation smoke',
                style: AppTextStyles.nano.copyWith(color: AppColors.success.withValues(alpha: 0.7))),
            if (result.smokeLatencyMs != null) ...[
              const SizedBox(width: 4),
              Text('(${result.smokeLatencyMs}ms)',
                  style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
            ],
            if (result.smokeContentChars != null) ...[
              const SizedBox(width: 4),
              Text('· content ${result.smokeContentChars} chars',
                  style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
            ],
            if (result.smokeThinkingChars != null) ...[
              const SizedBox(width: 4),
              Text('· reasoning ${result.smokeThinkingChars} chars',
                  style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
            ],
          ]),
          if (result.smokePrompt != null) ...[
            const SizedBox(height: 2),
            Text('prompt: ${result.smokePrompt}',
                style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
          ],
          if (result.smokeResponse != null && (result.smokeContentChars ?? 1) > 0) ...[
            const SizedBox(height: 4),
            Text('content:', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
            const SizedBox(height: 2),
            Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(color: Colors.black38, borderRadius: BorderRadius.circular(4)),
              child: Text(result.smokeResponse!, style: AppTextStyles.mono(11, color: AppColors.textMd)),
            ),
          ],
          if (result.smokeThinking != null) ...[
            const SizedBox(height: 4),
            _CollapsibleSection(
              label: 'reasoning (${result.smokeThinkingChars ?? result.smokeThinking!.length} chars)',
              child: Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(color: Colors.black45, borderRadius: BorderRadius.circular(4)),
                child: Text(result.smokeThinking!, style: AppTextStyles.mono(11, color: AppColors.textMuted)),
              ),
            ),
          ],
          if (result.smokeError != null) ...[
            const SizedBox(height: 4),
            Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(color: AppColors.fill(AppColors.danger), borderRadius: BorderRadius.circular(4)),
              child: Text('smoke generation failed: ${result.smokeError}',
                  style: AppTextStyles.mono(10, color: AppColors.danger)),
            ),
          ],
          if (result.smokeResponse == null && result.smokeThinking == null && result.smokeError == null) ...[
            const SizedBox(height: 4),
            Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(color: AppColors.fill(AppColors.warning), borderRadius: BorderRadius.circular(4)),
              child: Text('smoke generation returned empty — structured check passed but the model did not produce free-form text.',
                  style: AppTextStyles.nano.copyWith(color: AppColors.warning)),
            ),
          ],
        ],
        if (result.providerMeta != null) ...[
          const SizedBox(height: 8),
          Text('provider meta:', style: AppTextStyles.nano.copyWith(color: AppColors.success.withValues(alpha: 0.7))),
          const SizedBox(height: 4),
          Container(
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(color: Colors.black38, borderRadius: BorderRadius.circular(4)),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Text(prettyJson(result.providerMeta), style: AppTextStyles.mono(10, color: AppColors.textMd)),
            ),
          ),
        ],
      ]),
    );
  }

  Widget _kv(String label, String value) {
    return RichText(
      text: TextSpan(children: [
        TextSpan(text: '$label: ', style: AppTextStyles.nano.copyWith(color: AppColors.success.withValues(alpha: 0.7))),
        TextSpan(text: value, style: AppTextStyles.mono(11, color: AppColors.success)),
      ]),
    );
  }
}

class _CollapsibleSection extends StatefulWidget {
  const _CollapsibleSection({required this.label, required this.child});
  final String label;
  final Widget child;

  @override
  State<_CollapsibleSection> createState() => _CollapsibleSectionState();
}

class _CollapsibleSectionState extends State<_CollapsibleSection> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      GestureDetector(
        onTap: () => setState(() => _open = !_open),
        child: Row(children: [
          Icon(_open ? Icons.expand_less : Icons.expand_more, size: 14, color: AppColors.textDim),
          const SizedBox(width: 4),
          Text(widget.label, style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
        ]),
      ),
      if (_open) ...[const SizedBox(height: 4), widget.child],
    ]);
  }
}
