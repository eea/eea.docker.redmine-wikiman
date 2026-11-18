#!/usr/bin/env python3

"""Unit tests for the upgrade notifier (#310015).

Run: cd backup-audit-k8s-app && python3 -m unittest discover -s tests -v
(stdlib only; the app's requirements are needed so `integrations` imports).
"""

import json
import os
import sys
import unittest
import unittest.mock as mock

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, SRC)

from integrations.upgrade_notifier import UpgradeNotifier  # noqa: E402


INGRESS_TMPL = """
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {name}
spec:
  rules:
    - host: {host}
      http:
        paths:
          - path: /
"""


def make_release_payload(name, rev, status, description, app_version="", chart_version="",
                         image=None, deployed="2026-11-05T12:00:00.000000000Z",
                         chart_name=None, sources=None, notes="", ingress_host=None):
    metadata = {
        "name": chart_name or f"chart-{name}",
        "version": chart_version,
        "appVersion": app_version,
    }
    if sources:
        metadata["sources"] = sources
    manifest = f"image: {image}\n" if image else ""
    if ingress_host:
        manifest += INGRESS_TMPL.format(name=name, host=ingress_host)
    return {
        "name": name,
        "version": rev,
        "info": {
            "status": status,
            "description": description,
            "last_deployed": deployed,
            "notes": notes,
        },
        "chart": {"metadata": metadata},
        "manifest": manifest,
    }


class FakeSecret:
    def __init__(self, name, payload):
        self.metadata = mock.Mock()
        self.metadata.name = name
        self.data = {"release": json.dumps(payload)}


def fake_secret(name, payload):
    return FakeSecret(name, payload)


class FakeK8s:
    def __init__(self, namespaces, releases_by_ns):
        self.namespaces = namespaces
        self.releases_by_ns = releases_by_ns
        self.fail_ns = set()

    def get_namespaces(self):
        return list(self.namespaces)

    def list_release_revisions(self, namespace):
        if namespace in self.fail_ns:
            raise PermissionError(f"secrets is forbidden in {namespace}")
        return self.releases_by_ns.get(namespace, {})

    def decode_helm_release(self, data):
        return json.loads(data)


class FakeConfig:
    def get_excluded_namespaces(self):
        return {"kube-system", "default"}


class FakeResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class FakePost:
    def __init__(self, status=200):
        self.status = status
        self.calls = []

    def __call__(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "body": (json or {}).get("body", "")})
        return FakeResp(self.status)


class FakeNotesResolver:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def resolve(self, app_version, images, sources):
        self.calls.append((app_version, images, sources))
        return self.result


def make_notifier(releases_by_ns, namespaces=("app",), post=None, cluster_url="",
                  notes_resolver=None, rancher_url=""):
    k8s = FakeK8s(namespaces, releases_by_ns)
    notifier = UpgradeNotifier(
        k8s, FakeConfig(), "http://webhook.local/?key=k&room_id=!room", "01dev", 60,
        cluster_url=cluster_url, notes_resolver=notes_resolver,
        rancher_url=rancher_url
    )
    if post is None:
        post = FakePost()
    notifier._post_ = post  # test hook reference
    return notifier, post


