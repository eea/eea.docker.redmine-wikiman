import requests
import datetime
import svn.remote
import sys
import os
import logging
from natsort import natsorted
import json
import re
from requests.auth import HTTPBasicAuth

svnuser = os.getenv("SVN_USER", "")
svnpassword = os.getenv("SVN_PASSWORD", "")
github_token = os.getenv("GITHUB_TOKEN", "")


# Github authorization
authorization_header = {}
raw_header = {"Accept": "application/vnd.github.VERSION.raw"}
if github_token:
    logging.info("Received GitHub token value, will be using it to read github repos")
    authorization_header = {"Authorization": "bearer " + github_token}
    raw_header = {
        "Accept": "application/vnd.github.VERSION.raw",
        "Authorization": "bearer " + github_token,
    }


def get_dockerfile(url):
    logging.debug("Deployment url " + url)
    try:
        if "https://github.com/" in url:
            api = url.replace(
                "https://github.com/", "https://api.github.com/repos/"
            ).replace("tree/master", "contents")
            response = requests.get(api, headers=authorization_header)
            if response.status_code != 200:
                logging.debug(response.json())
                logging.warning("There was a problem with the github api response")
                return
            filter_dirs = [x for x in response.json() if x["type"] == "dir"]
            biggest = str(max(filter_dirs, key=lambda x: int(x["name"]))["name"])
            response2 = requests.get(
                api.strip("/") + "/" + biggest, headers=authorization_header
            )
            filter_dc = [
                x
                for x in response2.json()
                if "docker-compose" in str(x["name"]).lower()
            ]
            response3 = requests.get(str(filter_dc[0]["url"]), headers=raw_header)
            return response3.text

        if "https://eeasvn.eea.europa.eu/" in url:
            r = svn.remote.RemoteClient(url, username=svnuser, password=svnpassword)
            r.info()
            for x in r.list():
                if str(x).lower() == "docker-compose.yml":
                    return r.cat(x).decode("utf-8")
            for rel_path, e in r.list_recursive():
                if str(e["name"]).lower() == "docker-compose.yml":
                    return r.cat(rel_path + "/" + e["name"]).decode("utf-8")

    except BaseException:
        logging.warning(
            "There was a problem accessing the DeploymentRepoURL docker-compose.yml"
        )
        logging.error(sys.exc_info())


def get_docker_images(urls):
    docker_images = {}
    for url in urls:
        dockerfile = get_dockerfile(url)

        if not dockerfile:
            logging.warning("No docker-compose.yml file found, skipping the page")
            return {}
            break

        lines = dockerfile.splitlines()
        images = [x for x in lines if " image: " in x]
        for image in images:
            name = image.strip().split(" ")[1].strip('"').strip("'")
            # print name
            if name not in docker_images:
                docker_image = name.split(":")[0]
                response4 = requests.get(
                    "https://hub.docker.com/api/build/v1/source/?image=" + docker_image
                )
                github_url = ""
                dockerhub_url = ""
                if docker_image.find("/") > 0:
                    dockerhub_url = "https://hub.docker.com/r/" + docker_image
                    # print dockerhub_url
                    if response4.status_code == 200 and response4.json().get("objects"):
                        github_url = (
                            "https://github.com/"
                            + response4.json()["objects"][0]["owner"]
                            + "/"
                            + response4.json()["objects"][0]["repository"]
                        )
                        # print github_url
                else:
                    dockerhub_url = "https://hub.docker.com/r/_/" + docker_image
                    # print dockerhub_url
                docker_images[name] = [github_url, dockerhub_url]
        # print docker_images

    return docker_images


def get_dockerhub_login_token():
    auth_url = "https://hub.docker.com/v2/users/login/"
    data = {
        "username": os.getenv("DOCKERHUB_USER", ""),
        "password": os.getenv("DOCKERHUB_PASS", ""),
    }

    r = requests.post(auth_url, headers={"Content-Type": "application/json"}, json=data)
    if not r.status_code == 200:
        logging.info(f"Could not log in to docker hub.")
        return

    try:
        token = r.json()["token"]
    except (json.decoder.JSONDecodeError, KeyError):
        logging.info(f"Could not obtain a log int token")
        return
    return token


def get_image_flavour(image_name):
    if "-" not in image_name:
        return None

    image_pieces = image_name.split("-")
    flavour = ""
    for piece in image_pieces[::-1]:
        if piece.isalpha():
            flavour = piece + "-" + flavour
    return flavour


def filter_potential_image_updates(image_name, versions):
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
    image_flavour = get_image_flavour(image_tag)
    versions = [v for v in versions if get_image_flavour(v) == image_flavour]

    return versions


def get_image_potential_updates(image_name):
    index_url = "https://index.docker.io"
    auth_url = "https://auth.docker.io/token"
    error_color = "%{color:red}"

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

        payload = {"service": "registry.docker.io", "scope": f"repository:{image}:pull"}

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
        return False, f"{image_name}: {error_color}tags list can not be obtain%"

    if not versions:
        logging.info(f"{image_name}: tags list obtain is empty")
        return False, f"{image_name}: {error_color}tags list can not be obtain%"

    potential_updates = filter_potential_image_updates(image_name, versions)
    if not potential_updates:
        return False, f"{image_name}: {error_color}no similar flavour tag obtained%"

    return True, potential_updates


