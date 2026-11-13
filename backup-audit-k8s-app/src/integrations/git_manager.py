#!/usr/bin/env python3

import os
import shutil
import logging
import subprocess
import threading
import git
from datetime import datetime
from typing import Optional

from core.memory import drop_git_pack_cache

# Set up logging
logger = logging.getLogger(__name__)

# Git configuration
GIT_REMOTE_URL = os.getenv("GIT_REMOTE_URL", "")
GIT_BRANCH = os.getenv("GIT_BRANCH", "master")
GIT_USER_NAME = os.getenv("GIT_USER_NAME", "audit-logger")
GIT_USER_EMAIL = os.getenv("GIT_USER_EMAIL", "audit-logger@kubernetes.local")
GIT_SSH_KEY_PATH = os.getenv("GIT_SSH_KEY_PATH", "/home/audit/.ssh/id_rsa")
GIT_HTTPS_TOKEN = os.getenv("GIT_HTTPS_TOKEN", "")
GIT_USERNAME = os.getenv("GIT_USERNAME", "git")
GIT_AUTH_METHOD = os.getenv("GIT_AUTH_METHOD", "ssh")

# SSH configuration
GIT_SSH_HOST = os.getenv("GIT_SSH_HOST", "")
GIT_SSH_HOSTNAME = os.getenv("GIT_SSH_HOSTNAME", "")
GIT_SSH_PORT = os.getenv("GIT_SSH_PORT", "1022")
GIT_SSH_USER = os.getenv("GIT_SSH_USER", "git")


def get_authenticated_git_url(base_url: str) -> str:
    """Convert Git URL to include authentication if using token method"""
    if (
        GIT_AUTH_METHOD == "token"
        and GIT_HTTPS_TOKEN
        and base_url.startswith("https://")
    ):
        # Parse the URL to embed credentials
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(base_url)
        # Create authenticated URL: https://username:token@host/path
        netloc = f"{GIT_USERNAME}:{GIT_HTTPS_TOKEN}@{parsed.netloc}"
        auth_url = urlunparse(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )
        logger.info(f"Using token authentication for Git operations")
        return auth_url
    return base_url


