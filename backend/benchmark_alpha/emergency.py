"""Narrowly scoped, broker-reconciled reduce-only emergency executor (Task 6).

The ONLY order authority available during containment/KILL. It re-reads
broker positions immediately before acting, rejects buys and shorts, caps
every sell to currently-held quantity, and uses a deterministic risk-episode
client-order ID so a RethinkDB-outage action can later be recovered from
broker history (adversarial finding B04). It deliberately has no strategy,
allocator, candidate, or arbitrary-order interface.
"""


class ReduceOnlyEmergencyExecutor:
    def __init__(self, *, read_positions, submit_reduce, instance_id,
                 record_event=None):
        self._read_positions = read_positions
        self._submit_reduce = submit_reduce
        self._instance_id = str(instance_id)
        self._record_event = record_event

    @staticmethod
    def _client_order_id(instance_id, episode_id, symbol, qty):
        # Deterministic identity: instance, risk episode, symbol, side (always
        # sell), and quantity in micro-shares. Recoverable by prefix scan.
        return (f"emg-{instance_id}-{episode_id}-{symbol.lower()}-sell-"
                f"{int(round(qty * 1_000_000))}")

    def reduce_to_targets(self, risk_episode_id, targets):
        """Reduce held positions toward ``targets`` (symbol -> target qty).

        Increases are rejected; unknown symbols (not returned by the broker
        re-read) are skipped; sells are capped to held quantity. Returns the
        list of submitted actions."""
        positions = self._read_positions() or {}
        actions = []
        for symbol, target in (targets or {}).items():
            sym = str(symbol).upper()
            held = float(positions.get(sym, 0.0) or 0.0)
            if held <= 0:
                continue  # not broker-held: never invent or short a position
            target_qty = max(0.0, float(target or 0.0))
            if target_qty >= held:
                continue  # an increase (or no-op) is outside this authority
            sell_qty = min(held, held - target_qty)
            cid = self._client_order_id(
                self._instance_id, str(risk_episode_id), sym, sell_qty)
            broker_order_id = self._submit_reduce(sym, sell_qty, cid)
            action = {
                "symbol": sym,
                "side": "sell",
                "qty": sell_qty,
                "held_qty": held,
                "target_qty": target_qty,
                "client_order_id": cid,
                "broker_order_id": broker_order_id,
                "risk_episode_id": str(risk_episode_id),
            }
            degraded = False
            if self._record_event is not None:
                try:
                    self._record_event(action)
                except Exception:
                    # RethinkDB outage: the broker order history is the
                    # temporarily authoritative record; recovery backfills
                    # this exact action from the deterministic client ID.
                    degraded = True
            action["audit_degraded"] = degraded
            actions.append(action)
        return actions
