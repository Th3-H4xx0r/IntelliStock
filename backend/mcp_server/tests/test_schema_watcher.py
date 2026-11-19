"""The schema watcher: initial load with backoff, /health polling, and rejection of bad documents."""
import copy
import json
import logging
from pathlib import Path

import httpx
import pytest

from fake_api import HEALTH_PATH, OPENAPI_PATH, FakeApi, schema_hash
from mcp_server.schema_watcher import SchemaWatcher

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "openapi_sample.json"


class _Stop(Exception):
    """Raised by a fake sleep to end run()'s otherwise endless loop."""


def _sample():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _with_extra_operation(doc):
    """doc plus one more read operation, so its paths hash differently."""
    changed = copy.deepcopy(doc)
    changed["paths"]["/positions"] = {"get": {
        "summary": "List positions", "operationId": "list_positions",
        "responses": {"200": {"description": "Successful Response"}}, "x-mcp": {"class": "read"}}}
    return changed


def _connect_error(request):
    raise httpx.ConnectError("connection refused", request=request)


def _openapi_fetches(fake):
    return [request for request in fake.requests if request.url.path == OPENAPI_PATH]


def _errors(caplog):
    return [record.getMessage() for record in caplog.records
            if record.name == "mcp_server.schema_watcher" and record.levelno >= logging.ERROR]


@pytest.fixture
def fake():
    return FakeApi(openapi=_sample())


@pytest.fixture
async def api(fake):
    client = fake.client()
    yield client
    await client.aclose()


@pytest.fixture
def loaded():
    return []


@pytest.fixture
def watcher(api, loaded):
    return SchemaWatcher(api, loaded.append)


async def test_watcher_refreshes_on_hash_change(fake, watcher, loaded):
    assert watcher.current is None
    assert await watcher.refresh() is True
    first = watcher.current
    assert loaded == [first]
    assert first.schema_hash == schema_hash(_sample()["paths"])
    assert "get_instance" in first.operations

    await watcher.tick()  # /health reports the loaded hash: nothing to fetch
    assert loaded == [first]
    assert len(_openapi_fetches(fake)) == 1

    changed = _with_extra_operation(_sample())
    fake.set_openapi(changed)
    await watcher.tick()
    assert len(loaded) == 2 and watcher.current is loaded[1]
    assert watcher.current.schema_hash == schema_hash(changed["paths"]) != first.schema_hash
    assert "list_positions" in watcher.current.operations
    assert len(_openapi_fetches(fake)) == 2

    await watcher.tick()
    assert len(loaded) == 2 and len(_openapi_fetches(fake)) == 2
    # Neither endpoint is fetched on anyone's behalf.
    assert all("x-mcp-acting-user" not in request.headers for request in fake.requests)


async def test_tick_loads_when_nothing_is_loaded_yet(fake, watcher, loaded):
    await watcher.tick()
    assert len(loaded) == 1 and watcher.current is loaded[0]


async def test_watcher_keeps_old_catalog_on_api_failure(fake, watcher, loaded):
    assert await watcher.refresh() is True
    first = watcher.current
    fake.set_openapi(_with_extra_operation(_sample()))  # /health now reports a new hash

    fake.set_route("GET", OPENAPI_PATH, 0, _connect_error)
    await watcher.tick()
    assert await watcher.refresh() is False
    fake.set_route("GET", OPENAPI_PATH, 503, {"detail": "starting"})
    assert await watcher.refresh() is False
    fake.set_route("GET", OPENAPI_PATH, 200, b"<html>not json</html>")
    assert await watcher.refresh() is False
    assert watcher.current is first and loaded == [first]


async def test_watcher_keeps_old_catalog_on_invalid_document(fake, watcher, loaded, caplog):
    assert await watcher.refresh() is True
    first = watcher.current

    not_paths = {"openapi": "3.1.0", "paths": ["/x"], "x-mcp-schema-hash": schema_hash(["/x"])}
    bad_components = {"openapi": "3.1.0", "paths": {}, "components": [], "x-mcp-schema-hash": schema_hash({})}
    for doc in (not_paths, bad_components):
        fake.set_route("GET", OPENAPI_PATH, 200, doc)
        assert await watcher.refresh() is False
    assert watcher.current is first and loaded == [first]
    assert len(_errors(caplog)) == 2


