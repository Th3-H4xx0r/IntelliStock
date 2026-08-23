-- pg_partman's background worker is preloaded via shared_preload_libraries, and
-- it connects as the role named in pg_partman_bgw.role (default
-- "partman_maintainer"). Without this the worker starts, fails to log in
-- ("role does not exist"), and exits — so nobody keeps PriceHistory's monthly
-- partitions ahead of the writes.
--
-- Runs once, on an empty data directory, as the superuser.
CREATE SCHEMA IF NOT EXISTS partman;
CREATE EXTENSION IF NOT EXISTS pg_partman WITH SCHEMA partman;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'partman_maintainer') THEN
        CREATE ROLE partman_maintainer WITH LOGIN;
    END IF;
END
$$;

GRANT ALL ON SCHEMA partman TO partman_maintainer;
GRANT ALL ON ALL TABLES IN SCHEMA partman TO partman_maintainer;
GRANT ALL ON ALL FUNCTIONS IN SCHEMA partman TO partman_maintainer;
GRANT ALL ON SCHEMA public TO partman_maintainer;
