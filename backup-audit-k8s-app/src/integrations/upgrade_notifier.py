#!/usr/bin/env python3

"""Background watcher that announces successful Helm release changes
(install / upgrade / rollback) to a Matrix room via the per-cluster
matrix-webhook. Redmine #310015.

Detection source: Helm release Secrets (sh.helm.release.v{1,2,3}.<rel>.v<N>)
read cluster-wide through the existing KubernetesManager. A successful change
is a new revision whose info.status is "deployed". State is an in-memory
baseline: seeded silently at startup (a pod restart never replays
notifications) and only moved after a successful post, so a failed post is
retried on the next cycle. Failures anywhere (unreadable namespace, bad
secret, webhook down) are logged and never raised out of the loop.
"""

import logging
import re
import threading

import requests
import yaml

logger = logging.getLogger(__name__)

RELEASE_SECRET_RE = re.compile(r"^sh\.helm\.release\.v\d+\.(.+)\.v(\d+)$")
IMAGE_RE = re.compile(r"image:\s*[\"']?([^\"'\s]+)[\"']?")
ACTION_LABEL = {"install": "INSTALL", "upgrade": "UPGRADE", "rollback": "ROLLBACK"}
NOTES_MAX_CHARS = 400


class ReleaseSnapshot:
    """One decoded Helm release revision, the fields a notification needs."""

    __slots__ = (
        "release",
        "namespace",
        "revision",
        "status",
        "description",
        "last_deployed",
        "chart_name",
        "chart_version",
        "app_version",
        "images",
        "link",
        "notes",
        "urls",
        "sources",
    )

    def __init__(self, **kwargs):
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))
        if not self.images:
            self.images = []
        if not self.urls:
            self.urls = []
        if not self.sources:
            self.sources = []

    @classmethod
    def from_release_data(cls, release: dict, namespace: str, revision: int) -> "ReleaseSnapshot":
        info = release.get("info", {}) or {}
        metadata = (release.get("chart", {}) or {}).get("metadata", {}) or {}
        manifest = release.get("manifest", "") or ""
        images = []
        for image in IMAGE_RE.findall(manifest):
            if image and not image.startswith("{{") and image not in images:
                images.append(image)
        sources = metadata.get("sources") or []
        link = ""
        if isinstance(sources, list) and sources and isinstance(sources[0], str):
            link = sources[0]
        urls = []
        try:
            for doc in yaml.safe_load_all(manifest):
                if not isinstance(doc, dict) or doc.get("kind") != "Ingress":
                    continue
                for rule in ((doc.get("spec") or {}).get("rules") or []):
                    host = rule.get("host") if isinstance(rule, dict) else None
                    if host and host not in urls:
                        urls.append(host)
        except yaml.YAMLError:
            pass
        return cls(
            release=release.get("name", "?"),
            namespace=namespace,
            revision=revision,
            status=info.get("status", ""),
            description=info.get("description", ""),
            last_deployed=info.get("last_deployed", ""),
            chart_name=metadata.get("name", release.get("name", "?")),
            chart_version=metadata.get("version", ""),
            app_version=metadata.get("appVersion", ""),
            images=images,
            link=link,
            notes=info.get("notes", ""),
            urls=urls,
            sources=[s for s in sources if isinstance(s, str)] if isinstance(sources, list) else [],
        )


