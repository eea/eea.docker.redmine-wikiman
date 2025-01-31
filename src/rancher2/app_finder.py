import os
from dotenv import load_dotenv

from src.rancher2.auth import RancherClient

load_dotenv()


class Rancher2AppFinder:
    def __init__(self):
        self.rancherDataList = os.getenv("RANCHER2_CONFIG", "").split("|")

    def _get_clusters(self, rancher_client):
        clusters_response = rancher_client.get("/v3/clusters")
        clusters_list = clusters_response["data"]
        return clusters_list

    def _find_apps_by_service_location(self, rancher_client, cluster_id, service_location):
        namespace_id = None
        namespaces = rancher_client.get(f"/v3/clusters/{cluster_id}/namespaces")
        for namespace in namespaces["data"]:
            # try to find service location url in namespace description
            if service_location in namespace.get("description", ""):
                namespace_id = namespace["id"]
                break

        if not namespace_id:
            return []

        app_list = []
        apps = rancher_client.get(
            f"k8s/clusters/{cluster_id}/v1/catalog.cattle.io.apps"
        )
        app_base_link = f"{rancher_client.base_url}dashboard/c/{cluster_id}/apps/catalog.cattle.io.app"
        for app in apps["data"]:
            if namespace_id == app["spec"]["namespace"]:
                app_link = f"{app_base_link}/{app['id']}"
                app_list.append(f"\"{app['spec']['name']}\":{app_link}")

        return app_list

    def _find_apps_by_chart_name(self, rancher_client, cluster_id, chart_name):
        apps = rancher_client.get(
            f"k8s/clusters/{cluster_id}/v1/catalog.cattle.io.apps"
        )
        for app in apps["data"]:
            if chart_name == app["spec"]["chart"]["metadata"]["name"]:
                return [app]

        return []

    def find(self, service_location=None, chart_name=None):
        for rancher_data in self.rancherDataList:
            rancher_url, rancher_server_name, rancher_token = rancher_data.split(",")
            rancher_client = RancherClient(rancher_url, rancher_token)

            for cluster in self._get_clusters(rancher_client):
                if service_location:
                    found_apps = self._find_apps_by_service_location(
                        rancher_client, cluster["id"], service_location
                    )
                else:
                    found_apps = self._find_apps_by_chart_name(
                        rancher_client, cluster["id"], chart_name
                    )
                if found_apps:
                    return found_apps

        return []
