from collections import defaultdict

from src.image_checker import ImageChecker
from src.rancher2.base import Rancher2Base
from src.utils import memory_unit_conversion


class Rancher2Pods(Rancher2Base):
    def __init__(self, dryrun=False):
        self.pageTitle = "Rancher2_test_pods"
        self.image_checker = ImageChecker()
        super().__init__(dryrun)

    def _get_container_memory_data(self, container, resources_dict):
        resources = resources_dict.get(container["name"])
        if not resources:
            return 0, 0

        requested = round(memory_unit_conversion(resources["requests"]["memory"]), 2)
        limit = round(memory_unit_conversion(resources["limits"]["memory"]), 2)

        return requested, limit

    def _get_pods(self, rancher_client):
        pods_dict = defaultdict(list)
        pods_response = rancher_client.get("/v1/pods")
        for pod in pods_response["data"]:
            pods_dict[pod["spec"]["nodeName"]].append(pod)

        return pods_dict

    def _add_pods_data(self, cluster_content, cluster_link, node, pods_dict):
        cluster_content.append(
            "|_{min-width:14em}. Pod |_. Pod state |_. Namespace "
            "|_. Container |_. Container state |_. Image |_. Restarts "
            "|_. Reservation |_. Limit |_. Start time |_. Upgrade |"
        )

        for pod in pods_dict.get(node["nodeName"], []):
            # add pod/containers information
            pod_link = f"{cluster_link}/pod/{pod['id']}"
            resources_dict = {
                container["name"]: container["resources"]
                for container in pod["spec"]["containers"]
            }

            for container in pod["status"]["containerStatuses"]:
                container_state = next(iter(container["state"]))
                start_time = container["state"]["running"]["startedAt"] if container["started"] else "-"
                _, update_msg = self.image_checker.check_image_and_base_status(container["image"])
                requested, limit = self._get_container_memory_data(container, resources_dict)

                cluster_content.append(
                    f"| \"{pod['metadata']['name']}\":{pod_link} | {pod['status']['phase']} "
                    f"| {pod['metadata']['namespace']} | {container['name']} | {container_state} "
                    f"| {container['image']} |>. {container['restartCount']} "
                    f"|>. {requested} |>. {limit} | {start_time} | {update_msg} |"
                )

    def set_server_rancher_content(self, rancher_client, rancher_server_name):
        server_link = f"{rancher_client.base_url}dashboard"
        self.content.append(f'\nh2. "{rancher_server_name}":{server_link}\n')
        server_capacity = 0
        server_requested = 0
        server_limit = 0

        pods_dict = self._get_pods(rancher_client)
        clusters = self._get_clusters(rancher_client)
        cluster_content = []
        for cluster in clusters:
            cluster_link = self._add_cluster_short_content(
                rancher_client, cluster, cluster_content
            )

            # add the memory information
            capacity, requested, limit = self._get_memory_data(cluster)
            server_capacity += capacity
            server_requested += requested
            server_limit += limit

            cluster_content.append(f"*Total RAM*: {capacity} GiB\n")
            cluster_content.append(
                f"*Reserved RAM*: {requested} GiB, "
                f"{round(requested * 100 / capacity, 2)}% used or "
                f"{round(capacity - requested, 2)} GiB available\n"
            )
            cluster_content.append(
                f"*Limit RAM*: {limit} GiB, "
                f"{round(limit * 100 / capacity, 2)}% used or "
                f"{round(capacity - limit, 2)} GiB available\n"
            )
            cluster_content.append(
                f"*Reserved Pods*: {cluster['requested']['pods']} "
                f"from a total of {cluster['capacity']['pods']}\n"
            )

            nodes = self._get_nodes(rancher_client, cluster["id"])
            for node in nodes:
                # add node information
                node_link = f"{cluster_link}/node/{node['nodeName']}"
                cluster_content.append(f"\nh4. Node: \"{node['nodeName']}\":{node_link}\n")
                cluster_content.append(f"*Description*: {node.get('description', '-')}\n")
                cluster_content.append(
                    f"*State*: {node['state']} &nbsp; &nbsp; "
                    f"*Version*: {node['info']['kubernetes']['kubeletVersion']} &nbsp; &nbsp; "
                    f"*IP address*: {node['ipAddress']} &nbsp; &nbsp; "
                    f"*OS*: {node['info']['os']['operatingSystem']} &nbsp; &nbsp; "
                    f"*Created date*: {node['created']}\n"
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
                cluster_content.append(f"\n*Used Pods*: {node['requested']['pods']}\n")

                # add pods information
                self._add_pods_data(cluster_content, cluster_link, node, pods_dict)

        self.content.append(f"*Total RAM*: {server_capacity} GiB\n")
        self.content.append(
            f"*Reserved RAM*: {server_requested} GiB, "
            f"{round(server_requested * 100 / server_capacity, 2)}% used or "
            f"{round(server_capacity - server_requested, 2)} GiB available\n"
        )
        self.content.append(
            f"*Limit RAM*: {server_limit} GiB, "
            f"{round(server_limit * 100 / server_capacity, 2)}% used or "
            f"{round(server_capacity - server_limit, 2)} GiB available\n"
        )
        self.content.extend(cluster_content)
