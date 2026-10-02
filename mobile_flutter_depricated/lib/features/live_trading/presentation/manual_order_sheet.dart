import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/material_symbols.dart';
import '../application/live_state_notifier.dart';

/// Validates the manual order form fields.
/// Returns null if valid, or an error message string.
String? validateOrderForm(OrderForm form) {
  final sym = form.symbol.trim().toUpperCase();
  if (sym.isEmpty) return 'Symbol is required.';

  final qtyStr = form.qty.trim();
  final notionalStr = form.notional.trim();
  final hasQty = qtyStr.isNotEmpty;
  final hasNotional = notionalStr.isNotEmpty;

  if (!hasQty && !hasNotional) return 'Enter either qty or notional.';
  if (hasQty && hasNotional) return 'Fill qty OR notional, not both.';

  if (hasQty) {
    final qty = double.tryParse(qtyStr);
    if (qty == null || qty <= 0) return 'Qty must be a positive number.';
  }
  if (hasNotional) {
    final notional = double.tryParse(notionalStr);
    if (notional == null || notional <= 0) return 'Notional must be a positive number.';
  }
  if (form.orderType == 'limit') {
    final lp = double.tryParse(form.limitPrice.trim());
    if (lp == null || lp <= 0) return 'Limit order requires a positive limit price.';
  }
  if (form.extendedHours && (form.orderType != 'limit' || form.tif != 'day')) {
    return 'Extended hours requires limit order type + TIF=day.';
  }
  return null;
}

/// Builds the payload Map from a validated OrderForm.
Map<String, dynamic> buildOrderPayload(OrderForm form) {
  final payload = <String, dynamic>{
    'symbol': form.symbol.trim().toUpperCase(),
    'side': form.side,
    'order_type': form.orderType,
    'tif': form.tif,
    'extended_hours': form.extendedHours,
  };
  final qtyStr = form.qty.trim();
  final notionalStr = form.notional.trim();
  if (qtyStr.isNotEmpty) {
    payload['qty'] = double.parse(qtyStr);
  } else {
    payload['notional'] = double.parse(notionalStr);
  }
  if (form.orderType == 'limit') {
    payload['limit_price'] = double.parse(form.limitPrice.trim());
  }
  return payload;
}

// ── Form state ────────────────────────────────────────────────────────────────

class OrderForm {
  const OrderForm({
    this.symbol = '',
    this.side = 'buy',
    this.orderType = 'market',
    this.qty = '',
    this.notional = '',
    this.limitPrice = '',
    this.tif = 'day',
    this.extendedHours = false,
  });

  final String symbol;
  final String side;
  final String orderType;
  final String qty;
  final String notional;
  final String limitPrice;
  final String tif;
  final bool extendedHours;

  OrderForm copyWith({
    String? symbol,
    String? side,
    String? orderType,
    String? qty,
    String? notional,
    String? limitPrice,
    String? tif,
    bool? extendedHours,
  }) {
    return OrderForm(
      symbol: symbol ?? this.symbol,
      side: side ?? this.side,
      orderType: orderType ?? this.orderType,
      qty: qty ?? this.qty,
      notional: notional ?? this.notional,
      limitPrice: limitPrice ?? this.limitPrice,
      tif: tif ?? this.tif,
      extendedHours: extendedHours ?? this.extendedHours,
    );
  }
}

// ── Bottom sheet ──────────────────────────────────────────────────────────────

void showManualOrderSheet(
  BuildContext context,
  WidgetRef ref,
  String instanceId,
) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder: (_) => _ManualOrderSheet(instanceId: instanceId, ref: ref),
  );
}

class _ManualOrderSheet extends StatefulWidget {
  const _ManualOrderSheet({required this.instanceId, required this.ref});
  final String instanceId;
  final WidgetRef ref;

  @override
  State<_ManualOrderSheet> createState() => _ManualOrderSheetState();
}

class _ManualOrderSheetState extends State<_ManualOrderSheet> {
  OrderForm _form = const OrderForm();
  bool _submitting = false;
  String? _error;

  final _symbolCtrl = TextEditingController();
  final _qtyCtrl = TextEditingController();
  final _notionalCtrl = TextEditingController();
  final _limitCtrl = TextEditingController();

