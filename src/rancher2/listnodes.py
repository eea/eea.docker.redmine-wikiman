import os

from dotenv import load_dotenv

from rancher2.auth import RancherClient, RedmineClient
from rancher2.base import Rancher2Base

load_dotenv()


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
            "|_{width:14em}. Name |_. Check_MK "
            "|_. Total RAM |_. Used |_. %Used |_. Available |_. Used pods "
            "|_. IP address |_. Version |_. OS |_. Created date |"
        )

        nodes = self._get_nodes(rancher_client)
        for node in nodes:
            capacity, requested, limit = self._get_memory_data(node)
            node_link = f"{cluster_link}/node/{node['metadata']['name']}"
            check_mk_link = (
                f"https://goldeneye.eea.europa.eu/omdeea/check_mk/index.py"
                f"?start_url=%2Fomdeea%2Fcheck_mk%2Fview.py%3Fview_name%3Dhost%26host%3D"
                f"{node['metadata']['name'].split('.')[0]}%26site%3Domdeea"
            )

            cluster_content.append(
                f"| \"{node['metadata']['name']}\":{node_link} "
                f"| \"{node['metadata']['name'].split('.')[0]}\":{check_mk_link} "
                f"|>. {capacity} |>. {requested} |>. {round(requested * 100 / capacity, 2)} "
                f"|>. {round(capacity - requested, 2)} "
                f"|>. {eval(node['metadata']['annotations']['management.cattle.io/pod-requests'])['pods']} "
                f"| {node['metadata']['annotations']['alpha.kubernetes.io/provided-node-ip']} "
                f"| {node['status']['node_info']['kubelet_version']} "
                f"| {node['status']['node_info']['os_image']} "
                f"| {node['metadata']['creation_timestamp']} |"
            )

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


class Rancher2MergeNodes(Rancher2Base):
    def __init__(self, dryrun=False):
        redmineClient = RedmineClient()
        self.pageTitle = redmineClient.nodes_page
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
            merged_content[rancher_server_name]["content"].extend(text.splitlines())

        for server_content in merged_content.values():
            self.content.append(f"\n{server_content['title']}\n")
            self.content.extend(server_content["content"])
