import json
import logging
import os
from collections import defaultdict

from dotenv import load_dotenv

from image_checker import ImageChecker
from rancher2.auth import RancherClient, RedmineClient
from rancher2.base import Rancher2Base
from utils import memory_unit_conversion, retry_call

load_dotenv()

log = logging.getLogger(__name__)


class Rancher2Pods(Rancher2Base):
    def __init__(self, dryrun=False):
        redmineClient = RedmineClient()
        self.pageTitle = f"{redmineClient.pods_page}_{redmineClient.cluster_name}"
        super().__init__(redmineClient, dryrun)

    def _get_container_memory_data(self, container, resources_dict):
        resources = resources_dict.get(container["name"], {})
        requested = round(
            memory_unit_conversion(
                resources.get("requests", {}).get("memory", 0)
                if resources.get("requests")
                else 0
            ),
            2,
        )
        limit = round(
            memory_unit_conversion(
                resources.get("limits", {}).get("memory", 0)
                if resources.get("limits")
                else 0
            ),
            2,
        )

        return requested, limit

    def _get_pods(self, rancher_client):
        pods_dict = defaultdict(list)
        try:
            pods_response = retry_call(rancher_client.v1.list_pod_for_all_namespaces, _request_timeout=30)
            items = pods_response.to_dict().get("items", [])
            log.info("Found %d pods across all namespaces", len(items))
            for pod in items:
                try:
                    node_name = pod.get("spec", {}).get("node_name", "")
                    if node_name:
                        pods_dict[node_name].append(pod)
                except Exception:
                    pod_name = pod.get("metadata", {}).get("name", "unknown")
                    log.exception("Failed to process pod %s", pod_name)
            return pods_dict
        except Exception:
            log.exception("Failed to list pods from Rancher API")
            return pods_dict

    def _add_pods_data(self, cluster_content, cluster_link, node, pods_dict):
        redmine_error_color = "%{color:red}"
        cluster_content.append(
            "|_{min-width:14em}. Pod |_. State |_. Namespace |_. Kind |_. Chart "
            "|_. Image |_. Restarts |_. Reservation |_. Limit |_. Start time |_. Upgrade |"
        )

        node_name = node.get("metadata", {}).get("name", "unknown")
        pods = pods_dict.get(node_name, [])
        log.info("Node %s: processing %d pods", node_name, len(pods))

        for pod in pods:
            try:
                pod_name = pod.get("metadata", {}).get("name", "unknown")
                pod_namespace = pod.get("metadata", {}).get("namespace", "unknown")
                # add pod/containers information
                pod_link = f"{cluster_link}/pod/{pod_namespace}/{pod_name}"
                pod_state = pod.get("status", {}).get("phase", "Unknown")
                if pod_state == "Failed":
                    pod_state = f"{redmine_error_color}{pod_state}%"

                pod_kind = ""
                if pod.get("metadata", {}).get("owner_references"):
                    pod_kind = pod["metadata"]["owner_references"][0]["kind"]

                pod_chart = ""
                if pod.get("metadata", {}).get("labels"):
                    pod_chart = pod["metadata"]["labels"].get("app.kubernetes.io/name", "")

                resources_dict = {
                    container["name"]: container.get("resources", {})
                    for container in pod.get("spec", {}).get("containers") or []
                }

                for container in pod.get("status", {}).get("container_statuses") or []:
                    try:
                        start_time = "-"
                        if container.get("started"):
                            start_time = container.get("state", {}).get("running", {}).get(
                                "started_at", "-"
                            )

                        requested, limit = self._get_container_memory_data(
                            container, resources_dict
                        )

                        container_image = container.get("image", "unknown")
                        restart_count = container.get("restart_count", 0)

                        cluster_content.append(
                            f'| "{pod_name}":{pod_link} | {pod_state} '
                            f"| {pod_namespace} | {pod_kind} | {pod_chart} "
                            f"| {container_image} |>. {restart_count} "
                            f"|>. {requested} |>. {limit} | {start_time} | TODO |"
                        )
                    except Exception:
                        log.exception(
                            "Failed to process container %s in pod %s/%s",
                            container.get("name", "unknown"),
                            pod_namespace,
                            pod_name,
                        )
            except Exception:
                pod_name = pod.get("metadata", {}).get("name", "unknown")
                pod_namespace = pod.get("metadata", {}).get("namespace", "unknown")
                log.exception("Failed to process pod %s/%s", pod_namespace, pod_name)

    def set_content(self):
        rancher_client = RancherClient()
        server_link = f"{rancher_client.base_url}dashboard"
        cluster_link = f"{server_link}/c/{rancher_client.cluster_id}/explorer"
        cluster_capacity = 0
        cluster_requested = 0
        cluster_limit = 0
        cluster_content = []

        pods_dict = self._get_pods(rancher_client)
        nodes = self._get_nodes(rancher_client)
        log.info("Processing %d nodes for pods data", len(nodes))
        for node in nodes:
            try:
                node_name = node.get("metadata", {}).get("name", "unknown")
                # add node information
                node_link = f"{cluster_link}/node/{node_name}"
                cluster_content.append(
                    f'\nh4. Node: "{node_name}":{node_link}\n'
                )
                cluster_content.append(f"*Description*: {node.get('description', '-')}\n")
                cluster_content.append(
                    f"*Version*: {node.get('status', {}).get('node_info', {}).get('container_runtime_version', '-')} &nbsp; &nbsp; "
                    f"*IP address*: {node.get('metadata', {}).get('annotations', {}).get('alpha.kubernetes.io/provided-node-ip', '-')} &nbsp; &nbsp; "
                    f"*OS*: {node.get('status', {}).get('node_info', {}).get('os_image', '-')} &nbsp; &nbsp; "
                    f"*Created date*: {node.get('metadata', {}).get('creation_timestamp', '-')}\n"
                )

                # add the memory information
                capacity, requested, limit = self._get_memory_data(node)
                cluster_content.append(f"*Total RAM*: {capacity} GiB\n")
                cluster_content.append(
                    f"*Used RAM*: {requested} GiB, "
                    f"{round(requested * 100 / capacity, 2)}% used or "
                    f"{round(capacity - requested, 2)} GiB available\n"
                )
                cluster_content.append(
                    f"*Limit RAM*: {limit} GiB, "
                    f"{round(limit * 100 / capacity, 2)}% used or "
                    f"{round(capacity - limit, 2)} GiB available\n"
                )

                try:
                    pod_requests_raw = node.get("metadata", {}).get("annotations", {}).get(
                        "management.cattle.io/pod-requests", "{}"
                    )
                    pods_used = json.loads(pod_requests_raw).get("pods", "-")
                except (json.JSONDecodeError, TypeError, KeyError) as e:
                    log.warning("Node %s: failed to parse pod-requests for pod count: %s", node_name, e)
                    pods_used = "-"

                cluster_content.append(
                    f"\n*Used Pods*: {pods_used}\n"
                )

                # add pods information
                self._add_pods_data(cluster_content, cluster_link, node, pods_dict)

                cluster_capacity += capacity
                cluster_requested += requested
                cluster_limit += limit
            except Exception:
                node_name = node.get("metadata", {}).get("name", "unknown")
                log.exception("Failed to process node %s", node_name)

        # add the cluster information
        self.content.append(
            f'\nh3. Cluster: "{rancher_client.cluster_name}":{cluster_link}\n'
        )
        self.content.append(f"*Total RAM*: {round(cluster_capacity, 2)} GiB\n")
        self.content.append(
            f"*Reserved RAM*: {round(cluster_requested, 2)} GiB, "
            f"{round(cluster_requested * 100 / cluster_capacity, 2)}% used or "
            f"{round(cluster_capacity - cluster_requested, 2)} GiB available\n"
        )
        self.content.append(
            f"*Limit RAM*: {round(cluster_limit, 2)} GiB, "
            f"{round(cluster_limit * 100 / cluster_capacity, 2)}% used or "
            f"{round(cluster_capacity - cluster_limit, 2)} GiB available\n"
        )
        self.content.extend(cluster_content)


