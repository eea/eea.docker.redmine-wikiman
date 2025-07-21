#!/usr/bin/env python3

import logging
from datetime import datetime, timezone
from typing import Dict, Any

logger = logging.getLogger(__name__)


class EventProcessor:
    """Handles Kubernetes event processing"""

    def __init__(
        self,
        resource_processor,
        helm_processor,
        archive_manager,
        storage_manager,
        git_manager,
    ):
        self.resource_processor = resource_processor
        self.helm_processor = helm_processor
        self.archive_manager = archive_manager
        self.storage_manager = storage_manager
        self.git_manager = git_manager

    def process_admission_event(self, event: Dict[str, Any]):
        """Process an admission webhook event"""
        if not isinstance(event, dict):
            logger.warning("Event is not a dictionary, skipping")
            return

        request = event.get("request", {})
        operation = request.get("operation", "")
        if operation == "DELETE":
            obj = request.get("oldObject", {})
        else:
            obj = request.get("object", {})

        if not obj:
            logger.warning("No object found in event")
            return

        # FIXED: Add early skip check to avoid unnecessary processing
        if self.resource_processor.should_skip_resource(obj):
            resource_kind = obj.get("kind", "Unknown")
            resource_name = obj.get("metadata", {}).get("name", "Unknown")
            logger.info(f"Skipping excluded resource: {resource_kind} {resource_name}")
            return

        # Fix namespace extraction logic
        resource_kind = obj.get("kind", "Unknown")
        resource_name = obj.get("metadata", {}).get("name", "Unknown")
        
        # Correct namespace extraction based on resource kind
        if resource_kind == "namespace":
            namespace = obj.get("metadata", {}).get("name", "")
        else:
            namespace = obj.get("metadata", {}).get("namespace", "")

        logger.info(f"Processing {operation} for {resource_kind} {resource_name} in namespace {namespace}")

        try:
            if operation == "DELETE":
                self.handle_delete(obj, resource_kind, request)
            else:
                self.handle_create_update(obj, resource_kind, request)

            # Commit and push changes after processing
            logger.info(
                f"Starting git operations for {operation} {resource_kind} {resource_name}"
            )

            # Check git status before commit
            try:
                status = self.git_manager.repo.git.status("--porcelain")
                logger.info(f"Git status before commit: {status}")
            except Exception as e:
                logger.error(f"Failed to get git status: {e}")

            self.git_manager.commit_changes()

            # Check git status after commit
            try:
                status = self.git_manager.repo.git.status("--porcelain")
                logger.info(f"Git status after commit: {status}")
            except Exception as e:
                logger.error(f"Failed to get git status: {e}")

            self.git_manager.push_to_remote()
            logger.info(
                f"Successfully completed git operations for {operation} {resource_kind} {resource_name}"
            )

        except Exception as e:
            logger.error(
                f"Error processing {operation} for {resource_kind} {resource_name}: {e}"
            )
            # Still try to commit any changes that might have been made
            try:
                logger.info(
                    "Attempting to commit/push changes despite processing error"
                )
                self.git_manager.commit_changes()
                self.git_manager.push_to_remote()
                logger.info(
                    "Successfully committed and pushed changes despite processing error"
                )
            except Exception as git_error:
                logger.error(f"Failed to commit/push changes: {git_error}")

    def handle_delete(
        self, obj: Dict[str, Any], resource_kind: str, request: Dict[str, Any]
    ):
        """Handle DELETE operations"""
        try:
            release = self.resource_processor.get_helm_release_name(obj)
            user = self.get_original_user(obj, request)

            # Get correct namespace for change tracking
            if resource_kind == "namespace":
                namespace = obj.get("metadata", {}).get("name", "")
            else:
                namespace = obj.get("metadata", {}).get("namespace", "")

            archived_path = self.archive_manager.archive_resource(obj, release, user)
            logger.info(f"Archived deleted {resource_kind}: {archived_path}")

            # Save change info for DELETE operations
            change_info = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operation": "DELETE",
                "user": user,
                "resource_kind": resource_kind,
                "resource_name": obj.get("metadata", {}).get("name", ""),
                "namespace": namespace,
                "release": release,
            }

            self.storage_manager.save_latest_change(namespace, change_info)

        except Exception as e:
            logger.error(f"Error handling DELETE for {resource_kind}: {e}")

    def handle_create_update(
        self, obj: Dict[str, Any], resource_kind: str, request: Dict[str, Any]
    ):
        """Handle CREATE and UPDATE operations"""
        try:
            # Get Helm release name
            release = self.resource_processor.get_helm_release_name(obj)
            
            # Fix namespace extraction logic
            if resource_kind == "namespace":
                namespace = obj.get("metadata", {}).get("name", "")
            else:
                namespace = obj.get("metadata", {}).get("namespace", "")

            # FIXED: Process Helm release BEFORE saving the resource to ensure directory structure exists
            if release:
                logger.debug(f"Processing Helm release '{release}' for {resource_kind} {obj.get('metadata', {}).get('name', '')}")
                self.helm_processor.process_helm_release(release, namespace)

            # Save the resource
            self.resource_processor.save_resource(obj, release)

            # Save change info
            change_info = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operation": request.get("operation", "UNKNOWN"),
                "user": self.get_original_user(obj, request),
                "resource_kind": resource_kind,
                "resource_name": obj.get("metadata", {}).get("name", ""),
                "namespace": namespace,
                "release": release,
            }

            self.storage_manager.save_latest_change(namespace, change_info)

        except Exception as e:
            logger.error(f"Error handling CREATE/UPDATE for {resource_kind}: {e}")

    def get_original_user(self, obj: Dict[str, Any], request: Dict[str, Any]) -> str:
        """Extract original user from request"""
        if not isinstance(request, dict):
            return "unknown"

        user_info = request.get("userInfo", {})
        if not isinstance(user_info, dict):
            return "unknown"

        username = user_info.get("username", "unknown")
        if username == "system:serviceaccount:audit-system:audit-logger":
            metadata = obj.get("metadata", {})
            if isinstance(metadata, dict):
                annotations = metadata.get("annotations", {})
                if isinstance(annotations, dict):
                    original_user = annotations.get(
                        "audit-logger.kubernetes.io/original-user"
                    )
                    if original_user:
                        return original_user

        return username
