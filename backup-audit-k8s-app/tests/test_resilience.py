#!/usr/bin/env python3

"""Unit tests for the 2026-11-09 resilience pass (taskman-backup).

Covers: F1 (liveness/readiness split), F2 (startup grace, no inf),
M1 (memory self-visibility), M2 (helm release metadata-only + decode once),
M3 (per-kind streaming), M4 (page-cache drops), M5 (bounded webhook queue).

Run: cd backup-audit-k8s-app && python3 -m unittest discover -s tests -v
(stdlib only; app requirements needed so `integrations`/`processors` import.)
"""

import datetime
import logging
import os
import queue
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock as mock

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, SRC)

# pydantic is an app requirement (in the image) but not needed for these
# tests; stub it when absent so core.models imports cleanly.
try:
    import pydantic  # noqa: F401
except ImportError:
    sys.modules["pydantic"] = types.SimpleNamespace(BaseModel=object)

from core.memory import (  # noqa: E402
    read_memory,
    memory_summary,
    drop_file_cache,
    drop_git_pack_cache,
)

# api.py has import-time side effects (logging.basicConfig with a
# FileHandler to /app). Neutralize them before the first import - and patch
# basicConfig itself, otherwise the (mocked) handlers get attached to the
# root logger and every log line in every later test raises.
with mock.patch("logging.FileHandler"), mock.patch("logging.StreamHandler"), \
     mock.patch("logging.basicConfig"):
    import api  # noqa: E402

# git_manager imports GitPython, which is not needed for these tests.
with mock.patch.dict(sys.modules, {"git": mock.Mock()}):
    from integrations.git_manager import GitManager  # noqa: E402

# kubernetes_manager has import-time side effects (git safe.directory,
# /app log file) - neutralize both.
with mock.patch("subprocess.run"), mock.patch("logging.FileHandler"):
    from integrations import kubernetes_manager as km_mod  # noqa: E402

# ...which leaves mock handlers on its logger (created while FileHandler was
# patched). Drop them so real log lines don't blow up later tests.
for _h in list(logging.getLogger("integrations.kubernetes_manager").handlers):
    if not isinstance(_h, logging.Handler):
        logging.getLogger("integrations.kubernetes_manager").removeHandler(_h)


class MemoryReadTest(unittest.TestCase):
    """M1: the app must be able to see its own memory."""

    def test_read_memory_shape(self):
        m = read_memory()
        self.assertEqual(set(m), {"rss_mib", "hwm_mib", "data_mib"})
        for v in m.values():
            self.assertGreaterEqual(v, 0.0)
        self.assertIn("rss=", memory_summary())

    def test_read_memory_bad_proc(self):
        with mock.patch("core.memory._PROC_SELF_STATUS", "/nonexistent/status"):
            m = read_memory()
        self.assertEqual(m["rss_mib"], 0.0)
        self.assertEqual(m["hwm_mib"], 0.0)

    def test_drop_file_cache(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"x" * 4096)
            path = f.name
        try:
            drop_file_cache(path)  # real kernel call; must not raise
            drop_file_cache("/nonexistent/file")  # best-effort no-op
        finally:
            os.unlink(path)

    def test_drop_git_pack_cache(self):
        with tempfile.TemporaryDirectory() as d:
            pack = os.path.join(d, ".git", "objects", "pack")
            os.makedirs(pack)
            with open(os.path.join(pack, "pack-abc.pack"), "wb") as f:
                f.write(b"z" * 100)
            drop_git_pack_cache(d)  # must not raise
            drop_git_pack_cache(os.path.join(d, "no-git-here"))  # no-op


