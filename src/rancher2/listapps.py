from src.rancher2.base import Rancher2Base


class Rancher2Apps(Rancher2Base):
    def __init__(self, dryrun=False):
        self.pageTitle = "Rancher2_test"
        super().__init__(dryrun)

    def _get_namespaces_dict(self, rancher_client, cluster_id):
        namespaces = rancher_client.get(f"/v3/clusters/{cluster_id}/namespaces")
        namespaces_dict = {
            namespace["id"]: namespace for namespace in namespaces["data"]
        }

        return namespaces_dict

    def _get_apps_dict(self, rancher_client, cluster_id):
        # set apps dict
        apps = rancher_client.get(
            f"k8s/clusters/{cluster_id}/v1/catalog.cattle.io.apps"
        )
        app_dict = {}
        for app in apps["data"]:
            namespace = app["spec"]["namespace"] or "No Namespace"
            if namespace not in app_dict:
                app_dict[namespace] = []
            app_dict[namespace].append(app)

        return app_dict

    def set_server_rancher_content(self, rancher_client, rancher_server_name):
        server_link = f"{rancher_client.base_url}dashboard"
        self.content.append(f'\nh2. "{rancher_server_name}":{server_link}\n')

        clusters = self._get_clusters(rancher_client)
        self.content = []
        for cluster in clusters:
            cluster_link = self._add_cluster_short_content(
                rancher_client, cluster, self.content
            )

            namespaces = self._get_namespaces_dict(rancher_client, cluster["id"])
            apps = self._get_apps_dict(rancher_client, cluster["id"])

            for namespace_id, app_list in apps.items():
                # add namespace information
                if namespace_id not in namespaces:
                    # some apps do not have a namespace set
                    self.content.append(f'\nh3. "{namespace_id}"\n')
                else:
                    namespace = namespaces[namespace_id]
                    namespace_link = f"{cluster_link}/namespace/{namespace_id}"
                    self.content.append(f'\nh3. "{namespace_id}":{namespace_link}\n')
                    self.content.append(
                        f"*State*: {namespace['state']} &nbsp; &nbsp; "
                        f"*Created*: {namespace['created']} &nbsp; &nbsp; "
                        f"*ProjectID*: {namespace['projectId']}\n"
                    )

                # add app information
                self.content.append(
                    "|_{width:14em}. Name |_. State |_. Chart Name |_. Chart Version "
                    "|>. Resources |_. Created date |"
                )
                app_base_link = f"{rancher_client.base_url}dashboard/c/{cluster['id']}/apps/catalog.cattle.io.app"
                for app in app_list:
                    app_link = f"{app_base_link}/{app['id']}"
                    self.content.append(
                        f"| \"{app['spec']['name']}\":{app_link} | {app['spec']['info']['status']} "
                        f"| {app['spec']['chart']['metadata']['name']} "
                        f"| {app['spec']['chart']['metadata']['version']} "
                        f"|>. {len(app['spec']['resources'])} | {app['metadata']['creationTimestamp']} |"
                    )
