#!/usr/bin/env python3

import os
import yaml
import logging
from typing import Dict, Any, Optional
from core.models import ResourceMetadata

logger = logging.getLogger(__name__)


class ResourceProcessor:
    """Handles Kubernetes resource processing and storage"""

    def __init__(self, storage_path: str, config):
        self.storage_path = storage_path
        self.config = config

    def clean_resource(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        """Remove managed fields from resource"""
        if not isinstance(resource, dict):
            return resource

        cleaned = resource.copy()
        if "metadata" in cleaned:
            metadata = cleaned["metadata"]
            if isinstance(metadata, dict):
                for field in [
                    "managedFields",
                    "creationTimestamp",
                    "generation",
                    "resourceVersion",
                    "uid",
                ]:
                    metadata.pop(field, None)
        return cleaned

    def get_helm_release_name(self, resource: Dict[str, Any]) -> str:
        """Extract Helm release name from resource with improved detection"""
        if not isinstance(resource, dict):
            return ""

        metadata = resource.get("metadata", {})
        if not isinstance(metadata, dict):
            return ""

        resource_name = metadata.get("name", "unknown")
        resource_kind = resource.get("kind", "Unknown")
        namespace = metadata.get("namespace", "default")
        
        logger.debug(f"Attempting to extract Helm release for {resource_kind} {resource_name} in namespace {namespace}")

        # Check annotations first (Helm 3+)
        annotations = metadata.get("annotations", {})
        if isinstance(annotations, dict):
            # Try multiple annotation patterns
            for annotation in [
                "meta.helm.sh/release-name",
                "helm.sh/release-name",
                "app.kubernetes.io/instance",
            ]:
                release = annotations.get(annotation)
                if release:
                    logger.debug(f"Found Helm release '{release}' via annotation '{annotation}' for {resource_kind} {resource_name}")
                    return release

        # Check labels for various Helm label patterns
        labels = metadata.get("labels", {})
        if isinstance(labels, dict):
            # Try multiple label patterns - ONLY actual Helm release labels
            for label in [
                "app.kubernetes.io/instance",
                "release",
                "helm.sh/release-name",
            ]:
                release = labels.get(label)
                if release:
                    logger.debug(f"Found Helm release '{release}' via label '{label}' for {resource_kind} {resource_name}")
                    return release

        # Check owner references for Helm releases
        owner_refs = metadata.get("ownerReferences", [])
        if isinstance(owner_refs, list):
            for owner in owner_refs:
                if isinstance(owner, dict):
                    owner_kind = owner.get("kind", "")
                    owner_name = owner.get("name", "")
                    if owner_kind == "HelmRelease" and owner_name:
                        logger.debug(f"Found Helm release '{owner_name}' via owner reference for {resource_kind} {resource_name}")
                        return owner_name

        logger.debug(f"No Helm release found for {resource_kind} {resource_name} in namespace {namespace}")
        return ""

    def get_resource_metadata(self, resource: Dict[str, Any]) -> ResourceMetadata:
        """Extract metadata from resource"""
        if not isinstance(resource, dict):
            return ResourceMetadata(name="unknown", namespace="default", kind="Unknown")

        metadata = resource.get("metadata", {})
        if not isinstance(metadata, dict):
            return ResourceMetadata(name="unknown", namespace="default", kind="Unknown")

        return ResourceMetadata(
            name=metadata.get("name", "unknown"),
            namespace=metadata.get("namespace", "default"),
            kind=resource.get("kind", "Unknown"),
            release=self.get_helm_release_name(resource),
        )

    def get_resource_path(self, resource: Dict[str, Any], release: str = "") -> str:
        """Generate file path for storing a resource"""
        if not isinstance(resource, dict):
            return os.path.join(self.storage_path, "unknown", "unknown.yaml")

        kind = resource.get("kind")
        if kind is None:
            kind = "Unknown"
        kind = kind.lower()

        metadata = resource.get("metadata", {})
        if not isinstance(metadata, dict):
            return os.path.join(self.storage_path, "unknown", "unknown.yaml")

        name = metadata.get("name", "unknown")
        
        # Fix namespace extraction
        if kind == "namespace":
            namespace = name
        else:
            namespace = metadata.get("namespace", "default")

        # Ensure proper path structure according to README
        if release:
            return os.path.join(
                self.storage_path,
                namespace,
                release,
                "resources",
                f"{kind}-{name}.yaml",
            )
        else:
            return os.path.join(
                self.storage_path,
                namespace,
                "standalone-resources",
                f"{kind}-{name}.yaml",
            )

    def should_skip_resource(self, resource: Dict[str, Any]) -> bool:
        """Check if resource should be skipped"""
        if not isinstance(resource, dict):
            return True

        metadata = resource.get("metadata", {})
        if not isinstance(metadata, dict):
            return True

        # Skip excluded namespaces
        namespace = metadata.get("namespace", "")
        if namespace in self.config.get_excluded_namespaces():
            return True

        # Skip specific resources
        name = metadata.get("name", "")
        kind = resource.get("kind", "")

        # FIXED: Check for kube-root-ca.crt ConfigMap (case-insensitive)
        if kind.lower() in ["configmap", "config_map"] and name == "kube-root-ca.crt":
            logger.debug(f"Skipping kube-root-ca.crt ConfigMap in namespace {namespace}")
            return True

        # Additional exclusions for system resources
        system_resources = [
            # Default service account tokens
            ("Secret", "default-token-"),
            # Service account tokens
            ("Secret", "-token-"),
            # Helm release secrets (these are handled separately)
            ("Secret", "sh.helm.release.v"),
            # Default service accounts
            ("ServiceAccount", "default"),
            # Default services
            ("Service", "kubernetes"),
            # Default endpoints
            ("Endpoints", "kubernetes"),
        ]

        for resource_kind, name_pattern in system_resources:
            if kind.lower() == resource_kind.lower() and name.startswith(name_pattern):
                logger.debug(f"Skipping system resource {kind} {name} in namespace {namespace}")
                return True

        return False

    def save_resource(self, resource: Dict[str, Any], release: str = "") -> str:
        """Save a resource to storage"""
        if not isinstance(resource, dict):
            logger.warning("Resource is not a dictionary, skipping")
            return ""

        # Log the resource structure for debugging
        resource_kind = resource.get("kind", "Unknown")
        resource_name = resource.get("metadata", {}).get("name", "unknown")
        logger.debug(f"Saving resource: {resource_kind} {resource_name} with release: '{release}'")

        cleaned_resource = self.clean_resource(resource)
        file_path = self.get_resource_path(cleaned_resource, release)

        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        logger.info(f"Writing resource to {file_path}")
        with open(file_path, "w") as f:
            yaml.dump(
                cleaned_resource, f, default_flow_style=False, sort_keys=False, indent=2
            )

        logger.debug(f"Saved resource: {file_path}")
        return file_path
