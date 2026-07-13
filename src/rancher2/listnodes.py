import json
import logging
import os

from dotenv import load_dotenv

from rancher2.auth import RancherClient, RedmineClient
from rancher2.base import Rancher2Base

load_dotenv()

log = logging.getLogger(__name__)


class Rancher2Nodes(Rancher2Base):
    def __init__(self, dryrun=False):
        redmineClient = RedmineClient()
        self.pageTitle = f"{redmineClient.nodes_page}_{redmineClient.cluster_name}"
        super().__init__(redmineClient, dryrun)

    def set_content(self):
        rancher_client = RancherClient()
        server_link = f"{rancher_client.base_url}dashboard"
        cluster_link = f"{server_link}/c/{rancher_client.cluster_id}/explorer"
        cluster_capacity = 0
        cluster_requested = 0
        cluster_limit = 0
        cluster_content = []

        # add the nodes information
        cluster_content.append(
            "|_{min-width:14em}. Name |_. Check_MK |_. Taints "
            "|_. Total RAM |_. Used |_. %Used |_. Available |_. Used pods "
            "|_. IP address |_. Version |_. OS |_. Created date |"
        )

        nodes = self._get_nodes(rancher_client)
        for node in nodes:
            try:
                node_name = node.get("metadata", {}).get("name", "unknown")
                capacity, requested, limit = self._get_memory_data(node)
                node_link = f"{cluster_link}/node/{node_name}"
                check_mk_link = (
                    f"https://goldeneye.eea.europa.eu/omdeea/check_mk/index.py"
                    f"?start_url=%2Fomdeea%2Fcheck_mk%2Fview.py%3Fview_name%3Dhost%26host%3D"
                    f"{node_name.split('.')[0]}%26site%3Domdeea"
                )
                taints = ""
                if node.get("spec", {}).get("taints"):
                    taints = [taint["key"] for taint in node["spec"]["taints"]]
                    taints = "\n".join(taints)

                try:
                    pods_raw = node.get("metadata", {}).get("annotations", {}).get(
                        "management.cattle.io/pod-requests", "{}"
                    )
                    pods_used = json.loads(pods_raw).get("pods", "-")
                except (json.JSONDecodeError, TypeError, KeyError) as e:
                    log.warning("Node %s: failed to parse pod-requests for pod count: %s", node_name, e)
                    pods_used = "-"

                node_ip = node.get("metadata", {}).get("annotations", {}).get(
                    "alpha.kubernetes.io/provided-node-ip", "-"
                )
                node_version = node.get("status", {}).get("node_info", {}).get(
                    "container_runtime_version", "-"
                )
                node_os = node.get("status", {}).get("node_info", {}).get("os_image", "-")
                node_created = node.get("metadata", {}).get("creation_timestamp", "-")

                cluster_content.append(
                    f"| \"{node_name}\":{node_link} "
                    f"| \"{node_name.split('.')[0]}\":{check_mk_link} | {taints} "
                    f"|>. {capacity} |>. {requested} |>. {round(requested * 100 / capacity, 2)} "
                    f"|>. {round(capacity - requested, 2)} "
                    f"|>. {pods_used} "
                    f"| {node_ip} "
                    f"| {node_version} "
                    f"| {node_os} "
                    f"| {node_created} |"
                )

                cluster_capacity += capacity
                cluster_requested += requested
                cluster_limit += limit
            except Exception:
                node_name = node.get("metadata", {}).get("name", "unknown")
                log.exception("Failed to process node %s", node_name)

        # add the cluster information
        self.content.append(
            f"\nh3. Cluster: \"{rancher_client.cluster_name}\":{cluster_link}\n"
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


class Rancher2MergeNodes(Rancher2Base):
    def __init__(self, dryrun=False):
        redmineClient = RedmineClient()
        self.pageTitle = redmineClient.nodes_page
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
                merged_content[rancher_server_name]["content"].extend(text.splitlines())
            except Exception:
                log.exception("Failed to get page text for cluster %s", cluster)

        for server_content in merged_content.values():
            self.content.append(f"\n{server_content['title']}\n")
            self.content.extend(server_content["content"])
