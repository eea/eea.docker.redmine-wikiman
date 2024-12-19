import requests

from dotenv import load_dotenv
import os

from redminelib import Redmine

load_dotenv()


class RancherClient:
    def __init__(self):
        self.base_url = os.getenv("RANCHER2_BASE_URL", "")
        self.verify = os.getenv("RANCHER2_VERIFY_SSL", "true").lower() == "true"
        self.session = requests.Session()
        self.token = os.getenv("RANCHER2_TOKEN", "")
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})

    def get(self, url, params=None):
        response = self.session.get(
            self.base_url + url, params=params, verify=self.verify
        )
        response.raise_for_status()
        return response.json()


class RedmineClient:
    def __init__(self):
        self.base_url = os.getenv("WIKI_SERVER", "")
        self.apikey = os.getenv("WIKI_APIKEY", "")
        self.project_id = os.getenv("WIKI_PROJECT", "")
        self.redmine_server = Redmine(
            self.base_url, key=self.apikey, requests={"verify": True}
        )

    def has_changed(self, page_name, new_content):
        """
        Check if the content of the page has changed
        :param page_name: The name of the page
        :param new_content: text to compare

        :return: True if the content has changed, False otherwise
        """
        server = self._redmine()
        page = server.wiki_page.get(page_name, project_id=self.project_id)
        old_content = page.text
        marker = "_Do not update this page manually._"
        return old_content.split(marker)[1] != new_content.split(marker)[1]

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