class GitManager:
    """Handles Git operations for the audit logger"""

    # Wall-clock cap for the network-bound setup steps (clone/fetch/push).
    # Without it, a slow or unreachable GitLab blocks process startup for
    # as long as the TCP layer retries: on 2026-11-13 a fresh-volume full
    # clone outlived the pod's startup probe on 01dev, crash-looping the
    # app for hours (each restart re-ran the same oversized clone).
    GIT_SETUP_TIMEOUT_SECONDS = max(5, int(os.getenv("GIT_SETUP_TIMEOUT_SECONDS", "600")))

    def __init__(self, storage_path: str) -> None:
        self.storage_path = storage_path
        self.git_lock = threading.Lock()
        self.repo: Optional[git.Repo] = None
        self._setup_complete = False
        self._remote_configured = False
        self.last_push_success: Optional[datetime] = None
        self.last_push_error: Optional[str] = None
        # NO setup_git_repository() here: the clone/fetch is network-bound
        # and must not hold up the HTTP server bind (startup probe).
        # api.startup_sequence() runs it in the background before the
        # scheduler starts, and the periodic sync job re-runs it until it
        # completes (each step is capped by GIT_SETUP_TIMEOUT_SECONDS).

    def _run_git(self, args, timeout=GIT_SETUP_TIMEOUT_SECONDS, cwd=None):
        """Run a git subprocess with a wall-clock cap (see
        GIT_SETUP_TIMEOUT_SECONDS). The caller treats a failed or timed-out
        command as "not aligned yet" and retries on the next setup; the
        server keeps serving /health meanwhile."""
        cmd = ["git"]
        if cwd:
            cmd += ["-C", cwd]
        cmd += list(args)
        return subprocess.run(
            cmd,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            capture_output=True,
            text=True,
        )

    def _is_hollow_repo(self) -> bool:
        """No usable refs at all: dangling HEAD and no branches.

        Happens when a clone/fetch is killed mid-write - the .git dir
        survives but references nothing (e.g. HEAD -> refs/heads/.invalid).
        Fetching into such a repo advertises zero haves, so the remote
        streams its entire history on every attempt; wiping and re-cloning
        is the cheap, clean path."""
        if self.repo is None:
            return False
        try:
            self.repo.git.rev_parse("--verify", "HEAD")
            return False
        except Exception:
            pass
        try:
            return not self.repo.git.branch_list().stdout.strip()
        except Exception:
            return True

    def _clone_repository(self):
        """Clone the remote into the storage dir (capped by
        GIT_SETUP_TIMEOUT_SECONDS). Returns a Repo, or None on failure."""
        try:
            auth_url = get_authenticated_git_url(GIT_REMOTE_URL)
            logger.info(
                f"Cloning repository from {GIT_REMOTE_URL} "
                f"(timeout {self.GIT_SETUP_TIMEOUT_SECONDS}s)"
            )
            res = self._run_git(
                ["clone", "--branch", GIT_BRANCH, auth_url, self.storage_path]
            )
            if res.returncode != 0:
                logger.warning(f"Failed to clone repository: {res.stderr.strip()[:400]}")
                return None
            return git.Repo(self.storage_path)
        except subprocess.TimeoutExpired:
            logger.warning(
                f"Failed to clone repository: timed out after "
                f"{self.GIT_SETUP_TIMEOUT_SECONDS}s - the partial clone is "
                "recovered (wiped and re-cloned) on the next setup"
            )
            return None
        except Exception as e:
            logger.warning(f"Failed to clone repository: {e}")
            return None

    def setup_git_repository(self) -> None:
        """Initialize Git repository"""
        if self._setup_complete:
            return

        # Held for the whole method (not just the read-only checks) so this
        # can't interleave with a concurrent commit_changes()/push_to_remote()
        # call from another thread - both take git_lock too.
        with self.git_lock:
            try:
                # The storage dir is the app's own data dir: create it if it
                # isn't there yet. Without this, os.listdir() below raised
                # and the whole setup failed; the scheduler's later retry
                # then hit the git.Repo.init() fallback on a now-non-empty
                # dir and landed on git's default branch (pushes went to
                # the wrong remote ref).
                os.makedirs(self.storage_path, exist_ok=True)

                git_path = os.path.join(self.storage_path, ".git")

                if not os.path.exists(git_path):
                    logger.info("Setting up Git repository...")

                    is_empty = not os.listdir(self.storage_path)

                    # Try to clone if directory is empty and remote is set
                    if GIT_REMOTE_URL and is_empty:
                        self.repo = self._clone_repository()
                        if self.repo:
                            logger.info("Successfully cloned repository")
                            self._remote_configured = True
                        else:
                            # Also happens on a brand-new (commit-less)
                            # remote: "Remote branch <branch> not found in
                            # upstream origin". Init on GIT_BRANCH (not git's
                            # default branch) so the initial commit/push
                            # target the same ref as the configured remote
                            # branch.
                            logger.info("Falling back to repository initialization")
                            self.repo = git.Repo.init(
                                self.storage_path, initial_branch=GIT_BRANCH
                            )
                    else:
                        self.repo = git.Repo.init(
                            self.storage_path, initial_branch=GIT_BRANCH
                        )
                else:
                    logger.info("Git repository already exists")
                    self.repo = git.Repo(self.storage_path)

                    # A killed clone/fetch leaves a .git that references
                    # nothing: every fetch into it would re-download the
                    # whole remote history. Wipe and re-clone instead.
                    if GIT_REMOTE_URL and self._is_hollow_repo():
                        logger.warning(
                            "Existing repository has no usable refs (a "
                            "clone/fetch was likely killed mid-write) - "
                            "wiping and re-cloning"
                        )
                        shutil.rmtree(self.storage_path, ignore_errors=True)
                        os.makedirs(self.storage_path, exist_ok=True)
                        self.repo = self._clone_repository()
                        if self.repo:
                            logger.info("Successfully cloned repository")
                            self._remote_configured = True

                # --- Configure Git user info ---
                if self.repo:
                    try:
                        with self.repo.git.custom_environment(
                            GIT_AUTHOR_NAME=GIT_USER_NAME,
                            GIT_AUTHOR_EMAIL=GIT_USER_EMAIL,
                        ):
                            self.repo.config_writer().set_value(
                                "user", "name", GIT_USER_NAME
                            ).release()
                            self.repo.config_writer().set_value(
                                "user", "email", GIT_USER_EMAIL
                            ).release()
                    except Exception as e:
                        logger.error(f"Failed to configure Git: {e}")

                # --- Configure Git remote and reset to upstream ---
                if GIT_REMOTE_URL and self.repo:
                    try:
                        if "origin" in self.repo.remotes:
                            logger.debug("Removing existing origin remote")
                            self.repo.delete_remote("origin")
                    except Exception:
                        pass

                    try:
                        auth_url = get_authenticated_git_url(GIT_REMOTE_URL)
                        self.repo.create_remote("origin", auth_url)
                        logger.info(f"Configured remote origin: {GIT_REMOTE_URL}")

                        # Align local branch to remote
                        try:
                            # Fetch first: origin/<branch> was just wiped by
                            # the delete_remote() above and only exists
                            # locally again after this fetch, so any "are we
                            # ahead of origin" check must happen after it,
                            # not before.
                            fetch_res = self._run_git(
                                ["fetch", "origin"], cwd=self.storage_path
                            )
                            if fetch_res.returncode != 0:
                                raise RuntimeError(
                                    f"fetch failed: {fetch_res.stderr.strip()[:300]}"
                                )

                            # Warn about (and try to save) any unpushed local
                            # commits before the hard reset below discards
                            # them. This repo may be recovering from a stuck
                            # merge/rebase state, so the reset itself is not
                            # optional - this is best-effort only and must
                            # not block reaching a working state.
                            try:
                                ahead = self.repo.git.rev_list(
                                    f"origin/{GIT_BRANCH}..HEAD", "--count"
                                )
                                if int(ahead) > 0:
                                    logger.warning(
                                        f"Hard reset to origin/{GIT_BRANCH} "
                                        f"will discard {ahead} unpushed "
                                        f"local commit(s)"
                                    )
                                    try:
                                        push_res = self._run_git(
                                            ["push", "origin", f"HEAD:{GIT_BRANCH}"],
                                            cwd=self.storage_path,
                                        )
                                        if push_res.returncode != 0:
                                            raise RuntimeError(
                                                f"push failed: {push_res.stderr.strip()[:300]}"
                                            )
                                        logger.info(
                                            "Saved previously unpushed "
                                            "commits to remote before reset"
                                        )
                                        self._record_push_success()
                                    except Exception as push_e:
                                        logger.warning(
                                            f"Could not push unpushed "
                                            f"commits before reset, they "
                                            f"will be discarded: {push_e}"
                                        )
                                        self._record_push_error(str(push_e))
                            except Exception:
                                # HEAD or origin/<branch> may not exist yet
                                # (fresh repo/remote) - nothing to lose
                                pass

                            self.repo.git.reset("--hard", f"origin/{GIT_BRANCH}")
                            self.repo.git.branch(
                                f"--set-upstream-to=origin/{GIT_BRANCH}",
                                GIT_BRANCH,
                            )
                            logger.info(
                                f"Aligned local repo with origin/{GIT_BRANCH} and set upstream"
                            )
                            self._remote_configured = True
                        except Exception as e:
                            logger.warning(f"Failed to align with remote: {e}")
                    except Exception as e:
                        logger.error(f"Failed to configure remote: {e}")

                if self.repo:
                    self._setup_complete = True
                else:
                    # No repo (a hollow re-clone failed/timed out): leave
                    # setup incomplete so the periodic sync job retries it
                    # on its next cycle.
                    logger.warning(
                        "Git repository setup did not produce a repository - "
                        "will retry on the next sync cycle"
                    )

            except Exception as e:
                logger.error(f"Failed to setup Git repository: {e}")

    def pull_latest_changes(self) -> None:
        """Pull latest changes from remote"""
        try:
            with self.git_lock:
                if not self.repo:
                    logger.debug("Repository not available")
                    return

                try:
                    # Fetch latest changes (capped: a hung network must not
                    # stall the sync job for the full TCP timeout)
                    fetch_res = self._run_git(
                        ["fetch", "origin"], timeout=60, cwd=self.storage_path
                    )
                    if fetch_res.returncode != 0:
                        raise RuntimeError(
                            f"fetch failed: {fetch_res.stderr.strip()[:300]}"
                        )

                    # Check if HEAD is valid (has commits)
                    if not self.repo.head.is_valid():
                        logger.info(
                            "No local commits found, pulling initial content..."
                        )
                        try:
                            self.repo.git.pull("origin", GIT_BRANCH)
                            logger.info(
                                "Successfully pulled initial content from remote"
                            )
                        except Exception as e:
                            logger.debug(f"Could not pull initial content: {e}")
                        return

                    # Check if we're behind the remote
                    try:
                        # Check if origin branch exists
                        if self.repo and f"origin/{GIT_BRANCH}" in self.repo.refs:
                            # Get the current commit hash
                            current_commit = self.repo.head.commit.hexsha

                            # Get the remote commit hash
                            origin_branch_ref = self.repo.refs[f"origin/{GIT_BRANCH}"]
                            remote_commit = origin_branch_ref.commit.hexsha

                            if current_commit != remote_commit:
                                logger.info("Pulling latest changes from remote...")
                                self.repo.git.pull("origin", GIT_BRANCH, "--rebase")
                                logger.info("Successfully pulled latest changes")
                            else:
                                logger.info("Already up to date with remote")
                        else:
                            logger.info(
                                f"No remote {GIT_BRANCH} branch found (new repository)"
                            )
                            # Try to pull anyway
                            try:
                                self.repo.git.pull("origin", GIT_BRANCH)
                                logger.info("Successfully pulled from remote")
                            except Exception as e2:
                                logger.debug(f"Could not pull from remote: {e2}")
                    except Exception as e:
                        logger.debug(f"Could not check remote status: {e}")
                        # Try a simple pull
                        try:
                            if self.repo:
                                self.repo.git.pull("origin", GIT_BRANCH)
                                logger.info("Successfully pulled latest changes")
                        except Exception as e2:
                            logger.debug(f"Could not pull from remote: {e2}")

                except Exception as e:
                    logger.warning(f"Failed to pull from remote: {e}")
        except Exception as e:
            logger.error(f"Failed to pull latest changes: {e}")

    def commit_changes(self) -> None:
        """Commit changes to Git"""
        try:
            with self.git_lock:
                if not self.repo:
                    logger.debug("Repository not available")
                    return

                # Force add all changes and commit
                try:
                    # Add all files (including untracked)
                    self.repo.git.add(".")

                    # On a brand-new repo HEAD is "unborn" (no commits yet) -
                    # index.diff("HEAD") raises in that case instead of
                    # returning a diff, which previously made the very first
                    # commit silently fail forever (nothing to push, nothing
                    # ever fixes it). Treat unborn HEAD as "there are staged
                    # changes" instead of asking HEAD to diff against itself.
                    head_is_valid = self.repo.head.is_valid()
                    has_changes = (
                        not head_is_valid
                        or bool(self.repo.index.diff("HEAD"))
                        or bool(self.repo.untracked_files)
                    )

                    if has_changes:
                        commit_message = (
                            f"Initial commit - {datetime.now().isoformat()}"
                            if not head_is_valid
                            else f"Audit logger changes - {datetime.now().isoformat()}"
                        )

                        self.repo.index.commit(commit_message)
                        logger.info(f"Committed changes to git: {commit_message}")

                        # Set upstream after first commit
                        try:
                            self.repo.git.branch(
                                f"--set-upstream-to=origin/{GIT_BRANCH}", GIT_BRANCH
                            )
                            logger.info(f"Set upstream branch to origin/{GIT_BRANCH}")
                        except Exception as e:
                            logger.debug(f"Could not set upstream branch: {e}")
                    else:
                        logger.info("No changes to commit")
                except Exception as e:
                    logger.error(f"Failed to commit changes: {e}")
        except Exception as e:
            logger.error(f"Failed to commit changes: {e}")

    def _record_push_success(self) -> None:
        # Clear the error before setting the new success timestamp (not
        # after): a concurrent get_readiness_status() read between these two
        # statements should at worst see "no error yet, old success time"
        # rather than "fresh success time, stale error still set".
        self.last_push_error = None
        self.last_push_success = datetime.now()

        # The packs existed to be shipped to the remote; drop them from the
        # page cache so they stop counting against the container memory
        # limit (#309775). Best-effort, never fatal.
        drop_git_pack_cache(self.storage_path)

    def _record_push_error(self, error: str) -> None:
        self.last_push_error = error

    def push_to_remote(self) -> None:
        """Push changes to remote repository"""
        try:
            with self.git_lock:
                if not self.repo:
                    logger.debug("Repository not available")
                    return

                try:
                    # Check if we have commits to push
                    try:
                        # Get the current branch
                        current_branch = self.repo.active_branch.name

                        # Check if origin branch exists
                        try:
                            if self.repo and f"origin/{GIT_BRANCH}" in self.repo.refs:
                                origin_branch = self.repo.refs[f"origin/{GIT_BRANCH}"]
                                # Check if we're ahead of origin
                                ahead_count = len(
                                    list(
                                        self.repo.iter_commits(
                                            f"origin/{GIT_BRANCH}..{current_branch}"
                                        )
                                    )
                                )

                                if ahead_count > 0:
                                    logger.info(
                                        f"Pushing {ahead_count} commits to remote..."
                                    )
                                    self.repo.git.push("origin", current_branch)
                                    logger.info("Successfully pushed changes to remote")
                                    self._record_push_success()
                                else:
                                    logger.info("No commits to push")
                                    self._record_push_success()
                            else:
                                # origin branch doesn't exist, this is the first push
                                logger.info("First push to remote...")
                                self.repo.git.push("--set-upstream", "origin", GIT_BRANCH)
                                logger.info(
                                    "Successfully pushed initial commit to remote"
                                )
                                self._record_push_success()

                        except (KeyError, IndexError):
                            # origin branch doesn't exist, this is the first push
                            logger.info("First push to remote...")
                            self.repo.git.push("--set-upstream", "origin", GIT_BRANCH)
                            logger.info("Successfully pushed initial commit to remote")
                            self._record_push_success()

                    except git.exc.GitCommandError as e:
                        if "no upstream branch" in str(e):
                            # Set upstream and push
                            try:
                                logger.info("Setting upstream and pushing...")
                                self.repo.git.push("--set-upstream", "origin", GIT_BRANCH)
                                logger.info(
                                    "Successfully pushed changes to remote (set upstream)"
                                )
                                self._record_push_success()
                            except Exception as e2:
                                logger.error(f"Failed to push with upstream: {e2}")
                                self._record_push_error(str(e2))
                        else:
                            logger.error(f"Failed to push to remote: {e}")
                            self._record_push_error(str(e))
                except Exception as e:
                    logger.error(f"Failed to push to remote: {e}")
                    self._record_push_error(str(e))
        except Exception as e:
            logger.error(f"Failed to push to remote: {e}")
            self._record_push_error(str(e))

    def remove_changes(self, path: str) -> None:
        """Remove path from Git tracking and commit"""
        try:
            with self.git_lock:
                if not self.repo:
                    logger.debug("Repository not available")
                    return

                self.repo.git.rm("-r", "--cached", path, ignore_unmatch=True)
                self.repo.index.commit(
                    f"Remove {path} from tracking - {datetime.now().isoformat()}"
                )
                logger.info(f"Removed {path} from Git tracking")
        except Exception as e:
            logger.error(f"Failed to remove {path} from Git: {e}")
