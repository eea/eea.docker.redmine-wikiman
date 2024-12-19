import requests

from src.rancher2.auth import RancherClient


class RancherNodes:
    def __init__(self):
        self.client = RancherClient()

    def get_clusters(self):
        clusters_response = self.client.get("/v3/clusters")
        clusters_list = clusters_response["data"]
        return clusters_list

    def get_nodes(self, cluster_id):
        nodes_response = self.client.get(f"/v3/clusters/{cluster_id}/nodes")
        nodes_list = nodes_response["data"]
        return nodes_list

    def get_data(self):
        clusters = self.get_clusters()
        data = []
        for cluster in clusters:
            nodes = self.get_nodes(cluster["id"])
            data.append({"cluster": cluster, "nodes": nodes})
        return data