  @override
  void dispose() {
    _symbolCtrl.dispose();
    _qtyCtrl.dispose();
    _notionalCtrl.dispose();
    _limitCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final current = _form.copyWith(
      symbol: _symbolCtrl.text,
      qty: _qtyCtrl.text,
      notional: _notionalCtrl.text,
      limitPrice: _limitCtrl.text,
    );
    final err = validateOrderForm(current);
    if (err != null) {
      setState(() => _error = err);
      return;
    }
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      final payload = buildOrderPayload(current);
      await widget.ref
          .read(liveStateProvider(widget.instanceId).notifier)
          .runCommand('submit_order', payload);
      if (mounted) Navigator.of(context).pop();
    } catch (e) {
      setState(() {
        _error = e.toString();
        _submitting = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final mediaInset = MediaQuery.of(context).viewInsets.bottom;
    final isLimit = _form.orderType == 'limit';
    final extHoursEnabled = isLimit && _form.tif == 'day';

    return Padding(
      padding: EdgeInsets.only(bottom: mediaInset),
      child: Container(
        constraints: BoxConstraints(
          maxHeight: MediaQuery.of(context).size.height * 0.92,
        ),
        decoration: BoxDecoration(
          color: AppColors.panel,
          borderRadius: const BorderRadius.vertical(top: Radius.circular(20)),
          border: Border.all(color: AppColors.stroke(AppColors.info)),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Header
            Container(
              padding: const EdgeInsets.fromLTRB(20, 16, 16, 16),
              decoration: BoxDecoration(
                border: Border(
                  bottom: BorderSide(color: AppColors.stroke(AppColors.info)),
                ),
              ),
              child: Row(
                children: [
                  Icon(symbol('add_shopping_cart'), color: AppColors.info, size: 20),
                  const SizedBox(width: 10),
                  Text('Manual Order',
                      style: AppTextStyles.h3.copyWith(color: AppColors.textHi)),
                  const Spacer(),
                  IconButton(
                    icon: Icon(symbol('close'), color: AppColors.textMuted, size: 20),
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                ],
              ),
            ),

            // Form body
            Flexible(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Symbol
                    _Label('SYMBOL'),
                    const SizedBox(height: 6),
                    _FieldBox(
                      child: TextField(
                        controller: _symbolCtrl,
                        autocorrect: false,
                        enableSuggestions: false,
                        textCapitalization: TextCapitalization.characters,
                        style: AppTextStyles.mono(14, color: AppColors.textHi),
                        decoration: const InputDecoration(
                          hintText: 'AAPL',
                          border: InputBorder.none,
                          contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                        ),
                      ),
                    ),

                    const SizedBox(height: 14),

                    // Side + Order Type
                    Row(
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              _Label('SIDE'),
                              const SizedBox(height: 6),
                              _DropField<String>(
                                value: _form.side,
                                items: const [
                                  DropdownMenuItem(value: 'buy', child: Text('Buy')),
                                  DropdownMenuItem(value: 'sell', child: Text('Sell')),
                                ],
                                onChanged: (v) => setState(() => _form = _form.copyWith(side: v)),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              _Label('ORDER TYPE'),
                              const SizedBox(height: 6),
                              _DropField<String>(
                                value: _form.orderType,
                                items: const [
                                  DropdownMenuItem(value: 'market', child: Text('Market')),
                                  DropdownMenuItem(value: 'limit', child: Text('Limit')),
                                ],
                                onChanged: (v) {
                                  setState(() {
                                    _form = _form.copyWith(orderType: v);
                                    // Auto-clear ext hours if order type incompatible.
                                    if (v != 'limit' && _form.extendedHours) {
                                      _form = _form.copyWith(extendedHours: false);
                                    }
                                  });
                                },
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),

                    const SizedBox(height: 14),

                    // Qty / Notional
                    Row(
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              _Label('QTY (SHARES)'),
                              const SizedBox(height: 6),
                              _FieldBox(
                                child: TextField(
                                  controller: _qtyCtrl,
                                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                                  style: AppTextStyles.mono(14, color: AppColors.textHi),
                                  decoration: const InputDecoration(
                                    hintText: '0',
                                    border: InputBorder.none,
                                    contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              _Label('NOTIONAL (\$)'),
                              const SizedBox(height: 6),
                              _FieldBox(
                                child: TextField(
                                  controller: _notionalCtrl,
                                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                                  style: AppTextStyles.mono(14, color: AppColors.textHi),
                                  decoration: const InputDecoration(
                                    hintText: '0.00',
                                    border: InputBorder.none,
                                    contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text(
                        'Fill qty OR notional, not both.',
                        style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
                      ),
                    ),

                    // Limit price (only if limit)
                    if (isLimit) ...[
                      const SizedBox(height: 14),
                      _Label('LIMIT PRICE'),
                      const SizedBox(height: 6),
                      _FieldBox(
                        child: TextField(
                          controller: _limitCtrl,
                          keyboardType: const TextInputType.numberWithOptions(decimal: true),
                          style: AppTextStyles.mono(14, color: AppColors.textHi),
                          decoration: const InputDecoration(
                            hintText: '0.00',
                            border: InputBorder.none,
                            contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                          ),
                        ),
                      ),
                    ],

                    const SizedBox(height: 14),

                    // TIF + Extended hours
                    Row(
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              _Label('TIF'),
                              const SizedBox(height: 6),
                              _DropField<String>(
                                value: _form.tif,
                                items: const [
                                  DropdownMenuItem(value: 'day', child: Text('Day')),
                                  DropdownMenuItem(value: 'gtc', child: Text('GTC')),
                                  DropdownMenuItem(value: 'ioc', child: Text('IOC')),
                                  DropdownMenuItem(value: 'fok', child: Text('FOK')),
                                  DropdownMenuItem(value: 'opg', child: Text('OPG')),
                                  DropdownMenuItem(value: 'cls', child: Text('CLS')),
                                ],
                                onChanged: (v) {
                                  setState(() {
                                    _form = _form.copyWith(tif: v);
                                    if (v != 'day' && _form.extendedHours) {
                                      _form = _form.copyWith(extendedHours: false);
                                    }
                                  });
                                },
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Opacity(
                            opacity: extHoursEnabled ? 1 : 0.4,
                            child: Row(
                              children: [
                                Transform.scale(
                                  scale: 1.1,
                                  child: Checkbox(
                                    value: _form.extendedHours,
                                    onChanged: extHoursEnabled
                                        ? (v) => setState(
                                              () => _form = _form.copyWith(extendedHours: v),
                                            )
                                        : null,
                                    activeColor: AppColors.info,
                                    side: BorderSide(color: AppColors.border),
                                  ),
                                ),
                                Flexible(
                                  child: Text(
                                    'Extended hours',
                                    style: AppTextStyles.micro.copyWith(
                                      color: extHoursEnabled
                                          ? AppColors.textMuted
                                          : AppColors.textFaint,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ],
                    ),

                    // Error
                    if (_error != null) ...[
                      const SizedBox(height: 12),
                      Container(
                        padding: const EdgeInsets.all(10),
                        decoration: BoxDecoration(
                          color: AppColors.fill(AppColors.danger),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: AppColors.stroke(AppColors.danger)),
                        ),
                        child: Text(
                          _error!,
                          style: AppTextStyles.micro.copyWith(color: AppColors.danger),
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),

            // Footer
            Container(
              padding: const EdgeInsets.fromLTRB(20, 12, 20, 20),
              decoration: BoxDecoration(
                border: Border(top: BorderSide(color: AppColors.border)),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: AppButton.ghost(
                      label: 'Cancel',
                      onPressed: () => Navigator.of(context).pop(),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: AppButton.semantic(
                      label: 'Submit Order',
                      color: AppColors.info,
                      busy: _submitting,
                      onPressed: _submitting ? null : _submit,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

class _Label extends StatelessWidget {
  const _Label(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: AppTextStyles.nano.copyWith(
        color: AppColors.textDim,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.5,
      ),
    );
  }
}

class _FieldBox extends StatelessWidget {
  const _FieldBox({required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: AppColors.panelAlt,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: AppColors.border),
      ),
      child: child,
    );
  }
}

class _DropField<T> extends StatelessWidget {
  const _DropField({
    required this.value,
    required this.items,
    required this.onChanged,
  });

  final T value;
  final List<DropdownMenuItem<T>> items;
  final ValueChanged<T?> onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: AppColors.panelAlt,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: AppColors.border),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<T>(
          value: value,
          items: items,
          onChanged: onChanged,
          dropdownColor: AppColors.panel,
          style: AppTextStyles.body.copyWith(color: AppColors.textMd),
          isExpanded: true,
        ),
      ),
    );
  }
}
