import os
import time

from dotenv import load_dotenv

from utils import memory_unit_conversion

load_dotenv()


class Rancher2Base:
    def __init__(self, redmineClient, dryrun=False):
        self.redmineClient = redmineClient
        self.dryrun = dryrun

        if not self.pageTitle:
            raise Exception(
                "pageTitle is not set, please set pageTitle in the subclass"
            )

        self.content = []
        self.content.append(f"h1. {self.pageTitle}\n")
        self.content.append("{{>toc}}\n\n")
        self.content.append(
            "Automatically discovered on "
            + time.strftime("%d %B %Y")
            + f". {self.redmineClient.marker}"
        )

    def _get_nodes(self, rancher_client):
        nodes_response = rancher_client.v1.list_node()
        nodes_list = nodes_response.to_dict()["items"]
        return nodes_list

    def _get_memory_data(self, node):
        """
        Returns memory information
        @param object: dict - Node

        @return: The capacity, requested memory and memory limit

        """
        capacity = round(memory_unit_conversion(node["status"]["capacity"]["memory"]), 2)

        pod_requests = eval(node["metadata"]["annotations"]["management.cattle.io/pod-requests"])
        requested = round(memory_unit_conversion(pod_requests["memory"]), 2)

        pod_limits = eval(node["metadata"]["annotations"]["management.cattle.io/pod-limits"])
        limit = round(memory_unit_conversion(pod_limits["memory"]), 2)

        return capacity, requested, limit

    def write_page(self):
        # To be added: check if the content has changed

        content = "\n".join(self.content)
        if self.dryrun:
            print(f"Would write page {self.pageTitle}")
            print(content)
        else:
            self.redmineClient.write_page(self.pageTitle, content)

    def set_content(self):
        raise NotImplementedError
