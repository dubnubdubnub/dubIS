"""Concurrency tests: the inventory read path must be lock-safe.

The SQLite cache connection is a single shared object (created with
``check_same_thread=False``) used concurrently by the pywebview UI thread,
the FastAPI threadpool, and the PnP daemon thread. Reads that can trigger a
full rebuild (heavy SQLite writes) — and reads that query mid-mutation state
on the same connection — must hold ``InventoryApi._lock`` so they cannot
interleave with a mutation holding that lock.
"""

import sqlite3
import threading

import pytest

import cache_db
import domain.inventory
import domain.pricing
from tests.python.helpers import make_part as _make_part
from tests.python.helpers import write_ledger as _write_ledger


def _lock_held_by_another_thread(lock) -> bool:
    """True if *lock* cannot be acquired from a fresh thread (i.e. it is held).

    RLock is reentrant per-thread, so the probe must run on its own thread:
    a non-blocking acquire there fails iff some other thread holds the lock.
    """
    result = {}

    def probe():
        acquired = lock.acquire(blocking=False)
        result["acquired"] = acquired
        if acquired:
            lock.release()

    t = threading.Thread(target=probe)
    t.start()
    t.join(10)
    assert "acquired" in result, "lock probe thread did not finish"
    return not result["acquired"]


class TestReadPathHoldsLock:
    def test_load_organized_holds_lock(self, api, monkeypatch):
        """_load_organized can trigger a full rebuild (SQLite writes) via
        load_or_rebuild, so it must run under the API lock."""
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        observed = {}
        real = domain.inventory.load_or_rebuild

        def probe(**kwargs):
            observed["locked"] = _lock_held_by_another_thread(api._lock)
            return real(**kwargs)

        monkeypatch.setattr(domain.inventory, "load_or_rebuild", probe)
        api._load_organized()
        assert observed["locked"] is True

    def test_get_price_summary_holds_lock(self, api, monkeypatch):
        """Reads on the shared connection must not observe a mutation's
        uncommitted mid-transaction state — they take the same lock."""
        observed = {}
        real = domain.pricing.get_price_summary

        def probe(conn, events_dir, part_key):
            observed["locked"] = _lock_held_by_another_thread(api._lock)
            return real(conn, events_dir, part_key)

        monkeypatch.setattr(domain.pricing, "get_price_summary", probe)
        api.get_price_summary("C100000")
        assert observed["locked"] is True

    def test_get_sourced_distributors_holds_lock(self, api, monkeypatch):
        observed = {}
        real = domain.pricing.get_sourced_distributors

        def probe(conn, purchase_csv, part_key):
            observed["locked"] = _lock_held_by_another_thread(api._lock)
            return real(conn, purchase_csv, part_key)

        monkeypatch.setattr(domain.pricing, "get_sourced_distributors", probe)
        api.get_sourced_distributors("C100000")
        assert observed["locked"] is True

    def test_record_fetched_prices_holds_lock(self, api, monkeypatch):
        """record_fetched_prices WRITES to the shared connection — it must
        hold the lock like every other mutation."""
        observed = {}
        real = domain.pricing.record_fetched_prices

        def probe(conn, events_dir, part_key, distributor, price_tiers, **kwargs):
            observed["locked"] = _lock_held_by_another_thread(api._lock)
            return real(conn, events_dir, part_key, distributor, price_tiers, **kwargs)

        monkeypatch.setattr(domain.pricing, "record_fetched_prices", probe)
        api.record_fetched_prices("C100000", "lcsc", [{"qty": 1, "price": 0.1}])
        assert observed["locked"] is True


class TestReadBlocksDuringMutation:
    def test_read_waits_for_in_flight_mutation(self, api, monkeypatch):
        """A GET-style read (_load_organized) started while a mutation holds
        the lock must not complete until the mutation releases it."""
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        api.rebuild_inventory()  # prime the cache so adjust takes the fast branch

        in_mutation = threading.Event()
        release_mutation = threading.Event()
        real_verify = cache_db.verify_parts

        def blocking_verify(*args, **kwargs):
            in_mutation.set()
            assert release_mutation.wait(10), "test never released the mutation"
            return real_verify(*args, **kwargs)

        monkeypatch.setattr(cache_db, "verify_parts", blocking_verify)

        mut_err = []

        def mutate():
            try:
                api.adjust_part("add", "C100000", 1)
            except Exception as exc:  # pragma: no cover - surfaced via assert below
                mut_err.append(exc)

        mut = threading.Thread(target=mutate)
        mut.start()
        assert in_mutation.wait(10), "mutation never reached verify_parts"

        read_done = threading.Event()

        def reader():
            api._load_organized()
            read_done.set()

        rd = threading.Thread(target=reader)
        rd.start()
        # The reader must be blocked on the lock while the mutation is paused
        # inside its critical section. (A broken/no lock lets it finish
        # immediately; a working lock blocks it indefinitely.)
        assert not read_done.wait(0.3), "read completed while mutation held the lock"

        release_mutation.set()
        mut.join(10)
        rd.join(10)
        assert not mut.is_alive() and not rd.is_alive()
        assert not mut_err, f"mutation raised: {mut_err}"
        assert read_done.is_set()


class TestConnectionThreadSafety:
    def test_connect_requires_serialized_sqlite(self, tmp_path, monkeypatch):
        """Sharing one connection across threads (check_same_thread=False) is
        only safe when the sqlite3 build is serialized (threadsafety == 3).
        connect() must throw, not silently hand out an unsafe connection."""
        monkeypatch.setattr(sqlite3, "threadsafety", 1)
        with pytest.raises(RuntimeError, match="threadsafety"):
            cache_db.connect(str(tmp_path / "cache.db"))
