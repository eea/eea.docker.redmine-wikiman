import glob
import json
import logging
import os
import requests
import shutil
import tarfile
import yaml

from rancher2.auth import RedmineClient

log = logging.getLogger(__name__)

ARCHIVES_DIR = "./archives/"
EXTRACTION_DIR = "./source_files/"


def get_chart_version(chart_name):
    redmineClient = RedmineClient()
    try:
        text = redmineClient.get_page_text(redmineClient.apps_page)
    except Exception:
        log.exception("Failed to get apps page for chart version lookup")
        return ""

    for line in text.splitlines():
        columns = line.split("|")
        if len(columns) > 3 and chart_name == columns[3].strip():
            chart_version = columns[4].strip()
            return chart_version

    return ""


def extract_non_eea_images(subchart):
    # get all versions for non-EEA subchart
    try:
        response = requests.get(f"{subchart['repository']}/index.yaml", timeout=60)
        if response.status_code != 200:
            log.warning("Non-EEA subchart index.yaml not found for %s", subchart.get("repository", "unknown"))
            return []

        charts_dict = yaml.load(response.text, Loader=yaml.FullLoader).get("entries", {})
        chart_data_all_versions = charts_dict.get(subchart["name"], [])
        if not chart_data_all_versions:
            log.warning("No versions found for non-EEA subchart %s", subchart.get("name", "unknown"))
            return []

        # get data for this non-EEA subchart version or get latest
        chart_data = chart_data_all_versions[0]
        for version_data in chart_data_all_versions:
            if version_data.get("version") == subchart.get("version"):
                chart_data = version_data
                break

        app_version = chart_data.get('appVersion', chart_data.get('version', 'version-unknown'))
        image = f"{subchart['name']}:{app_version}"
        return [image]
    except Exception:
        log.exception("Failed to extract non-EEA images for subchart %s", subchart.get("name", "unknown"))
        return []


def extract_images(url, chart_data_all_versions, version=None):
    # check if link is EEA helm chart
    try:
        response = requests.get(url, timeout=60)
    except requests.RequestException:
        log.exception("Network error fetching helm chart %s", url)
        return {}, []

    if response.status_code != 200:
        log.warning("Helm chart not found, skipping %s", url)
        return {}, []

    # get data for this chart version or get latest
    chart_data = chart_data_all_versions[0]
    for version_data in chart_data_all_versions:
        if version_data.get("version") == version:
            chart_data = version_data
            break

    chart_name = chart_data.get("name", "unknown")
    source_files_archive = chart_data.get("urls", [""])[0]
    source_files_archive_path = f"{ARCHIVES_DIR}{source_files_archive}"
    if not os.path.isfile(source_files_archive_path):
        # download source files archive just once
        try:
            response = requests.get(f"https://eea.github.io/helm-charts/{source_files_archive}", timeout=60)
            if response.status_code != 200:
                log.warning("Source files archive not found for chart %s, skipping %s", chart_name, url)
                return {}, []
            with open(source_files_archive_path, "wb") as f:
                f.write(response.content)
        except requests.RequestException:
            log.exception("Network error downloading source files archive for chart %s", chart_name)
            return {}, []

    # extract source files from archive
    try:
        archive_file = tarfile.open(f"{ARCHIVES_DIR}{source_files_archive}")
        archive_file.extractall(EXTRACTION_DIR)

        # get the main docker image under image: repository:
        values_file_path = f"{EXTRACTION_DIR}{chart_name}/values.yaml"
        with open(values_file_path) as values_file:
            values_dict = yaml.load(values_file.read(), Loader=yaml.FullLoader)
            app_version = chart_data.get('appVersion', chart_data.get('version', 'version-unknown'))

            image_cfg = values_dict.get("image")
            if not image_cfg or "repository" not in image_cfg:
                return {}, []

            images = [f"{image_cfg['repository']}:{app_version}"]

        # get all images found in templates/
        templates_dir = f"{EXTRACTION_DIR}{chart_name}/templates/"
        if os.path.isdir(templates_dir):
            for file_path in glob.glob(f"{templates_dir}*.yaml"):
                try:
                    with open(file_path) as f:
                        for line in f.read().splitlines():
                            if " image: " in line and "{{" not in line:
                                images.append(line)
                except (OSError, IOError):
                    log.warning("Failed to read template file %s", file_path)

        return chart_data, images
    except Exception:
        log.exception("Failed to extract images from chart %s", chart_name)
        return {}, []
    finally:
        # delete extracted files
        try:
            archive_file.close()
        except Exception:
            pass
        try:
            if os.path.exists(EXTRACTION_DIR):
                shutil.rmtree(EXTRACTION_DIR)
        except Exception:
            pass


