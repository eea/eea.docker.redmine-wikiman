import os
from collections import defaultdict

from dotenv import load_dotenv

from image_checker import ImageChecker
from rancher2.auth import RancherClient, RedmineClient
from rancher2.base import Rancher2Base
from utils import memory_unit_conversion

load_dotenv()


class Rancher2Pods(Rancher2Base):
    def __init__(self, dryrun=False):
        redmineClient = RedmineClient()
        self.pageTitle = f"{redmineClient.pods_page}_{redmineClient.cluster_name}"
        super().__init__(redmineClient, dryrun)

    def _get_container_memory_data(self, container, resources_dict):
        resources = resources_dict.get(container["name"])
        requested = (
            round(memory_unit_conversion(resources["requests"]["memory"]), 2)
            if resources.get("requests")
            else 0
        )
        limit = (
            round(memory_unit_conversion(resources["limits"]["memory"]), 2)
            if resources.get("limits")
            else 0
        )

        return requested, limit

    def _get_pods(self, rancher_client):
        pods_dict = defaultdict(list)
        pods_response = rancher_client.v1.list_pod_for_all_namespaces()
        for pod in pods_response.to_dict()["items"]:
            pods_dict[pod["spec"]["node_name"]].append(pod)

        return pods_dict

    def _add_pods_data(self, cluster_content, cluster_link, node, pods_dict):
        cluster_content.append(
            "|_{min-width:14em}. Pod |_. Pod state |_. Namespace "
            "|_. Container |_. Container state |_. Image |_. Restarts "
            "|_. Reservation |_. Limit |_. Start time |_. Upgrade |"
        )

        for pod in pods_dict.get(node["metadata"]["name"], []):
            # add pod/containers information
            pod_link = f"{cluster_link}/pod/{pod['metadata']['namespace']}/{pod['metadata']['name']}"
            resources_dict = {
                container["name"]: container["resources"]
                for container in pod["spec"]["containers"]
            }

            for container in pod["status"]["container_statuses"]:
                container_state = next(iter(container["state"]))
                start_time = container["state"]["running"]["started_at"] if container["started"] else "-"
                requested, limit = self._get_container_memory_data(container, resources_dict)

                cluster_content.append(
                    f"| \"{pod['metadata']['name']}\":{pod_link} | {pod['status']['phase']} "
                    f"| {pod['metadata']['namespace']} | {container['name']} | {container_state} "
                    f"| {container['image']} |>. {container['restart_count']} "
                    f"|>. {requested} |>. {limit} | {start_time} | TODO |"
                )

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
        for node in nodes:
            # add node information
            node_link = f"{cluster_link}/node/{node['metadata']['name']}"
            cluster_content.append(f"\nh4. Node: \"{node['metadata']['name']}\":{node_link}\n")
            cluster_content.append(f"*Description*: {node.get('description', '-')}\n")
            cluster_content.append(
                f"*Version*: {node['status']['node_info']['kubelet_version']} &nbsp; &nbsp; "
                f"*IP address*: {node['metadata']['annotations']['alpha.kubernetes.io/provided-node-ip']} &nbsp; &nbsp; "
                f"*OS*: {node['status']['node_info']['os_image']} &nbsp; &nbsp; "
                f"*Created date*: {node['metadata']['creation_timestamp']}\n"
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
            cluster_content.append(
                f"\n*Used Pods*: {eval(node['metadata']['annotations']['management.cattle.io/pod-requests'])['pods']}\n"
            )

            # add pods information
            self._add_pods_data(cluster_content, cluster_link, node, pods_dict)

            cluster_capacity += capacity
            cluster_requested += requested
            cluster_limit += limit

        # add the cluster information
        self.content.append(
            f"\nh3. Cluster: \"{rancher_client.cluster_name}\":{cluster_link}\n"
        )
        self.content.append(f"*Total RAM*: {cluster_capacity} GiB\n")
        self.content.append(
            f"*Reserved RAM*: {cluster_requested} GiB, "
            f"{round(cluster_requested * 100 / cluster_capacity, 2)}% used or "
            f"{round(cluster_capacity - cluster_requested, 2)} GiB available\n"
        )
        self.content.append(
            f"*Limit RAM*: {cluster_limit} GiB, "
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
            rancher_url, rancher_server_name, cluster = cluster_data.split(",")
            if rancher_server_name not in merged_content:
                merged_content[rancher_server_name] = {
                    "title": f'h2. "{rancher_server_name}":{rancher_url}dashboard',
                    "content": [],
                }

            text = self.redmineClient.get_page_text(f"{self.pageTitle}_{cluster}")
            for line in text.splitlines():
                if "TODO" not in line:
                    merged_content[rancher_server_name]["content"].append(line)
                    continue

                image = line.split("|")[6].strip()
                _, update_msg = self.image_checker.check_image_and_base_status(image)
                merged_content[rancher_server_name]["content"].append(
                    line.replace("TODO", update_msg)
                )

        for server_content in merged_content.values():
            self.content.append(f"\n{server_content['title']}\n")
            self.content.extend(server_content["content"])
