import time
from dotenv import load_dotenv
import os

from src.rancher2.auth import RancherClient, RedmineClient
from src.utils import memory_unit_conversion, cpu_unit_conversion

load_dotenv()


class RancherNodes:
    def __init__(self, dryrun=False):
        self.redmineClient = RedmineClient()
        self.rancherDataList = os.getenv("RANCHER2_CONFIG", "").split("|")
        self.pageTitle = "Rancher2_test"
        self.dryrun = dryrun

        self.content = []
        self.content.append("{{>toc}}\n\n")
        self.content.append(f"h1. {self.pageTitle}\n")
        self.content.append(
            "Automatically discovered on "
            + time.strftime("%d %B %Y")
            + ". _Do not update this page manually._"
        )

    def _get_clusters(self, rancher_client):
        clusters_response = rancher_client.get("/v3/clusters")
        clusters_list = clusters_response["data"]
        return clusters_list

    def _get_nodes(self, rancher_client, cluster_id):
        nodes_response = rancher_client.get(f"/v3/clusters/{cluster_id}/nodes")
        nodes_list = nodes_response["data"]
        return nodes_list

    def write_page(self):
        # To be added: check if the content has changed

        content = "\n".join(self.content)
        if self.dryrun:
            print(f"Would write page {self.pageTitle}")
            print(content)
        else:
            self.redmineClient.write_page(self.pageTitle, content)

    def _get_memory_cpu(self, object):
        """
        Return memory and CPU information
        @param object: dict - The object to print
        The object should have the following structure:
        {
            "capacity": {
                "memory": "2131Mi",
                "cpu": "2131m",
            },
            "requested": {
                "memory": "2131Mi",
                "cpu": "2131m",
            }
        }

        @return: The capacity and requested memory and CPU

        """

        capacity = {
            "memory": round(memory_unit_conversion(object["capacity"]["memory"]), 2),
            "cpu": round(cpu_unit_conversion(object["capacity"]["cpu"]), 2),
        }
        requested = {
            "memory": round(memory_unit_conversion(object["requested"]["memory"]), 2),
            "cpu": round(cpu_unit_conversion(object["requested"]["cpu"]), 2),
        }

        return capacity, requested

    def set_server_rancher_content(self, rancher_client, rancher_server_name):
        server_link = f"{rancher_client.base_url}dashboard"
        self.content.append(f'\nh2. "{rancher_server_name}":{server_link}\n')
        server_capacity = 0
        server_requested = 0

        clusters = self._get_clusters(rancher_client)
        cluster_content = []
        for cluster in clusters:
            cluster_link = (
                f"{rancher_client.base_url}dashboard/c/{cluster['id']}/explorer"
            )
            cluster_content.append(f"\nh3. \"{cluster['name']}\":{cluster_link}\n")

            # add cluster information
            cluster_content.append(f"*Description*: {cluster['description'] or '-'}\n")
            cluster_content.append(
                f"*State*: {cluster['state']} &nbsp; &nbsp; "
                f"*Provider*: {cluster['provider']} &nbsp; &nbsp; "
                f"*Kubernetes Version*: {cluster['version']['gitVersion']} &nbsp; &nbsp; "
                f"*Created date*: {cluster['created']}\n"
            )

            # add the memory and CPU information
            capacity, requested = self._get_memory_cpu(cluster)
            server_capacity += capacity["memory"]
            server_requested += requested["memory"]
            cluster_content.append(
                f"*Reserved Memory*: {requested['memory']} "
                f"GiB from a total of {capacity['memory']} GiB\n"
            ),
            cluster_content.append(
                f"*Reserved CPUs*: {requested['cpu']} cores "
                f"from a total of {capacity['cpu']} cores\n"
            )
            cluster_content.append(
                f"*Reserved Pods*: {cluster['requested']['pods']} "
                f"from a total of {cluster['capacity']['pods']}\n"
            )

            # add the nodes information
            cluster_content.append(
                "|_{width:14em}. Name |_. State |_. Version |_. IP address |_. OS |_. Used CPU (core) "
                "|_{width:8em}. Used Memory (Gib) |_. Used Pods |_. Created date |"
            )

            nodes = self._get_nodes(rancher_client, cluster["id"])
            for node in nodes:
                capacity, requested = self._get_memory_cpu(node)
                node_link = f"{cluster_link}/node/{node['nodeName']}"

                cluster_content.append(
                    f"| \"{node['nodeName']}\":{node_link} | {node['state']} "
                    f"| {node['info']['kubernetes']['kubeletVersion']} "
                    f"| {node['ipAddress']} | {node['info']['os']['operatingSystem']} "
                    f"|>. {requested['cpu']} |>. {requested['memory']} "
                    f"|>. {node['requested']['pods']} | {node['created']} |"
                )

        self.content.append(
            f"\n*Total Reserved Memory*: {server_requested} GiB from a total of {server_capacity} GiB\n"
        )
        self.content.extend(cluster_content)

    def set_content(self):
        for rancher_data in self.rancherDataList:
            rancher_url, rancher_server_name, rancher_token = rancher_data.split(",")
            rancher_client = RancherClient(rancher_url, rancher_token)
            self.set_server_rancher_content(rancher_client, rancher_server_name)