class HealthSplitTest(unittest.TestCase):
    """F1: /health = liveness (never fails on dependency outages),
    /ready = readiness (old /health logic). F2: startup grace from
    process start time, not inf."""

    def test_liveness_alive_with_push_failure(self):
        git = mock.Mock(last_push_error="502 from GitLab", last_push_success=None)
        with mock.patch.object(api, "scheduler", mock.Mock(git_manager=git)):
            alive, _ = api.get_liveness_status()
        self.assertTrue(alive)

    def test_liveness_alive_without_components(self):
        with mock.patch.object(api, "event_processor", None), \
             mock.patch.object(api, "scheduler", None):
            alive, reason = api.get_liveness_status()
        self.assertTrue(alive)
        self.assertIn("components not initialized", reason)

    def test_readiness_not_ready_without_components(self):
        with mock.patch.object(api, "event_processor", None), \
             mock.patch.object(api, "scheduler", None):
            ready, reason = api.get_readiness_status()
        self.assertFalse(ready)
        self.assertIn("components not initialized", reason)

    def _components(self, git=None):
        """event_processor + scheduler set, as after initialize_components()."""
        return (
            mock.patch.object(api, "event_processor", mock.Mock()),
            mock.patch.object(api, "scheduler", mock.Mock(git_manager=git)),
        )

    def test_readiness_startup_grace(self):
        ep, sch = self._components()
        with ep, sch, \
             mock.patch.object(api, "startup_complete", False), \
             mock.patch.object(api, "process_start_time", time.time()):
            ready, _ = api.get_readiness_status()
        self.assertTrue(ready)

    def test_readiness_startup_grace_expired(self):
        ep, sch = self._components()
        with ep, sch, \
             mock.patch.object(api, "startup_complete", False), \
             mock.patch.object(api, "process_start_time", time.time() - 9999):
            ready, reason = api.get_readiness_status()
        self.assertFalse(ready)
        self.assertIn("startup did not complete", reason)

    def test_readiness_push_failure_fresh_process_within_grace(self):
        # F2: a fresh process whose remote is down gets the grace period.
        # The old code computed seconds_since_success = inf here and failed
        # immediately - the flaw behind the 2026-11-09 restart storm.
        git = mock.Mock(last_push_error="502 from GitLab", last_push_success=None)
        ep, sch = self._components(git)
        with ep, sch, \
             mock.patch.object(api, "startup_complete", True), \
             mock.patch.object(api, "process_start_time", time.time()):
            ready, _ = api.get_readiness_status()
        self.assertTrue(ready)

    def test_readiness_push_failure_past_grace(self):
        git = mock.Mock(last_push_error="502 from GitLab", last_push_success=None)
        ep, sch = self._components(git)
        with ep, sch, \
             mock.patch.object(api, "startup_complete", True), \
             mock.patch.object(api, "process_start_time", time.time() - 9999):
            ready, reason = api.get_readiness_status()
        self.assertFalse(ready)
        self.assertIn("git push failing since", reason)

    def test_readiness_push_failure_recent_success(self):
        git = mock.Mock(
            last_push_error="502 from GitLab",
            last_push_success=datetime.datetime.now(),
        )
        ep, sch = self._components(git)
        with ep, sch, \
             mock.patch.object(api, "startup_complete", True), \
             mock.patch.object(api, "process_start_time", time.time() - 9999):
            ready, _ = api.get_readiness_status()
        self.assertTrue(ready)

    def test_storm_scenario_liveness_up_readiness_down(self):
        # The 2026-11-09 storm shape: remote down for a long time on a
        # fresh process. Liveness must stay up (no kill storm), readiness
        # goes down (pod NotReady, no webhook traffic) instead.
        git = mock.Mock(last_push_error="502 from GitLab", last_push_success=None)
        ep, sch = self._components(git)
        with ep, sch, \
             mock.patch.object(api, "startup_complete", True), \
             mock.patch.object(api, "process_start_time", time.time() - 9999):
            alive, _ = api.get_liveness_status()
            ready, _ = api.get_readiness_status()
        self.assertTrue(alive)
        self.assertFalse(ready)


class QueueBoundTest(unittest.TestCase):
    """M5: the webhook queue is bounded and drops the oldest event."""

    def setUp(self):
        self._old_q = api.webhook_queue
        self._old_cnt = api.webhook_dropped_count
        api.webhook_queue = queue.Queue(maxsize=2)
        api.webhook_dropped_count = 0

    def tearDown(self):
        api.webhook_queue = self._old_q
        api.webhook_dropped_count = self._old_cnt

    def test_unbounded_growth_impossible(self):
        for i in range(10):
            api.enqueue_webhook_event({"n": i})
        self.assertEqual(api.webhook_queue.qsize(), 2)
        self.assertEqual(api.webhook_dropped_count, 8)
        self.assertEqual(api.webhook_queue.get_nowait()["n"], 8)
        self.assertEqual(api.webhook_queue.get_nowait()["n"], 9)

    def test_drop_oldest_keeps_newest(self):
        api.enqueue_webhook_event({"n": 0})
        api.enqueue_webhook_event({"n": 1})
        api.enqueue_webhook_event({"n": 2})
        self.assertEqual(api.webhook_dropped_count, 1)
        self.assertEqual(api.webhook_queue.get_nowait()["n"], 1)
        self.assertEqual(api.webhook_queue.get_nowait()["n"], 2)


