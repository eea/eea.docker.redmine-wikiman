import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any

logger = logging.getLogger(__name__)


class StorageManager:
    """Handles file system operations and storage management"""

    def __init__(self, storage_path: str):
        self.storage_path = storage_path

    def save_latest_change(self, namespace: str, change: Dict[str, Any]):
        """Save latest change information with enhanced tracking"""
        try:
            change_file = os.path.join(self.storage_path, namespace, "latest-changes.json")
            os.makedirs(os.path.dirname(change_file), exist_ok=True)
            
            # Add additional metadata if not present
            if "timestamp" not in change:
                change["timestamp"] = datetime.now(timezone.utc).isoformat()
            
            with open(change_file, "w") as f:
                json.dump(change, f, indent=2)
            
            logger.info(f"Updated latest changes for namespace {namespace}: {change.get('operation', 'UNKNOWN')} {change.get('resource_kind', 'Unknown')} {change.get('resource_name', 'Unknown')}")
            
        except Exception as e:
            logger.error(f"Failed to save latest change for {namespace}: {e}")

    def update_namespace_latest_changes(self, namespace: str):
        """Update latest changes for namespace"""
        try:
            change_info = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operation": "INITIAL_SYNC",
                "user": "audit-logger",
                "resource_kind": "Namespace",
                "resource_name": namespace,
                "namespace": namespace,
                "release": "",
                "message": "Namespace processed during initial sync",
            }
            self.save_latest_change(namespace, change_info)
        except Exception as e:
            logger.error(f"Failed to update latest changes for {namespace}: {e}")
