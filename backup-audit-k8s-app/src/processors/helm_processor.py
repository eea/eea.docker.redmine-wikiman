#!/usr/bin/env python3

import os
import yaml
import logging
from typing import Dict, Any, List, Optional
from core.memory import drop_file_cache
from core.models import HelmDeploymentInfo

logger = logging.getLogger(__name__)

class HelmProcessor:
    """Handles Helm-specific processing and operations"""
    
    def __init__(self, k8s_manager, storage_path: str, storage_lock=None):
        self.k8s_manager = k8s_manager
        self.storage_path = storage_path
        # Shared with ArchiveManager/ResourceProcessor (same lock instance)
        # so writing a release's values.yaml/deployment-info.yaml can't
        # interleave with a concurrent archive/move/delete of that release
        # directory - see process_helm_release()/process_namespace_helm_releases().
        self.storage_lock = storage_lock
    
    def process_helm_release(self, release: str, namespace: str):
        """Process Helm release data with enhanced directory structure creation"""
        try:
            logger.info(f"Processing Helm release '{release}' in namespace '{namespace}'")
            
            helm_releases = self.k8s_manager.get_helm_releases(namespace)
            
            release_entry = None
            for release_info in helm_releases:
                if not isinstance(release_info, dict):
                    continue
                
                release_name = release_info.get("name")
                if release_name == release:
                    release_entry = release_info
                    break
            
            if release_entry and release_entry.get("release"):
                # Decode the release payload exactly once (#309775): values
                # and deployment info both come from this single decoded
                # dict, and the compressed payload is dropped right after.
                release_data = self.k8s_manager.decode_helm_release(release_entry["release"])
                values = self.k8s_manager.extract_helm_values(release_data)
                deployment_info = self.k8s_manager.extract_helm_deployment_info(
                    release_data, release, namespace,
                    release_entry.get("release_version_label"),
                )
                
                # Add version information to deployment info
                deployment_info["helm_release_version"] = release_entry.get("version", "unknown")
                
                # FIXED: Ensure complete directory structure exists
                release_path = os.path.join(self.storage_path, namespace, release)
                resources_path = os.path.join(release_path, "resources")

                def _write():
                    os.makedirs(resources_path, exist_ok=True)

                    # Save values.yaml
                    values_path = os.path.join(release_path, "values.yaml")
                    with open(values_path, "w") as f:
                        yaml.dump(values, f, default_flow_style=False, sort_keys=False, indent=2)

                    # Save deployment info
                    deployment_info_path = os.path.join(release_path, "deployment-info.yaml")
                    with open(deployment_info_path, "w") as f:
                        yaml.dump(deployment_info, f, default_flow_style=False, sort_keys=False, indent=2)

                    # git re-reads both files; drop them from the page cache
                    # instead of holding the container memory limit (#309775).
                    drop_file_cache(values_path)
                    drop_file_cache(deployment_info_path)

                if self.storage_lock:
                    with self.storage_lock:
                        _write()
                else:
                    _write()

                logger.info(f"Saved Helm data for release {release} (version {release_version}) in {namespace}")
            else:
                logger.warning(f"No Helm secret found for release {release} in namespace {namespace}")
                
        except Exception as e:
            logger.error(f"Error processing Helm release {release} in namespace {namespace}: {e}")
    
    def process_namespace_helm_releases(self, namespace: str):
        """Process Helm releases in a namespace"""
        try:
            logger.info(f"Processing Helm releases for namespace {namespace}")
            
            helm_releases = self.k8s_manager.get_helm_releases(namespace)
            processed_releases = set()
            
            for release_info in helm_releases:
                if not isinstance(release_info, dict):
                    logger.warning(f"Invalid release info in namespace {namespace}, skipping")
                    continue
                    
                release_name = release_info.get("name")
                if not release_name:
                    logger.warning(f"Release info missing name in namespace {namespace}, skipping")
                    continue
                
                if release_name in processed_releases:
                    logger.debug(f"Release {release_name} already processed in namespace {namespace}, skipping")
                    continue
                
                processed_releases.add(release_name)
                release_b64 = release_info.get("release")
                
                if not release_b64:
                    logger.warning(f"No release payload found for release {release_name} in namespace {namespace}")
                    continue
                
                # Decode the release payload exactly once (#309775): both the
                # values and the deployment info come from this single decoded
                # dict, and the compressed payload is dropped right after.
                release_data = self.k8s_manager.decode_helm_release(release_b64)
                
                # FIXED: Ensure complete directory structure exists
                release_path = os.path.join(self.storage_path, namespace, release_name)
                resources_path = os.path.join(release_path, "resources")

                # Extract values/deployment info before taking the lock -
                # these hit the k8s API, no need to hold storage_lock for them
                values = self.k8s_manager.extract_helm_values(release_data)
                deployment_info = self.k8s_manager.extract_helm_deployment_info(
                    release_data, release_name, namespace,
                    release_info.get("release_version_label"),
                )

                def _write():
                    os.makedirs(resources_path, exist_ok=True)

                    values_path = os.path.join(release_path, "values.yaml")
                    with open(values_path, "w") as f:
                        yaml.dump(values, f, default_flow_style=False, sort_keys=False, indent=2)

                    deployment_info_path = os.path.join(release_path, "deployment-info.yaml")
                    with open(deployment_info_path, "w") as f:
                        yaml.dump(deployment_info, f, default_flow_style=False, sort_keys=False, indent=2)

                if self.storage_lock:
                    with self.storage_lock:
                        _write()
                else:
                    _write()

                logger.info(f"Processed Helm release {release_name} in namespace {namespace}")
                    
        except Exception as e:
            logger.error(f"Failed to process Helm releases for {namespace}: {e}")