def _fake_secret(name, labels, data):
    return types.SimpleNamespace(
        metadata=types.SimpleNamespace(name=name, labels=labels),
        data=data,
    )


class HelmReleaseMemoryTest(unittest.TestCase):
    """M2: get_helm_releases carries metadata + payload only, and the
    processor decodes the payload exactly once per release."""

    def _manager(self):
        km = km_mod.KubernetesManager.__new__(km_mod.KubernetesManager)
        km.v1 = mock.Mock()
        km.v1.list_namespaced_secret.return_value.items = [
            _fake_secret(
                "sh.helm.release.v1.rel.v1",
                {"name": "rel", "helm.sh/release-version": "1"},
                {"release": "b29sZA==", "other": "x"},
            ),
            _fake_secret(
                "sh.helm.release.v1.rel.v2",
                {"name": "rel", "helm.sh/release-version": "2"},
                {"release": "bmV3"},
            ),
            _fake_secret("normal-secret", {}, {"data": "1"}),
        ]
        return km

    def test_entries_are_metadata_only(self):
        releases = self._manager().get_helm_releases("ns")
        self.assertEqual(len(releases), 1)
        e = releases[0]
        self.assertEqual(e["name"], "rel")
        self.assertEqual(e["version"], 2)
        self.assertEqual(e["release"], "bmV3")
        self.assertEqual(e["release_version_label"], "2")
        # No full k8s Secret object is retained (that is what used to pile
        # up in memory across a whole-namespace sync).
        self.assertNotIn("secret", e)

    def test_decode_happens_once_per_release(self):
        from processors.helm_processor import HelmProcessor

        k8s = mock.Mock()
        k8s.get_helm_releases.return_value = [
            {
                "name": "rel",
                "version": 2,
                "release": "payload-b64",
                "release_version_label": "2",
            }
        ]
        decoded = {"name": "rel", "chart": {}, "config": {"a": 1}}
        k8s.decode_helm_release.return_value = decoded
        hp = HelmProcessor(k8s, "/tmp/helm-processor-test-no-such-dir")
        with mock.patch("builtins.open", mock.mock_open()), mock.patch("os.makedirs"):
            hp.process_namespace_helm_releases("ns")
        self.assertEqual(k8s.decode_helm_release.call_count, 1)
        self.assertEqual(k8s.decode_helm_release.call_args, mock.call("payload-b64"))
        k8s.extract_helm_values.assert_called_once_with(decoded)
        k8s.extract_helm_deployment_info.assert_called_once()
        args, _ = k8s.extract_helm_deployment_info.call_args
        self.assertIs(args[0], decoded)
        self.assertEqual(args[3], "2")  # release_version_label passed through


class SyncKindStreamingTest(unittest.TestCase):
    """M3: the sync lists, saves and drops one resource kind at a time."""

    def test_streams_one_kind_at_a_time(self):
        from managers.sync_manager import SyncManager

        k8s = mock.Mock()
        k8s.get_kind_resources.side_effect = (
            lambda ns, kt: [{"kind": kt[4], "metadata": {"name": "x"}}]
        )
        rp = mock.Mock()
        rp.should_skip_resource.return_value = False
        rp.get_helm_release_name.return_value = ""
        cfg = mock.Mock()
        cfg.helm_tracking = False
        cfg.get_excluded_namespaces.return_value = set()

        sm = SyncManager(k8s, rp, mock.Mock(), mock.Mock(), mock.Mock(), cfg)
        sm.process_namespace_resources("ns")

        self.assertEqual(k8s.get_kind_resources.call_count, 13)
        self.assertEqual(rp.save_resource.call_count, 13)
        k8s.get_namespace_resources.assert_not_called()


