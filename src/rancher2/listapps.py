import base64
import gzip
import io
import json
import logging
import os

from dotenv import load_dotenv

from rancher2.auth import RancherClient, RedmineClient
from rancher2.base import Rancher2Base

load_dotenv()

log = logging.getLogger(__name__)


class Rancher2Apps(Rancher2Base):
    def __init__(self, dryrun=False):
        redmineClient = RedmineClient()
        self.pageTitle = f"{redmineClient.apps_page}_{redmineClient.cluster_name}"
        super().__init__(redmineClient, dryrun)

    def _decode_chart_data(self, encoded_data):
        try:
            decoded_data = base64.b64decode(encoded_data)
            decoded_data = base64.b64decode(decoded_data)
            with gzip.GzipFile(fileobj=io.BytesIO(decoded_data), mode="rb") as f:
                decompressed_data = f.read()
            chart_data = json.loads(decompressed_data)["chart"]
            return chart_data
        except Exception:
            log.exception("Failed to decode chart data from secret")
            return {}

    def _get_namespaces(self, rancher_client):
        try:
            namespaces_response = rancher_client.v1.list_namespace()
            namespaces_list = namespaces_response.to_dict().get("items", [])
            log.info("Found %d namespaces", len(namespaces_list))
            return namespaces_list
        except Exception:
            log.exception("Failed to list namespaces from Rancher API")
            return []

    def _get_apps(self, rancher_client, namespace_id):
        try:
            apps_response = rancher_client.v1.list_namespaced_secret(
                namespace_id, label_selector="owner=helm,status=deployed"
            )
            apps_list = apps_response.to_dict().get("items", [])
            return apps_list
        except Exception:
            log.exception("Failed to list apps for namespace %s", namespace_id)
            return []

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
            try:
                namespace_id = namespace.get("metadata", {}).get("name", "")
                if not namespace_id:
                    continue

                apps = self._get_apps(rancher_client, namespace_id)
                if not apps:
                    continue

                log.info("Namespace %s: processing %d apps", namespace_id, len(apps))

                # add namespace information
                namespace_link = f"{cluster_link}/namespace/{namespace_id}"
                self.content.append(
                    f"\nh4. _Namespace: \"{namespace_id}\":{namespace_link}_\n"
                )
                namespace_phase = namespace.get("status", {}).get("phase", "Unknown")
                namespace_created = namespace.get("metadata", {}).get("creation_timestamp", "-")
                self.content.append(
                    f"*State*: {namespace_phase} &nbsp; &nbsp; "
                    f"*Created*: {namespace_created}\n"
                )

                # add app information
                self.content.append(
                    "|_{min-width:14em}. Name |_. State |_. Chart Name |_. Chart Version "
                    "|_. Created date |_. Description |"
                )
                description = namespace.get("metadata", {}).get("annotations", {}).get(
                    "field.cattle.io/description", ""
                )
                app_base_link = (
                    f"{rancher_client.base_url}dashboard/c/{rancher_client.cluster_id}"
                    f"/apps/catalog.cattle.io.app/{namespace_id}"
                )
                for app in apps:
                    try:
                        app_data = app.get("data", {})
                        release_data = app_data.get("release", "")
                        if not release_data:
                            log.warning("App in namespace %s has no release data", namespace_id)
                            continue

                        chart_data = self._decode_chart_data(release_data)
                        if not chart_data:
                            continue

                        app_name = app.get("metadata", {}).get("labels", {}).get("name", "unknown")
                        app_link = f"{app_base_link}/{app_name}"
                        if namespace_id.endswith("-system"):
                            app_name = f">. _{app_name}_"

                        app_status = app.get("metadata", {}).get("labels", {}).get("status", "unknown")
                        chart_metadata = chart_data.get("metadata", {})
                        chart_name = chart_metadata.get("name", "unknown")
                        chart_version = chart_metadata.get("version", "unknown")
                        app_created = app.get("metadata", {}).get("creation_timestamp", "-")

                        self.content.append(
                            f"|\"{app_name}\":{app_link} | {app_status} "
                            f"| {chart_name} | {chart_version} "
                            f"| {app_created} | {description} |"
                        )
                    except Exception:
                        app_name = app.get("metadata", {}).get("name", "unknown")
                        log.exception("Failed to process app %s in namespace %s", app_name, namespace_id)
            except Exception:
                namespace_id = namespace.get("metadata", {}).get("name", "unknown")
                log.exception("Failed to process namespace %s", namespace_id)


class Rancher2MergeApps(Rancher2Base):
    def __init__(self, dryrun=False):
        redmineClient = RedmineClient()
        self.pageTitle = redmineClient.apps_page
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