async def test_watcher_rejects_doc_with_wrong_hash(fake, watcher, loaded, caplog):
    assert await watcher.refresh() is True
    first = watcher.current

    altered = _with_extra_operation(_sample())
    altered["x-mcp-schema-hash"] = schema_hash(_sample()["paths"])  # the old hash on new paths
    fake.set_openapi(altered)
    assert await watcher.refresh() is False
    assert watcher.current is first and loaded == [first]
    assert any("x-mcp-schema-hash" in message for message in _errors(caplog))

    unpublished = _with_extra_operation(_sample())
    fake.set_route("GET", OPENAPI_PATH, 200, unpublished)  # served with no x-mcp-schema-hash at all
    assert await watcher.refresh() is False
    assert watcher.current is first and loaded == [first]

    fake.set_route("GET", OPENAPI_PATH, 200, {**unpublished, "x-mcp-schema-hash": schema_hash(unpublished["paths"])})
    assert await watcher.refresh() is True
    assert "list_positions" in watcher.current.operations


async def test_watcher_skips_tick_when_health_unavailable(fake, watcher, loaded):
    for status, body in ((0, _connect_error), (503, {"detail": "starting"}), (200, {"status": "ok"}),
                         (200, {"status": "ok", "mcp_schema_hash": 7})):
        fake.set_route("GET", HEALTH_PATH, status, body)
        await watcher.tick()
    assert _openapi_fetches(fake) == []
    assert watcher.current is None and loaded == []

    fake.set_route("GET", HEALTH_PATH, 200, {"status": "ok", "mcp_schema_hash": schema_hash(_sample()["paths"])})
    await watcher.tick()
    assert len(loaded) == 1


async def test_watcher_initial_backoff_until_api_up(fake, api, loaded):
    state = {"up": False}
    served = {**_sample(), "x-mcp-schema-hash": schema_hash(_sample()["paths"])}

    def openapi(request):
        if not state["up"]:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json=served)

    fake.set_route("GET", OPENAPI_PATH, 0, openapi)
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 3:
            state["up"] = True  # the API comes back during the third backoff
        if seconds == 999:
            raise _Stop

    watcher = SchemaWatcher(api, loaded.append, poll_s=999, sleep=sleep)
    with pytest.raises(_Stop):
        await watcher.run()
    assert slept == [1, 2, 4, 999]
    assert len(_openapi_fetches(fake)) == 4
    assert len(loaded) == 1 and watcher.current is loaded[0]


async def test_initial_backoff_caps_at_60_seconds(fake, api, loaded):
    fake.set_route("GET", OPENAPI_PATH, 0, _connect_error)
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 9:
            raise _Stop

    with pytest.raises(_Stop):
        await SchemaWatcher(api, loaded.append, sleep=sleep).run()
    assert slept == [1, 2, 4, 8, 16, 32, 60, 60, 60]
    assert loaded == []


async def test_run_polls_and_survives_a_failing_tick(fake, api, caplog):
    calls = []

    def on_catalog(catalog):
        calls.append(catalog)
        if len(calls) == 2:
            raise RuntimeError("registry rejected the catalog")

    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 1:
            fake.set_openapi(_with_extra_operation(_sample()))  # a new hash for the first poll
        if len(slept) == 3:
            raise _Stop

    watcher = SchemaWatcher(api, on_catalog, poll_s=60, sleep=sleep)
    with pytest.raises(_Stop):
        await watcher.run()
    assert slept == [60, 60, 60]
    # The first poll's load raised and was logged; the next poll retried it and the catalog moved on.
    assert len(calls) == 3
    assert "list_positions" in watcher.current.operations
    logged = [record.exc_info[1] for record in caplog.records
              if record.name == "mcp_server.schema_watcher" and record.exc_info]
    assert [str(exc) for exc in logged] == ["registry rejected the catalog"]