class StandaloneArchiveTest(unittest.TestCase):
    """Cleanup archives audit files of standalone resources that were
    deleted: a file not written by the latest sync pass is a candidate,
    it is re-verified against the cluster, and if it is gone it is moved
    to archived/<ns>/standalone-resources/ (plain rename)."""

    def _setup(self, d):
        from managers.archive_manager import ArchiveManager

        ns_storage = os.path.join(d, "ns1", "standalone-resources")
        os.makedirs(ns_storage)
        for fname in (
            "configmap-capture-a.yaml",
            "configmap-still-there.yaml",
            "pod-noisy.yaml",
        ):
            with open(os.path.join(ns_storage, fname), "w") as f:
                f.write("apiVersion: v1\nkind: ConfigMap\n")
        k8s = mock.Mock()
        # capture-a is gone; still-there and noisy still exist.
        k8s.resource_exists.side_effect = lambda ns, kind, name: (
            kind,
            name,
        ) in {("configmap", "still-there"), ("pod", "noisy")}
        cfg = mock.Mock()
        cfg.get_excluded_namespaces.return_value = set()
        am = ArchiveManager(d, mock.Mock(), threading.Lock(), cfg,
                            k8s_manager=k8s)
        return am

    def _paths(self, d):
        active_dir = os.path.join(d, "ns1", "standalone-resources")
        archived_dir = os.path.join(
            d, "archived", "ns1", "standalone-resources"
        )
        return active_dir, archived_dir

    def test_deleted_archived_live_kept(self):
        with tempfile.TemporaryDirectory() as d:
            am = self._setup(d)
            am.archive_missing_standalone_resources(
                {"ns1"},
                {"ns1": {"configmap-still-there.yaml"}},
            )
            active_dir, archived_dir = self._paths(d)
            # Deleted resource: moved out, content preserved as a rename.
            self.assertFalse(
                os.path.exists(
                    os.path.join(active_dir, "configmap-capture-a.yaml")
                )
            )
            with open(
                os.path.join(archived_dir, "configmap-capture-a.yaml")
            ) as f:
                self.assertEqual(f.read(), "apiVersion: v1\nkind: ConfigMap\n")
            # Candidate that is still alive in the cluster: untouched.
            self.assertTrue(
                os.path.exists(
                    os.path.join(active_dir, "pod-noisy.yaml")
                )
            )
            # Resource written by the latest sync: untouched.
            self.assertTrue(
                os.path.exists(
                    os.path.join(active_dir, "configmap-still-there.yaml")
                )
            )

    def test_never_synced_namespace_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            am = self._setup(d)
            am.archive_missing_standalone_resources({"ns1"}, {})
            active_dir, _ = self._paths(d)
            self.assertTrue(
                os.path.exists(
                    os.path.join(active_dir, "configmap-capture-a.yaml")
                )
            )

    def test_sync_tracks_active_standalone(self):
        from managers.sync_manager import SyncManager

        k8s = mock.Mock()
        k8s.get_kind_resources.side_effect = (
            lambda ns, kt: [{"kind": kt[4], "metadata": {"name": "x"}}]
        )
        rp = mock.Mock()
        rp.should_skip_resource.return_value = False
        rp.get_helm_release_name.return_value = ""

        def save(resource, release=""):
            return os.path.join(
                "/storage",
                "ns1",
                "standalone-resources",
                f"{resource['kind'].lower()}-{resource['metadata']['name']}.yaml",
            )

        rp.save_resource.side_effect = save
        cfg = mock.Mock()
        cfg.helm_tracking = False
        cfg.get_excluded_namespaces.return_value = set()

        sm = SyncManager(k8s, rp, mock.Mock(), mock.Mock(), mock.Mock(), cfg)
        sm.process_namespace_resources("ns1")
        self.assertEqual(len(sm._active_standalone["ns1"]), 13)
        self.assertIn("configmap-x.yaml", sm._active_standalone["ns1"])
        # A new pass with nothing left resets the set to empty (falsy but
        # present) so every stored file becomes a candidate.
        rp.save_resource.side_effect = _never
        k8s.get_kind_resources.side_effect = lambda ns, kt: []
        sm.process_namespace_resources("ns1")
        self.assertEqual(sm._active_standalone["ns1"], set())


def _never(*args, **kwargs):
    raise AssertionError("save_resource must not be called")


class GitPackCacheTest(unittest.TestCase):
    """M4: a successful push drops the local pack files' page cache."""

    def test_push_success_drops_pack_cache(self):
        # Import the live module (and use its class): the module-level
        # import above happened inside a mock.patch.dict(sys.modules, ...)
        # context, which removed integrations.git_manager from sys.modules
        # on exit - a patch by dotted path would re-import a *second* module
        # instance that the bound class no longer references.
        import integrations.git_manager as g

        with mock.patch.object(g, "drop_git_pack_cache") as drop:
            gm = g.GitManager.__new__(g.GitManager)
            gm.storage_path = "/app/logs"
            gm._record_push_success()
            drop.assert_called_once_with("/app/logs")
        self.assertIsNone(gm.last_push_error)
