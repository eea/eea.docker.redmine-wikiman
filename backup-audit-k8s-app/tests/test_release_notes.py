#!/usr/bin/env python3

"""Unit tests for the git-based release-notes resolver (#310015).

Run: cd backup-audit-k8s-app && python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
import unittest.mock as mock

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, SRC)

from integrations.release_notes import (  # noqa: E402
    ReleaseNotesResolver,
    repo_candidates,
    version_candidates,
)


class FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class CandidateTest(unittest.TestCase):
    def test_version_candidates_from_appversion_and_image(self):
        got = version_candidates(
            "v4.3.9-eea.0.0.120-patch.5",
            ["eeacms/danswer:backend-v4.3.9-eea.0.0.120-patch.5", "busybox:1.36"],
        )
        self.assertEqual(got, ["v4.3.9-eea.0.0.120-patch.5", "1.36"])

    def test_version_candidates_dedupes(self):
        got = version_candidates("1.2.3", ["eeacms/x:app-1.2.3"])
        self.assertEqual(got, ["1.2.3"])

    def test_repo_candidates_prefers_sources_and_maps_eeacms_to_eea(self):
        got = repo_candidates(
            "v1",
            ["eeacms/danswer:backend-v1"],
            ["https://github.com/onyx-dot-app/onyx"],
        )
        self.assertEqual(got, ["onyx-dot-app/onyx", "eea/danswer", "eeacms/danswer"])

    def test_repo_candidates_registry_prefixed_image(self):
        got = repo_candidates("v1", ["docker.io/eeacms/onyx:v1"], [])
        self.assertEqual(got, ["eea/onyx", "eeacms/onyx"])


class ResolveTest(unittest.TestCase):
    def _patched(self, responses):
        patcher = mock.patch("integrations.release_notes.requests.get")
        self.addCleanup(patcher.stop)
        m = patcher.start()
        m.side_effect = [FakeResp(s, p) for s, p in responses]
        return m

    def test_resolve_uses_first_matching_tag_and_commit_message(self):
        m = self._patched([
            (404, {}),  # onyx-dot-app/onyx @ v4.3.9...
            (200, {"object": {"type": "commit", "sha": "66c1a9fa" * 5}}),
            (404, {}),  # no GitHub Release
            (200, {"commit": {"message": "fix(web): bump Next.js to 16.4.3\n\ndetails"}}),
        ])
        r = ReleaseNotesResolver().resolve(
            "v4.3.9-eea.0.0.120-patch.5",
            ["eeacms/danswer:backend-v4.3.9-eea.0.0.120-patch.5"],
            ["https://github.com/onyx-dot-app/onyx"],
        )
        self.assertEqual(r["text"], "fix(web): bump Next.js to 16.4.3")
        self.assertEqual(r["sha"], "66c1a9f")
        self.assertEqual(r["kind"], "commit")
        self.assertTrue(r["url"].endswith("/commit/" + "66c1a9fa" * 5))
        urls = [c.args[0] for c in m.call_args_list]
        self.assertTrue(
            any("/repos/eea/danswer/git/ref/tags/v4.3.9-eea.0.0.120-patch.5" in u for u in urls),
            urls)

    def test_release_body_preferred_over_commit(self):
        self._patched([
            (200, {"object": {"type": "commit", "sha": "abc12345" * 5}}),
            (200, {"body": "Big feature release."}),
        ])
        r = ReleaseNotesResolver().resolve("v9", ["eeacms/app:app-v9"], [])
        self.assertEqual(r["text"], "Big feature release.")
        self.assertEqual(r["kind"], "release")

    def test_annotated_tag_is_peeled(self):
        self._patched([
            (200, {"object": {"type": "tag", "sha": "tagobjsha" * 4}}),
            (200, {"object": {"sha": "deadbeef" * 5}}),
            (404, {}),
            (200, {"commit": {"message": "patch: thing"}}),
        ])
        r = ReleaseNotesResolver().resolve("v1", ["eeacms/app:app-v1"], [])
        self.assertEqual(r["text"], "patch: thing")
        self.assertEqual(r["sha"], "deadbee")

    def test_all_failures_yield_none(self):
        self._patched([(500, {}), (500, {})])
        r = ReleaseNotesResolver().resolve("v1", ["eeacms/app:app-v1"], [])
        self.assertIsNone(r)

    def test_result_is_cached(self):
        m = self._patched([
            (200, {"object": {"type": "commit", "sha": "abc" * 10}}),
            (404, {}),
            (200, {"commit": {"message": "one"}}),
        ])
        r = ReleaseNotesResolver()
        self.assertEqual(r.resolve("v1", ["eeacms/app:app-v1"], [])["text"], "one")
        calls_after_first = len(m.call_args_list)
        self.assertEqual(r.resolve("v1", ["eeacms/app:app-v1"], [])["text"], "one")
        self.assertEqual(len(m.call_args_list), calls_after_first, "second resolve must hit cache")
