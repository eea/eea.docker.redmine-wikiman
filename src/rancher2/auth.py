import requests

from dotenv import load_dotenv
import os

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
