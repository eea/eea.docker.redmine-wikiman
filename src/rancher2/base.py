import time
from dotenv import load_dotenv
import os

from src.rancher2.auth import RancherClient, RedmineClient
from src.utils import memory_unit_conversion, cpu_unit_conversion

load_dotenv()


class Rancher2Base:
    def __init__(self, dryrun=False):
        self.redmineClient = RedmineClient()
        self.rancherDataList = os.getenv("RANCHER2_CONFIG", "").split("|")
        self.dryrun = dryrun

        if not self.pageTitle:
            raise Exception(
                "pageTitle is not set, please set pageTitle in the subclass"
            )

        self.content = []
        self.content.append("{{>toc}}\n\n")
        self.content.append(f"h1. {self.pageTitle}\n")
        self.content.append(
            "Automatically discovered on "
            + time.strftime("%d %B %Y")
            + ". _Do not update this page manually._"
        )

    def _add_cluster_short_content(self, rancher_client, cluster, cluster_content):
        """
        Add the cluster information to the content and return the cluster link
        @param rancher_client: RancherClient - The Rancher client
        @param cluster: dict - The cluster to print
        @param cluster_content: list - The list to append the content to

        @return: The cluster link
        """

        cluster_link = f"{rancher_client.base_url}dashboard/c/{cluster['id']}/explorer"
        cluster_content.append(f"\nh3. \"{cluster['name']}\":{cluster_link}\n")

        # add cluster information
        cluster_content.append(f"*Description*: {cluster['description'] or '-'}\n")
        cluster_content.append(
            f"*State*: {cluster['state']} &nbsp; &nbsp; "
            f"*Provider*: {cluster['provider']} &nbsp; &nbsp; "
            f"*Kubernetes Version*: {cluster['version']['gitVersion']} &nbsp; &nbsp; "
            f"*Created date*: {cluster['created']}\n"
        )

        return cluster_link

    def _get_clusters(self, rancher_client):
        clusters_response = rancher_client.get("/v3/clusters")
        clusters_list = clusters_response["data"]
        return clusters_list

    def _get_nodes(self, rancher_client, cluster_id):
        nodes_response = rancher_client.get(f"/v3/clusters/{cluster_id}/nodes")
        nodes_list = nodes_response["data"]
        return nodes_list

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

    def write_page(self):
        # To be added: check if the content has changed

        content = "\n".join(self.content)
        if self.dryrun:
            print(f"Would write page {self.pageTitle}")
            print(content)
        else:
            self.redmineClient.write_page(self.pageTitle, content)

    def set_server_rancher_content(self, rancher_client, rancher_server_name):
        raise NotImplementedError

    def set_content(self):
        for rancher_data in self.rancherDataList:
            rancher_url, rancher_server_name, rancher_token = rancher_data.split(",")
            rancher_client = RancherClient(rancher_url, rancher_token)
            self.set_server_rancher_content(rancher_client, rancher_server_name)
