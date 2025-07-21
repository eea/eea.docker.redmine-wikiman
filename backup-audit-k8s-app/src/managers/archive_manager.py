import os
import yaml
import logging
import subprocess
import shutil
from datetime import datetime, timezone
from typing import Dict, Any, Set

logger = logging.getLogger(__name__)


class ArchiveManager:
    """Handles all archiving operations for resources, namespaces, and releases"""

    def __init__(self, storage_path: str, git_manager, archive_lock, config):
        self.storage_path = storage_path
        self.git_manager = git_manager
        self.archive_lock = archive_lock
        self.config = config

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

        # Remove original file
        if os.path.exists(original_path):
            os.remove(original_path)
            logger.info(f"Removed from active storage: {original_path}")

        return archived_path

    def archive_missing_namespaces(self, k8s_namespaces: Set[str]):
        """Archive namespaces that don't exist in Kubernetes"""
        try:
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
                archived_path = os.path.join(self.storage_path, "archived", ns_name)

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
            for ns_name, k8s_releases in k8s_releases_by_namespace.items():
                ns_path = os.path.join(self.storage_path, ns_name)
                if not os.path.exists(ns_path):
                    continue

                storage_releases = set()
                for item in os.listdir(ns_path):
                    item_path = os.path.join(ns_path, item)
                    if os.path.isdir(item_path) and not item.startswith("archived"):
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
                        os.makedirs(os.path.dirname(archived_path), exist_ok=True)
                        try:
                            if os.path.exists(archived_path):
                                subprocess.run(["rm", "-rf", archived_path], check=True)
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
