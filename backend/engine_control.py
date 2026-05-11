"""
Single RethinkDB table EngineControl: one document per engine.
Each engine listens to its document for control changes (running, config, etc.).
Server and API/CLI use this for all engine setup and config.
"""

from datetime import datetime, timezone

from rethinkdb import RethinkDB

r = RethinkDB()
DB_NAME = "IntelliStock"

ENGINE_CONTROL_TABLE = "EngineControl"

# Document ids (one per engine)
ENGINE_ID_BACKTEST = "backtest_engine"
ENGINE_ID_DAILY_DIGEST = "daily_digest_engine"
ENGINE_ID_DISCORD_BOT = "discord_bot"
ENGINE_ID_NEXUS_GRAPH = "nexus_graph_engine"
ENGINE_ID_PRICE = "price_engine"
ENGINE_ID_AI_BACKTEST = "ai_backtest_engine"
ENGINE_ID_DISCOVER = "discover_engine"

ALL_ENGINE_IDS = [
    ENGINE_ID_BACKTEST,
    ENGINE_ID_DAILY_DIGEST,
    ENGINE_ID_DISCORD_BOT,
    ENGINE_ID_NEXUS_GRAPH,
    ENGINE_ID_PRICE,
    ENGINE_ID_AI_BACKTEST,
    ENGINE_ID_DISCOVER,
]


def default_doc(engine_id: str) -> dict:
    """Default control document for each engine."""
    if engine_id == ENGINE_ID_BACKTEST:
        return {"id": engine_id, "running": False, "config": {}}
    if engine_id == ENGINE_ID_DAILY_DIGEST:
        return {
            "id": engine_id,
            "running": True,
            "send_now": False,
            "last_sent_at": None,
            "last_morning_at": None,
            "last_evening_at": None,
        }
    if engine_id == ENGINE_ID_DISCORD_BOT:
        return {"id": engine_id, "running": False, "config": {}}
    if engine_id == ENGINE_ID_NEXUS_GRAPH:
        return {
            "id": engine_id,
            "running": False,
            "start_phase": None,
            "end_phase": None,
            "selected_phases": None,
            "phase_selector_schema_version": 3,
            "force_bootstrap_rebuild": False,
            "auto_update_enabled": False,
            "auto_update_interval_hours": 168,
            "auto_update_start_phase": 3,
            "auto_update_end_phase": 14,
            "phase7_history_quarters": 1,
            "historical_mode_enabled": False,
            "historical_start_date": None,
            "historical_bootstrap_complete": False,
            "historical_coverage_end": None,
            "historical_phase_manifests": {},
            "last_historical_bootstrap_started_at": None,
            "last_historical_bootstrap_completed_at": None,
            "last_historical_bootstrap_duration_sec": None,
            "last_historical_bootstrap_status": None,
            "next_auto_update_at": None,
            "last_auto_update_at": None,
            "last_run_started_at": None,
            "last_run_completed_at": None,
            "last_run_status": None,
            "rebuild_requested_at": None,
            "rebuild_operation_active": False,
            "rebuild_operation_destructive": False,
            "rebuild_operation_step": None,
            "rebuild_operation_message": None,
            "rebuild_operation_current": None,
            "rebuild_operation_total": None,
            "rebuild_operation_unit": None,
            "rebuild_operation_started_at": None,
            "rebuild_operation_updated_at": None,
            "delete_operation_active": False,
            "delete_operation_step": None,
            "delete_operation_message": None,
            "delete_operation_current": None,
            "delete_operation_total": None,
            "delete_operation_unit": None,
            "delete_operation_started_at": None,
            "delete_operation_updated_at": None,
            "delete_operation_error": None,
            "delete_operation_selected_phases": None,
            "delete_operation_phase_rows": [],
        }
    if engine_id == ENGINE_ID_PRICE:
        return {
            "id": engine_id,
            "running": True,
            "terminate": False,
            "run_price_service": True,  # start broker when True
        }
    if engine_id == ENGINE_ID_AI_BACKTEST:
        return {
            "id": engine_id,
            "running": False,
            "paused": False,
            "last_run_date": None,
            "count_today": 0,
            "resume_at": None,
            "resume_at_cleared_at": None,
            "special_request": None,
        }
    if engine_id == ENGINE_ID_DISCOVER:
        return {"id": engine_id, "running": True, "terminate": False}
    return {"id": engine_id, "running": False, "config": {}}


def ensure_engine_control_table(conn):
    """Ensure EngineControl table exists and has a default document for each engine."""
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if ENGINE_CONTROL_TABLE not in tables:
        r.db(DB_NAME).table_create(ENGINE_CONTROL_TABLE).run(conn)
    for engine_id in ALL_ENGINE_IDS:
        doc = r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).get(engine_id).run(conn)
        if doc is None:
            r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).insert(default_doc(engine_id)).run(conn)


def get_engine_doc(conn, engine_id: str) -> dict | None:
    """Get the control document for an engine."""
    ensure_engine_control_table(conn)
    return r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).get(engine_id).run(conn)


def update_engine_doc(conn, engine_id: str, update: dict):
    """Update fields in an engine's control document. Merges with existing."""
    ensure_engine_control_table(conn)
    r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).get(engine_id).update(update).run(conn)


def engine_changes(conn, engine_id: str):
    """Changefeed for a single engine document."""
    return r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).get(engine_id).changes().run(conn)