def compare_versions(image, potential_updates, curr_version):
    minor_color = "%{color:orange}"
    major_color = "%{color:purple}"
    uptodate_color = "%{color:green}"

    last_version = natsorted(potential_updates)[-1]

    if last_version == curr_version:
        return True, f"{image}:{curr_version}: {uptodate_color}Up to date%"
    else:
        curr_major = curr_version.split(".")[0]
        last_major = last_version.split(".")[0]

        if curr_major == last_major:
            return (
                False,
                f"{image}:{curr_version}: {minor_color}minor upgrade to {last_version}%",
            )
        else:
            status = (
                f"{image}:{curr_version}: {major_color}major upgrade to {last_version}%"
            )

            same_major_updates = [
                v for v in potential_updates if v.split(".")[0] == curr_major
            ]
            if same_major_updates:
                last_minor_version = natsorted(same_major_updates)[-1]

                if last_minor_version and last_minor_version != curr_version:
                    status += (
                        f"; {minor_color}minor upgrade to {image}:{last_minor_version}%"
                    )
            return False, status


def check_base_image(image, token):
    error_color = "%{color:red}"

    if not token:
        return

    if "/" not in image or not image.startswith("eeacms/"):
        # Already a base image or not owned
        return

    if ":" not in image or image.split(":")[1] == "latest":
        return

    image_name, version = image.split(":")
    repo, image_name = image_name.split("/")

    # Fetch build list
    build_list_url = f"https://hub.docker.com/api/audit/v1/action/?include_related=true&limit=500&object=%2Fapi%2Frepo%2Fv1%2Frepository%2F{repo}%2F{image_name}%2F"
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(build_list_url, headers=h)
    if r.status_code == 401:
        logging.error(f"{image}: access denied when looking for base image")
        return (
            False,
            f"{image}: {error_color}access denied when looking for base image%",
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
        logging.info([item["build_tag"] for item in history])
        return (
            False,
            f"{image}: {error_color}could not find tag {version} in build history.%",
        )

    # Get build details
    r = requests.get(f"https://hub.docker.com{version_uri}", headers=h)
    try:
        dockerfile = r.json()["dockerfile"]
    except (json.decoder.JSONDecodeError, KeyError):
        logging.info(f"{image}: dockerfile is not available in build history")
        return False, f"{image}: {error_color}dockerfile is not available in build history%"

    if "FROM" not in dockerfile:
        logging.info(f"{image}: can't find FROM statement in dockerfile")
        return False, f"{image}: {error_color}can't find FROM statement in dockerfile%"

    base_images = set()
    for line in dockerfile.splitlines():
        if line.startswith("FROM"):
            image = line.replace("FROM", "").strip().split()[0]
            base_images.add(image)

    status, msg = False, ""
    for base_image in base_images:
        if ":" not in base_image or base_image.split(":")[1] == "latest":
            msg += f"{base_image}: {error_color}can not check for updates to 'latest' tag% "
        elif "/" in base_image:
            recursive_call = check_base_image(base_image, token)
            if recursive_call:
                recursive_status, recursive_msg = recursive_call
                status |= recursive_status
                msg += recursive_msg + " "
        else:
            success, versions = get_image_potential_updates(base_image)
            if not success:
                msg += versions
            else:
                base_image, curr_version = base_image.split(":")
                update_status, update_msg = compare_versions(
                    base_image, versions, curr_version
                )
                status |= update_status
                msg += update_msg + " "
    return status, msg


"""
    Returns a boolean (true = up to date, false = otherwise) and a message.
"""


def check_image_status(image_name, token):
    error_color = "%{color:red}"
    if image_name.startswith("docker.io/"):
        image_name = image_name.replace("docker.io/", "")

    if ":" not in image_name or image_name.split(":")[1] == "latest":
        # "latest" tag or no tag means no upgrade available
        status = False
        msg = f"{image_name}: {error_color}can not check for updates to 'latest' tag% "
    else:
        success, versions = get_image_potential_updates(image_name)
        if not success:
            status = False
            msg = versions
        else:
            image, curr_version = image_name.split(":")
            status, msg = compare_versions(image, versions, curr_version)

    if "/" in image_name:
        # Image is not a base image already
        base_status = check_base_image(image_name, token)
        if base_status:
            base_update_status, base_update_msg = base_status
            msg += "\n" + base_update_msg
            status |= base_update_status
    return status, msg


def generate_images_text(docker_images):
    login_token = get_dockerhub_login_token()
    update_section = False

    text = ""
    for name in sorted(docker_images):
        text = text + '* *"' + name + '":' + docker_images[name][1] + "*"
        if docker_images[name][0]:
            text = text + ' | "Source code":' + docker_images[name][0]
        update_needed, update_msg = check_image_status(name, login_token)
        update_section |= update_needed
        text = text + " | " + update_msg + "\n"

    text = text + "\n"
    return update_section, text