class UpgradeNotifierTest(unittest.TestCase):
    def setUp(self):
        self._patch = mock.patch("integrations.upgrade_notifier.requests.post")
        self.mock_post = self._patch.start()
        self.addCleanup(self._patch.stop)

    def _run(self, notifier, post, seed=False):
        self.mock_post.side_effect = post
        notifier._cycle(seed=seed)
        if not seed:
            # The unit tests do not start the worker thread: drain the work
            # queue inline, exactly as the work loop would.
            while not notifier._work_queue.empty():
                ns, rel = notifier._work_queue.get_nowait()
                try:
                    notifier._process_change(ns, rel)
                finally:
                    notifier._work_queue.task_done()
                    notifier._pending_keys.discard((ns, rel))

    def test_seed_is_silent(self):
        releases = {"app": {"onyx": [(1, fake_secret("sh.helm.release.v1.onyx.v1",
                       make_release_payload("onyx", 1, "deployed", "Install complete",
                                            app_version="1.0", chart_version="0.1.0",
                                            image="eeacms/onyx:1.0")))]}}
        notifier, post = make_notifier(releases)
        self._run(notifier, post, seed=True)
        self.assertEqual(len(post.calls), 0)
        self.assertIn(("app", "onyx"), notifier.baseline)

    def test_upgrade_posts_once_and_moves_baseline(self):
        p1 = make_release_payload("onyx", 1, "superseded", "Install complete",
                                  app_version="1.0", chart_version="0.1.0",
                                  image="eeacms/onyx:1.0")
        p2 = make_release_payload("onyx", 2, "deployed", "Upgrade complete",
                                  app_version="1.1", chart_version="0.1.0",
                                  image="eeacms/onyx:1.1")
        releases = {"app": {"onyx": [
            (2, fake_secret("sh.helm.release.v1.onyx.v2", p2)),
            (1, fake_secret("sh.helm.release.v1.onyx.v1", p1)),
        ]}}
        notifier, post = make_notifier(releases)
        self._run(notifier, post, seed=True)
        self._run(notifier, post)  # seed again with same state: no post
        self.assertEqual(len(post.calls), 0)

        # bump: rev 3 deployed
        p3 = make_release_payload("onyx", 3, "deployed", "Upgrade complete",
                                  app_version="1.2", chart_version="0.2.0",
                                  image="eeacms/onyx:1.2")
        p2s = make_release_payload("onyx", 2, "superseded", "Upgrade complete",
                                   app_version="1.1", chart_version="0.1.0",
                                   image="eeacms/onyx:1.1")
        releases["app"]["onyx"] = [
            (3, fake_secret("sh.helm.release.v1.onyx.v3", p3)),
            (2, fake_secret("sh.helm.release.v1.onyx.v2", p2s)),
            (1, fake_secret("sh.helm.release.v1.onyx.v1", p1)),
        ]
        self._run(notifier, post)
        self.assertEqual(len(post.calls), 1)
        self.assertEqual(notifier.baseline[("app", "onyx")].revision, 3)
        body = post.calls[0]["body"]
        self.assertIn("#### [UPGRADE] onyx", body)
        self.assertIn("Cluster: 01dev", body)
        self.assertIn("Namespace: app", body)
        self.assertIn("App: 1.1 → 1.2", body)
        self.assertIn("Chart: 0.1.0 → 0.2.0", body)

        # no change: no further posts
        self._run(notifier, post)
        self.assertEqual(len(post.calls), 1)

    def test_failed_revision_on_top_no_post(self):
        p1 = make_release_payload("onyx", 1, "superseded", "Install complete",
                                  app_version="1.0", chart_version="0.1.0")
        p2 = make_release_payload("onyx", 2, "deployed", "Upgrade complete",
                                  app_version="1.1", chart_version="0.1.0")
        p3 = make_release_payload("onyx", 3, "failed", "Upgrade complete",
                                  app_version="1.2", chart_version="0.2.0")
        releases = {"app": {"onyx": [
            (3, fake_secret("sh.helm.release.v1.onyx.v3", p3)),
            (2, fake_secret("sh.helm.release.v1.onyx.v2", p2)),
            (1, fake_secret("sh.helm.release.v1.onyx.v1", p1)),
        ]}}
        notifier, post = make_notifier(releases)
        self._run(notifier, post, seed=True)
        self._run(notifier, post)
        self.assertEqual(len(post.calls), 0)
        self.assertEqual(notifier.baseline[("app", "onyx")].revision, 2)

    def test_rollback_after_failure_reports_last_good(self):
        p1 = make_release_payload("onyx", 1, "superseded", "Install complete",
                                  app_version="1.0", chart_version="0.1.0",
                                  image="eeacms/onyx:1.0")
        p2 = make_release_payload("onyx", 2, "deployed", "Upgrade complete",
                                  app_version="1.1", chart_version="0.1.0",
                                  image="eeacms/onyx:1.1")
        p3 = make_release_payload("onyx", 3, "failed", "Upgrade complete",
                                  app_version="1.2", chart_version="0.2.0")
        p4 = make_release_payload("onyx", 4, "deployed", "Rollback to 2",
                                  app_version="1.1", chart_version="0.1.0",
                                  image="eeacms/onyx:1.1")
        # pre-rollback state: rev 2 deployed, rev 3 failed on top
        releases = {"app": {"onyx": [
            (3, fake_secret("sh.helm.release.v1.onyx.v3", p3)),
            (2, fake_secret("sh.helm.release.v1.onyx.v2", p2)),
            (1, fake_secret("sh.helm.release.v1.onyx.v1", p1)),
        ]}}
        notifier, post = make_notifier(releases)
        self._run(notifier, post, seed=True)
        self.assertEqual(notifier.baseline[("app", "onyx")].revision, 2)
        # the rollback lands: rev 4 deployed, rev 2 flips to superseded
        p2s = make_release_payload("onyx", 2, "superseded", "Upgrade complete",
                                   app_version="1.1", chart_version="0.1.0",
                                   image="eeacms/onyx:1.1")
        releases["app"]["onyx"] = [
            (4, fake_secret("sh.helm.release.v1.onyx.v4", p4)),
            (3, fake_secret("sh.helm.release.v1.onyx.v3", p3)),
            (2, fake_secret("sh.helm.release.v1.onyx.v2", p2s)),
            (1, fake_secret("sh.helm.release.v1.onyx.v1", p1)),
        ]
        self._run(notifier, post)
        self.assertEqual(len(post.calls), 1)
        body = post.calls[0]["body"]
        self.assertIn("#### [ROLLBACK] onyx", body)
        self.assertIn("App: 1.1 (unchanged)", body)
        self.assertEqual(notifier.baseline[("app", "onyx")].revision, 4)

    def test_uninstall_drops_baseline_silently(self):
        p1 = make_release_payload("onyx", 1, "deployed", "Install complete")
        releases = {"app": {"onyx": [(1, fake_secret("sh.helm.release.v1.onyx.v1", p1))]}}
        notifier, post = make_notifier(releases)
        self._run(notifier, post, seed=True)
        # helm uninstall: the release key disappears from the namespace
        releases["app"] = {}
        self._run(notifier, post)
        self.assertEqual(len(post.calls), 0)
        self.assertNotIn(("app", "onyx"), notifier.baseline)

    def test_uninstall_with_only_failed_revisions_drops_silently(self):
        p1 = make_release_payload("onyx", 1, "deployed", "Install complete")
        releases = {"app": {"onyx": [(1, fake_secret("sh.helm.release.v1.onyx.v1", p1))]}}
        notifier, post = make_notifier(releases)
        self._run(notifier, post, seed=True)
        # key present but no deployed revision left (edge: revs gone but not
        # yet fully pruned from the list)
        releases["app"]["onyx"] = []
        self._run(notifier, post)
        self.assertEqual(len(post.calls), 0)
        self.assertNotIn(("app", "onyx"), notifier.baseline)

    def test_post_failure_keeps_baseline_and_retries(self):
        p1 = make_release_payload("onyx", 1, "deployed", "Install complete",
                                  app_version="1.0", chart_version="0.1.0")
        p2 = make_release_payload("onyx", 2, "deployed", "Upgrade complete",
                                  app_version="1.1", chart_version="0.1.0")
        releases = {"app": {"onyx": [
            (1, fake_secret("sh.helm.release.v1.onyx.v1", p1)),
        ]}}
        failing = FakePost(status=500)
        ok = FakePost(status=200)
        notifier, _ = make_notifier(releases)
        self._run(notifier, ok, seed=True)
        # upgrade lands while the webhook is down
        p1s = make_release_payload("onyx", 1, "superseded", "Install complete",
                                   app_version="1.0", chart_version="0.1.0")
        releases["app"]["onyx"] = [
            (2, fake_secret("sh.helm.release.v1.onyx.v2", p2)),
            (1, fake_secret("sh.helm.release.v1.onyx.v1", p1s)),
        ]
        self._run(notifier, failing)  # upgrade detected, post fails
        self.assertEqual(len(failing.calls), 1)
        self.assertEqual(notifier.baseline[("app", "onyx")].revision, 1)
        self._run(notifier, ok)  # next cycle retries the same event
        self.assertEqual(len(ok.calls), 1)
        self.assertEqual(notifier.baseline[("app", "onyx")].revision, 2)

    def test_new_release_after_seed_notifies_install(self):
        notifier, post = make_notifier({"app": {}})
        self._run(notifier, post, seed=True)
        p1 = make_release_payload("newapp", 1, "deployed", "Install complete",
                                  app_version="2.0", chart_version="1.0.0",
                                  image="eeacms/newapp:2.0")
        notifier.k8s_manager.releases_by_ns["app"] = {
            "newapp": [(1, fake_secret("sh.helm.release.v1.newapp.v1", p1))]
        }
        self._run(notifier, post)
        self.assertEqual(len(post.calls), 1)
        body = post.calls[0]["body"]
        self.assertIn("#### [INSTALL] newapp", body)
        self.assertIn("App: 2.0", body)
        self.assertIn("Chart: 1.0.0", body)

    def test_excluded_namespace_ignored(self):
        p1 = make_release_payload("x", 1, "deployed", "Install complete")
        releases = {
            "app": {},
            "kube-system": {"x": [(1, fake_secret("sh.helm.release.v1.x.v1", p1))]},
        }
        notifier, post = make_notifier(releases, namespaces=("app", "kube-system"))
        self._run(notifier, post, seed=True)
        self._run(notifier, post)
        self.assertEqual(len(post.calls), 0)
        self.assertEqual(notifier.baseline, {})

    def test_ns_list_failure_keeps_baseline(self):
        p1 = make_release_payload("onyx", 1, "deployed", "Install complete")
        releases = {"app": {"onyx": [(1, fake_secret("sh.helm.release.v1.onyx.v1", p1))]}}
        notifier, post = make_notifier(releases)
        self._run(notifier, post, seed=True)
        notifier.k8s_manager.fail_ns.add("app")
        self._run(notifier, post)  # must not raise, must not drop baseline
        self.assertIn(("app", "onyx"), notifier.baseline)
        self.assertEqual(len(post.calls), 0)

    def test_message_format_fields(self):
        p1 = make_release_payload(
            "bise-backend", 1, "deployed", "Install complete",
            app_version="6.1.4-23", chart_version="2.3.1",
            image="eeacms/bise-backend:6.1.4-23",
            sources=["https://github.com/eea/helm-charts/"])
        p2 = make_release_payload(
            "bise-backend", 2, "deployed", "Upgrade complete",
            app_version="6.1.4-24", chart_version="2.3.1",
            image="eeacms/bise-backend:6.1.4-24",
            deployed="2026-11-05T11:59:59.000000000Z",
            sources=["https://github.com/eea/helm-charts/"],
            notes="Bise backend deployed with the new schema. " * 3)
        releases = {"bise": {"bise-backend": [
            (1, fake_secret("sh.helm.release.v1.bise-backend.v1", p1)),
        ]}}
        notifier, post = make_notifier(
            releases, namespaces=("bise",),
            cluster_url="https://01dev.eea.europa.eu")
        self._run(notifier, post, seed=True)
        p1s = make_release_payload(
            "bise-backend", 1, "superseded", "Install complete",
            app_version="6.1.4-23", chart_version="2.3.1",
            image="eeacms/bise-backend:6.1.4-23",
            sources=["https://github.com/eea/helm-charts/"])
        releases["bise"]["bise-backend"] = [
            (2, fake_secret("sh.helm.release.v1.bise-backend.v2", p2)),
            (1, fake_secret("sh.helm.release.v1.bise-backend.v1", p1s)),
        ]
        self._run(notifier, post)
        body = post.calls[0]["body"]
        self.assertIn("#### [UPGRADE] bise-backend", body)
        self.assertIn("Cluster: 01dev", body)
        self.assertIn("Namespace: bise", body)
        self.assertIn("App: 6.1.4-23 → 6.1.4-24", body)
        self.assertIn("Chart: 2.3.1 (unchanged)", body)
        self.assertIn("Cluster: [https://01dev.eea.europa.eu](https://01dev.eea.europa.eu)", body)
        # No resolver -> no Commit/Release lines, and (user decision
        # 2026-11-09) the release notes / NOTES.txt text is no longer shown
        # either.
        self.assertNotIn("Commit:", body)
        self.assertNotIn("Release:", body)
        self.assertNotIn("Release notes:", body)
        self.assertNotIn("Bise backend deployed", body)

    def test_message_omits_url_and_notes_when_absent(self):
        p1 = make_release_payload(
            "onyx", 1, "deployed", "Install complete",
            app_version="6.1.4-23", chart_version="2.3.1",
            image="eeacms/onyx:6.1.4-23")
        releases = {"app": {"onyx": [
            (1, fake_secret("sh.helm.release.v1.onyx.v1", p1)),
        ]}}
        notifier, post = make_notifier(releases)
        self._run(notifier, post, seed=True)
        p1s = make_release_payload(
            "onyx", 1, "superseded", "Install complete",
            app_version="6.1.4-23", chart_version="2.3.1",
            image="eeacms/onyx:6.1.4-23")
        p2 = make_release_payload(
            "onyx", 2, "deployed", "Upgrade complete",
            app_version="6.1.4-24", chart_version="2.3.2",
            image="eeacms/onyx:6.1.4-24")
        releases["app"]["onyx"] = [
            (2, fake_secret("sh.helm.release.v1.onyx.v2", p2)),
            (1, fake_secret("sh.helm.release.v1.onyx.v1", p1s)),
        ]
        self._run(notifier, post)
        body = post.calls[0]["body"]
        self.assertIn("#### [UPGRADE] onyx", body)
        self.assertIn("Chart: 2.3.1 → 2.3.2", body)
        self.assertNotIn("Release notes:", body)
        self.assertNotIn("https://", body)

    def test_card_shows_ingress_url_and_git_release_notes(self):
        resolver = FakeNotesResolver({
            "repo": "eea/danswer", "tag": "v4.3.9-eea.0.0.120-patch.5",
            "sha": "66c1a9f", "kind": "commit",
            "url": "https://github.com/eea/danswer/commit/66c1a9f",
            "text": "fix(web): bump Next.js to 16.4.3",
        })
        p1 = make_release_payload(
            "onyx", 1, "deployed", "Install complete",
            app_version="v4.3.9-eea.0.0.119", chart_version="0.38.0",
            image="eeacms/danswer:backend-v4.3.9-eea.0.0.119",
            sources=["https://github.com/onyx-dot-app/onyx"],
            ingress_host="aihub-latest.01dev.eea.europa.eu")
        releases = {"gptlab2": {"onyx": [
            (1, fake_secret("sh.helm.release.v1.onyx.v1", p1)),
        ]}}
        notifier, post = make_notifier(
            releases, namespaces=("gptlab2",), notes_resolver=resolver,
            rancher_url="https://rancher2develop.eea.europa.eu/c/c-m-9pf4z69b")
        self._run(notifier, post, seed=True)
        p2 = make_release_payload(
            "onyx", 2, "deployed", "Upgrade complete",
            app_version="v4.3.9-eea.0.0.120-patch.5", chart_version="0.38.0",
            image="eeacms/danswer:backend-v4.3.9-eea.0.0.120-patch.5",
            sources=["https://github.com/onyx-dot-app/onyx"],
            ingress_host="aihub-latest.01dev.eea.europa.eu")
        p1s = make_release_payload(
            "onyx", 1, "superseded", "Install complete",
            app_version="v4.3.9-eea.0.0.119", chart_version="0.38.0",
            image="eeacms/danswer:backend-v4.3.9-eea.0.0.119",
            sources=["https://github.com/onyx-dot-app/onyx"],
            ingress_host="aihub-latest.01dev.eea.europa.eu")
        releases["gptlab2"]["onyx"] = [
            (2, fake_secret("sh.helm.release.v1.onyx.v2", p2)),
            (1, fake_secret("sh.helm.release.v1.onyx.v1", p1s)),
        ]
        self._run(notifier, post)
        body = post.calls[0]["body"]
        # The app name links to the installed helm app in Rancher
        # (catalog.cattle.io App), the ingress URL keeps its own line.
        self.assertIn(
            "#### [UPGRADE] [onyx]"
            "(https://rancher2develop.eea.europa.eu/c/c-m-9pf4z69b"
            "/exploration/gptlab2/catalog.cattle.io.apps.onyx)", body)
        self.assertIn("URL: [aihub-latest.01dev.eea.europa.eu]"
                      "(https://aihub-latest.01dev.eea.europa.eu)", body)
        # Single upstream link labeled "Release": no real GitHub Release
        # here (kind "commit"), so it points at the exact commit.
        self.assertIn("Release: [66c1a9f](https://github.com/eea/danswer/commit/66c1a9f)", body)
        self.assertNotIn("Commit:", body)
        self.assertNotIn("**Release notes:**", body)
        self.assertNotIn("fix(web): bump Next.js to 16.4.3", body)
        self.assertEqual(len(resolver.calls), 1)
        self.assertEqual(resolver.calls[0][0], "v4.3.9-eea.0.0.120-patch.5")
        self.assertIn("eeacms/danswer:backend-v4.3.9-eea.0.0.120-patch.5",
                      resolver.calls[0][1])

    def test_card_shows_release_line_for_github_release(self):
        # When the resolver finds a real GitHub Release, the card links the
        # release page (notes + comments) above the commit.
        resolver = FakeNotesResolver({
            "repo": "eea/danswer", "tag": "v4.3.9-eea.0.0.120-patch.5",
            "sha": "66c1a9f", "kind": "release",
            "url": "https://github.com/eea/danswer/commit/66c1a9f",
            "release_url": ("https://github.com/eea/danswer/releases/tag/"
                            "v4.3.9-eea.0.0.120-patch.5"),
            "text": "Big feature release.",
        })
        p1 = make_release_payload(
            "onyx", 1, "deployed", "Install complete",
            app_version="v4.3.9-eea.0.0.119", chart_version="0.38.0",
            image="eeacms/danswer:backend-v4.3.9-eea.0.0.119")
        releases = {"gptlab2": {"onyx": [
            (1, fake_secret("sh.helm.release.v1.onyx.v1", p1)),
        ]}}
        notifier, post = make_notifier(releases, namespaces=("gptlab2",),
                                       notes_resolver=resolver)
        self._run(notifier, post, seed=True)
        p2 = make_release_payload(
            "onyx", 2, "deployed", "Upgrade complete",
            app_version="v4.3.9-eea.0.0.120-patch.5", chart_version="0.38.0",
            image="eeacms/danswer:backend-v4.3.9-eea.0.0.120-patch.5")
        p1s = make_release_payload(
            "onyx", 1, "superseded", "Install complete",
            app_version="v4.3.9-eea.0.0.119", chart_version="0.38.0",
            image="eeacms/danswer:backend-v4.3.9-eea.0.0.119")
        releases["gptlab2"]["onyx"] = [
            (2, fake_secret("sh.helm.release.v1.onyx.v2", p2)),
            (1, fake_secret("sh.helm.release.v1.onyx.v1", p1s)),
        ]
        self._run(notifier, post)
        body = post.calls[0]["body"]
        self.assertIn("Release: [v4.3.9-eea.0.0.120-patch.5]"
                      "(https://github.com/eea/danswer/releases/tag/"
                      "v4.3.9-eea.0.0.120-patch.5)", body)
        # The Commit line is gone - "Release:" is the only upstream link.
        self.assertNotIn("Commit:", body)