class UpgradeNotifier:
    """Daemon thread that scans Helm releases and posts upgrade notifications."""

    def __init__(self, k8s_manager, config, webhook_url: str, cluster: str, interval: int = 60,
                 cluster_url: str = "", notes_resolver=None):
        self.k8s_manager = k8s_manager
        self.config = config
        self.webhook_url = webhook_url
        self.cluster = cluster
        self.cluster_url = cluster_url
        self.notes_resolver = notes_resolver
        self.interval = max(10, int(interval))
        # (namespace, release) -> ReleaseSnapshot of the last notified deployed rev
        self.baseline = {}
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="upgrade-notifier"
        )
        self._thread.start()
        logger.info(
            f"Upgrade notifier started (cluster={self.cluster}, every {self.interval}s)"
        )

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            self._cycle(seed=True)
            logger.info(
                f"Upgrade notifier: startup snapshot complete "
                f"({len(self.baseline)} releases baselined, no notifications sent)"
            )
        except Exception as e:
            logger.error(f"Upgrade notifier: startup snapshot failed: {e}")
        while not self._stop.wait(self.interval):
            try:
                self._cycle(seed=False)
            except Exception as e:
                logger.error(f"Upgrade notifier: scan cycle failed: {e}")

    def _cycle(self, seed: bool = False):
        excluded = self.config.get_excluded_namespaces()
        for namespace in self.k8s_manager.get_namespaces():
            if namespace in excluded:
                continue
            try:
                revisions = self.k8s_manager.list_release_revisions(namespace)
            except Exception as e:
                # Per-ns isolation: an unreadable namespace must not break the
                # cycle, and its baseline must be kept (a failed list is not
                # "the releases are gone").
                logger.warning(f"Upgrade notifier: cannot list releases in ns {namespace}: {e}")
                continue
            for release, revs in revisions.items():
                try:
                    self._check_release(namespace, release, revs, seed)
                except Exception as e:
                    logger.error(f"Upgrade notifier: error checking {namespace}/{release}: {e}")
            # A release that vanished from the namespace entirely (helm
            # uninstall) is not in `revisions`, so _check_release never sees
            # it - drop its baseline here instead.
            if not seed:
                known = set(revisions.keys())
                for key in list(self.baseline):
                    if key[0] == namespace and key[1] not in known:
                        self.baseline.pop(key)
                        logger.info(
                            f"Upgrade notifier: {key[0]}/{key[1]} uninstalled, baseline dropped"
                        )

    def _check_release(self, namespace: str, release: str, revs: list, seed: bool):
        new, _ = self._latest_deployed(namespace, revs)
        key = (namespace, release)

        if new is None:
            # No deployed revision left: release uninstalled (or only failed
            # revisions) -> drop the baseline silently, no notification.
            if key in self.baseline:
                self.baseline.pop(key)
                logger.info(f"Upgrade notifier: {namespace}/{release} gone, baseline dropped")
            return

        if seed:
            self.baseline[key] = new
            logger.debug(f"Upgrade notifier: seeded {namespace}/{release} @ rev {new.revision}")
            return

        prev = self.baseline.get(key)
        if prev is None:
            action = "install"
        elif new.revision > prev.revision:
            action = "rollback" if "rollback" in (new.description or "").lower() else "upgrade"
        else:
            # Same or lower deployed rev: nothing new, or only failed/pending
            # revisions on top (baseline correctly stays put).
            return

        if self._post(action, prev, new):
            self.baseline[key] = new
        else:
            logger.error(
                f"Upgrade notifier: post failed for {namespace}/{release}, "
                f"baseline kept at rev {prev.revision if prev else 0} - retrying next cycle"
            )

    def _latest_deployed(self, namespace: str, revs: list):
        """(newest deployed snapshot, nearest older deployed/superseded snapshot).

        revs are ordered newest first. Failed/pending revisions are skipped,
        which also makes a post-failure rollback report against the last good
        revision rather than the failed one."""
        new = None
        old = None
        for revision, secret in revs:
            snap = self._snapshot(namespace, revision, secret)
            if snap is None:
                continue
            if new is None:
                if snap.status == "deployed":
                    new = snap
            elif snap.status in ("deployed", "superseded"):
                old = snap
                break
        return new, old

    def _snapshot(self, namespace: str, revision: int, secret) -> "ReleaseSnapshot | None":
        try:
            data = secret.data.get("release") if secret.data else None
            if not data:
                return None
            release = self.k8s_manager.decode_helm_release(data)
            if not isinstance(release, dict) or not release:
                return None
            return ReleaseSnapshot.from_release_data(release, namespace, revision)
        except Exception as e:
            logger.warning(
                f"Upgrade notifier: could not decode release in {namespace} (rev {revision}): {e}"
            )
            return None

    @staticmethod
    def _fmt_version(prev, field: str, value: str) -> str:
        old = getattr(prev, field, "") if prev else ""
        if not old:
            return value or "?"
        if old == value:
            return f"{value or '?'} (unchanged)"
        return f"{old} → {value or '?'}"

    def _render(self, action: str, prev, new: ReleaseSnapshot, notes_info=None) -> str:
        lines = [
            f"[{ACTION_LABEL.get(action, action.upper())}] {new.release}",
            f"Cluster: {self.cluster}",
            f"Namespace: {new.namespace}",
            f"App version: {self._fmt_version(prev, 'app_version', new.app_version)}",
            f"Chart version: {self._fmt_version(prev, 'chart_version', new.chart_version)}",
        ]
        if self.cluster_url:
            lines.append(f"[{self.cluster_url}]({self.cluster_url})")
        if new.urls:
            url = "https://" + new.urls[0]
            if len(new.urls) > 1:
                url += f" (+{len(new.urls) - 1} more)"
            lines.append(f"URL: {url}")
        if notes_info:
            lines.append(
                f"Release notes: {notes_info['text']} ({notes_info['sha']})"
            )
        else:
            notes = " ".join((new.notes or "").split())
            if notes:
                if len(notes) > NOTES_MAX_CHARS:
                    notes = notes[:NOTES_MAX_CHARS].rstrip() + "…"
                lines.append("Release notes:")
                lines.append(notes)
        # Two trailing spaces per line: hard breaks, since the webhook renders
        # the body with python-markdown without nl2br.
        return "  \n".join(lines)

    def _post(self, action: str, prev, new: ReleaseSnapshot) -> bool:
        notes_info = None
        if self.notes_resolver is not None:
            try:
                notes_info = self.notes_resolver.resolve(
                    new.app_version, new.images, new.sources
                )
            except Exception as e:
                logger.warning(f"Upgrade notifier: release-notes resolution failed: {e}")
        text = self._render(action, prev, new, notes_info)
        logger.info(
            f"Upgrade notifier: {action} detected for {new.namespace}/{new.release} "
            f"(rev {prev.revision if prev else 0} -> {new.revision}), posting"
        )
        try:
            resp = requests.post(self.webhook_url, json={"body": text}, timeout=30)
        except requests.RequestException as e:
            logger.error(f"Upgrade notifier: POST to matrix-webhook failed: {e}")
            return False
        if resp.status_code // 100 == 2:
            logger.info(f"Upgrade notifier: posted to matrix-webhook (HTTP {resp.status_code})")
            return True
        logger.error(
            f"Upgrade notifier: matrix-webhook returned HTTP {resp.status_code}: "
            f"{resp.text[:200]}"
        )
        return False
