import os
import re
import json
import logging
import requests
from natsort import natsorted

class ImageChecker:
    def __init__(self):
        self.dockerhub_token = self.get_dockerhub_login_token()
        self.images_cache = {}
        self.images_base_cache = {}
        
        self.redmine_error_color = "%{color:red}"
        self.redmine_minor_color = "%{color:orange}"
        self.redmine_major_color = "%{color:purple}"
        self.redmine_ok_color = "%{color:green}"
        self.redmine_info_color = "%{color:black}"

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

    def non_semantic_version(self, version):
        """
            We are considering a semantic version any concatenation of no more
            than 3 1,2,3-digit numbers separated by a '.' , linked by '-' to
            none ore many alphanumeric or other 1,2,3-digit numbers '.' separated.

            - we don not accept alpha/beta images
            - we force the version to start with digit/"v" e.g: redis:rc-alpine3.11 is not valid
            - we force the version to contain a numerical orderd string e.g.: redis:32bit-stretch is not valid
            - we allow 'v' starting versions e.g: eeacms/esbootstrap:v3.0.4
        """
        digit_pattern = re.compile("^v?\d{1,3}$")
        word_pattern = re.compile("^[a-zA-Z0-9]+$")
        develop_tags = ["beta", "alpha", "rc", "RC"]

        version_pieces = version.split('-')
        actual_version = version_pieces[0]
        flavour = version_pieces[1:] if len(version_pieces) > 1 else []

        for element in actual_version.split('.'):
            if not re.match(digit_pattern, element):
                return True

        for piece in flavour:
            for element in piece.split('.'):
                if not re.match(digit_pattern, element):
                    if not re.match(word_pattern, element) or element in develop_tags:
                        return True

        return False


    def check_non_semantic_version(self, image, version):
        """
            Non semantic images will be checked by a separate logic.
            We will get the full details on every tag that was created at some point
            and return the most recent one.
        """

        tags_details_url = f"https://hub.docker.com/v2/repositories/{image}/tags/"
        h = {"Authorization": f"Bearer {self.dockerhub_token}"}
        r = requests.get(tags_details_url, headers=h)
        if r.status_code == 401:
            logging.error(f"{image}: access denied when looking for hub.docker tags creation details")
            return (
                False,
                f"{image}: {self.redmine_error_color}access denied when looking for hub.docker tags creation details%",
            )

        try:
            tags_details = r.json()["results"]
        except (json.decoder.JSONDecodeError, KeyError):
            logging.info(f"{image}: could not fetch tags creation details.")
            return False, f"{image}: could not fetch tags creation details."

        # Sort tags by their update time and keep the relevant names
        tags = [(tag['name'], tag['last_updated']) for tag in tags_details]
        tags = sorted(tags, key=lambda x: x[0], reverse=True)
        tags = [tag for tag in tags if (tag[0] != 'latest' and tag[0] != 'master' and 'dev' not in tag[0]
                                        and 'alpha' not in tag[0] and 'beta' not in tag[0])]

        if not tags:
            logging.error(f"{image}: no non develop tags obtained")
            return (
                False,
                f"{image}: {self.redmine_error_color}no non develop tags obtained%",
            )

        if tags[0][0] != version:
            return (
                False,
                f"{image}:{version}: {self.redmine_major_color}non semantic version - upgrade to {tags[0][0]}%"
            )
        return (
            True,
            f"{image}:{version}: {self.redmine_ok_color}Up to date%"
        )

    def filter_potential_image_updates(self, image_name, versions):
        versions = [v for v in versions if not self.non_semantic_version(v)]

        image_tag = image_name.split(":")[1]
        image_flavour = self.get_image_flavour(image_tag)
        versions = [v for v in versions if self.get_image_flavour(v) == image_flavour]

        return versions

    def get_image_versions(self, image_name):
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
        if r.status_code == 401:
            logging.error(f"{image}: access denied when looking for tags list")
            return (
                False,
                f"{image}: {self.redmine_error_color}access denied when looking for tags list%",
            )
        try:
            versions = r.json()["tags"]
        except (json.decoder.JSONDecodeError, KeyError):
            logging.info(f"{image_name}: tags list obtain is empty")
            return (
                False,
                f"{image_name}: {self.redmine_error_color}tags list can not be obtain%",
            )

        if not versions:
            logging.info(f"{image_name}: tags list obtained is empty")
            return (
                False,
                f"{image_name}: {self.redmine_error_color}tags list obtained is empty%",
            )

        return True, versions


    def get_potential_updates(self, image_name, versions):
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

        logging.debug(f"processing image {image_name}")
        if image_name in self.images_cache:
            status, msg = self.images_cache[image_name]
        else:
            if ":" not in image_name or image_name.split(":")[1] == "latest":
                status = False
                msg = f"{image_name}: {self.redmine_error_color}'latest' tag is not upgradeable%"
            else:
                image, curr_version = image_name.split(":", 1)
                if self.non_semantic_version(curr_version):
                    logging.info(f"{image_name}: non semantic version tag")
                    status, msg = self.check_non_semantic_version(image, curr_version)
                else:
                    success, versions = self.get_image_versions(image)
                    if not success:
                        status = False
                        msg = versions
                    else:
                        success, potential_updates = self.get_potential_updates(image_name, versions)
                        if not success:
                            status = False
                            msg = potential_updates
                        else:
                            status, msg = self.compare_versions(image, potential_updates, curr_version)
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

    
        if image in self.images_base_cache:
            base_status, base_msg = self.images_base_cache[image]        
            return base_status, base_msg

        full_image_name, version = image, 'latest'
        if ":" in image:
            full_image_name, version = image.split(":")

        repo, image_name = full_image_name.split("/")

        
        # Fetch build list
        build_list_url = f"https://hub.docker.com/api/audit/v1/action/?include_related=true&limit=500&object=%2Fapi%2Frepo%2Fv1%2Frepository%2F{repo}%2F{image_name}%2F"
        h = {"Authorization": f"Bearer {self.dockerhub_token}"}
        try:
            r = requests.get(build_list_url, headers=h)
        except (ConnectionError):
            logging.error(f"{image}: connection error when looking for base image")
            return (
                False,
                f"{image}: {self.redmine_error_color}connection error when looking for base image%",
            )
            

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
            if version == 'latest':
                return

            success, versions = self.get_image_versions(full_image_name)
            if not success:
                logging.info(f"{versions}")
                return (
                    False,
                    f"{versions}"
                )

            if version in versions:
                logging.info(f"{image}: tag {version} was built externally, no Dockerfile available.")
                return (
                    False,
                    f"{image}: {self.redmine_info_color} was built externally, no Dockerfile available.%",
                )
            else:
                logging.info(f"{image}: could not find tag {version} in build history.")
                return (
                    False,
                    f"{image}: {self.redmine_error_color}could not find tag {version} in build history.%",
                )

        # Get build details
        r = requests.get(f"https://hub.docker.com{version_uri}", headers=h)
        if r.status_code == 401:
            logging.error(f"{image}: access denied when looking for base image build details")
            return (
                False,
                f"{image}: {self.redmine_error_color}access denied when looking for base image build details%",
            )
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
        
        self.images_base_cache[image] = (base_status, base_msg)    
        
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
