import os
import time

from dotenv import load_dotenv
from kubernetes import client, config
from redminelib import Redmine
from redminelib.exceptions import ResourceNotFoundError

load_dotenv()


class RancherClient:
    def __init__(self):
        self.base_url = os.getenv("RANCHER2_SERVER_URL", "")
        self.cluster_id = os.getenv("RANCHER2_CLUSTER_ID", "")
        self.cluster_name = os.getenv("RANCHER2_CLUSTER_NAME", "")
        token = os.getenv("RANCHER2_TOKEN", "")

        if token:
            configuration = client.Configuration()
            configuration.api_key["authorization"] = token
            configuration.api_key_prefix["authorization"] = "Bearer"
            configuration.verify_ssl = os.getenv("RANCHER2_VERIFY_SSL", "true").lower() == "true"
            configuration.host = self.base_url
            api_client = client.ApiClient(configuration)
            self.v1 = client.CoreV1Api(api_client)
        else:
            config.load_incluster_config()
            self.v1 = client.CoreV1Api()


class RedmineClient:
    def __init__(self):
        self.base_url = os.getenv("WIKI_SERVER", "")
        self.apikey = os.getenv("WIKI_APIKEY", "")
        self.project_id = os.getenv("WIKI_PROJECT", "")
        self.redmine_server = Redmine(
            self.base_url, key=self.apikey, requests={"verify": True}
        )

        self.apps_page = os.getenv("WIKI_APPS_PAGE", "")
        self.nodes_page = os.getenv("WIKI_NODES_PAGE", "")
        self.pods_page = os.getenv("WIKI_PODS_PAGE", "")
        self.cluster_name = os.getenv("RANCHER2_CLUSTER_NAME", "")

        self.marker = "_Do not update this page manually._"

    def has_changed(self, page_name, new_content):
        """
        Check if the content of the page has changed
        :param page_name: The name of the page
        :param new_content: text to compare

        :return: True if the content has changed, False otherwise
        """
        page = self.redmine_server.wiki_page.get(page_name, project_id=self.project_id)
        old_content = page.text
        return old_content.split(self.marker)[1] != new_content.split(self.marker)[1]

    def write_page(self, page_name, content):
        """
        Write a page to the wiki server
        :param page_name: The name of the page
        :param content: The content of the page
        """

        self.redmine_server.wiki_page.update(
            page_name,
            project_id=self.project_id,
            text=content,
        )

    def get_page_text(self, page_name):
        try:
            text = self.redmine_server.wiki_page.get(
                page_name, project_id=self.project_id
            ).text
        except ResourceNotFoundError:
            print(f"Page {page_name} does not exist")
            return ""

        today = time.strftime("%d %B %Y")
        if today not in text:
            print(f"Page {page_name} was not updated")
            return ""

        return text.split(self.marker)[1]
