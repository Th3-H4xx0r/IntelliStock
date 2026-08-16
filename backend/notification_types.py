"""The notification taxonomy: every operational Discord message in the codebase
classified into a type, grouped for the settings UI.

Each message that flows through the Discord outbox is classified into one of
these types (by an explicit key from ``notify()``, or by content/channel for
direct ``enqueue_discord_message`` callers). Per-type preferences then decide
Discord and/or iOS push. ``other`` is the safe fallback (Discord on) so an
unclassified message is never dropped.

Adding a new notification type: add an entry here (key + group + label +
prefixes) and it automatically appears in the settings UIs and becomes routable.
"""
from __future__ import annotations

# Ordered list — order is also the UI display order. ``prefixes`` are matched
# against the message content and embed title to classify direct sends.
NOTIFICATION_TYPES = [
    # --- Trading ---
    {"key": "order_submit", "group": "Trading", "label": "Order submitted",
     "desc": "An order was sent to the broker", "channel": "trades",
     "discord": True, "push": False, "prefixes": ["ORDER SUBMIT ["]},
    {"key": "order_fill", "group": "Trading", "label": "Order filled",
     "desc": "An order was filled", "channel": "trades",
     "discord": True, "push": True, "prefixes": ["ORDER FILL ["]},
    {"key": "order_reject", "group": "Trading", "label": "Order rejected",
     "desc": "The broker rejected an order", "channel": "trades",
     "discord": True, "push": True, "prefixes": ["ORDER REJECT ["]},
    {"key": "order_retry", "group": "Trading", "label": "Order retried",
     "desc": "An order is being retried after a recoverable reject",
     "channel": "trades", "discord": True, "push": False, "prefixes": ["ORDER RETRY ["]},

    # --- Strategy ---
    {"key": "strategy_start", "group": "Strategy", "label": "Strategy start",
     "desc": "A strategy fired its first run of the session", "channel": "trades",
     "discord": True, "push": False, "prefixes": ["STRATEGY START ["]},
    {"key": "strategy_error", "group": "Strategy", "label": "Strategy error",
     "desc": "An unrecoverable strategy error occurred", "channel": "trades",
     "discord": True, "push": True, "prefixes": ["STRATEGY ERROR ["]},

    # --- Kalshi ---
    {"key": "kalshi_edge_flag", "group": "Kalshi", "label": "Kalshi edge flagged",
     "desc": "A +EV Kalshi contract cleared the edge threshold", "channel": "trades",
     "discord": True, "push": False, "prefixes": ["KALSHI EDGE ["]},
    {"key": "kalshi_fill", "group": "Kalshi", "label": "Kalshi fill",
     "desc": "A Kalshi order was filled", "channel": "trades",
     "discord": True, "push": True, "prefixes": ["KALSHI FILL ["]},
    {"key": "kalshi_settlement", "group": "Kalshi", "label": "Kalshi settlement",
     "desc": "A Kalshi contract settled (resolved)", "channel": "trades",
     "discord": True, "push": True, "prefixes": ["KALSHI SETTLE ["]},
    {"key": "kalshi_risk_block", "group": "Kalshi", "label": "Kalshi risk block",
     "desc": "A risk cap blocked a Kalshi trade", "channel": "trades",
     "discord": True, "push": False, "prefixes": ["KALSHI RISK ["]},
    {"key": "kalshi_budget_low", "group": "Kalshi", "label": "Kalshi odds budget low",
     "desc": "The OddsPapi monthly request budget is nearly exhausted", "channel": "notifications",
     "discord": True, "push": False, "prefixes": ["KALSHI BUDGET ["]},
    {"key": "kalshi_runtime", "group": "Kalshi", "label": "Kalshi runtime crashed",
     "desc": "The Kalshi instance runtime crashed unexpectedly", "channel": "notifications",
     "discord": True, "push": True, "prefixes": ["KALSHI RUNTIME ["]},

    # --- Risk & Halts ---
    {"key": "halt", "group": "Risk & Halts", "label": "Halt",
     "desc": "Live trading was halted", "channel": "notifications",
     "discord": True, "push": True, "prefixes": ["HALT ["]},
    {"key": "drawdown_halt", "group": "Risk & Halts", "label": "Drawdown halt",
     "desc": "A drawdown risk-off guard tripped", "channel": "trades",
     "discord": True, "push": True, "prefixes": ["DRAWDOWN HALT ["]},
    {"key": "drawdown_resumed", "group": "Risk & Halts", "label": "Drawdown resumed",
     "desc": "Buys resumed after a drawdown halt cleared", "channel": "trades",
     "discord": True, "push": False, "prefixes": ["DRAWDOWN RESUMED ["]},
    {"key": "crash_loop", "group": "Risk & Halts", "label": "Crash loop",
     "desc": "The broker subprocess entered a crash loop", "channel": "notifications",
     "discord": True, "push": True, "prefixes": ["CRASH LOOP ["]},
    {"key": "instance_crash", "group": "Risk & Halts", "label": "Instance crashed",
     "desc": "An instance process died (not an operator Stop) and was held open so its "
             "logs stay viewable", "channel": "notifications",
     "discord": True, "push": True, "prefixes": ["INSTANCE CRASH ["]},

    # --- Broker Health ---
    {"key": "broker_boot", "group": "Broker Health", "label": "Broker boot",
     "desc": "A broker subprocess started", "channel": "notifications",
     "discord": True, "push": False, "prefixes": ["Broker boot |"]},
    {"key": "monitor_cycle", "group": "Broker Health", "label": "Monitor cycle",
     "desc": "Periodic broker monitor heartbeat", "channel": "notifications",
     "discord": True, "push": False, "prefixes": ["Monitor cycle |"]},

    # --- Backtests ---
    {"key": "backtest", "group": "Backtests", "label": "Backtest status",
     "desc": "Backtest queued / running / finished / failed / stopped",
     "channel": "backtests", "discord": True, "push": False,
     "prefixes": ["Backtest Queued", "Backtest Running", "Backtest Failed",
                  "Backtest Finished", "Backtest Stopped"]},

    # --- AI Agent ---
    {"key": "agent_run", "group": "AI Agent", "label": "AI agent runs",
     "desc": "AI backtest-agent progress and results", "channel": "ai-backtest-agent",
     "discord": True, "push": False,
     "prefixes": ["Best Strategies Found", "Strategy ", "Status:"]},

    # --- Daily Briefs ---
    {"key": "brief_morning", "group": "Daily Briefs", "label": "Morning brief",
     "desc": "Pre-market morning brief", "channel": "briefs",
     "discord": True, "push": False, "prefixes": ["Morning Brief"]},
    {"key": "brief_evening", "group": "Daily Briefs", "label": "Evening digest",
     "desc": "Post-market evening digest", "channel": "briefs",
     "discord": True, "push": False, "prefixes": ["Evening Digest"]},

    # --- Infrastructure ---
    {"key": "claude_rate_limit", "group": "Infrastructure", "label": "Claude rate limit",
     "desc": "An LLM provider hit a rate limit", "channel": "notifications",
     "discord": True, "push": True, "prefixes": ["Claude Code rate limit"]},

    # --- Self-Learning ---
    # Approval requests PUSH by default and the rest do not: a proposal waiting
    # at a live rung holds indefinitely, so a missed notification stalls the
    # loop, whereas a finding can wait for the digest.
    {"key": "learning_finding", "group": "Self-Learning", "label": "Learning finding",
     "desc": "The self-learning guards raised a defect or insight",
     "channel": "notifications", "discord": True, "push": False,
     "prefixes": ["LEARNING FINDING ["]},
    {"key": "learning_proposal", "group": "Self-Learning", "label": "Approval requested",
     "desc": "A change is waiting for your approval before it is applied",
     "channel": "notifications", "discord": True, "push": True,
     "prefixes": ["LEARNING PROPOSAL ["]},
    {"key": "learning_applied", "group": "Self-Learning", "label": "Change applied",
     "desc": "The subsystem applied a change at some rung of the ladder",
     "channel": "notifications", "discord": True, "push": True,
     "prefixes": ["LEARNING APPLIED ["]},
    {"key": "learning_demoted", "group": "Self-Learning", "label": "Change rolled back",
     "desc": "A change was demoted and reverted automatically",
     "channel": "notifications", "discord": True, "push": True,
     "prefixes": ["LEARNING DEMOTED ["]},
    {"key": "learning_inert", "group": "Self-Learning", "label": "Change proved inert",
     "desc": "A treatment changed nothing — not testable as specified",
     "channel": "notifications", "discord": True, "push": False,
     "prefixes": ["LEARNING INERT ["]},
    {"key": "learning_budget", "group": "Self-Learning", "label": "Learning budget",
     "desc": "The self-learning spend ceiling was reached",
     "channel": "notifications", "discord": True, "push": True,
     "prefixes": ["LEARNING BUDGET ["]},

    # --- Fallback (hidden default) — never dropped ---
    {"key": "other", "group": "Other", "label": "Other notifications",
     "desc": "Anything not otherwise categorized", "channel": "notifications",
     "discord": True, "push": False, "prefixes": []},
]

