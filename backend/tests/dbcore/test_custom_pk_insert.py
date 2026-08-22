"""Auto-minted ids belong to ``id``-keyed tables ONLY.

RethinkDB minted a uuid4 for a document with no primary key and returned it
in ``generated_keys`` -- but only for a table keyed on ``id``. A document
missing a table's CUSTOM primary key raised there ("Primary key
`fixture_key` not found in document"). store.insert minted for every table,
so a kalshi document that lost its key was written under a uuid nothing ever
looks up: silent data loss where RethinkDB was loud.

Both backends share ``_row_id_or_generate``, and every test runs against
each of them, because a FakeStore that mints where the real store rejects
would let a unit test green-light a write production refuses.
"""
import pytest

from db import schema
from db.errors import StoreError
from db.fake import FakeStore
from db.store import WRITE_CHUNK

from .conftest import PG_TEST_DSN

# DiscordOutbox keys on id; kalshi_markets keys on market_ticker;
# PriceHistory keys on id but carries a COMPOUND pk (ticker, ts, id) -- the
# distinction the mint rule turns on is pk_field, not pk.
_TABLES = ["DiscordOutbox", "kalshi_markets", "PriceHistory"]

_PH = {"ticker": "T.AAPL", "price": 1.25,
       "timestamp": "2026-08-22T14:30:00.000Z", "type": "minute"}


@pytest.fixture
def pg_schema_or_skip(request):
    """Only the 'real' parametrisation needs a database schema."""
    if request.node.callspec.params.get("s") == "real":
        yield request.getfixturevalue("pg_schema")
    else:
        yield None


@pytest.fixture(params=["fake", "real"])
def s(request, pg_schema_or_skip):
    if request.param == "fake":
        return FakeStore()
    if not PG_TEST_DSN:
        pytest.skip("PG_TEST_DSN not set")
    from db import store as real
    schema.ensure_schema(tables=_TABLES)
    return real


# ------------------------------------------------- an id-keyed table mints --
def test_an_id_keyed_table_mints_a_uuid_for_a_document_with_no_id(s):
    res = s.insert("DiscordOutbox", {"channel": "ops", "body": "hi"})
    assert (res["inserted"], res["errors"]) == (1, 0)
    assert len(res["generated_keys"]) == 1
    key = res["generated_keys"][0]
    # ReQL wrote the generated value INTO the document, so a read sees it.
    assert s.get("DiscordOutbox", key) == {"id": key, "channel": "ops",
                                           "body": "hi"}


def test_a_compound_pk_table_still_mints_because_its_pk_field_is_id(s):
    """PriceHistory's pk is (ticker, ts, id) but its pk_field is ``id``, and
    priceBroker has never supplied one. The rule keys on pk_field."""
    res = s.insert("PriceHistory", dict(_PH))
    assert (res["inserted"], res["errors"]) == (1, 0)
    assert len(res["generated_keys"]) == 1


def test_a_supplied_id_is_never_replaced_by_a_mint(s):
    res = s.insert("DiscordOutbox", {"id": "given", "body": "hi"})
    assert res["generated_keys"] == []
    assert s.get("DiscordOutbox", "given")["body"] == "hi"


# ------------------------------------------- a custom-pk table must NOT mint --
def test_a_custom_pk_table_missing_its_key_raises_and_writes_nothing(s):
    with pytest.raises(StoreError) as exc:
        s.insert("kalshi_markets", {"title": "Arsenal vs Chelsea"})
    assert "market_ticker" in str(exc.value)
    assert s.count("kalshi_markets") == 0


def test_a_custom_pk_table_with_its_key_present_inserts_under_that_key(s):
    res = s.insert("kalshi_markets", {"market_ticker": "KXSOCCER-ARS",
                                      "title": "Arsenal vs Chelsea"})
    assert (res["inserted"], res["errors"]) == (1, 0)
    assert res["generated_keys"] == []
    row = s.get("kalshi_markets", "KXSOCCER-ARS")
    assert row["title"] == "Arsenal vs Chelsea"


def test_a_null_custom_pk_is_as_missing_as_an_absent_one(s):
    with pytest.raises(StoreError):
        s.insert("kalshi_markets", {"market_ticker": None, "title": "x"})
    assert s.count("kalshi_markets") == 0


# --------------------------------------------------------- batch semantics --
def test_one_keyless_document_rejects_the_WHOLE_batch(s):
    """Same rule as the NaN batch (test_review_fixes.py::
    test_a_client_side_rejection_writes_no_part_of_the_batch): a missing
    primary key is a CLIENT-side rejection -- RethinkDB raised before
    anything reached the server -- so insert raises and no row of the batch
    lands, not even the documents that precede the bad one.

    The batch deliberately spans more than one WRITE_CHUNK: validating
    inside the write loop instead would leave the earlier chunk committed.
    """
    docs = ([{"market_ticker": "OK%d" % i} for i in range(WRITE_CHUNK)]
            + [{"title": "no ticker"}, {"market_ticker": "LAST"}])
    with pytest.raises(StoreError) as exc:
        s.insert("kalshi_markets", docs)
    assert "market_ticker" in str(exc.value)
    assert s.count("kalshi_markets") == 0


def test_a_wholly_valid_batch_still_lands(s):
    """The guard rejects the keyless document, not the batch shape."""
    docs = [{"market_ticker": "OK%d" % i} for i in range(WRITE_CHUNK + 2)]
    res = s.insert("kalshi_markets", docs)
    assert (res["inserted"], res["errors"]) == (WRITE_CHUNK + 2, 0)
    assert s.count("kalshi_markets") == WRITE_CHUNK + 2


def test_a_mixed_batch_on_an_id_keyed_table_mints_only_the_keyless_ones(s):
    """The id-keyed path is unchanged: partial minting inside a batch works,
    and generated_keys reports only what was actually minted."""
    res = s.insert("DiscordOutbox", [{"id": "a"}, {"body": "minted"},
                                     {"id": "c"}])
    assert (res["inserted"], res["errors"]) == (3, 0)
    assert len(res["generated_keys"]) == 1
    assert s.get("DiscordOutbox", res["generated_keys"][0])["body"] == "minted"
