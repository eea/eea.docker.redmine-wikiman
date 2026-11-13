import os
import yaml
import logging
import subprocess
import shutil
from datetime import datetime, timezone
from typing import Dict, Any, Set

from core.memory import drop_file_cache

logger = logging.getLogger(__name__)


class ArchiveManager:
    """Handles all archiving operations for resources, namespaces, and releases"""

    def __init__(self, storage_path: str, git_manager, archive_lock, config,
                 k8s_manager=None):
        self.storage_path = storage_path
        self.git_manager = git_manager
        self.archive_lock = archive_lock
        self.config = config
        # Used to re-verify deletion candidates against the cluster before
        # archiving their audit files (None in tests that don't exercise
        # archive_missing_standalone_resources).
        self.k8s_manager = k8s_manager

    def archive_resource(
        self, resource: Dict[str, Any], release: str = "", user: str = "unknown"
    ) -> str:
        """Archive a resource and remove it from active storage"""
        if not isinstance(resource, dict):
            return ""

        kind = resource.get("kind", "Unknown").lower()
        metadata = resource.get("metadata", {})

        if not isinstance(metadata, dict):
            return ""

        name = metadata.get("name", "unknown")
        if kind == "namespace":
            namespace = name
        else:
            namespace = metadata.get("namespace", "default")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

        if release:
            original_path = os.path.join(
                self.storage_path,
                namespace,
                release,
                "resources",
                f"{kind}-{name}.yaml",
            )
            archived_path = os.path.join(
                self.storage_path,
                "archived",
                namespace,
                release,
                "resources",
                f"{kind}-{name}-{timestamp}.yaml",
            )
        else:
            original_path = os.path.join(
                self.storage_path,
                namespace,
                "standalone-resources",
                f"{kind}-{name}.yaml",
            )
            archived_path = os.path.join(
                self.storage_path,
                "archived",
                namespace,
                "standalone-resources",
                f"{kind}-{name}-{timestamp}.yaml",
            )

        with self.archive_lock:
            os.makedirs(os.path.dirname(archived_path), exist_ok=True)

            # Copy and enrich the resource
            archived_resource = resource.copy()
            archived_resource["_archived"] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operation": "DELETE",
                "user": user,
                "original_path": original_path,
            }

            # Write to archive
            with open(archived_path, "w") as f:
                yaml.dump(
                    archived_resource,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    indent=2,
                )

            drop_file_cache(archived_path)

            # Remove original file
            if os.path.exists(original_path):
                os.remove(original_path)
                logger.info(f"Removed from active storage: {original_path}")

        return archived_path

    def archive_missing_namespaces(self, k8s_namespaces: Set[str]):
        """Archive namespaces that don't exist in Kubernetes"""
        try:
            with self.archive_lock:
                storage_namespaces = set()

                if os.path.exists(self.storage_path):
                    for item in os.listdir(self.storage_path):
                        item_path = os.path.join(self.storage_path, item)
                        if (
                            os.path.isdir(item_path)
                            and not item.startswith("archived")
                            and item != ".git"
                        ):
                            # Check if it's a valid namespace folder
                            has_resources = os.path.isdir(
                                os.path.join(item_path, "standalone-resources")
                            )
                            has_releases = any(
                                os.path.isfile(
                                    os.path.join(item_path, subdir, "values.yaml")
                                )
                                for subdir in os.listdir(item_path)
                                if os.path.isdir(os.path.join(item_path, subdir))
                            )

                            if has_resources or has_releases:
                                storage_namespaces.add(item)

                missing_namespaces = storage_namespaces - k8s_namespaces

                for ns_name in missing_namespaces:
                    if ns_name in self.config.get_excluded_namespaces():
                        continue

                    logger.info(f"Archiving missing namespace: {ns_name}")

                    source_path = os.path.join(self.storage_path, ns_name)
                    archived_path = os.path.join(
                        self.storage_path, "archived", ns_name
                    )

                    if os.path.exists(source_path):
                        try:
                            self._move_files(source_path, archived_path)
                            logger.info(f"Merged and archived namespace: {ns_name}")
                        except Exception as e:
                            logger.error(f"Failed to archive {ns_name}: {e}")

        except Exception as e:
            logger.error(f"Failed to archive missing namespaces: {e}")

    def archive_missing_releases(self, k8s_releases_by_namespace: Dict[str, Set[str]]):
        """Archive releases that don't exist in Kubernetes"""
        try:
            with self.archive_lock:
                for ns_name, k8s_releases in k8s_releases_by_namespace.items():
                    ns_path = os.path.join(self.storage_path, ns_name)
                    if not os.path.exists(ns_path):
                        continue

                    storage_releases = set()
                    for item in os.listdir(ns_path):
                        item_path = os.path.join(ns_path, item)
                        if os.path.isdir(item_path) and not item.startswith(
                            "archived"
                        ):
                            values_path = os.path.join(item_path, "values.yaml")
                            if os.path.exists(values_path):
                                storage_releases.add(item)

                    missing_releases = storage_releases - k8s_releases

                    for release_name in missing_releases:
                        logger.info(
                            f"Archiving missing release: {release_name} in {ns_name}"
                        )

                        source_path = os.path.join(ns_path, release_name)
                        archived_path = os.path.join(
                            self.storage_path, "archived", ns_name, release_name
                        )

                        if os.path.exists(source_path):
                            os.makedirs(
                                os.path.dirname(archived_path), exist_ok=True
                            )
                            try:
                                if os.path.exists(archived_path):
                                    subprocess.run(
                                        ["rm", "-rf", archived_path], check=True
                                    )
                                    logger.info(
                                        f"Removed existing archived release: {archived_path}"
                                    )
                                subprocess.run(
                                    ["mv", source_path, archived_path], check=True
                                )
                                logger.info(
                                    f"Moved release {release_name} in {ns_name} to archived"
                                )
                            except subprocess.CalledProcessError as e:
                                logger.error(
                                    f"Failed to move release {release_name} to archived: {e}"
                                )
                            except Exception as e:
                                logger.error(
                                    f"Failed to move release {release_name} to archived: {e}"
                                )

        except Exception as e:
            logger.error(f"Failed to archive missing releases: {e}")

    def archive_missing_standalone_resources(
        self, k8s_namespaces, active_standalone
    ):
        """Archive audit files of standalone resources that no longer exist.

        active_standalone maps namespace -> set of <kind>-<name>.yaml
        basenames written by the latest sync pass. A file missing from that
        set is a deletion candidate; candidates are re-verified against the
        cluster in ONE list call per (namespace, kind) before moving (a
        transient listing failure could have dropped live resources from
        the set; a failed list keeps every candidate). Moves are plain
        renames, so git records them as R100 like release archiving."""
        try:
            for ns_name in k8s_namespaces:
                if ns_name in self.config.get_excluded_namespaces():
                    continue
                if ns_name not in active_standalone:
                    # No sync pass has covered this namespace yet - nothing
                    # to compare against, skip (conservative). An empty set
                    # is NOT skipped: it means the last sync saw zero
                    # standalone resources, so every stored file is a
                    # candidate.
                    continue
                active = active_standalone[ns_name]
                standalone_path = os.path.join(
                    self.storage_path, ns_name, "standalone-resources"
                )
                if not os.path.isdir(standalone_path):
                    continue

                # Group candidates by kind so each kind is verified with a
                # single cluster list call, not one call per file (on a
                # busy namespace a per-file list is an N+1 that took ~90 ms
                # per file in 2026-11-13 01dev: ~25 k stale files would
                # have blocked the initial sync for hours).
                candidates_by_kind = {}
                for fname in sorted(os.listdir(standalone_path)):
                    if not fname.endswith(".yaml") or fname in active:
                        continue
                    stem = fname[: -len(".yaml")]
                    kind, _, name = stem.partition("-")
                    candidates_by_kind.setdefault(kind, []).append(
                        (fname, name)
                    )

                for kind, candidates in candidates_by_kind.items():
                    if self.k8s_manager is None:
                        continue
                    live_names = self.k8s_manager.list_kind_names(
                        ns_name, kind
                    )
                    if live_names is None:
                        # Unverifiable: keep every candidate (conservative)
                        logger.warning(
                            f"Could not list {kind} in {ns_name} - "
                            f"keeping {len(candidates)} candidate(s) "
                            "unverified"
                        )
                        continue

                    for fname, name in candidates:
                        if name in live_names:
                            continue
                        stem = fname[: -len(".yaml")]

                        logger.info(
                            f"Archiving deleted standalone resource: "
                            f"{ns_name}/{kind}/{name}"
                        )
                        archived_dir = os.path.join(
                            self.storage_path, "archived", ns_name, "standalone-resources"
                        )
                        with self.archive_lock:
                            os.makedirs(archived_dir, exist_ok=True)
                            dest = os.path.join(archived_dir, fname)
                            if os.path.exists(dest):
                                ts = datetime.now(timezone.utc).strftime(
                                    "%Y%m%d-%H%M%S"
                                )
                                dest = os.path.join(
                                    archived_dir, f"{stem}-{ts}.yaml"
                                )
                            shutil.move(os.path.join(standalone_path, fname), dest)
                            drop_file_cache(dest)
                            logger.info(f"Moved to archived: {dest}")
        except Exception as e:
            logger.error(
                f"Failed to archive deleted standalone resources: {e}"
            )

    def _move_files(self, source_path: str, dest_path: str):
        """Move directory contents into destination, merging if needed"""
        if not os.path.exists(source_path):
            return

        os.makedirs(dest_path, exist_ok=True)

        for root, dirs, files in os.walk(source_path):
            # Compute destination path for this level
            relative_path = os.path.relpath(root, source_path)
            target_root = os.path.join(dest_path, relative_path)

            os.makedirs(target_root, exist_ok=True)

            # Copy files
            for file in files:
                src_file = os.path.join(root, file)
                dst_file = os.path.join(target_root, file)
                shutil.copy2(src_file, dst_file)

        # Remove source after copy
        shutil.rmtree(source_path)