NOTIFICATION_TYPE_KEYS = tuple(t["key"] for t in NOTIFICATION_TYPES)
_TYPE_BY_KEY = {t["key"]: t for t in NOTIFICATION_TYPES}


def type_for_key(key):
    return _TYPE_BY_KEY.get(key)


# Push is opt-in by default for everything EXCEPT these curated high-signal keys.
# A trading instance going dark is the one alert worth a phone push out of the box;
# the operator can still toggle it off per-channel in the settings screen.
_PUSH_ON_BY_DEFAULT = {"instance_crash"}


def default_routing():
    """The default routing matrix: every type's Discord ON, push OFF except the
    curated ``_PUSH_ON_BY_DEFAULT`` set (push on out of the box)."""
    return {t["key"]: {"discord": bool(t["discord"]),
                       "push": t["key"] in _PUSH_ON_BY_DEFAULT}
            for t in NOTIFICATION_TYPES}


def public_types():
    """Ordered taxonomy metadata for the settings UIs (key/group/label/desc),
    excluding the hidden ``other`` fallback. Order = display order."""
    return [
        {"key": t["key"], "group": t["group"], "label": t["label"], "desc": t["desc"]}
        for t in NOTIFICATION_TYPES if t["key"] != "other"
    ]


def groups_in_order():
    """Group names in first-appearance order (UI section order), excluding the
    hidden ``Other`` fallback."""
    seen = []
    for t in NOTIFICATION_TYPES:
        g = t["group"]
        if g != "Other" and g not in seen:
            seen.append(g)
    return seen


def _first_token_text(content, embed):
    text = content if isinstance(content, str) else ""
    title = ""
    if isinstance(embed, dict):
        title = str(embed.get("title") or "")
    return text, title


def classify(channel=None, content=None, embed=None, notif_key=None):
    """Resolve a message to a notification type key.

    An explicit ``notif_key`` wins (passed by ``notify()``). Otherwise match the
    content/embed-title against each type's prefixes; fall back to ``other``.
    Never raises.
    """
    try:
        if notif_key and notif_key in _TYPE_BY_KEY:
            return notif_key
        text, title = _first_token_text(content, embed)
        for t in NOTIFICATION_TYPES:
            for p in t["prefixes"]:
                if not p:
                    continue
                if text.startswith(p) or title.startswith(p) or (p in text) or (p in title):
                    return t["key"]
    except Exception:
        pass
    return "other"
