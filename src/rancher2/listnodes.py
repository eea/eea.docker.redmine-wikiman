from src.rancher2.auth import RancherClient, RedmineClient


class RancherNodes:
    def __init__(self):
        self.rancher_client = RancherClient()
        self.redmine_client = RedmineClient()
        self.page_name = "Rancher2 Nodes"

    def _get_clusters(self):
        clusters_response = self.rancher_client.get("/v3/clusters")
        clusters_list = clusters_response["data"]
        return clusters_list

    def _get_nodes(self, cluster_id):
        nodes_response = self.rancher_client.get(f"/v3/clusters/{cluster_id}/nodes")
        nodes_list = nodes_response["data"]
        return nodes_list

    def get_data(self):
        clusters = self._get_clusters()
        data = []
        for cluster in clusters:
            nodes = self._get_nodes(cluster["id"])
            data.append({"cluster": cluster, "nodes": nodes})
        return data