def make_multi_image_payload(name, rev, status, description, images,
                             app_version="1.0", chart_version="0.1.0"):
    p = make_release_payload(name, rev, status, description,
                             app_version=app_version, chart_version=chart_version)
    p["manifest"] = "".join(f"image: {i}\n" for i in images)
    return p


class NoImageLinesTest(unittest.TestCase):
    """Image lines were removed from the card by user decision (2026-11-18):
    App and Chart are the version lines, the release link covers the detail.
    Image diffs still count as a visible change, so image-only upgrades
    (onyx-style) keep the full card instead of the compact one."""

    def setUp(self):
        self._patch = mock.patch("integrations.upgrade_notifier.requests.post")
        self.mock_post = self._patch.start()
        self.addCleanup(self._patch.stop)

    def _run(self, notifier, post, seed=False):
        self.mock_post.side_effect = post
        notifier._cycle(seed=seed)
        if not seed:
            while not notifier._work_queue.empty():
                ns, rel = notifier._work_queue.get_nowait()
                try:
                    notifier._process_change(ns, rel)
                finally:
                    notifier._work_queue.task_done()
                    notifier._pending_keys.discard((ns, rel))

    def test_image_only_upgrade_full_card_no_image_line(self):
        p1 = make_multi_image_payload("onyx", 1, "deployed", "Install complete",
                                      ["onyxdotapp/onyx-backend:v5.1.2"],
                                      app_version="v2.2.4", chart_version="0.4.4")
        releases = {"app": {"onyx": [(1, fake_secret("sh.helm.release.v1.onyx.v1", p1))]}}
        notifier, post = make_notifier(releases)
        self._run(notifier, post, seed=True)
        p2 = make_multi_image_payload("onyx", 2, "deployed", "Upgrade complete",
                                      ["onyxdotapp/onyx-backend:v5.1.3"],
                                      app_version="v2.2.4", chart_version="0.4.4")
        releases["app"]["onyx"] = [
            (2, fake_secret("sh.helm.release.v1.onyx.v2", p2)),
            (1, fake_secret("sh.helm.release.v1.onyx.v1",
                            make_multi_image_payload("onyx", 1, "superseded", "Install complete",
                                                     ["onyxdotapp/onyx-backend:v5.1.2"],
                                                     app_version="v2.2.4",
                                                     chart_version="0.4.4"))),
        ]
        self._run(notifier, post)
        body = post.calls[0]["body"]
        # Image-only change: full card (NOT "No visible changes"), no Image
        # line - App and Chart are the only version lines.
        self.assertIn("App: v2.2.4 (unchanged)", body)
        self.assertIn("Chart: 0.4.4 (unchanged)", body)
        self.assertNotIn("Image:", body)
        self.assertNotIn("No visible changes", body)

    def test_unchanged_images_compact_card(self):
        p1 = make_multi_image_payload("app", 1, "deployed", "Install complete", ["x/y:1.0"])
        releases = {"app": {"app": [(1, fake_secret("sh.helm.release.v1.app.v1", p1))]}}
        notifier, post = make_notifier(releases)
        self._run(notifier, post, seed=True)
        p2 = make_multi_image_payload("app", 2, "deployed", "Upgrade complete", ["x/y:1.0"])
        releases["app"]["app"] = [
            (2, fake_secret("sh.helm.release.v1.app.v2", p2)),
            (1, fake_secret("sh.helm.release.v1.app.v1",
                            make_multi_image_payload("app", 1, "superseded", "Install complete", ["x/y:1.0"]))),
        ]
        self._run(notifier, post)
        body = post.calls[0]["body"]
        # Nothing moved at all -> compact card, still no Image line.
        self.assertIn("No visible changes", body)
        self.assertNotIn("Image:", body)

    def test_install_no_image_line(self):
        notifier, post = make_notifier({"app": {}})
        self._run(notifier, post, seed=True)
        p1 = make_multi_image_payload("newapp", 1, "deployed", "Install complete",
                                      ["x/y:2.0"], app_version="2.0")
        notifier.k8s_manager.releases_by_ns["app"] = {
            "newapp": [(1, fake_secret("sh.helm.release.v1.newapp.v1", p1))]
        }
        self._run(notifier, post)
        body = post.calls[0]["body"]
        self.assertIn("#### [INSTALL] newapp", body)
        self.assertIn("App: 2.0", body)
        self.assertNotIn("Image:", body)

    def test_image_change_counts_as_visible_change(self):
        old = [f"r/a{i}:1" for i in range(7)]
        p1 = make_multi_image_payload("big", 1, "deployed", "Install complete", old)
        releases = {"app": {"big": [(1, fake_secret("sh.helm.release.v1.big.v1", p1))]}}
        notifier, post = make_notifier(releases)
        self._run(notifier, post, seed=True)
        p2 = make_multi_image_payload("big", 2, "deployed", "Upgrade complete",
                                      [f"r/a{i}:2" for i in range(7)])
        releases["app"]["big"] = [
            (2, fake_secret("sh.helm.release.v1.big.v2", p2)),
            (1, fake_secret("sh.helm.release.v1.big.v1",
                            make_multi_image_payload("big", 1, "superseded", "Install complete", old))),
        ]
        self._run(notifier, post)
        body = post.calls[0]["body"]
        self.assertNotIn("No visible changes", body)
        self.assertNotIn("Image:", body)


