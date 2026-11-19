"""Keeps the catalog in step with the API's schema (contract section 4).

run() loads the catalog at startup, retrying with a 1, 2, 4 ... 60 s backoff
while the API is down or redeploying; the server serves sign-in meanwhile and
the tools say the catalog is loading. It then polls /health every poll_s
seconds and refetches /internal/mcp/openapi.json when the mcp_schema_hash
there differs from the loaded catalog's. Neither endpoint needs an acting user.

A fetched document is accepted only when its published x-mcp-schema-hash
equals canonical_paths_hash(doc["paths"]). A missing or different hash, an
invalid document or an unreachable API leaves the previous catalog in place.
"""
import logging
from typing import Awaitable, Callable, Optional

import anyio

from mcp_server.api_client import ApiClient, ApiUnavailable
from mcp_server.catalog import Catalog, build_catalog, canonical_paths_hash

INITIAL_BACKOFF_S = 1.0
MAX_BACKOFF_S = 60.0

logger = logging.getLogger(__name__)


class SchemaWatcher:
    def __init__(self, api: ApiClient, on_catalog: Callable[[Catalog], None], *, poll_s: float = 60.0,
                 sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> None:
        self._api = api
        self._on_catalog = on_catalog
        self._poll_s = poll_s
        self._sleep = sleep
        self.current: Optional[Catalog] = None

    async def refresh(self) -> bool:
        """Fetch, check and load the schema; False, keeping the current catalog, when any step fails.

        An exception from on_catalog propagates, and current stays as it was.
        """
        try:
            doc = await self._api.get_openapi()
        except ApiUnavailable as exc:
            logger.warning("Could not fetch the API schema: %s (correlation id %s); keeping the current catalog.",
                           exc, exc.correlation_id)
            return False
        paths = doc.get("paths")
        if not isinstance(paths, dict):
            logger.error("Rejected the API schema: 'paths' is missing or not an object; keeping the current catalog.")
            return False
        computed = canonical_paths_hash(paths)
        published = doc.get("x-mcp-schema-hash")
        if published != computed:
            logger.error("Rejected the API schema: its x-mcp-schema-hash %.80r does not match %s, the hash of its "
                         "paths; keeping the current catalog.", published, computed)
            return False
        try:
            catalog = build_catalog(doc)
        except Exception as exc:  # any malformed document keeps the current catalog
            logger.error("Rejected the API schema: %s: %s; keeping the current catalog.", type(exc).__name__, exc)
            return False
        self._on_catalog(catalog)
        self.current = catalog
        logger.info("Loaded the endpoint catalog: %d operations, %d skipped, schema hash %s.",
                    len(catalog.operations), len(catalog.skipped), catalog.schema_hash)
        for entry in catalog.skipped:
            logger.warning("Skipped API operation %s", entry)
        return True

    async def tick(self) -> None:
        """One poll: refresh when /health reports a hash other than the loaded one; nothing when it reports none."""
        reported = await self._api.health_schema_hash()
        if reported is None:
            return
        if self.current is not None and reported == self.current.schema_hash:
            return
        if await self.refresh() and reported != self.current.schema_hash:
            logger.warning("/health reports schema hash %s but the schema served hashes to %s; it will be "
                           "refetched on every poll until they agree.", reported, self.current.schema_hash)

    async def run(self) -> None:
        """Load the catalog, retrying with backoff, then poll forever; a failed load or poll is logged, never raised."""
        delay = INITIAL_BACKOFF_S
        while True:
            try:
                if await self.refresh():
                    break
            except Exception:
                logger.exception("Loading the endpoint catalog failed.")
            await self._sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF_S)
        while True:
            await self._sleep(self._poll_s)
            try:
                await self.tick()
            except Exception:
                logger.exception("Polling the API schema failed.")
