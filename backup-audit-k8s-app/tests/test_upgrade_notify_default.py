#!/usr/bin/env python3

"""Pin the #310015 default: upgrade notifications are opt-in and off by
default.

Two levels:
1. Config: UPGRADE_NOTIFY_ENABLED unset (or not "true") ->
   Config().upgrade_notify_enabled is False.
2. Wiring: initialize_components() must not construct an UpgradeNotifier
   when the flag is off (the default) or when it is on but the webhook URL
   is empty; it must construct exactly one when both are set.

Run: cd backup-audit-k8s-app && python3 -m unittest discover -s tests -v
(stdlib only; the app's requirements are needed so `integrations` imports).
"""

import contextlib
import logging
import os
import sys
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

# api.py has import-time side effects (logging.basicConfig with a
# FileHandler to /app). Neutralize them before the first import - and patch
# basicConfig itself, otherwise the (mocked) handlers get attached to the
# root logger and every log line in every later test raises.
with mock.patch("logging.FileHandler"), mock.patch("logging.StreamHandler"), \
     mock.patch("logging.basicConfig"):
    import api  # noqa: E402

from core.config import Config  # noqa: E402

# git_manager imports GitPython, which is not needed for these tests.
with mock.patch.dict(sys.modules, {"git": mock.Mock()}):
    from integrations import git_manager  # noqa: F401,E402

# kubernetes_manager has import-time side effects (git safe.directory,
# /app log file) - neutralize both.
with mock.patch("subprocess.run"), mock.patch("logging.FileHandler"):
    from integrations import kubernetes_manager as km_mod  # noqa: E402

# ...which leaves mock handlers on its logger (created while FileHandler was
# patched). Drop them so real log lines don't blow up later tests.
for _h in list(logging.getLogger("integrations.kubernetes_manager").handlers):
    if not isinstance(_h, logging.Handler):
        logging.getLogger("integrations.kubernetes_manager").removeHandler(_h)

# initialize_components() imports its collaborators lazily inside the
# function, so patch the classes at their source modules.
PATCH_TARGETS = [
    "integrations.git_manager.GitManager",
    "integrations.kubernetes_manager.KubernetesManager",
    "managers.storage_manager.StorageManager",
    "managers.archive_manager.ArchiveManager",
    "processors.resource_processor.ResourceProcessor",
    "processors.helm_processor.HelmProcessor",
    "processors.event_processor.EventProcessor",
    "scheduler.AuditScheduler",
    "integrations.upgrade_notifier.UpgradeNotifier",
    "integrations.release_notes.ReleaseNotesResolver",
]

NOTIFY_ENVS = [
    "UPGRADE_NOTIFY_ENABLED",
    "UPGRADE_NOTIFY_WEBHOOK_URL",
    "UPGRADE_NOTIFY_CLUSTER",
    "UPGRADE_NOTIFY_CLUSTER_URL",
    "UPGRADE_NOTIFY_RELEASE_NOTES",
    "UPGRADE_NOTIFY_INTERVAL",
]


class UpgradeNotifyDefaultTest(unittest.TestCase):
    """The notification flag must be opt-in: off by default, and the
    notifier must never start unless both the flag and a webhook URL are
    set."""

    def setUp(self):
        # Snapshot the env and the module globals; restore in tearDown so a
        # host that happens to run with UPGRADE_NOTIFY_* set can't skew the
        # "default" case.
        self._saved_env = {k: os.environ.get(k) for k in NOTIFY_ENVS}
        self._saved_globals = {
            "upgrade_notifier": api.upgrade_notifier,
            "event_processor": api.event_processor,
            "scheduler": api.scheduler,
        }
        for k in NOTIFY_ENVS:
            os.environ.pop(k, None)
        for k in self._saved_globals:
            setattr(api, k, None)

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k, v in self._saved_globals.items():
            setattr(api, k, v)

    def run_init(self):
        with contextlib.ExitStack() as stack:
            mocks = {
                t: stack.enter_context(mock.patch(t)) for t in PATCH_TARGETS
            }
            api.initialize_components()
        return mocks

    def test_config_default_is_disabled(self):
        self.assertFalse(Config().upgrade_notify_enabled)

    def test_config_flag_values(self):
        with mock.patch.dict(os.environ, {"UPGRADE_NOTIFY_ENABLED": "true"}):
            self.assertTrue(Config().upgrade_notify_enabled)
        with mock.patch.dict(os.environ, {"UPGRADE_NOTIFY_ENABLED": "TRUE"}):
            self.assertTrue(Config().upgrade_notify_enabled)
        for off in ("false", "False", "0", "no", ""):
            with mock.patch.dict(os.environ, {"UPGRADE_NOTIFY_ENABLED": off}):
                self.assertFalse(Config().upgrade_notify_enabled, off)

    def test_default_is_off_no_notifier(self):
        mocks = self.run_init()
        self.assertIsNone(api.upgrade_notifier)
        mocks["integrations.upgrade_notifier.UpgradeNotifier"].assert_not_called()

    def test_enabled_without_url_stays_off(self):
        os.environ["UPGRADE_NOTIFY_ENABLED"] = "true"
        mocks = self.run_init()
        self.assertIsNone(api.upgrade_notifier)
        mocks["integrations.upgrade_notifier.UpgradeNotifier"].assert_not_called()

    def test_enabled_with_url_constructs_notifier(self):
        os.environ["UPGRADE_NOTIFY_ENABLED"] = "true"
        os.environ["UPGRADE_NOTIFY_WEBHOOK_URL"] = "http://example.local/hook"
        mocks = self.run_init()
        notifier_cls = mocks["integrations.upgrade_notifier.UpgradeNotifier"]
        notifier_cls.assert_called_once()
        self.assertIs(api.upgrade_notifier, notifier_cls.return_value)