class NoChangeCardTest(unittest.TestCase):
    """Re-deploys with no visible change (values-only) and charts without
    appVersion must not render 'App: ?' or a wall of '(unchanged)'."""

    def setUp(self):
        self._patch = mock.patch("integrations.upgrade_notifier.requests.post")
        self.mock_post = self._patch.start()
        self.addCleanup(self._patch.stop)

    def _run(self, notifier, post, seed=False):
        self.mock_post.side_effect = post
        notifier._cycle(seed=seed)
        if not seed:
            while not notifier._work_queue.empty():
                ns, rel = notifier._work_queue.get_nowait()
                try:
                    notifier._process_change(ns, rel)
                finally:
                    notifier._work_queue.task_done()
                    notifier._pending_keys.discard((ns, rel))

    def test_no_visible_changes_compact_card(self):
        imgs = ["x/es:8.19.13", "x/kb:8.19.13"]
        p1 = make_multi_image_payload("sdi", 59, "deployed", "Upgrade complete", imgs,
                                      app_version="", chart_version="0.14.13")
        releases = {"ns": {"sdi": [(59, fake_secret("sh.helm.release.v1.sdi.v59", p1))]}}
        notifier, post = make_notifier(releases, namespaces=("ns",))
        self._run(notifier, post, seed=True)
        p2 = make_multi_image_payload("sdi", 61, "deployed", "Upgrade complete", imgs,
                                      app_version="", chart_version="0.14.13")
        releases["ns"]["sdi"] = [
            (61, fake_secret("sh.helm.release.v1.sdi.v61", p2)),
            (59, fake_secret("sh.helm.release.v1.sdi.v59",
                             make_multi_image_payload("sdi", 59, "superseded", "Upgrade complete",
                                                      imgs, app_version="",
                                                      chart_version="0.14.13"))),
        ]
        self._run(notifier, post)
        body = post.calls[0]["body"]
        self.assertIn("**No visible changes** — re-deploy / values-only", body)
        self.assertNotIn("App:", body)
        self.assertNotIn("?", body)

    def test_app_version_line_omitted_when_unset(self):
        p1 = make_release_payload("app", 1, "deployed", "Install complete",
                                  app_version="", chart_version="0.1.0")
        releases = {"ns": {"app": [(1, fake_secret("sh.helm.release.v1.app.v1", p1))]}}
        notifier, post = make_notifier(releases, namespaces=("ns",))
        self._run(notifier, post, seed=True)
        p2 = make_release_payload("app", 2, "deployed", "Upgrade complete",
                                  app_version="", chart_version="0.2.0")
        releases["ns"]["app"] = [
            (2, fake_secret("sh.helm.release.v1.app.v2", p2)),
            (1, fake_secret("sh.helm.release.v1.app.v1",
                            make_release_payload("app", 1, "superseded", "Install complete",
                                                 app_version="", chart_version="0.1.0"))),
        ]
        self._run(notifier, post)
        body = post.calls[0]["body"]
        self.assertNotIn("App:", body)
        self.assertIn("Chart: 0.1.0 → 0.2.0", body)

    def test_app_version_removed_shows_not_set(self):
        p1 = make_release_payload("app", 1, "deployed", "Install complete",
                                  app_version="1.0", chart_version="0.1.0")
        releases = {"ns": {"app": [(1, fake_secret("sh.helm.release.v1.app.v1", p1))]}}
        notifier, post = make_notifier(releases, namespaces=("ns",))
        self._run(notifier, post, seed=True)
        p2 = make_release_payload("app", 2, "deployed", "Upgrade complete",
                                  app_version="", chart_version="0.2.0")
        releases["ns"]["app"] = [
            (2, fake_secret("sh.helm.release.v1.app.v2", p2)),
            (1, fake_secret("sh.helm.release.v1.app.v1",
                            make_release_payload("app", 1, "superseded", "Install complete",
                                                 app_version="1.0", chart_version="0.1.0"))),
        ]
        self._run(notifier, post)
        body = post.calls[0]["body"]
        self.assertIn("App: 1.0 → (not set)", body)


class ListReleaseRevisionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # kubernetes_manager has import-time side effects (git safe.directory,
        # /app log file); neutralize both for the unit test environment.
        with mock.patch("subprocess.run"), mock.patch("logging.FileHandler"):
            from integrations import kubernetes_manager

            cls.km = kubernetes_manager

    def _manager_with_secrets(self, secrets, raise_on_list=False):
        manager = self.km.KubernetesManager.__new__(self.km.KubernetesManager)

        class FakeV1:
            def list_namespaced_secret(self, namespace):
                if raise_on_list:
                    raise PermissionError("forbidden")
                items = mock.Mock()
                items.items = secrets
                return items

        manager.v1 = FakeV1()
        return manager

    def _secret(self, name):
        s = mock.Mock()
        s.metadata.name = name
        return s

    def test_groups_and_sorts_newest_first(self):
        secrets = [
            self._secret("sh.helm.release.v1.onyx.v3"),
            self._secret("sh.helm.release.v1.onyx.v1"),
            self._secret("sh.helm.release.v2.other.v7"),
            self._secret("sh.helm.release.v2.other.v9"),
            self._secret("not-a-helm-secret"),
            self._secret("sh.helm.release.v1.onyx.v2"),
        ]
        manager = self._manager_with_secrets(secrets)
        result = manager.list_release_revisions("ns")
        self.assertEqual([rev for rev, _ in result["onyx"]], [3, 2, 1])
        self.assertEqual([rev for rev, _ in result["other"]], [9, 7])
        self.assertNotIn("not-a-helm-secret", result)

    def test_raises_on_api_error(self):
        manager = self._manager_with_secrets([], raise_on_list=True)
        with self.assertRaises(PermissionError):
            manager.list_release_revisions("ns")


if __name__ == "__main__":
    unittest.main()