def get_docker_images_rancher2(urls):
    # create dir for archives (maybe create a docker volume for persistent data)
    if not os.path.exists(ARCHIVES_DIR):
        os.makedirs(ARCHIVES_DIR)

    # get all EEA helm chart versions here
    try:
        response = requests.get("https://eea.github.io/helm-charts/index.yaml", timeout=60)
    except requests.RequestException:
        log.exception("Network error fetching EEA helm chart index")
        return {}

    if response.status_code != 200:
        log.error("EEA index.yaml not found (HTTP %d)", response.status_code)
        return {}

    docker_images = {}
    try:
        charts_dict = yaml.load(response.text, Loader=yaml.FullLoader).get("entries", {})
    except yaml.YAMLError:
        log.exception("Failed to parse EEA helm chart index")
        return {}

    for url in urls:
        try:
            chart_name = None
            if len(url.rsplit("/", 1)) > 1:
                chart_name = url.rsplit("/", 1)[1].strip()

            chart_data_all_versions = charts_dict.get(chart_name)
            if not chart_data_all_versions:
                log.warning("Helm chart not found for url %s", url)
                continue

            chart_version = get_chart_version(chart_name)
            chart_data, images = extract_images(url, chart_data_all_versions, chart_version)
            all_images = list(images)

            # get images for subcharts
            for subchart in chart_data.get("dependencies", []):
                subchart_name = subchart.get("name", "unknown")
                subchart_data_all_versions = charts_dict.get(subchart_name)
                if subchart_data_all_versions:
                    subchart_url = f"{url.rsplit('/', 1)[0]}/{subchart_name}"
                    _, images = extract_images(
                        subchart_url, subchart_data_all_versions, subchart.get("version")
                    )
                else:
                    images = extract_non_eea_images(subchart)  # non-EEA subchart
                all_images.extend(images)

            docker_images[url] = {
                "chart_name": chart_name,
                "chart_version": chart_version,
                "latest_version": chart_data_all_versions[0].get("version", "unknown"),
                "images": {},
            }
            for image in all_images:
                name = image.strip().replace("image: ", "")
                docker_image = name.split(":")[0]
                try:
                    response4 = requests.get(
                        "https://hub.docker.com/api/build/v1/source/?image=" + docker_image,
                        timeout=60,
                    )
                except requests.RequestException:
                    log.warning("Network error fetching Docker Hub source for %s", docker_image)
                    response4 = None

                github_url = ""
                dockerhub_url = ""
                if docker_image.find("/") > 0:
                    dockerhub_url = "https://hub.docker.com/r/" + docker_image
                    if response4 and response4.status_code == 200:
                        try:
                            objects = response4.json().get("objects", [])
                            if objects:
                                github_url = (
                                    "https://github.com/"
                                    + objects[0]["owner"]
                                    + "/"
                                    + objects[0]["repository"]
                                )
                        except (KeyError, json.decoder.JSONDecodeError) as e:
                            log.warning("Failed to parse Docker Hub source response for %s: %s", docker_image, e)
                else:
                    dockerhub_url = "https://hub.docker.com/r/_/" + docker_image

                docker_images[url]["images"][name] = [github_url, dockerhub_url]
        except Exception:
            log.exception("Failed to process images for URL %s", url)

    return docker_images


def generate_images_text_rancher2(docker_images, image_checker):
    text = ""
    update_section = False

    for url, data in docker_images.items():
        try:
            _, msg = image_checker.compare_versions(
                data.get("chart_name", "unknown"), [data.get("latest_version", "unknown")], data.get("chart_version", "unknown")
            )
            text += f"h4. Helm chart \"eea/{data['chart_name']}\":{url} | {msg}\n\n"
            images = data.get("images", {})
            for name in sorted(images):
                text += '* *"' + name + '":' + images[name][1] + "*"
                if images[name][0]:
                    text += ' | "Source code":' + images[name][0]
                update_needed, update_msg = image_checker.check_image_and_base_status(name)
                update_section |= update_needed
                text += " | " + update_msg + "\n"

            text = text + "\n"
        except Exception:
            log.exception("Failed to generate images text for chart %s", data.get("chart_name", "unknown"))

    return update_section, text
