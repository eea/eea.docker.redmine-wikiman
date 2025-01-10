from src.rancher2.base import Rancher2Base


class Rancher2Nodes(Rancher2Base):
    def __init__(self, dryrun=False):
        self.pageTitle = "Rancher2_test"
        super().__init__(dryrun)

    def set_server_rancher_content(self, rancher_client, rancher_server_name):
        server_link = f"{rancher_client.base_url}dashboard"
        self.content.append(f'\nh2. "{rancher_server_name}":{server_link}\n')
        server_capacity = 0
        server_requested = 0

        clusters = self._get_clusters(rancher_client)
        cluster_content = []
        for cluster in clusters:
            cluster_link = self._add_cluster_short_content(
                rancher_client, cluster, cluster_content
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
