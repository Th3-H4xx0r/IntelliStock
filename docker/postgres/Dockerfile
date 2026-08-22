# PostgreSQL 17 + pg_partman, for the IntelliStock store.
#
# pg_partman's background worker keeps PriceHistory's monthly partitions ahead
# of the write head (premake 3). There is deliberately NO default partition:
# a default partition forbids DETACH CONCURRENTLY, which also cannot run
# inside a transaction block.
FROM postgres:17

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-17-partman \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# lz4 TOAST compression is a disk win, not a correctness one. If this image's
# build lacks it, `SHOW default_toast_compression` reports pglz and the
# compose flag is a no-op -- check it in the cutover pre-flight.
