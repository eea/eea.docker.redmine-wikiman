#!/usr/bin/env python3

"""Best-effort resolution of release notes from the deployed app's git repo.

Redmine #310015. The Helm release payload carries the app's source repo
(chart metadata `sources`) and version (`appVersion`, image tags). We build
ordered (repo, tag) candidates and ask GitHub for the first tag that exists;
the tag's commit message (or a GitHub Release body, when one exists) is the
release note. Everything is cached in-memory per (repo, tag) because tags are
immutable, and any failure simply yields None - notifications never wait on
or depend on this.

Only public repos, unauthenticated API (60 req/h per IP is plenty: each
deploy resolves at most a handful of cached lookups).
"""

import logging
import re
import threading

import requests

logger = logging.getLogger(__name__)

VERSION_RE = re.compile(r"(v?\d+(?:\.\d+)+[A-Za-z0-9.\-]*)$")
IMAGE_REF_RE = re.compile(r"^(?:[A-Za-z0-9.-]+/)?(?P<name>[A-Za-z0-9._-]+):(?P<tag>[^:@/]+)")
GITHUB_API = "https://api.github.com"
REQUEST_TIMEOUT = 5


def version_candidates(app_version: str, images: list) -> list:
    """Ordered deduped version/tag candidates.

    Chart appVersion first (the chart author's own version of the app), then
    the trailing version part of each image tag
    (eeacms/danswer:backend-v4.3.9-eea.0.0.120-patch.5 -> v4.3.9-eea.0.0.120-patch.5)."""
    out = []

    def add(v):
        v = (v or "").strip()
        if v and v not in out:
            out.append(v)

    add(app_version)
    for image in images or []:
        ref = IMAGE_REF_RE.match(image.split("@", 1)[0])
        if not ref:
            continue
        m = VERSION_RE.search(ref.group("tag"))
        if m:
            add(m.group(1))
    return out


def repo_candidates(app_version: str, images: list, sources: list) -> list:
    """Ordered deduped owner/repo candidates.

    Chart `sources[0]` first, then for an image like eeacms/danswer:try
    eea/<name> and eeacms/<name> (EEA docker org maps onto the eea git org)."""
    out = []

    def add(r):
        r = (r or "").strip().lower()
        if r and r not in out:
            out.append(r)

    if isinstance(sources, list) and sources:
        add(_repo_from_url(sources[0]))
    for image in images or []:
        org, name = _image_org_name(image)
        if org and name:
            add(f"eea/{name}")
            add(f"{org}/{name}")
    return out


def _repo_from_url(url: str) -> str:
    m = re.search(r"github\.com[/:]([^/]+)/([^/.\s#?]+)", url or "")
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def _image_org_name(image: str):
    """(org, name) from an image ref like eeacms/danswer:tag or docker.io/org/name:tag."""
    base = image.split("@", 1)[0].split(":", 1)[0]
    parts = base.split("/")
    if len(parts) == 3 and "." in parts[0]:
        return parts[1], parts[2]  # registry/org/name
    if len(parts) == 2 and ("." in parts[0] or parts[0] == "library"):
        return None, parts[1]  # registry root namespace: no git org
    if len(parts) == 2:
        return parts[0], parts[1]  # org/name
    return None, parts[-1] if parts else None


class ReleaseNotesResolver:
    """(repo, tag) -> note lookup with in-memory cache and failure memo."""

    def __init__(self, github_token: str = ""):
        self._cache = {}
        self._lock = threading.Lock()
        self._token = github_token

    def resolve(self, app_version: str, images: list, sources: list):
        for tag in version_candidates(app_version, images):
            for repo in repo_candidates(app_version, images, sources):
                result = self._lookup(repo, tag)
                if result:
                    return result
        return None

    def _lookup(self, repo: str, tag: str):
        key = (repo, tag)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        result = self._lookup_uncached(repo, tag)
        with self._lock:
            self._cache[key] = result
        return result

    def _headers(self):
        h = {"Accept": "application/vnd.github+json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _lookup_uncached(self, repo: str, tag: str):
        try:
            ref = requests.get(f"{GITHUB_API}/repos/{repo}/git/ref/tags/{tag}",
                               headers=self._headers(), timeout=REQUEST_TIMEOUT)
            if ref.status_code != 200:
                return None
            object_ = ref.json().get("object", {})
            if object_.get("type") == "tag":
                # annotated tag: peel to the commit
                tag_obj = requests.get(
                    f"{GITHUB_API}/repos/{repo}/git/tags/{object_['sha']}",
                    headers=self._headers(), timeout=REQUEST_TIMEOUT)
                sha = tag_obj.json().get("object", {}).get("sha")
            else:
                sha = object_.get("sha")
            if not sha:
                return None
            return self._note_for(repo, tag, sha)
        except requests.RequestException as e:
            logger.warning(f"Release notes: GitHub lookup failed for {repo}@{tag}: {e}")
            return None

    def _note_for(self, repo: str, tag: str, sha: str):
        # A real GitHub Release body is the best source, when the repo uses them.
        try:
            rel = requests.get(f"{GITHUB_API}/repos/{repo}/releases/tags/{tag}",
                               headers=self._headers(), timeout=REQUEST_TIMEOUT)
            if rel.status_code == 200:
                body = (rel.json().get("body") or "").strip()
                if body:
                    return self._result(repo, tag, sha, "release", body)
        except requests.RequestException:
            pass
        # Otherwise the tag commit's message.
        try:
            commit = requests.get(f"{GITHUB_API}/repos/{repo}/commits/{sha}",
                                  headers=self._headers(), timeout=REQUEST_TIMEOUT)
            if commit.status_code == 200:
                message = (commit.json().get("commit", {}).get("message") or "").strip()
                if message:
                    return self._result(repo, tag, sha, "commit", message.splitlines()[0])
        except requests.RequestException as e:
            logger.warning(f"Release notes: commit fetch failed for {repo}@{sha}: {e}")
        return None

    @staticmethod
    def _result(repo, tag, sha, kind, text):
        short = sha[:7]
        return {
            "repo": repo,
            "tag": tag,
            "sha": short,
            "url": f"https://github.com/{repo}/commit/{sha}",
            "kind": kind,
            "text": " ".join(text.split())[:400],
        }
