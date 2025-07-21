#!/usr/bin/env python3

import os
import yaml
import logging
from typing import Dict, Any, List, Optional
from core.models import HelmDeploymentInfo

logger = logging.getLogger(__name__)

class HelmProcessor:
    """Handles Helm-specific processing and operations"""
    
    def __init__(self, k8s_manager, storage_path: str):
        self.k8s_manager = k8s_manager
        self.storage_path = storage_path
    
    def process_helm_release(self, release: str, namespace: str):
        """Process Helm release data with enhanced directory structure creation"""
        try:
            logger.info(f"Processing Helm release '{release}' in namespace '{namespace}'")
            
            helm_releases = self.k8s_manager.get_helm_releases(namespace)
            
            helm_secret = None
            release_version = None
            for release_info in helm_releases:
                if not isinstance(release_info, dict):
                    continue
                
                release_name = release_info.get("name")
                if release_name == release:
                    helm_secret = release_info.get("secret")
                    release_version = release_info.get("version", "unknown")
                    break
            
            if helm_secret:
                # Extract values and deployment info
                values = self.k8s_manager.extract_helm_values_from_secret(helm_secret)
                deployment_info = self.k8s_manager.extract_helm_deployment_info(helm_secret, release, namespace)
                
                # Add version information to deployment info
                deployment_info["helm_release_version"] = release_version
                
                # FIXED: Ensure complete directory structure exists
                release_path = os.path.join(self.storage_path, namespace, release)
                resources_path = os.path.join(release_path, "resources")
                os.makedirs(resources_path, exist_ok=True)
                
                # Save values.yaml
                values_path = os.path.join(release_path, "values.yaml")
                with open(values_path, "w") as f:
                    yaml.dump(values, f, default_flow_style=False, sort_keys=False, indent=2)
                
                # Save deployment info
                deployment_info_path = os.path.join(release_path, "deployment-info.yaml")
                with open(deployment_info_path, "w") as f:
                    yaml.dump(deployment_info, f, default_flow_style=False, sort_keys=False, indent=2)
                
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
                secret = release_info.get("secret")
                
                if not secret:
                    logger.warning(f"No secret found for release {release_name} in namespace {namespace}")
                    continue
                
                # FIXED: Ensure complete directory structure exists
                release_path = os.path.join(self.storage_path, namespace, release_name)
                resources_path = os.path.join(release_path, "resources")
                os.makedirs(resources_path, exist_ok=True)
                
                # Extract and save values
                values = self.k8s_manager.extract_helm_values_from_secret(secret)
                values_path = os.path.join(release_path, "values.yaml")
                with open(values_path, "w") as f:
                    yaml.dump(values, f, default_flow_style=False, sort_keys=False, indent=2)
                
                # Save deployment info
                deployment_info = self.k8s_manager.extract_helm_deployment_info(secret, release_name, namespace)
                deployment_info_path = os.path.join(release_path, "deployment-info.yaml")
                with open(deployment_info_path, "w") as f:
                    yaml.dump(deployment_info, f, default_flow_style=False, sort_keys=False, indent=2)
                
                logger.info(f"Processed Helm release {release_name} in namespace {namespace}")
                    
        except Exception as e:
            logger.error(f"Failed to process Helm releases for {namespace}: {e}")