#!/usr/bin/env python3

import os
import logging
import base64
import gzip
import requests
from typing import List, Dict, Any, Optional

try:
    from kubernetes import client, config  # type: ignore[import]
except ImportError:
    client = None
    config = None
import subprocess
import getpass
import yaml

# Set up logging
logger = logging.getLogger(__name__)

# Helm tracking configuration
HELM_TRACKING = os.getenv("HELM_TRACKING", "true").lower() == "true"

print(f"Running as user: {getpass.getuser()}")
print(f"Current working directory: {os.getcwd()}")
print(f"Marking /app/logs as safe git directory")


def ensure_git_safe_directory(path):
    try:
        # Print for debugging
        print(f"Marking {path} as safe git directory")
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", path],
            check=True,
        )
    except Exception as e:
        print(f"Failed to mark {path} as safe git directory: {e}")


# Call this before any git.Repo() or git command
ensure_git_safe_directory("/app/logs")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = "/app/audit-logger.log"

# Create a custom logger
logger.setLevel(LOG_LEVEL)

# Create handlers
console_handler = logging.StreamHandler()
console_handler.setLevel(LOG_LEVEL)

# Create formatters and add them to handlers
formatter = logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s %(funcName)s:%(lineno)d %(message)s"
)
console_handler.setFormatter(formatter)

# Add handlers to the logger
if not logger.hasHandlers():
    logger.addHandler(console_handler)

# File handler
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setLevel(LOG_LEVEL)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