class Rancher2MergePods(Rancher2Base):
    def __init__(self, dryrun=False):
        redmineClient = RedmineClient()
        self.pageTitle = redmineClient.pods_page
        self.image_checker = ImageChecker()
        self.clusters_to_merge = os.getenv("RANCHER2_CLUSTERS_TO_MERGE", "").split("|")
        super().__init__(redmineClient, dryrun)

    def set_content(self):
        merged_content = {}
        for cluster_data in self.clusters_to_merge:
            try:
                rancher_url, rancher_server_name, cluster = cluster_data.split(",")
            except ValueError:
                log.error("Invalid RANCHER2_CLUSTERS_TO_MERGE entry: %s", cluster_data)
                continue

            if rancher_server_name not in merged_content:
                merged_content[rancher_server_name] = {
                    "title": f'h2. "{rancher_server_name}":{rancher_url}dashboard',
                    "content": [],
                }

            try:
                page_key = f"{self.pageTitle}_{cluster}"
                text = self.redmineClient.get_page_text(page_key)
                if not text:
                    log.warning("No text found for page %s", page_key)
            except Exception:
                log.exception("Failed to get page text for cluster %s", cluster)
                continue

            for line in text.splitlines():
                if "TODO" not in line:
                    merged_content[rancher_server_name]["content"].append(line)
                    continue

                try:
                    image = line.split("|")[6].strip()  # check image column number
                    _, update_msg = self.image_checker.check_image_and_base_status(image)
                    merged_content[rancher_server_name]["content"].append(
                        line.replace("TODO", update_msg)
                    )
                except (IndexError, Exception):
                    log.exception("Failed to check image status for line: %s", line[:80])
                    merged_content[rancher_server_name]["content"].append(line)

        for server_content in merged_content.values():
            self.content.append(f"\n{server_content['title']}\n")
            self.content.extend(server_content["content"])
