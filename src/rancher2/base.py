import json
import logging
import time

from utils import memory_unit_conversion

log = logging.getLogger(__name__)


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
        try:
            nodes_response = rancher_client.v1.list_node()
            nodes_list = nodes_response.to_dict()["items"]
            log.info("Found %d nodes", len(nodes_list))
            return nodes_list
        except Exception:
            log.exception("Failed to list nodes from Rancher API")
            return []

    def _get_memory_data(self, node):
        """
        Returns memory information
        @param object: dict - Node

        @return: The capacity, requested memory and memory limit

        """
        node_name = node.get("metadata", {}).get("name", "unknown")
        capacity = round(
            memory_unit_conversion(
                node.get("status", {}).get("capacity", {}).get("memory")
            ),
            2,
        )

        try:
            pod_requests_raw = node.get("metadata", {}).get("annotations", {}).get(
                "management.cattle.io/pod-requests", "{}"
            )
            pod_requests = json.loads(pod_requests_raw)
            requested = round(memory_unit_conversion(pod_requests.get("memory")), 2)
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            log.warning("Node %s: failed to parse pod-requests annotation: %s", node_name, e)
            requested = 0

        try:
            pod_limits_raw = node.get("metadata", {}).get("annotations", {}).get(
                "management.cattle.io/pod-limits", "{}"
            )
            pod_limits = json.loads(pod_limits_raw)
            limit = round(memory_unit_conversion(pod_limits.get("memory")), 2)
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            log.warning("Node %s: failed to parse pod-limits annotation: %s", node_name, e)
            limit = 0

        return capacity, requested, limit

    def write_page(self):
        content = "\n".join(self.content)
        if self.dryrun:
            log.info("Would write page %s", self.pageTitle)
            print(content)
        else:
            try:
                self.redmineClient.write_page(self.pageTitle, content)
                log.info("Wrote page %s", self.pageTitle)
            except Exception:
                log.exception("Failed to write page %s", self.pageTitle)

    def set_content(self):
        raise NotImplementedError
