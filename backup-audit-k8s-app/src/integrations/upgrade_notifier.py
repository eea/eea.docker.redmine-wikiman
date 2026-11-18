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
import queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
import yaml

logger = logging.getLogger(__name__)

RELEASE_SECRET_RE = re.compile(r"^sh\.helm\.release\.v\d+\.(.+)\.v(\d+)$")
IMAGE_RE = re.compile(r"image:\s*[\"']?([^\"'\s]+)[\"']?")
ACTION_LABEL = {"install": "INSTALL", "upgrade": "UPGRADE", "rollback": "ROLLBACK"}


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
    """Announces successful Helm release changes (install / upgrade /
    rollback) to a Matrix room. Split into two jobs:

    - the SCAN thread (every `interval` seconds) scans all namespaces in
      parallel. Revision numbers come free from the secret names, so a
      steady-state cycle never decodes a manifest - it only detects which
      releases moved and delegates them to the work queue.
    - the WORK thread drains the queue and does the heavy lifting
      (gzip/base64/yaml decode, render, webhook post). A slow or failing
      webhook therefore can never stall the next scan, and detection
      latency is bounded by the scan interval, not the post time.
    """

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
        # (namespace, release) keys queued or in flight in the work thread
        self._work_queue = queue.Queue(maxsize=100)
        self._pending_keys = set()
        self._stop = threading.Event()
        self._thread = None
        self._worker_thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._worker_thread = threading.Thread(
            target=self._work_loop, daemon=True, name="upgrade-notifier-worker"
        )
        self._worker_thread.start()
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
        namespaces = [
            ns for ns in self.k8s_manager.get_namespaces() if ns not in excluded
        ]
        # Namespaces are independent: scan them concurrently. The serial
        # version took ~63 s per cycle on 01dev (70 namespaces), which
        # made detection latency up to interval + 63 s even when nothing
        # changed.
        with ThreadPoolExecutor(max_workers=8) as pool:
            for namespace in namespaces:
                pool.submit(self._scan_namespace, namespace, seed)

    def _scan_namespace(self, namespace: str, seed: bool):
        try:
            revisions = self.k8s_manager.list_release_revisions(namespace)
        except Exception as e:
            # Per-ns isolation: an unreadable namespace must not break the
            # cycle, and its baseline must be kept (a failed list is not
            # "the releases are gone").
            logger.warning(f"Upgrade notifier: cannot list releases in ns {namespace}: {e}")
            return
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
        key = (namespace, release)

        if seed:
            # Startup snapshot: decode everything and baseline it silently
            # (a pod restart must never replay notifications).
            new, _ = self._latest_deployed(namespace, revs)
            if new is None:
                return
            self.baseline[key] = new
            logger.debug(f"Upgrade notifier: seeded {namespace}/{release} @ rev {new.revision}")
            return

        # Steady-state scan: revision numbers come from the secret names,
        # which the per-namespace list already returned for free. Revisions
        # only ever increase (a new deploy/rollback always creates a higher
        # one), so if the newest revision is not newer than the last
        # notified one, no install/upgrade/rollback happened - skip it
        # without any manifest decode. If it moved, delegate the heavy work
        # to the work thread instead of doing it here: the scan must stay
        # cheap, and a slow webhook must not block the next cycle.
        prev = self.baseline.get(key)
        if prev is not None and revs and revs[0][0] <= prev.revision:
            return
        if key in self._pending_keys:
            return  # already queued or in flight

        try:
            self._work_queue.put_nowait((namespace, release))
            self._pending_keys.add(key)
        except queue.Full:
            # Only possible with 100+ stuck changes at once: drop it - the
            # baseline is unchanged, so the next scan re-detects it.
            logger.error(
                f"Upgrade notifier: work queue full, {namespace}/{release} "
                f"will be retried next cycle"
            )

    def _work_loop(self):
        while True:
            try:
                namespace, release = self._work_queue.get(timeout=1)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            key = (namespace, release)
            try:
                self._process_change(namespace, release)
            except Exception as e:
                logger.error(
                    f"Upgrade notifier: work for {namespace}/{release} failed: {e}"
                )
            finally:
                self._work_queue.task_done()
                # Release the key (on success or failure) so the next scan
                # can re-detect it if the post did not go through.
                self._pending_keys.discard(key)

    def _process_change(self, namespace: str, release: str):
        key = (namespace, release)
        # Re-read the namespace: the deploy may have moved on again between
        # detection and now - always act on the latest state.
        try:
            revisions = self.k8s_manager.list_release_revisions(namespace)
        except Exception as e:
            logger.warning(
                f"Upgrade notifier: cannot re-list {namespace} for {release}: {e}"
            )
            return
        revs = revisions.get(release) or []
        new, _ = self._latest_deployed(namespace, revs)

        prev = self.baseline.get(key)
        if new is None:
            # No deployed revision left: release uninstalled (or only failed
            # revisions) -> drop the baseline silently, no notification.
            if key in self.baseline:
                self.baseline.pop(key)
                logger.info(f"Upgrade notifier: {namespace}/{release} gone, baseline dropped")
            return

        if prev is None:
            action = "install"
        elif new.revision > prev.revision:
            action = "rollback" if "rollback" in (new.description or "").lower() else "upgrade"
        else:
            # Resolved before we got here (a newer change was already
            # posted, or the new top revision was not actually deployed):
            # nothing to do.
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
    def _image_parts(image: str):
        """(repository, display tag). Handles registry:port and @digest."""
        image = image.split("@", 1)[0]
        if ":" in image.rsplit("/", 1)[-1]:
            ref, tag = image.rsplit(":", 1)
        else:
            ref, tag = image, "latest"
        return ref, tag

    def _image_lines(self, prev, new: "ReleaseSnapshot") -> list:
        """Container-image lines: image tags are the only version info for
        charts whose appVersion is stale (e.g. onyx: chart appVersion stays
        v2.2.4 while the app ships as image tags). Changed images are shown
        old -> new, with the current tag highlighted (light gray box);
        unchanged ones are omitted. The repository is shown by its short
        name (last path segment) so the line stays short enough not to wrap
        on a phone. Capped at 5 lines."""
        if not new.images:
            return []
        old_images = prev.images if prev else []
        old_by_ref = {}
        for img in old_images:
            ref, tag = self._image_parts(img)
            old_by_ref.setdefault(ref, tag)
        lines = []
        for img in new.images:
            if prev is not None and img in old_images:
                continue
            ref, tag = self._image_parts(img)
            short = ref.rsplit("/", 1)[-1]
            if prev is not None and ref in old_by_ref and old_by_ref[ref] != tag:
                lines.append(f"Image: {short} {old_by_ref[ref]} → {tag}")
            else:
                lines.append(f"Image: {short} {tag}")
        shown = lines[:5]
        if len(lines) > 5:
            shown.append(f"(and {len(lines) - 5} more images)")
        return shown

    @staticmethod
    def _fmt_version(prev, field: str, value: str) -> str:
        # Plain text, no highlight: the only background tint the message
        # layer can produce is the code span, and its gray shade is fixed by
        # the Matrix client theme (inline styles are sanitized away), so it
        # could not be made light enough. A change is signalled by the arrow
        # and the "(unchanged)" suffix instead - the text carries the
        # meaning, nothing relies on styling.
        old = getattr(prev, field, "") or "" if prev else ""
        new = value or "(not set)"
        if not old:
            return new
        if old == value:
            return f"{new} (unchanged)"
        return f"{old} → {new}"

    @staticmethod
    def _visible_change(prev, new: "ReleaseSnapshot") -> bool:
        """True when appVersion, chart version or container images differ."""
        if prev is None:
            return True
        return (
            (prev.app_version or "") != (new.app_version or "")
            or (prev.chart_version or "") != (new.chart_version or "")
            or (prev.images or []) != (new.images or [])
        )

    def _render(self, action: str, prev, new: ReleaseSnapshot, notes_info=None) -> str:
        # Mobile-first, plain layout: no highlight, no bold, no italics on
        # the data - a change is signalled by the arrow, an unchanged fact
        # by the explicit "(unchanged)" suffix, so the text alone carries
        # the meaning. (The only background tint available, the code span,
        # renders as a client-themed gray box that proved too heavy; its
        # shade cannot be tuned per-message.) "Label: value" lines with
        # short labels and link text keep every line under ~40 chars so
        # nothing wraps on a phone. Fixed order: what/where -> what changed
        # -> where to look. Sections are separated by a blank line.
        header = [
            f"#### {ACTION_LABEL.get(action, action.upper())} {new.release}",
            f"Namespace: {new.namespace}",
            f"Cluster: {self.cluster}",
        ]
        facts = []
        # A rollback must always get the full card, even when it restores the
        # exact previous images (that is the whole point of a rollback).
        if action == "upgrade" and prev is not None and not self._visible_change(prev, new):
            # Re-deploy with no visible change (values-only at most): keep the
            # ack of the deploy, but say plainly that nothing we can see moved.
            facts.append("**No visible changes** — re-deploy / values-only")
        else:
            if new.app_version or (prev and prev.app_version):
                facts.append(f"App: {self._fmt_version(prev, 'app_version', new.app_version)}")
            facts.append(f"Chart: {self._fmt_version(prev, 'chart_version', new.chart_version)}")
            facts.extend(self._image_lines(prev, new))
        links = []
        if self.cluster_url:
            links.append(f"Cluster: [{self.cluster_url}]({self.cluster_url})")
        if new.urls:
            host = new.urls[0]
            extra = f" (+{len(new.urls) - 1} more)" if len(new.urls) > 1 else ""
            links.append(f"URL: [{host}{extra}](https://{host})")
        if notes_info:
            # When the resolver found a real GitHub Release, link the
            # release page (notes + comments) first; the Commit line
            # always follows, as the exact upstream point the deployed
            # version was built from. The release-notes text itself is
            # deliberately NOT shown (user decision 2026-11-09) - the
            # resolver still returns it in notes_info["text"].
            if notes_info.get("kind") == "release" and notes_info.get("release_url"):
                links.append(f"Release: [{notes_info['tag']}]({notes_info['release_url']})")
            links.append(f"Commit: [{notes_info['sha']}]({notes_info['url']})")
        sections = [header]
        if facts:
            sections.append(facts)
        if links:
            sections.append(links)
        # Two trailing spaces per line: hard breaks, since the webhook renders
        # the body with python-markdown without nl2br.
        return "\n\n".join("  \n".join(section) for section in sections)

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
