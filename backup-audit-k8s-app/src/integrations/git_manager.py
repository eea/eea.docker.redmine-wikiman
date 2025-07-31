#!/usr/bin/env python3

import os
import logging
import threading
import git
from datetime import datetime
from typing import Optional

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
GIT_SSH_HOST = os.getenv("GIT_SSH_HOST", "git.storiette.ro")
GIT_SSH_HOSTNAME = os.getenv("GIT_SSH_HOSTNAME", "git.storiette.ro")
GIT_SSH_PORT = os.getenv("GIT_SSH_PORT", "1022")
GIT_SSH_USER = os.getenv("GIT_SSH_USER", "git")


def get_authenticated_git_url(base_url: str) -> str:
    """Convert Git URL to include authentication if using token method"""
    if GIT_AUTH_METHOD == "token" and GIT_HTTPS_TOKEN and base_url.startswith("https://"):
        # Parse the URL to embed credentials
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(base_url)
        # Create authenticated URL: https://username:token@host/path
        netloc = f"{GIT_USERNAME}:{GIT_HTTPS_TOKEN}@{parsed.netloc}"
        auth_url = urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
        logger.info(f"Using token authentication for Git operations")
        return auth_url
    return base_url


class GitManager:
    """Handles Git operations for the audit logger"""

    def __init__(self, storage_path: str) -> None:
        self.storage_path = storage_path
        self.git_lock = threading.Lock()
        self.repo: Optional[git.Repo] = None
        self._setup_complete = False
        self._remote_configured = False
        self.setup_git_repository()

    def setup_git_repository(self) -> None:
        """Initialize Git repository"""
        if self._setup_complete:
            logger.debug("Git repository already setup, skipping")
            return

        try:
            git_path = os.path.join(self.storage_path, ".git")

            if not os.path.exists(git_path):
                logger.info("Setting up Git repository...")

                is_empty = not os.listdir(self.storage_path)

                # Try to clone if directory is empty and remote is set
                if GIT_REMOTE_URL and is_empty:
                    try:
                        auth_url = get_authenticated_git_url(GIT_REMOTE_URL)
                        logger.info(f"Cloning repository from {GIT_REMOTE_URL}")
                        self.repo = git.Repo.clone_from(
                            auth_url, self.storage_path, branch=GIT_BRANCH
                        )
                        logger.info("Successfully cloned repository")
                        self._remote_configured = True
                    except Exception as e:
                        logger.warning(f"Failed to clone repository: {e}")
                        logger.info("Falling back to repository initialization")
                        self.repo = git.Repo.init(self.storage_path)
                else:
                    self.repo = git.Repo.init(self.storage_path)
            else:
                logger.info("Git repository already exists")
                self.repo = git.Repo(self.storage_path)

            # --- Configure Git user info ---
            if self.repo:
                try:
                    with self.repo.git.custom_environment(
                        GIT_AUTHOR_NAME=GIT_USER_NAME, GIT_AUTHOR_EMAIL=GIT_USER_EMAIL
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
                        self.repo.git.fetch("origin")
                        self.repo.git.reset("--hard", "origin/master")
                        self.repo.git.branch(
                            "--set-upstream-to=origin/master", "master"
                        )
                        logger.info(
                            "Aligned local repo with origin/master and set upstream"
                        )
                        self._remote_configured = True
                    except Exception as e:
                        logger.warning(f"Failed to align with remote: {e}")
                except Exception as e:
                    logger.error(f"Failed to configure remote: {e}")

            self._setup_complete = True

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
                    # Fetch latest changes
                    self.repo.git.fetch("origin")

                    # Check if HEAD is valid (has commits)
                    if not self.repo.head.is_valid():
                        logger.info(
                            "No local commits found, pulling initial content..."
                        )
                        try:
                            self.repo.git.pull("origin", "master")
                            logger.info(
                                "Successfully pulled initial content from remote"
                            )
                        except Exception as e:
                            logger.debug(f"Could not pull initial content: {e}")
                        return

                    # Check if we're behind the remote
                    try:
                        # Check if origin/master exists
                        if self.repo and "origin/master" in self.repo.refs:
                            # Get the current commit hash
                            current_commit = self.repo.head.commit.hexsha

                            # Get the remote commit hash
                            origin_master_ref = self.repo.refs["origin/master"]
                            remote_commit = origin_master_ref.commit.hexsha

                            if current_commit != remote_commit:
                                logger.info("Pulling latest changes from remote...")
                                self.repo.git.pull("origin", "master", "--rebase")
                                logger.info("Successfully pulled latest changes")
                            else:
                                logger.info("Already up to date with remote")
                        else:
                            logger.info(
                                "No remote master branch found (new repository)"
                            )
                            # Try to pull anyway
                            try:
                                self.repo.git.pull("origin", "master")
                                logger.info("Successfully pulled from remote")
                            except Exception as e2:
                                logger.debug(f"Could not pull from remote: {e2}")
                    except Exception as e:
                        logger.debug(f"Could not check remote status: {e}")
                        # Try a simple pull
                        try:
                            if self.repo:
                                self.repo.git.pull("origin", "master")
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

                    # Check if there are any staged changes
                    if self.repo.index.diff("HEAD") or self.repo.untracked_files:
                        # Check if this is the first commit
                        try:
                            # Try to get HEAD commit
                            self.repo.head.commit  # Just check if it exists
                            commit_message = (
                                f"Audit logger changes - {datetime.now().isoformat()}"
                            )
                        except ValueError:
                            # This is the first commit
                            commit_message = (
                                f"Initial commit - {datetime.now().isoformat()}"
                            )

                        self.repo.index.commit(commit_message)
                        logger.info(f"Committed changes to git: {commit_message}")

                        # Set upstream after first commit
                        try:
                            self.repo.git.branch(
                                "--set-upstream-to=origin/master", "master"
                            )
                            logger.info("Set upstream branch to origin/master")
                        except Exception as e:
                            logger.debug(f"Could not set upstream branch: {e}")
                    else:
                        logger.info("No changes to commit")
                except Exception as e:
                    logger.error(f"Failed to commit changes: {e}")
        except Exception as e:
            logger.error(f"Failed to commit changes: {e}")

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

                        # Check if origin/master exists
                        try:
                            if self.repo and "origin/master" in self.repo.refs:
                                origin_master = self.repo.refs["origin/master"]
                                # Check if we're ahead of origin
                                ahead_count = len(
                                    list(
                                        self.repo.iter_commits(
                                            f"origin/master..{current_branch}"
                                        )
                                    )
                                )

                                if ahead_count > 0:
                                    logger.info(
                                        f"Pushing {ahead_count} commits to remote..."
                                    )
                                    self.repo.git.push("origin", current_branch)
                                    logger.info("Successfully pushed changes to remote")
                                else:
                                    logger.info("No commits to push")
                            else:
                                # origin/master doesn't exist, this is the first push
                                logger.info("First push to remote...")
                                self.repo.git.push("--set-upstream", "origin", "master")
                                logger.info(
                                    "Successfully pushed initial commit to remote"
                                )

                        except (KeyError, IndexError):
                            # origin/master doesn't exist, this is the first push
                            logger.info("First push to remote...")
                            self.repo.git.push("--set-upstream", "origin", "master")
                            logger.info("Successfully pushed initial commit to remote")

                    except git.exc.GitCommandError as e:
                        if "no upstream branch" in str(e):
                            # Set upstream and push
                            try:
                                logger.info("Setting upstream and pushing...")
                                self.repo.git.push("--set-upstream", "origin", "master")
                                logger.info(
                                    "Successfully pushed changes to remote (set upstream)"
                                )
                            except Exception as e2:
                                logger.error(f"Failed to push with upstream: {e2}")
                        else:
                            logger.error(f"Failed to push to remote: {e}")
                except Exception as e:
                    logger.error(f"Failed to push to remote: {e}")
        except Exception as e:
            logger.error(f"Failed to push to remote: {e}")

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