class KubernetesManager:
    """Handle all Kubernetes and Helm operations"""

    def __init__(self):
        self.v1 = None
        self.apps_v1 = None
        self.batch_v1 = None
        self.networking_v1 = None
        self._init_k8s_client()

    def _init_k8s_client(self):
        """Initialize Kubernetes client"""
        try:
            config.load_incluster_config()  # type: ignore[attr-defined]
            logger.info("Loaded in-cluster kube config")
        except Exception:
            config.load_kube_config()  # type: ignore[attr-defined]
            logger.info("Loaded local kube config")
        self.v1 = client.CoreV1Api()  # type: ignore[attr-defined]
        self.apps_v1 = client.AppsV1Api()  # type: ignore[attr-defined]
        self.batch_v1 = client.BatchV1Api()  # type: ignore[attr-defined]
        self.networking_v1 = client.NetworkingV1Api()  # type: ignore[attr-defined]

    def check_chart_exists_in_repository(self, chart_name: str, repo_org: str, repo_name: str = "helm-charts", charts_dir: str = "charts") -> bool:
        """Check if a chart exists in a repository"""
        try:
            # For EEA helm-charts, use the GitHub Pages hosted repository
            if repo_org == "eea" and repo_name == "helm-charts":
                # Check if chart exists in the hosted Helm repository
                url = f"https://eea.github.io/helm-charts/{chart_name}-*.tgz"
                # Try to get the index.yaml to check for the chart
                index_url = "https://eea.github.io/helm-charts/index.yaml"
                
                response = requests.get(index_url, timeout=10)
                if response.status_code == 200:
                    # Parse the index.yaml to check if chart exists
                    try:
                        import yaml
                        index_data = yaml.safe_load(response.text)
                        if "entries" in index_data and chart_name in index_data["entries"]:
                            logger.debug(f"Chart '{chart_name}' found in eea.github.io/helm-charts")
                            return True
                        else:
                            logger.debug(f"Chart '{chart_name}' not found in eea.github.io/helm-charts")
                            return False
                    except Exception as e:
                        logger.warning(f"Failed to parse index.yaml from eea.github.io/helm-charts: {e}")
                        return False
                else:
                    logger.warning(f"Failed to fetch index.yaml from eea.github.io/helm-charts: {response.status_code}")
                    return False
            else:
                # For other repositories, use GitHub API
                url = f"https://api.github.com/repos/{repo_org}/{repo_name}/contents/{charts_dir}/{chart_name}"
                
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    logger.debug(f"Chart '{chart_name}' found in {repo_org}/{repo_name}/{charts_dir}")
                    return True
                elif response.status_code == 404:
                    logger.debug(f"Chart '{chart_name}' not found in {repo_org}/{repo_name}/{charts_dir}")
                    return False
                else:
                    logger.warning(f"Unexpected response {response.status_code} when checking {repo_org}/{repo_name}/{charts_dir} for chart '{chart_name}'")
                    return False
                
        except requests.RequestException as e:
            logger.warning(f"Failed to check chart '{chart_name}' in {repo_org}/{repo_name}/{charts_dir}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error checking chart '{chart_name}' in {repo_org}/{repo_name}/{charts_dir}: {e}")
            return False

    def find_chart_repository(self, chart_name: str) -> Optional[Dict[str, str]]:
        """Search for a chart across known repositories, starting with EEA"""
        # Define repositories to search in priority order with their specific directory structures
        repositories = [
            {"org": "eea", "name": "helm-charts", "url": "https://github.com/eea/helm-charts", "charts_dir": "sources"},
            {"org": "bitnami", "name": "charts", "url": "https://github.com/bitnami/charts", "charts_dir": "charts"},
            {"org": "prometheus-community", "name": "helm-charts", "url": "https://github.com/prometheus-community/helm-charts", "charts_dir": "charts"},
            {"org": "jetstack", "name": "cert-manager", "url": "https://github.com/jetstack/cert-manager", "charts_dir": "charts"},
            {"org": "kubernetes", "name": "ingress-nginx", "url": "https://github.com/kubernetes/ingress-nginx", "charts_dir": "charts"},
        ]
        
        for repo in repositories:
            try:
                charts_dir = repo.get("charts_dir", "charts")
                if self.check_chart_exists_in_repository(chart_name, repo["org"], repo["name"], charts_dir):
                    logger.info(f"Found chart '{chart_name}' in {repo['org']}/{repo['name']}/{charts_dir}")
                    return {
                        "repository": f"{repo['org']}/{repo['name']}",
                        "repository_url": repo["url"],
                        "org": repo["org"],
                        "repo_name": repo["name"]
                    }
            except Exception as e:
                logger.warning(f"Error searching for chart '{chart_name}' in {repo['org']}/{repo['name']}: {e}")
                continue
        
        logger.warning(f"Chart '{chart_name}' not found in any known repositories")
        return None

    def decode_helm_release(self, release_data: str) -> dict:
        """Decode Helm release data (handles double base64 encoding and gzip, then YAML)"""
        try:
            logger.debug(f"Decoding Helm release data of length: {len(release_data)}")

            # First base64 decode
            decoded = base64.b64decode(release_data)
            logger.debug(f"First base64 decode successful, length: {len(decoded)}")

            # Try to decompress gzip
            try:
                decompressed = gzip.decompress(decoded)
                logger.debug(
                    f"Gzip decompression successful, length: {len(decompressed)}"
                )
            except OSError:
                logger.debug(
                    "First gzip decompression failed, trying double base64 decode"
                )
                # If not gzipped, maybe it's base64 again
                decoded = base64.b64decode(decoded)
                logger.debug(f"Second base64 decode successful, length: {len(decoded)}")
                decompressed = gzip.decompress(decoded)
                logger.debug(
                    f"Second gzip decompression successful, length: {len(decompressed)}"
                )

            # Try to parse as YAML
            try:
                release_yaml = yaml.safe_load(decompressed)
                logger.debug(
                    f"YAML parsing successful, keys: {list(release_yaml.keys()) if isinstance(release_yaml, dict) else 'Not a dict'}"
                )
                return release_yaml
            except Exception as e:
                logger.warning(f"Failed to parse Helm release YAML: {e}")
                return {}
        except Exception as e:
            logger.warning(f"Failed to decode Helm release: {e}")
            return {}

    def extract_helm_values(self, release_data: dict) -> dict:
        """Extract values from Helm release data"""
        try:
            return release_data.get("config", {})
        except Exception as e:
            logger.warning(f"Failed to extract Helm values: {e}")
            return {}

    def get_namespaces(self) -> List[str]:
        """Get all namespaces from Kubernetes"""
        try:
            namespaces = self.v1.list_namespace().items  # type: ignore[attr-defined]
            return [ns.metadata.name for ns in namespaces]
        except Exception as e:
            logger.error(f"Failed to get namespaces: {e}")
            return []

    def get_helm_releases(self, namespace: str) -> List[Dict[str, Any]]:
        """Get all Helm releases in a namespace"""
        try:
            secrets = self.v1.list_namespaced_secret(namespace).items  # type: ignore[attr-defined]
            helm_releases = {}  # Use dict to group by release name

            for secret in secrets:
                # Add defensive checks
                if secret is None:
                    logger.warning(
                        f"Found None secret in namespace {namespace}, skipping"
                    )
                    continue

                if not hasattr(secret, "metadata") or secret.metadata is None:
                    logger.warning(
                        f"Found secret without metadata in namespace {namespace}, skipping"
                    )
                    continue

                if not hasattr(secret.metadata, "name") or secret.metadata.name is None:
                    logger.warning(
                        f"Found secret without name in namespace {namespace}, skipping"
                    )
                    continue

                # Check for different Helm secret patterns
                secret_name = secret.metadata.name
                is_helm_secret = (
                    secret_name.startswith("sh.helm.release.v1.")
                    or secret_name.startswith("sh.helm.release.v2.")
                    or secret_name.startswith("sh.helm.release.v3.")
                )

                if is_helm_secret:
                    # Add defensive check for labels
                    if (
                        not hasattr(secret.metadata, "labels")
                        or secret.metadata.labels is None
                    ):
                        logger.warning(
                            f"Found Helm secret without labels in namespace {namespace}, skipping"
                        )
                        continue

                    release_name = secret.metadata.labels.get("name")
                    if release_name:
                        # Get the release version - try both label formats
                        release_version = (
                            secret.metadata.labels.get("helm.sh/release-version")
                            or secret.metadata.labels.get("version")
                            or "0"
                        )
                        try:
                            version_num = int(release_version)
                        except (ValueError, TypeError):
                            version_num = 0

                        # Add debug logging
                        logger.debug(
                            f"Found Helm release '{release_name}' version {version_num} (secret: {secret_name})"
                        )

                        # Keep the latest version for each release
                        if release_name not in helm_releases:
                            logger.debug(
                                f"First time seeing release '{release_name}', adding version {version_num}"
                            )
                            helm_releases[release_name] = {
                                "name": release_name,
                                "secret": secret,
                                "version": version_num,
                            }
                        elif version_num > helm_releases[release_name]["version"]:
                            logger.debug(
                                f"Found newer version {version_num} for release '{release_name}' (previous: {helm_releases[release_name]['version']})"
                            )
                            helm_releases[release_name] = {
                                "name": release_name,
                                "secret": secret,
                                "version": version_num,
                            }
                        else:
                            logger.debug(
                                f"Skipping older version {version_num} for release '{release_name}' (current: {helm_releases[release_name]['version']})"
                            )
                    else:
                        logger.warning(
                            f"Found Helm secret without release name in namespace {namespace}"
                        )

            # Log final results
            for release_name, release_info in helm_releases.items():
                logger.info(
                    f"Selected version {release_info['version']} for Helm release '{release_name}' in namespace {namespace}"
                )

            # Return only the latest version of each release
            return [release_info for release_info in helm_releases.values()]
        except Exception as e:
            logger.error(f"Failed to get Helm releases for namespace {namespace}: {e}")
            return []

    def get_namespace_resources(
        self, namespace: str, resource_kinds: List[tuple]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Get all resources of specified kinds in a namespace"""
        resources = {}

        for kind, api_version, method_name in resource_kinds:
            try:
                if api_version == "v1":
                    api = self.v1
                elif api_version == "apps_v1":
                    api = self.apps_v1
                elif api_version == "batch_v1":
                    api = self.batch_v1
                elif api_version == "networking_v1":
                    api = self.networking_v1
                else:
                    logger.warning(f"Unknown API version: {api_version}")
                    continue

                method = getattr(api, method_name)
                result = method(namespace)

                # Convert to dict format
                resource_list = []
                for item in result.items:
                    resource_dict = {
                        "apiVersion": item.api_version,
                        "kind": item.kind,
                        "metadata": {
                            "name": item.metadata.name,
                            "namespace": item.metadata.namespace,
                            "labels": dict(item.metadata.labels)
                            if item.metadata.labels
                            else {},
                            "annotations": dict(item.metadata.annotations)
                            if item.metadata.annotations
                            else {},
                        },
                        "spec": item.spec.to_dict()
                        if hasattr(item, "spec") and item.spec
                        else {},
                        "status": item.status.to_dict()
                        if hasattr(item, "status") and item.status
                        else {},
                    }
                    resource_list.append(resource_dict)

                resources[kind] = resource_list

            except Exception as e:
                logger.error(f"Failed to get {kind} in namespace {namespace}: {e}")
                resources[kind] = []

        return resources

    def extract_helm_values_from_secret(self, secret) -> Dict[str, Any]:
        """Extract Helm values from a secret"""
        try:
            release_data = self.decode_helm_release(secret.data["release"])

            # Add detailed debug logging
            logger.debug(
                f"Helm release data keys: {list(release_data.keys()) if isinstance(release_data, dict) else 'Not a dict'}"
            )

            # Try different possible locations for values
            values = {}

            # Method 1: Try the standard "config" field
            if "config" in release_data:
                values = release_data["config"]
                logger.debug(f"Found values in 'config' field: {len(values)} keys")
                logger.debug(
                    f"Config keys: {list(values.keys()) if isinstance(values, dict) else 'Not a dict'}"
                )

            # Method 2: Try "values" field
            elif "values" in release_data:
                values = release_data["values"]
                logger.debug(f"Found values in 'values' field: {len(values)} keys")
                logger.debug(
                    f"Values keys: {list(values.keys()) if isinstance(values, dict) else 'Not a dict'}"
                )

            # Method 3: Try "chart" -> "values" field
            elif "chart" in release_data and isinstance(release_data["chart"], dict):
                chart = release_data["chart"]
                if "values" in chart:
                    values = chart["values"]
                    logger.debug(
                        f"Found values in 'chart.values' field: {len(values)} keys"
                    )
                    logger.debug(
                        f"Chart values keys: {list(values.keys()) if isinstance(values, dict) else 'Not a dict'}"
                    )

            # Method 4: Try "info" -> "values" field
            elif "info" in release_data and isinstance(release_data["info"], dict):
                info = release_data["info"]
                if "values" in info:
                    values = info["values"]
                    logger.debug(
                        f"Found values in 'info.values' field: {len(values)} keys"
                    )
                    logger.debug(
                        f"Info values keys: {list(values.keys()) if isinstance(values, dict) else 'Not a dict'}"
                    )

            # Method 5: Try "manifest" field (older Helm versions)
            elif "manifest" in release_data:
                logger.debug(
                    "Found 'manifest' field, but values extraction from manifest not implemented"
                )

            # Method 6: Try "hooks" field
            elif "hooks" in release_data:
                logger.debug("Found 'hooks' field, checking for values")
                hooks = release_data["hooks"]
                if isinstance(hooks, list):
                    for hook in hooks:
                        if isinstance(hook, dict) and "manifest" in hook:
                            logger.debug("Found hook with manifest")

            if not values:
                logger.warning(
                    f"No values found in Helm release data. Available keys: {list(release_data.keys()) if isinstance(release_data, dict) else 'Not a dict'}"
                )
                # Log the full structure for debugging
                logger.debug(f"Full release data structure: {release_data}")

            return values

        except Exception as e:
            logger.error(f"Failed to extract Helm values: {e}")
            return {}

    def extract_helm_deployment_info(
        self, secret, release_name=None, namespace=None
    ) -> Dict[str, Any]:
        """Extract Helm deployment information from a secret"""
        try:
            release_data = self.decode_helm_release(secret.data["release"])
            # Extract release version from secret labels
            release_version = None
            if (
                hasattr(secret, "metadata")
                and hasattr(secret.metadata, "labels")
                and secret.metadata.labels
            ):
                # Try both common keys
                release_version = secret.metadata.labels.get("helm.sh/release-version")
                if not release_version:
                    release_version = secret.metadata.labels.get("version")
            deployment_info = {
                "chart_name": "unknown",
                "chart_version": "unknown",
                "app_version": "unknown",
                "release_version": release_version or "unknown",
                "repository": "unknown",
                "repository_url": "",
                "deploy_command": "",
                "values": {},
            }

            # Try to extract chart info
            chart = release_data.get("chart", {})
            metadata = chart.get("metadata", {})
            deployment_info["chart_name"] = metadata.get("name", "unknown")
            deployment_info["chart_version"] = metadata.get("version", "unknown")
            deployment_info["app_version"] = metadata.get("appVersion", "unknown")

            # Try to extract repository info from sources array first
            sources = metadata.get("sources", [])
            if sources and isinstance(sources, list):
                # Look for the main chart repository with priority order
                eea_helm_repo_url = None
                chart_repo_url = None
                primary_repo_url = None
                
                for source_url in sources:
                    if isinstance(source_url, str) and "github.com" in source_url:
                        # First priority: EEA helm-charts repository
                        if "github.com/eea/helm-charts" in source_url:
                            eea_helm_repo_url = source_url
                            break
                        # Second priority: helm chart repositories (containing -helm)
                        elif "-helm" in source_url:
                            chart_repo_url = source_url
                        # Otherwise, keep the first GitHub URL as fallback
                        elif not primary_repo_url:
                            primary_repo_url = source_url
                
                # Use EEA repo first, then chart repo, then primary repo URL
                repo_url = eea_helm_repo_url or chart_repo_url or primary_repo_url
                
                if repo_url:
                    deployment_info["repository_url"] = repo_url
                    
                    # Extract org/repo from GitHub URL
                    parts = repo_url.split("github.com/")
                    if len(parts) > 1:
                        repo_path = parts[1].rstrip("/")  # Remove trailing slash
                        # Extract organization/repository name
                        path_parts = repo_path.split("/")
                        if len(path_parts) >= 2:
                            org_name = path_parts[0]
                            repo_name = path_parts[1]
                            deployment_info["repository"] = f"{org_name}/{repo_name}"
                            
                            # For Helm charts, use the organization name as the repo prefix
                            if release_name and namespace:
                                deployment_info["deploy_command"] = (
                                    f"helm install {release_name} {org_name}/{deployment_info['chart_name']} --namespace {namespace}"
                                )
                            else:
                                deployment_info["deploy_command"] = (
                                    f"helm install {deployment_info['chart_name']} {org_name}/{deployment_info['chart_name']}"
                                )
                            return deployment_info

            # Try to extract repository info from annotations (fallback)
            if "annotations" in metadata:
                repo_url = metadata["annotations"].get("artifacthub.io/source-url")
                if repo_url:
                    deployment_info["repository_url"] = repo_url

                # Try to extract repository from source URL
                if repo_url and "github.com" in repo_url:
                    # Extract org/repo from GitHub URL
                    parts = repo_url.split("github.com/")
                    if len(parts) > 1:
                        repo_path = parts[1].rstrip("/")  # Remove trailing slash
                        path_parts = repo_path.split("/")
                        if len(path_parts) >= 2:
                            org_name = path_parts[0]
                            repo_name = path_parts[1]
                            deployment_info["repository"] = f"{org_name}/{repo_name}"
                            
                            if release_name and namespace:
                                deployment_info["deploy_command"] = (
                                    f"helm install {release_name} {org_name}/{deployment_info['chart_name']} --namespace {namespace}"
                                )
                            else:
                                deployment_info["deploy_command"] = (
                                    f"helm install {deployment_info['chart_name']} {org_name}/{deployment_info['chart_name']}"
                                )
                            return deployment_info

            # Try to extract repository from chart name
            chart_name = deployment_info["chart_name"]
            if chart_name and "/" in chart_name:
                repo_name, chart_name_only = chart_name.split("/", 1)
                deployment_info["repository"] = repo_name
                if release_name and namespace:
                    deployment_info["deploy_command"] = (
                        f"helm install {release_name} {repo_name}/{chart_name_only} --namespace {namespace}"
                    )
                else:
                    deployment_info["deploy_command"] = (
                        f"helm install {chart_name_only} {repo_name}/{chart_name_only}"
                    )
            elif chart_name:
                # For charts without repository prefix, search for the chart in known repositories
                logger.info(f"No repository info found in metadata, searching for chart '{chart_name}' in known repositories")
                
                chart_repo_info = self.find_chart_repository(chart_name)
                if chart_repo_info:
                    # Found the chart in a repository
                    deployment_info["repository"] = chart_repo_info["repository"]
                    deployment_info["repository_url"] = chart_repo_info["repository_url"]
                    
                    org_name = chart_repo_info["org"]
                    if release_name and namespace:
                        deployment_info["deploy_command"] = (
                            f"helm install {release_name} {org_name}/{chart_name} --namespace {namespace}"
                        )
                    else:
                        deployment_info["deploy_command"] = (
                            f"helm install {chart_name} {org_name}/{chart_name}"
                        )
                    logger.info(f"Chart '{chart_name}' located in {chart_repo_info['repository']}")
                else:
                    # Chart not found in any known repository, fall back to EEA as default
                    logger.warning(f"Chart '{chart_name}' not found in any repository, defaulting to EEA")
                    deployment_info["repository"] = "eea"
                    deployment_info["repository_url"] = "https://github.com/eea/helm-charts"
                    
                    if release_name and namespace:
                        deployment_info["deploy_command"] = (
                            f"helm install {release_name} eea/{chart_name} --namespace {namespace}"
                        )
                    else:
                        deployment_info["deploy_command"] = (
                            f"helm install {chart_name} eea/{chart_name}"
                        )
            else:
                deployment_info["repository"] = "custom"
                if release_name and namespace:
                    deployment_info["deploy_command"] = (
                        f"helm install {release_name} {chart_name} --namespace {namespace}"
                    )
                else:
                    deployment_info["deploy_command"] = (
                        f"helm install {chart_name} {chart_name}"
                    )

            # Use the enhanced values extraction
            deployment_info["values"] = self.extract_helm_values_from_secret(secret)

            return deployment_info
        except Exception as e:
            logger.error(f"Failed to extract Helm deployment info: {e}")
            return {
                "chart_name": "unknown",
                "chart_version": "unknown",
                "app_version": "unknown",
                "release_version": "unknown",
                "repository": "unknown",
                "repository_url": "",
                "deploy_command": "",
                "values": {},
            }