class UpgradeNotifierFastPathTest(unittest.TestCase):
    """Scan-speed fast path: a release whose newest revision did not move
    since the last cycle must not be re-decoded (gzip/base64/yaml of its
    manifest took ~0.3-1 s per release, ~63 s per full 01dev cycle)."""

    def _notifier(self):
        from integrations.upgrade_notifier import UpgradeNotifier

        return UpgradeNotifier(
            k8s_manager=None, config=None, webhook_url="http://x", cluster="t"
        )

    def test_unchanged_release_skips_decode(self):
        n = self._notifier()
        n.baseline[("ns", "rel")] = mock.Mock(revision=5)
        revs = [(5, mock.Mock()), (4, mock.Mock())]
        with mock.patch.object(n, "_latest_deployed") as ld:
            n._check_release("ns", "rel", revs, seed=False)
        ld.assert_not_called()

    def test_pruned_history_skips_decode(self):
        # Helm prunes old revisions: top can be OLDER than the baseline
        # without any new deploy having happened.
        n = self._notifier()
        n.baseline[("ns", "rel")] = mock.Mock(revision=5)
        revs = [(4, mock.Mock()), (3, mock.Mock())]
        with mock.patch.object(n, "_latest_deployed") as ld:
            n._check_release("ns", "rel", revs, seed=False)
        ld.assert_not_called()

    def test_new_revision_enqueued_then_worker_posts_upgrade(self):
        n = self._notifier()
        old = mock.Mock(revision=5)
        new = mock.Mock(revision=6, description="")
        n.baseline[("ns", "rel")] = old
        # The scan must NOT decode - it only sees the revision numbers.
        with mock.patch.object(n, "_latest_deployed") as ld:
            n._check_release("ns", "rel", [(6, mock.Mock()), (5, mock.Mock())], seed=False)
        ld.assert_not_called()
        self.assertEqual(n._work_queue.get_nowait(), ("ns", "rel"))
        self.assertIn(("ns", "rel"), n._pending_keys)

        # The work thread re-lists, decodes, and posts.
        n.k8s_manager = mock.Mock()
        n.k8s_manager.list_release_revisions.return_value = {"rel": [(6, mock.Mock()), (5, mock.Mock())]}
        with mock.patch.object(n, "_latest_deployed", return_value=(new, old)) as ld, \
             mock.patch.object(n, "_post", return_value=True) as post:
            n._process_change("ns", "rel")
        ld.assert_called_once()
        post.assert_called_once_with("upgrade", old, new)
        self.assertIs(n.baseline[("ns", "rel")], new)

    def test_new_release_worker_posts_install(self):
        n = self._notifier()
        n._check_release("ns", "brandnew", [(1, mock.Mock())], seed=False)
        self.assertEqual(n._work_queue.get_nowait(), ("ns", "brandnew"))

        new = mock.Mock(revision=1, description="")
        n.k8s_manager = mock.Mock()
        n.k8s_manager.list_release_revisions.return_value = {"brandnew": [(1, mock.Mock())]}
        with mock.patch.object(n, "_latest_deployed", return_value=(new, None)), \
             mock.patch.object(n, "_post", return_value=True) as post:
            n._process_change("ns", "brandnew")
        post.assert_called_once_with("install", None, new)
        self.assertIs(n.baseline[("ns", "brandnew")], new)

    def test_duplicate_enqueues_suppressed_while_pending(self):
        n = self._notifier()
        n._check_release("ns", "rel", [(6, mock.Mock())], seed=False)
        # Still in flight: the next scan must not queue it a second time.
        n._check_release("ns", "rel", [(6, mock.Mock())], seed=False)
        self.assertEqual(n._work_queue.qsize(), 1)

    def test_failed_post_retried_on_next_scan(self):
        n = self._notifier()
        old = mock.Mock(revision=5)
        n.baseline[("ns", "rel")] = old
        n._check_release("ns", "rel", [(6, mock.Mock())], seed=False)
        n._work_queue.get_nowait()

        n.k8s_manager = mock.Mock()
        n.k8s_manager.list_release_revisions.return_value = {"rel": [(6, mock.Mock())]}
        with mock.patch.object(
            n, "_latest_deployed", return_value=(mock.Mock(revision=6, description=""), old)
        ), mock.patch.object(n, "_post", return_value=False):
            n._process_change("ns", "rel")
        # Post failed: baseline kept, key released (as the work loop does).
        self.assertIs(n.baseline[("ns", "rel")], old)
        n._pending_keys.discard(("ns", "rel"))

        # Next scan re-detects and re-queues.
        n._check_release("ns", "rel", [(6, mock.Mock())], seed=False)
        self.assertEqual(n._work_queue.qsize(), 1)

    def test_seed_always_decodes(self):
        n = self._notifier()
        n.baseline[("ns", "rel")] = mock.Mock(revision=5)
        revs = [(5, mock.Mock())]
        with mock.patch.object(
            n, "_latest_deployed", return_value=(mock.Mock(revision=5), None)
        ) as ld:
            n._check_release("ns", "rel", revs, seed=True)
        ld.assert_called_once()

    def test_cycle_isolates_namespace_failures(self):
        n = self._notifier()
        k8s = mock.Mock()
        k8s.get_namespaces.return_value = ["bad", "good"]

        def list_revs(ns):
            if ns == "bad":
                raise RuntimeError("api error")
            return {}

        k8s.list_release_revisions.side_effect = list_revs
        cfg = mock.Mock()
        cfg.get_excluded_namespaces.return_value = set()
        n.k8s_manager = k8s
        n.config = cfg

        n._cycle(seed=False)  # must not raise
        k8s.list_release_revisions.assert_any_call("good")


if __name__ == "__main__":
    unittest.main()
