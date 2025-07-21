#!/usr/bin/env python3

import os
import logging
import sys
import threading

from core.config import Config
from processors.resource_processor import ResourceProcessor
from managers.storage_manager import StorageManager
from processors.helm_processor import HelmProcessor
from processors.event_processor import EventProcessor
from managers.archive_manager import ArchiveManager
from managers.sync_manager import SyncManager

logger = logging.getLogger(__name__)


class AuditLogger:
    """Main orchestrator for audit logging operations"""

    def __init__(self, storage_path: str):
        self.storage_path = storage_path
        self.config = Config()
        self.archive_lock = threading.Lock()

        # Import managers
        from integrations.git_manager import GitManager
        from integrations.kubernetes_manager import KubernetesManager

        self.git_manager = GitManager(storage_path)
        self.k8s_manager = KubernetesManager()

        # Initialize specialized processors
        self.resource_processor = ResourceProcessor(storage_path, self.config)
        self.storage_manager = StorageManager(storage_path)
        self.helm_processor = HelmProcessor(self.k8s_manager, storage_path)
        self.archive_manager = ArchiveManager(
            storage_path, self.git_manager, self.archive_lock, self.config
        )
        self.sync_manager = SyncManager(
            self.k8s_manager,
            self.resource_processor,
            self.helm_processor,
            self.archive_manager,
            self.storage_manager,
            self.config,
        )
        self.event_processor = EventProcessor(
            self.resource_processor,
            self.helm_processor,
            self.archive_manager,
            self.storage_manager,
            self.git_manager,  # Add the git_manager parameter
        )


def verify_logs_structure(logs_path="/app/logs"):
    """Verify the logs structure"""
    import os

    errors = []

    for ns in os.listdir(logs_path):
        ns_path = os.path.join(logs_path, ns)
        if not os.path.isdir(ns_path) or ns == ".git" or ns == "archived":
            continue

        # Check latest-changes.json
        latest_changes = os.path.join(ns_path, "latest-changes.json")
        if not os.path.exists(latest_changes):
            errors.append(f"Missing latest-changes.json in {ns}")

        # Check namespace structure
        for item in os.listdir(ns_path):
            item_path = os.path.join(ns_path, item)
            if os.path.isfile(item_path) and item != "latest-changes.json":
                errors.append(f"Unexpected file at namespace root: {item_path}")

            if os.path.isdir(item_path) and item != "archived":
                # Check release folder structure
                values_yaml = os.path.join(item_path, "values.yaml")
                resources_dir = os.path.join(item_path, "resources")

                if not os.path.exists(values_yaml):
                    errors.append(f"Missing values.yaml in {item_path}")
                if not os.path.isdir(resources_dir):
                    errors.append(f"Missing resources/ dir in {item_path}")
                else:
                    # Check resources directory
                    for res_file in os.listdir(resources_dir):
                        if not res_file.endswith(".yaml"):
                            errors.append(
                                f"Non-YAML file in resources/: {os.path.join(resources_dir, res_file)}"
                            )

    if errors:
        print("\n==== STRUCTURE VERIFICATION ERRORS ====")
        for err in errors:
            print(err)
        print(f"\nTotal errors: {len(errors)}")
        return False

    print("\n==== STRUCTURE VERIFICATION PASSED ====")
    return True


if __name__ == "__main__":
    ok = verify_logs_structure()
    sys.exit(0 if ok else 1)
