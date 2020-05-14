import requests
import os
import logging
from natsort import natsorted
import json
import re


class ImageChecker:
    def __init__(self):
        self.dockerhub_token = self.get_dockerhub_login_token()
        self.images_cache = {}

        self.redmine_error_color = "%{color:red}"
        self.redmine_minor_color = "%{color:orange}"
        self.redmine_major_color = "%{color:purple}"
        self.redmine_ok_color = "%{color:green}"

    def get_dockerhub_login_token(self):
        auth_url = "https://hub.docker.com/v2/users/login/"
        data = {
            "username": os.getenv("DOCKERHUB_USER", ""),
            "password": os.getenv("DOCKERHUB_PASS", ""),
        }

        r = requests.post(
            auth_url, headers={"Content-Type": "application/json"}, json=data
        )
        if not r.status_code == 200:
            logging.info(f"Could not log in to docker hub.")
            return

        try:
            token = r.json()["token"]
        except (json.decoder.JSONDecodeError, KeyError):
            logging.error(f"Could not obtain a log int token")
            return
        return token

    def get_image_flavour(self, image_name):
        if "-" not in image_name:
            return None

        flavour = ""
        image_pieces = image_name.split("-")
        for piece in image_pieces[::-1]:
            if piece.isalpha():
                flavour = piece + "-" + flavour
        return flavour

    def filter_potential_image_updates(self, image_name, versions):
        """
            Filter the versions to contain at least a digit and be a stable version.
            - we don not accept alpha/beta images
            - we force the version to start with digit/"v" e.g: redis:rc-alpine3.11 is not valid
            - we force the version to contain a '.' e.g.: redis:32bit-stretch is not valid
            - we allow 'v' starting versions e.g: eeacms/esbootstrap:v3.0.4
        """

        versions = [v for v in versions if any(char.isdigit() for char in v)]
        versions = [
            v
            for v in versions
            if "beta" not in v and "alpha" not in v and "RC" not in v and "." in v
        ]
        if "v" in image_name:
            versions = [v for v in versions if re.match(r"^v?[\d.]*", v).group(0)]
        else:
            versions = [v for v in versions if re.match(r"^[\d.]*", v).group(0)]

        image_tag = image_name.split(":")[1]
        image_flavour = self.get_image_flavour(image_tag)
        versions = [v for v in versions if self.get_image_flavour(v) == image_flavour]

        return versions

    def get_image_potential_updates(self, image_name):
        index_url = "https://index.docker.io"
        auth_url = "https://auth.docker.io/token"

        if image_name.startswith("docker.elastic.co/"):
            index_url = "https://docker.elastic.co"
            auth_url = "https://docker-auth.elastic.co/auth"
            image = image_name.replace("docker.elastic.co/", "")

            payload = {
                "service": "token-service",
                "scope": f"repository:{image}:pull",
            }
        else:
            image = image_name
            if ":" in image_name:
                # Check if image was provided with version
                image = image_name.split(":")[0]

            if "/" not in image:
                # Check for docker base images
                image = f"library/{image}"

            payload = {
                "service": "registry.docker.io",
                "scope": f"repository:{image}:pull",
            }

        r = requests.get(auth_url, params=payload)
        if not r.status_code == 200:
            logging.info(f"could not fetch docker hub token for {image_name}.")
            return False, f"{image_name}: could not fetch docker hub token"

        token = r.json()["token"]

        # Fetch versions
        h = {"Authorization": f"Bearer {token}"}
        r = requests.get(f"{index_url}/v2/{image}/tags/list", headers=h)
        try:
            versions = r.json()["tags"]
        except (json.decoder.JSONDecodeError, KeyError):
            logging.info(f"{image_name}: tags list obtain is empty")
            return (
                False,
                f"{image_name}: {self.redmine_error_color}tags list can not be obtain%",
            )

        if not versions:
            logging.info(f"{image_name}: tags list obtain is empty")
            return (
                False,
                f"{image_name}: {self.redmine_error_color}tags list can not be obtain%",
            )

        potential_updates = self.filter_potential_image_updates(image_name, versions)
        if not potential_updates:
            return (
                False,
                f"{image_name}: {self.redmine_error_color}no similar flavour tag obtained%",
            )

        return True, potential_updates

    def compare_versions(self, image, potential_updates, curr_version):
        last_version = natsorted(potential_updates)[-1]

        if last_version == curr_version:
            return True, f"{image}:{curr_version}: {self.redmine_ok_color}Up to date%"
        else:
            curr_major = curr_version.split(".")[0]
            last_major = last_version.split(".")[0]

            if curr_major == last_major:
                return (
                    False,
                    f"{image}:{curr_version}: {self.redmine_minor_color}minor upgrade to {last_version}%",
                )
            else:
                status = f"{image}:{curr_version}: {self.redmine_major_color}major upgrade to {last_version}%"

                same_major_updates = [
                    v for v in potential_updates if v.split(".")[0] == curr_major
                ]
                if same_major_updates:
                    last_minor_version = natsorted(same_major_updates)[-1]

                    if last_minor_version and last_minor_version != curr_version:
                        status += f"; {self.redmine_minor_color}minor upgrade to {image}:{last_minor_version}%"
                return False, status

    """
        Returns a boolean (true = up to date, false = otherwise) and a message.
    """

    def check_image_status(self, image_name):
        if image_name.startswith("docker.io/"):
            image_name = image_name.replace("docker.io/", "")

        if image_name in self.images_cache:
            status, msg = self.images_cache[image_name]
        else:
            if ":" not in image_name or image_name.split(":")[1] == "latest":
                status = False
                msg = f"{image_name}: {self.redmine_error_color}can not check for updates to 'latest' tag%"
            else:
                success, versions = self.get_image_potential_updates(image_name)
                if not success:
                    status = False
                    msg = versions
                else:
                    image, curr_version = image_name.split(":")
                    status, msg = self.compare_versions(image, versions, curr_version)
            self.images_cache[image_name] = (status, msg)

        return status, msg

    def check_base_image(self, image):
        if not self.dockerhub_token:
            return

        if image.startswith("docker.io/"):
            image = image.replace("docker.io/", "")

        if not image.startswith("eeacms/"):
            # Already a base image or not owned
            return

        if ":" not in image or image.split(":")[1] == "latest":
            return

        image_name, version = image.split(":")
        repo, image_name = image_name.split("/")

        # Fetch build list
        build_list_url = f"https://hub.docker.com/api/audit/v1/action/?include_related=true&limit=500&object=%2Fapi%2Frepo%2Fv1%2Frepository%2F{repo}%2F{image_name}%2F"
        h = {"Authorization": f"Bearer {self.dockerhub_token}"}
        r = requests.get(build_list_url, headers=h)
        if r.status_code == 401:
            logging.error(f"{image}: access denied when looking for base image")
            return (
                False,
                f"{image}: {self.redmine_error_color}access denied when looking for base image%",
            )

        try:
            history = r.json()["objects"]
        except (json.decoder.JSONDecodeError, KeyError):
            logging.info(f"{image}: could not fetch build history.")
            return False, f"{image}: could not fetch build history."

        version_uri = None
        for item in history:
            if item["build_tag"] == version:
                version_uri = item["resource_uri"]

        if not version_uri:
            logging.info(f"{image}: could not find tag {version} in build history.")
            return (
                False,
                f"{image}: {self.redmine_error_color}could not find tag {version} in build history.%",
            )

        # Get build details
        r = requests.get(f"https://hub.docker.com{version_uri}", headers=h)
        try:
            dockerfile = r.json()["dockerfile"]
        except (json.decoder.JSONDecodeError, KeyError):
            logging.info(f"{image}: dockerfile is not available in build history")
            return (
                False,
                f"{image}: {self.redmine_error_color}dockerfile is not available in build history%",
            )

        if "FROM" not in dockerfile:
            logging.info(f"{image}: can't find FROM statement in dockerfile")
            return (
                False,
                f"{image}: {self.redmine_error_color}can't find FROM statement in dockerfile%",
            )

        base_images = set()
        for line in dockerfile.splitlines():
            if line.startswith("FROM"):
                base_image = line.replace("FROM", "").strip().split()[0]
                base_images.add(base_image)

        base_status, base_msg = False, ""
        for base_image in base_images:
            status, msg = False, ""
            if not image.startswith('eeacms/'):
                recursive_resp = self.check_base_image(base_image)
                if recursive_resp:
                    status, msg = recursive_resp
            else:
                status, msg = self.check_image_status(base_image)

            base_status |= status
            base_msg += f"{msg} "
        return base_status, base_msg

    def check_image_and_base_status(self, image_name):
        image_status, image_msg = self.check_image_status(image_name)

        if "/" in image_name:
            # Image is not a base image already
            base_resp = self.check_base_image(image_name)
            if base_resp:
                base_status, base_msg = base_resp
                image_status |= base_status
                image_msg += f"\n{base_msg}"

        return image_status, image_msg
