import logging
import os

from core.memory import memory_summary

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
        # namespace -> set of <kind>-<name>.yaml basenames of standalone
        # resources written by the latest sync pass. Cleanup compares the
        # storage against this to find resources that were deleted
        # (archive_missing_standalone_resources).
        self._active_standalone = {}

    def run_sync(self, is_initial: bool = True):
        """Sync active namespaces. Also used for periodic resyncs (is_initial=False)
        to catch drift - those must NOT touch latest-changes.json: that file
        tracks the latest real change to a namespace (written by
        event_processor from actual webhook events), and blindly overwriting
        it with an INITIAL_SYNC marker on every resync cycle would clobber
        that real record even when nothing had changed."""
        try:
            logger.info(
                f"{'Starting initial sync' if is_initial else 'Starting sync'}... "
                f"(memory {memory_summary()})"
            )

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

                if is_initial:
                    self.storage_manager.update_namespace_latest_changes(ns_name)

            logger.info(
                f"{'Initial sync completed' if is_initial else 'Sync completed'} "
                f"(memory {memory_summary()})"
            )

        except Exception as e:
            logger.error(f"Initial sync failed: {e}")
            raise

    def process_namespace_resources(self, ns_name: str):
        """Process all resources in a namespace"""
        if ns_name in self.config.get_excluded_namespaces():
            return

        # Reset before the pass so files of resources that vanished since
        # the last sync fall out of the set (cleanup then archives them).
        self._active_standalone[ns_name] = set()

        # (storage key, client attr, list method, apiVersion, Kind) - apiVersion
        # and Kind are passed explicitly because list responses don't come
        # back with either populated on each item (a Kubernetes client
        # quirk), and they must match real API values (singular, correctly
        # cased) for should_skip_resource()'s kind-based exclusions
        # (Helm release secrets, default tokens, kube-root-ca.crt) to work.
        resource_kinds = [
            ("pods", "v1", "list_namespaced_pod", "v1", "Pod"),
            ("services", "v1", "list_namespaced_service", "v1", "Service"),
            ("configmaps", "v1", "list_namespaced_config_map", "v1", "ConfigMap"),
            ("secrets", "v1", "list_namespaced_secret", "v1", "Secret"),
            (
                "persistentvolumeclaims",
                "v1",
                "list_namespaced_persistent_volume_claim",
                "v1",
                "PersistentVolumeClaim",
            ),
            (
                "deployments",
                "apps_v1",
                "list_namespaced_deployment",
                "apps/v1",
                "Deployment",
            ),
            (
                "statefulsets",
                "apps_v1",
                "list_namespaced_stateful_set",
                "apps/v1",
                "StatefulSet",
            ),
            (
                "daemonsets",
                "apps_v1",
                "list_namespaced_daemon_set",
                "apps/v1",
                "DaemonSet",
            ),
            (
                "replicasets",
                "apps_v1",
                "list_namespaced_replica_set",
                "apps/v1",
                "ReplicaSet",
            ),
            ("jobs", "batch_v1", "list_namespaced_job", "batch/v1", "Job"),
            (
                "cronjobs",
                "batch_v1",
                "list_namespaced_cron_job",
                "batch/v1",
                "CronJob",
            ),
            (
                "ingresses",
                "networking_v1",
                "list_namespaced_ingress",
                "networking.k8s.io/v1",
                "Ingress",
            ),
            (
                "networkpolicies",
                "networking_v1",
                "list_namespaced_network_policy",
                "networking.k8s.io/v1",
                "NetworkPolicy",
            ),
        ]

        # Stream one kind at a time (M3/#309775): list, save and drop each
        # kind before listing the next, instead of holding all 13 kinds of
        # the namespace in memory simultaneously.
        logger.info(f"Processing {len(resource_kinds)} resource types in namespace {ns_name}")

        for kind_tuple in resource_kinds:
            kind = kind_tuple[0]
            resource_list = self.k8s_manager.get_kind_resources(ns_name, kind_tuple)
            logger.info(f"Processing {len(resource_list)} {kind} resources in namespace {ns_name}")
            for resource in resource_list:
                try:
                    resource_kind = resource.get("kind", "Unknown")
                    resource_name = resource.get("metadata", {}).get("name", "unknown")
                    logger.debug(f"Processing resource: {resource_kind} {resource_name}")

                    # Apply the same exclusions the webhook event path uses
                    # (kube-root-ca.crt, default/service-account tokens, Helm
                    # release secrets, etc.) - previously only the live
                    # webhook path filtered these, so a full sync would
                    # capture them all.
                    if self.resource_processor.should_skip_resource(resource):
                        logger.debug(
                            f"Skipping excluded resource: {resource_kind} {resource_name}"
                        )
                        continue

                    release = ""
                    if self.config.helm_tracking:
                        release = self.resource_processor.get_helm_release_name(resource)
                        if release:
                            logger.debug(f"Found Helm release '{release}' for {resource_kind} {resource_name}")
                        else:
                            logger.debug(f"No Helm release found for {resource_kind} {resource_name}")

                    file_path = self.resource_processor.save_resource(resource, release)
                    if not release and file_path:
                        self._active_standalone[ns_name].add(os.path.basename(file_path))
                except Exception as e:
                    logger.error(f"Error saving {kind} in {ns_name}: {e}")

    def run_cleanup(self):
        """Clean up deleted resources"""
        try:
            logger.info(f"Starting cleanup... (memory {memory_summary()})")

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
            self.archive_manager.archive_missing_standalone_resources(
                k8s_namespaces, self._active_standalone
            )

            logger.info(f"Cleanup completed (memory {memory_summary()})")

        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
            raise
