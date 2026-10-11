from contextlib import contextmanager
from threading import Event, RLock, Thread
from time import sleep
from types import SimpleNamespace

from emu68hatcher.builder import ags_inspection
from emu68hatcher.builder.ags_source import AGSComponentInventory, AGSEntry, AGSPartition
from emu68hatcher.builder.errors import BuildCancelledError


def test_cache_key_is_read_after_source_lock(monkeypatch, tmp_path):
    image = tmp_path / "ags.img"
    image.touch()
    state = {"revision": "old", "looked_up": None}
    cached = SimpleNamespace(components=[SimpleNamespace(role="whdload")])

    @contextmanager
    def wait_for_source(_path, _cancel_check):
        state["revision"] = "new"
        yield

    def lookup(key, _roles):
        state["looked_up"] = key
        return cached

    monkeypatch.setattr(ags_inspection, "ags_source_lock", wait_for_source)
    monkeypatch.setattr(ags_inspection, "_cache_key", lambda _path: (state["revision"],))
    monkeypatch.setattr(ags_inspection, "_lookup", lookup)

    assert ags_inspection.inspect_ags_source(image, ("whdload",)) is cached
    assert state["looked_up"] == ("new",)


def test_waiting_for_source_lock_can_be_cancelled(tmp_path):
    image = tmp_path / "ags.img"
    image.touch()
    identity = ags_inspection.source_identity(image)
    key = identity[:2]
    lock = RLock()
    lock.acquire()
    with ags_inspection._cache_lock:
        previous = ags_inspection._source_locks.get(key)
        ags_inspection._source_locks[key] = lock
    cancelled = Event()
    errors = []

    def wait_for_source():
        try:
            with ags_inspection.ags_source_lock(image, cancelled.is_set):
                pass
        except BuildCancelledError as exc:
            errors.append(exc)

    worker = Thread(target=wait_for_source)
    try:
        worker.start()
        sleep(0.05)
        cancelled.set()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert len(errors) == 1
    finally:
        lock.release()
        with ags_inspection._cache_lock:
            if previous is None:
                ags_inspection._source_locks.pop(key, None)
            else:
                ags_inspection._source_locks[key] = previous


def test_concurrent_inspection_reuses_completed_source(monkeypatch, tmp_path):
    from emu68hatcher.builder import ags_profiles, ags_scripts

    image = tmp_path / "ags.img"
    image.touch()
    identity = ags_inspection.source_identity(image)
    key = (str(image.resolve()), *identity, 2, "hst")
    partition = AGSPartition(1, "DH1", "WHDLoad", 1024)
    scans = []
    results = []

    def inspect_component(_path, _partitions, role, _cancel_check):
        scans.append(role)
        sleep(0.05)
        return AGSComponentInventory(
            "whdload", partition, "", (AGSEntry("Game", 0, True, 0, ""),), 0, 0, 1
        )

    monkeypatch.setattr(ags_inspection, "_cache_key", lambda _path: key)
    monkeypatch.setattr(
        ags_inspection, "inspect_ags_partitions", lambda _path, _check: (partition,)
    )
    monkeypatch.setattr(ags_inspection, "read_ags_scripts", lambda *_args: {"marker": b"ok"})
    monkeypatch.setattr(ags_inspection, "inspect_ags_component", inspect_component)
    monkeypatch.setattr(
        ags_profiles, "match_profile", lambda _hashes: SimpleNamespace(name="v30", version="3.0")
    )
    monkeypatch.setattr(ags_scripts, "required_script_paths", lambda _profile: ("marker",))
    with ags_inspection._cache_lock:
        previous = ags_inspection._cache.copy()
        ags_inspection._cache.clear()
    try:
        workers = [
            Thread(
                target=lambda: results.append(
                    ags_inspection.inspect_ags_source(image, ("whdload",))
                )
            )
            for _ in range(2)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=2)
            assert not worker.is_alive()
        assert scans == ["whdload"]
        assert len(results) == 2
    finally:
        with ags_inspection._cache_lock:
            ags_inspection._cache.clear()
            ags_inspection._cache.update(previous)
