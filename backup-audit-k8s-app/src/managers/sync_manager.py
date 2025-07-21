import logging


logger = logging.getLogger(__name__)


class SyncManager:
    """Handles sync and cleanup operations"""

    def __init__(
        self,
        k8s_manager,
        resource_processor,
        helm_processor,
        archive_manager,
        storage_manager,
        config,
    ):
        self.k8s_manager = k8s_manager
        self.resource_processor = resource_processor
        self.helm_processor = helm_processor
        self.archive_manager = archive_manager
        self.storage_manager = storage_manager
        self.config = config

    def run_sync(self):
        """Run initial sync for active namespaces"""
        try:
            logger.info("Starting initial sync...")

            k8s_namespaces = set(self.k8s_manager.get_namespaces())
            logger.info(f"Found {len(k8s_namespaces)} active namespaces")

            for ns_name in k8s_namespaces:
                if ns_name in self.config.get_excluded_namespaces():
                    logger.info(f"Skipping excluded namespace: {ns_name}")
                    continue

                logger.info(f"Processing namespace: {ns_name}")

                # Process Helm releases
                if self.config.helm_tracking:
                    self.helm_processor.process_namespace_helm_releases(ns_name)

                # Process resources
                self.process_namespace_resources(ns_name)

                # Update latest changes
                self.storage_manager.update_namespace_latest_changes(ns_name)

            logger.info("Initial sync completed")

        except Exception as e:
            logger.error(f"Initial sync failed: {e}")
            raise

    def process_namespace_resources(self, ns_name: str):
        """Process all resources in a namespace"""
        if ns_name in self.config.get_excluded_namespaces():
            return

        resource_kinds = [
            ("pods", "v1", "list_namespaced_pod"),
            ("services", "v1", "list_namespaced_service"),
            ("configmaps", "v1", "list_namespaced_config_map"),
            ("secrets", "v1", "list_namespaced_secret"),
            ("persistentvolumeclaims", "v1", "list_namespaced_persistent_volume_claim"),
            ("deployments", "apps_v1", "list_namespaced_deployment"),
            ("statefulsets", "apps_v1", "list_namespaced_stateful_set"),
            ("daemonsets", "apps_v1", "list_namespaced_daemon_set"),
            ("replicasets", "apps_v1", "list_namespaced_replica_set"),
            ("jobs", "batch_v1", "list_namespaced_job"),
            ("cronjobs", "batch_v1", "list_namespaced_cron_job"),
            ("ingresses", "networking_v1", "list_namespaced_ingress"),
            ("networkpolicies", "networking_v1", "list_namespaced_network_policy"),
        ]

        resources = self.k8s_manager.get_namespace_resources(ns_name, resource_kinds)
        logger.info(f"Processing {len(resources)} resource types in namespace {ns_name}")
        
        for kind, resource_list in resources.items():
            logger.info(f"Processing {len(resource_list)} {kind} resources in namespace {ns_name}")
            for resource in resource_list:
                try:
                    # FIXED: Add debugging for resource processing
                    resource_kind = resource.get("kind", "Unknown")
                    resource_name = resource.get("metadata", {}).get("name", "unknown")
                    logger.debug(f"Processing resource: {resource_kind} {resource_name}")
                    
                    release = ""
                    if self.config.helm_tracking:
                        release = self.resource_processor.get_helm_release_name(resource)
                        if release:
                            logger.debug(f"Found Helm release '{release}' for {resource_kind} {resource_name}")
                        else:
                            logger.debug(f"No Helm release found for {resource_kind} {resource_name}")
                    
                    # FIXED: Ensure the resource kind is preserved
                    if resource_kind == "Unknown" or resource_kind is None:
                        # Use the kind from the resource_kinds list as fallback
                        resource["kind"] = kind.title()  # Convert 'deployments' to 'Deployment'
                        logger.debug(f"Fixed resource kind from 'Unknown' to '{resource['kind']}' for {resource_name}")
                    
                    self.resource_processor.save_resource(resource, release)
                except Exception as e:
                    logger.error(f"Error saving {kind} in {ns_name}: {e}")

    def run_cleanup(self):
        """Clean up deleted resources"""
        try:
            logger.info("Starting cleanup...")

            # Get current Kubernetes state
            k8s_namespaces = set(self.k8s_manager.get_namespaces())
            k8s_releases_by_namespace = {}

            # Get all current Helm releases
            for ns_name in k8s_namespaces:
                if ns_name not in self.config.get_excluded_namespaces():
                    helm_releases = self.k8s_manager.get_helm_releases(ns_name)
                    k8s_releases = set()
                    for release_info in helm_releases:
                        if isinstance(release_info, dict):
                            release_name = release_info.get("name")
                            if release_name:
                                k8s_releases.add(release_name)
                    k8s_releases_by_namespace[ns_name] = k8s_releases

            # Archive missing namespaces and releases using archive_manager
            self.archive_manager.archive_missing_namespaces(k8s_namespaces)
            self.archive_manager.archive_missing_releases(k8s_releases_by_namespace)

            logger.info("Cleanup completed")

        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
            raise
