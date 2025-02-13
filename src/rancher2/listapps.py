import base64
import gzip
import io
import json
import os

from dotenv import load_dotenv

from rancher2.auth import RancherClient, RedmineClient
from rancher2.base import Rancher2Base

load_dotenv()


class Rancher2Apps(Rancher2Base):
    def __init__(self, dryrun=False):
        redmineClient = RedmineClient()
        self.pageTitle = f"{redmineClient.apps_page}_{redmineClient.cluster_name}"
        super().__init__(redmineClient, dryrun)

    def _decode_chart_data(self, encoded_data):
        decoded_data = base64.b64decode(encoded_data)
        decoded_data = base64.b64decode(decoded_data)
        with gzip.GzipFile(fileobj=io.BytesIO(decoded_data), mode="rb") as f:
            decompressed_data = f.read()

        chart_data = json.loads(decompressed_data)["chart"]
        return chart_data

    def _get_namespaces(self, rancher_client):
        namespaces_response = rancher_client.v1.list_namespace()
        namespaces_list = namespaces_response.to_dict()["items"]
        return namespaces_list

    def _get_apps(self, rancher_client, namespace_id):
        apps_response = rancher_client.v1.list_namespaced_secret(
            namespace_id, label_selector="owner=helm,status=deployed"
        )
        apps_list = apps_response.to_dict()["items"]
        return apps_list

    def set_content(self):
        rancher_client = RancherClient()
        server_link = f"{rancher_client.base_url}dashboard"
        cluster_link = f"{server_link}/c/{rancher_client.cluster_id}/explorer"

        # add the cluster information
        self.content.append(
            f"\nh3. Cluster: \"{rancher_client.cluster_name}\":{cluster_link}\n"
        )

        namespaces = self._get_namespaces(rancher_client)
        for namespace in namespaces:
            namespace_id = namespace["metadata"]["name"]
            apps = self._get_apps(rancher_client, namespace_id)
            if not apps:
                continue

            # add namespace information
            namespace_link = f"{cluster_link}/namespace/{namespace_id}"
            self.content.append(
                f"\nh4. _Namespace: \"{namespace['metadata']['name']}\":{namespace_link}_\n"
            )
            self.content.append(
                f"*State*: {namespace['status']['phase']} &nbsp; &nbsp; "
                f"*Created*: {namespace['metadata']['creation_timestamp']}\n"
            )

            # add app information
            self.content.append(
                "|_{width:14em}. Name |_. State |_. Chart Name |_. Chart Version "
                "|_. Created date |_. Description |"
            )
            description = namespace["metadata"]["annotations"].get("field.cattle.io/description", "")
            app_base_link = (
                f"{rancher_client.base_url}dashboard/c/{rancher_client.cluster_id}"
                f"/apps/catalog.cattle.io.app/{namespace_id}"
            )
            for app in apps:
                chart_data = self._decode_chart_data(app["data"]["release"])
                app_name = app["metadata"]["labels"]["name"]
                app_link = f"{app_base_link}/{app_name}"
                if namespace_id.endswith("-system"):
                    app_name = f">. _{app_name}_"

                self.content.append(
                    f"|\"{app_name}\":{app_link} | {app['metadata']['labels']['status']} "
                    f"| {chart_data['metadata']['name']} | {chart_data['metadata']['version']} "
                    f"| {app['metadata']['creation_timestamp']} | {description} |"
                )


class Rancher2MergeApps(Rancher2Base):
    def __init__(self, dryrun=False):
        redmineClient = RedmineClient()
        self.pageTitle = redmineClient.apps_page
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
