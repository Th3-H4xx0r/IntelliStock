# Copyright (c) 2020 Pranav Krishna — MIT License (see LICENSE)
# Custom colored logger for IntelliStock server output.

from datetime import datetime
import re
import sys
from colorama import init
from termcolor import colored

init()


# Secret redaction: scrub broker credentials from log output.
# - Alpaca keys start with PK (paper) or AK (live) followed by 18-char alnum.
# - Secrets are 40+ char alnum strings. We redact only after a known key-name
#   prefix to avoid false positives on SHA1 hashes.
# - Bearer tokens and explicit "APCA-API-*" header forms are always redacted.
_REDACT_PATTERNS = [
    # Header-style: APCA-API-KEY-ID: <value> or APCA-API-SECRET-KEY: <value>
    (re.compile(
        r"(APCA-API-(?:KEY-ID|SECRET-KEY)\s*[:=]\s*)([^\s'\"]+)",
        re.IGNORECASE,
    ), r"\1***REDACTED***"),
    # Bearer tokens
    (re.compile(r"(Bearer\s+)([A-Za-z0-9._\-]{20,})"), r"\1***REDACTED***"),
    # Standalone Alpaca paper/live key pattern - PK/AK + 16+ alnum.
    (re.compile(r"\b(?:PK|AK)[A-Z0-9]{16,}\b"), "***REDACTED***"),
    # OpenAI / Anthropic / Stripe / GitHub key prefixes. Match the literal
    # provider prefix so we don't accidentally redact every 40-char hash.
    (re.compile(r"\bsk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_\-]{32,}\b"), "***REDACTED***"),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{36,}\b"), "***REDACTED***"),
    (re.compile(r"\bpk_(?:live|test)_[A-Za-z0-9]{20,}\b"), "***REDACTED***"),
    # Google API keys (Gemini, Maps, etc.)
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b"), "***REDACTED***"),
    # NVIDIA NIM
    (re.compile(r"\bnvapi-[A-Za-z0-9_\-]{30,}\b"), "***REDACTED***"),
    # Discord bot tokens
    (re.compile(r"\b(?:MT[A-Z][A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{20,})\b"), "***REDACTED***"),
    # AWS access keys
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "***REDACTED***"),
    # JWT-shaped tokens (3 base64url segments separated by dots)
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), "***REDACTED***"),
    # Fernet ciphertext (well-known prefix)
    (re.compile(r"\bgAAAAA[A-Za-z0-9_\-=]{40,}\b"), "***REDACTED***"),
    # Connection strings with embedded credentials
    (re.compile(
        r"\b((?:bolt|mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis|rediss)://)"
        r"([^/\s:@]+):([^/\s@]+)@",
        re.IGNORECASE,
    ), r"\1***REDACTED***@"),
    # Explicit key=value / JSON form. The name alternation covers
    # broker creds, all the LLM provider keys we now ship with, and
    # the platform-internal secrets (Fernet key, JWT signing key).
    (re.compile(
        r"((?:alpaca[_-]?(?:key|secret|api[_-]?key|api[_-]?secret)"
        r"|robinhood[_-]?(?:access[_-]?token|refresh[_-]?token|device[_-]?token)"
        r"|openai[_-]?api[_-]?key|gemini[_-]?api[_-]?key|nvidia[_-]?api[_-]?key"
        r"|deepseek[_-]?api[_-]?key|azure[_-]?openai[_-]?api[_-]?key"
        r"|polygon[_-]?api[_-]?key|benzinga[_-]?api[_-]?key"
        r"|discord[_-]?bot[_-]?token|discord[_-]?bot[_-]?api[_-]?key"
        r"|jwt[_-]?secret|secret[_-]?auth[_-]?key|intellistock[_-]?cred[_-]?key"
        r"|api[_-]?key|api[_-]?secret"
        r"|refresh[_-]?token|access[_-]?token"
        r"|client[_-]?secret|password)"
        r"\s*[:=]\s*['\"]?)([^\s'\",}]{8,})",
        re.IGNORECASE,
    ), r"\1***REDACTED***"),
]


