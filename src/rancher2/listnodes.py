from src.rancher2.base import Rancher2Base


class Rancher2Nodes(Rancher2Base):
    def __init__(self, dryrun=False):
        self.pageTitle = "Rancher2_test_nodes"
        super().__init__(dryrun)

    def set_server_rancher_content(self, rancher_client, rancher_server_name):
        server_link = f"{rancher_client.base_url}dashboard"
        self.content.append(f'\nh2. "{rancher_server_name}":{server_link}\n')
        server_capacity = 0
        server_requested = 0
        server_limit = 0

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

            # add the nodes information
            cluster_content.append(
                "|_{width:14em}. Name |_. Check_MK |_. State "
                "|_. Total RAM |_. Used |_. %Used |_. Available |_. Used pods "
                "|_. IP address |_. Version |_. OS |_. Created date |"
            )

            nodes = self._get_nodes(rancher_client, cluster["id"])
            for node in nodes:
                capacity, requested, _ = self._get_memory_data(node)
                node_link = f"{cluster_link}/node/{node['nodeName']}"
                check_mk_link = (
                    f"https://goldeneye.eea.europa.eu/omdeea/check_mk/index.py"
                    f"?start_url=%2Fomdeea%2Fcheck_mk%2Fview.py%3Fview_name%3Dhost%26host%3D"
                    f"{node['nodeName'].split('.')[0]}%26site%3Domdeea"
                )

                cluster_content.append(
                    f"| \"{node['nodeName']}\":{node_link} "
                    f"| \"{node['nodeName'].split('.')[0]}\":{check_mk_link} | {node['state']} "
                    f"|>. {capacity} |>. {requested} |>. {round(requested * 100 / capacity, 2)} "
                    f"|>. {round(capacity - requested, 2)} |>. {node['requested']['pods']} "
                    f"| {node['ipAddress']} | {node['info']['kubernetes']['kubeletVersion']} "
                    f"| {node['info']['os']['operatingSystem']} | {node['created']} |"
                )

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
