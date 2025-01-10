from collections import defaultdict

from src.rancher2.base import Rancher2Base


class Rancher2Pods(Rancher2Base):
    def __init__(self, dryrun=False):
        self.pageTitle = "Rancher2_test"
        super().__init__(dryrun)

    def _get_pods(self, rancher_client):
        pods_dict = defaultdict(list)
        pods_response = rancher_client.get("/v1/pods")
        for pod in pods_response["data"]:
            pods_dict[pod["spec"]["nodeName"]].append(pod)

        return pods_dict

    def _add_pods_data(self, cluster_content, cluster_link, node, pods_dict):
        for pod in pods_dict[node["nodeName"]]:
            # add pod information
            pod_images = []
            containers_ready = 0
            containers_restarts = 0
            pod_link = f"{cluster_link}/pod/{pod['id']}"

            for container in pod["status"]["containerStatuses"]:
                pod_images.append(container["image"])
                containers_ready += int(container["ready"] == True)
                containers_restarts += container["restartCount"]

            cluster_content.append(f"\nh4. _Pod: \"{pod['metadata']['name']}\":{pod_link}_\n")
            cluster_content.append(f"*Description*: {pod.get('description', '-')}\n")
            cluster_content.append(
                f"*State*: {pod['status']['phase']} &nbsp; &nbsp; "
                f"*Namespace*: {pod['metadata']['namespace']} &nbsp; &nbsp; "
                f"*Images*: {', '.join(pod_images)}\n"
            )
            cluster_content.append(
                f"*Ready*: {containers_ready}/{len(pod['status']['containerStatuses'])} &nbsp; &nbsp; "
                f"*Restarts*: {containers_restarts} &nbsp; &nbsp; "
                f"*IP address*: {pod['status'].get('podIP', '-')} &nbsp; &nbsp; "
                f"*Start time*: {pod['status']['startTime']}\n"
            )

            # add containers information
            cluster_content.append(
                "|_{width:14em}. Name |_. State |_. Image "
                "|_. Ready |_. Restarts |_. Start time |"
            )
            for container in pod["status"]["containerStatuses"]:
                container_state = next(iter(container["state"]))
                start_time = container["state"]["running"]["startedAt"] if container["started"] else "-"
                cluster_content.append(
                    f"| {container['name']} | {container_state} | {container['image']} "
                    f"| {container['ready']} | {container['restartCount']} | {start_time} |"
                )

    def set_server_rancher_content(self, rancher_client, rancher_server_name):
        server_link = f"{rancher_client.base_url}dashboard"
        self.content.append(f'\nh2. "{rancher_server_name}":{server_link}\n')
        server_capacity = 0
        server_requested = 0

        pods_dict = self._get_pods(rancher_client)
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

            nodes = self._get_nodes(rancher_client, cluster["id"])
            for node in nodes:
                # add node information
                node_link = f"{cluster_link}/node/{node['nodeName']}"
                cluster_content.append(f"\nh4. Node: \"{node['nodeName']}\":{node_link}\n")
                cluster_content.append(f"*Description*: {node.get('description', '-')}\n")
                cluster_content.append(
                    f"*State*: {node['state']} &nbsp; &nbsp; "
                    f"*Version*: {node['info']['kubernetes']['kubeletVersion']} &nbsp; &nbsp; "
                    f"*IP address*: {node['ipAddress']} &nbsp; &nbsp; "
                    f"*OS*: {node['info']['os']['operatingSystem']} &nbsp; &nbsp; "
                    f"*Created date*: {node['created']}\n"
                )

                # add the memory and CPU information
                _, requested = self._get_memory_cpu(node)
                cluster_content.append(
                    f"*Used Memory (Gib)*: {requested['memory']} &nbsp; &nbsp; "
                    f"*Used CPU (cores)*: {requested['cpu']} &nbsp; &nbsp; "
                    f"*Used Pods*: {node['requested']['pods']}\n"
                )

                # add pods information
                self._add_pods_data(cluster_content, cluster_link, node, pods_dict)

        self.content.append(
            f"\n*Total Reserved Memory*: {server_requested} GiB from a total of {server_capacity} GiB\n"
        )
        self.content.extend(cluster_content)