def _redact(text: str) -> str:
    """Mask secrets in a log line. Idempotent - safe to call multiple times."""
    if not text:
        return text
    for pattern, repl in _REDACT_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class IntelliStockLogger:
    """Logger with multi-context file + buffer sinks.

    Contexts let different subsystems (backtests, nexus_graph) tail their own
    log file + in-memory ring buffer without interfering. The legacy
    `set_backtest_*` methods are kept as thin wrappers over the "backtest"
    context for back-compat.
    """

    def __init__(self):
        # Multi-context sinks: ctx_name -> (file_obj, buffer_list, max_lines)
        self._ctx_files: dict[str, object] = {}
        self._ctx_buffers: dict[str, tuple[list, int]] = {}

    # --- Generic context API (preferred) ---

    def set_context_log_file(self, ctx: str, file_obj):
        """Attach (or detach with None) a file-like object to the given context.
        All future log() calls mirror to this file until replaced/closed."""
        if file_obj is None:
            self._ctx_files.pop(ctx, None)
        else:
            self._ctx_files[ctx] = file_obj

    def set_context_log_buffer(self, ctx: str, buffer_list, max_lines: int = 500):
        if buffer_list is None:
            self._ctx_buffers.pop(ctx, None)
        else:
            self._ctx_buffers[ctx] = (buffer_list, max(int(max_lines), 1))

    def clear_context_log_buffer(self, ctx: str):
        self._ctx_buffers.pop(ctx, None)

    def close_context_log_file(self, ctx: str):
        f = self._ctx_files.pop(ctx, None)
        if f is not None:
            try:
                f.flush()
                f.close()
            except Exception:
                pass

    # --- Legacy back-compat (delegates to "backtest" context) ---

    @property
    def _backtest_log_file(self):
        return self._ctx_files.get("backtest")

    @property
    def _backtest_log_buffer(self):
        entry = self._ctx_buffers.get("backtest")
        return entry[0] if entry else None

    @property
    def _backtest_log_max_lines(self):
        entry = self._ctx_buffers.get("backtest")
        return entry[1] if entry else 500

    def set_backtest_log_buffer(self, buffer_list, max_lines=500):
        """Set a list to capture log lines. Only last max_lines are kept. Pass None to stop capturing."""
        self.set_context_log_buffer("backtest", buffer_list, max_lines)

    def set_backtest_log_file(self, file_obj):
        """Set an open (line-buffered) file object. Pass None to stop."""
        self.set_context_log_file("backtest", file_obj)

    def clear_backtest_log_buffer(self):
        self.clear_context_log_buffer("backtest")

    def close_backtest_log_file(self):
        self.close_context_log_file("backtest")

    # --- Nexus graph engine shortcuts ---

    def set_nexus_graph_log_file(self, file_obj):
        self.set_context_log_file("nexus_graph", file_obj)

    def set_nexus_graph_log_buffer(self, buffer_list, max_lines=500):
        self.set_context_log_buffer("nexus_graph", buffer_list, max_lines)

    def clear_nexus_graph_log_buffer(self):
        self.clear_context_log_buffer("nexus_graph")

    def close_nexus_graph_log_file(self):
        self.close_context_log_file("nexus_graph")

    def _write_console_line(self, line, color=None):
        # Redact secrets from every console line before coloring / encoding.
        line = _redact(line)
        rendered = line if color is None else colored(line, color)
        try:
            print(rendered)
            return
        except UnicodeEncodeError:
            pass

        stream = sys.stdout
        encoding = getattr(stream, "encoding", None) or "utf-8"
        try:
            safe_rendered = rendered.encode(encoding, errors="replace").decode(encoding, errors="replace")
            stream.write(safe_rendered + "\n")
            stream.flush()
        except Exception:
            safe_line = line.encode("ascii", errors="replace").decode("ascii")
            stream.write(safe_line + "\n")
            stream.flush()

    def log(self, msg, color=None, service=None):
        """
        Print a message with optional timestamp, service tag, and color.

        Args:
            msg: Message to print.
            color: termcolor color name (e.g. 'red', 'green', 'yellow'). None for default.
            service: Optional service tag printed as [SERVICE] before the message (e.g. 'SERVER', 'STATUS').
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        parts = [f"[{timestamp}]"]
        if service is not None:
            parts.append(f"[{service}]")
        parts.append(str(msg))
        line = " ".join(parts)
        # Redact BEFORE writing anywhere so file logs and DB buffers never carry secrets.
        line = _redact(line)
        self._write_console_line(line, color=color)
        # Fan out to every attached context file (backtest, nexus_graph, ...)
        for ctx_file in list(self._ctx_files.values()):
            try:
                ctx_file.write(line + "\n")
            except Exception:
                pass
        # Fan out to every attached context buffer, trimming FIFO to max_lines.
        for buf_list, max_lines in list(self._ctx_buffers.values()):
            if isinstance(buf_list, list):
                buf_list.append(line)
                while len(buf_list) > max_lines:
                    buf_list.pop(0)


# Singleton instance for use across the app
intellistock_logger = IntelliStockLogger()
