"""Tests for webview_profile — WebView2 persistent-profile self-heal."""

from __future__ import annotations

import webview_profile as wp


def test_prepare_writes_sentinel_and_no_wipe_on_clean_launch(tmp_path):
    profile = tmp_path / "webview2"
    profile.mkdir()
    (profile / "marker.txt").write_text("keep me")
    sentinel = tmp_path / ".webview2_launching"

    wiped = wp.prepare_for_launch(str(profile), str(sentinel))

    assert wiped is False
    assert sentinel.exists()  # sentinel written for this launch
    assert (profile / "marker.txt").exists()  # profile untouched on a clean launch


def test_prepare_wipes_profile_when_stale_sentinel_present(tmp_path):
    profile = tmp_path / "webview2"
    profile.mkdir()
    (profile / "Default").mkdir()
    (profile / "Default" / "leveldb.ldb").write_text("corrupt")
    sentinel = tmp_path / ".webview2_launching"
    sentinel.write_text("launching")  # leftover from a launch that never became ready

    wiped = wp.prepare_for_launch(str(profile), str(sentinel))

    assert wiped is True
    assert not (profile / "Default").exists()  # corrupt profile cleared
    assert sentinel.exists()  # a fresh sentinel was written for the retry launch


def test_prepare_handles_missing_profile_dir(tmp_path):
    # First-ever launch: no profile yet, no sentinel. Must not raise.
    profile = tmp_path / "webview2"
    sentinel = tmp_path / ".webview2_launching"
    wiped = wp.prepare_for_launch(str(profile), str(sentinel))
    assert wiped is False
    assert sentinel.exists()


def test_mark_ready_removes_sentinel(tmp_path):
    sentinel = tmp_path / ".webview2_launching"
    sentinel.write_text("launching")
    wp.mark_ready(str(sentinel))
    assert not sentinel.exists()


def test_mark_ready_is_idempotent_when_sentinel_absent(tmp_path):
    sentinel = tmp_path / ".webview2_launching"
    wp.mark_ready(str(sentinel))  # must not raise
    assert not sentinel.exists()


def test_should_heal_and_relaunch_truth_table():
    # Heal only when using the persistent profile, the bridge never signaled
    # ready, and we have not already attempted a heal (loop guard).
    assert wp.should_heal_and_relaunch(ready=False, persist_profile=True, already_healed=False) is True
    assert wp.should_heal_and_relaunch(ready=True, persist_profile=True, already_healed=False) is False
    assert wp.should_heal_and_relaunch(ready=False, persist_profile=False, already_healed=False) is False
    assert wp.should_heal_and_relaunch(ready=False, persist_profile=True, already_healed=True) is False


def test_descendant_pids_walks_full_tree():
    # (pid, ppid, name)
    procs = [
        (100, 1, "pythonw.exe"),       # the app
        (200, 100, "msedgewebview2.exe"),   # browser (child of app)
        (300, 200, "msedgewebview2.exe"),   # renderer (grandchild)
        (400, 200, "msedgewebview2.exe"),   # gpu (grandchild)
        (500, 999, "msedgewebview2.exe"),   # unrelated WebView2 from another app
        (600, 100, "conhost.exe"),          # unrelated child of the app
    ]
    desc = wp._descendant_pids(100, procs)
    assert desc == {200, 300, 400, 600}
    # the unrelated WebView2 (500) is NOT descended from us
    assert 500 not in desc


def test_descendant_pids_ignores_self_and_cycles():
    procs = [(100, 100, "a.exe"), (200, 100, "b.exe")]  # 100 lists itself as parent
    desc = wp._descendant_pids(100, procs)
    assert 100 not in desc  # never include the parent itself
    assert desc == {200}


def test_webview_child_pids_filters_by_name():
    procs = [
        (100, 1, "pythonw.exe"),
        (200, 100, "msedgewebview2.exe"),
        (300, 100, "conhost.exe"),
        (400, 200, "MSEDGEWEBVIEW2.EXE"),  # case-insensitive
    ]
    assert wp._webview_child_pids(100, procs) == {200, 400}
