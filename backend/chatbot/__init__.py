"""IntelliStock integrated chatbot.

Persists per-user conversations in RethinkDB, runs an LLM↔tool-call loop
against the existing API surface, and streams structured rich blocks
(charts / tables / stat cards / markdown) back to the frontend dock.
"""
