import glob
import os
import requests
import shutil
import tarfile
import yaml

from rancher2.app_finder import Rancher2AppFinder


ARCHIVES_DIR = "./archives/"
EXTRACTION_DIR = "./source_files/"


def extract_non_eea_images(subchart):
    # get all versions for non-EEA subchart
    response = requests.get(f"{subchart['repository']}/index.yaml")
    if response.status_code != 200:
        print(f"Non-EEA subchart index.yaml not found for {subchart['repository']}")
        return []

    charts_dict = yaml.load(response.text, Loader=yaml.FullLoader)["entries"]
    chart_data_all_versions = charts_dict[subchart["name"]]

    # get data for this non-EEA subchart version or get latest
    chart_data = chart_data_all_versions[0]
    for version_data in chart_data_all_versions:
        if version_data["version"] == subchart["version"]:
            chart_data = version_data
            break

    image = f"{subchart['name']}:{chart_data['appVersion']}"
    return [image]


def extract_images(url, chart_data_all_versions, version=None):
    # check if link is EEA helm chart
    response = requests.get(url, timeout=60)
    if response.status_code != 200:
        print(f"Helm chart not found, skipping {url}")
        return []

    # get data for this chart version or get latest
    chart_data = chart_data_all_versions[0]
    for version_data in chart_data_all_versions:
        if version_data["version"] == version:
            chart_data = version_data
            break

    chart_name = chart_data["name"]
    source_files_archive = chart_data["urls"][0]
    source_files_archive_path = f"{ARCHIVES_DIR}{source_files_archive}"
    if not os.path.isfile(source_files_archive_path):
        # download source files archive just once
        response = requests.get(f"https://eea.github.io/helm-charts/{source_files_archive}")
        if response.status_code != 200:
            print(f"Source files archive not found, skipping {url}")
            return []

        with open(source_files_archive_path, "wb") as f:
            f.write(response.content)

    # extract source files from archive
    archive_file = tarfile.open(f"{ARCHIVES_DIR}{source_files_archive}")
    archive_file.extractall(EXTRACTION_DIR)

    # get the main docker image under image: repository:
    values_file_path = f"{EXTRACTION_DIR}{chart_name}/values.yaml"
    with open(values_file_path) as values_file:
        values_dict = yaml.load(values_file.read(), Loader=yaml.FullLoader)
        images = [f"{values_dict['image']['repository']}:{chart_data['appVersion']}"]

    # get all images found in templates/
    templates_dir = f"{EXTRACTION_DIR}{chart_name}/templates/"
    for file_path in glob.glob(f"{templates_dir}*.yaml"):
        with open(file_path) as f:
            for line in f.read().splitlines():
                if " image: " in line and "{{" not in line:
                    images.append(line)

    # delete extracted files
    archive_file.close()
    shutil.rmtree(EXTRACTION_DIR)
    return chart_data, images


def get_docker_images_rancher2(urls):
    # get all EEA helm chart versions here
    response = requests.get("https://eea.github.io/helm-charts/index.yaml")
    if response.status_code != 200:
        print("EEA index.yaml not found")
        return {}

    docker_images = {}
    charts_dict = yaml.load(response.text, Loader=yaml.FullLoader)["entries"]
    app_finder = Rancher2AppFinder()
    for url in urls:
        # get images for main chart
        all_images = []
        chart_name = url.rsplit("/", 1)[1]
        apps = app_finder.find(chart_name=chart_name)
        chart_version = apps[0]["spec"]["chart"]["metadata"]["version"] if apps else None
        chart_data_all_versions = charts_dict[chart_name]
        chart_data, images = extract_images(url, chart_data_all_versions, chart_version)
        all_images.extend(images)

        # get images for subcharts
        for subchart in chart_data.get("dependencies", []):
            subchart_data_all_versions = charts_dict.get(subchart["name"])
            if subchart_data_all_versions:
                subchart_url = f"{url.rsplit('/', 1)[0]}/{subchart['name']}"
                _, images = extract_images(
                    subchart_url, subchart_data_all_versions, subchart["version"]
                )
            else:
                images = extract_non_eea_images(subchart)  # non-EEA subchart
            all_images.extend(images)

        docker_images[url] = {
            "chart_name": chart_name,
            "chart_version": chart_version,
            "latest_version": chart_data_all_versions[0]["version"],
            "images": {},
        }
        for image in all_images:
            name = image.strip().replace("image: ", "")
            docker_image = name.split(":")[0]
            response4 = requests.get(
                "https://hub.docker.com/api/build/v1/source/?image=" + docker_image,
                timeout=60,
            )
            github_url = ""
            dockerhub_url = ""
            if docker_image.find("/") > 0:
                dockerhub_url = "https://hub.docker.com/r/" + docker_image
                if response4.status_code == 200 and response4.json().get("objects"):
                    github_url = (
                        "https://github.com/"
                        + response4.json()["objects"][0]["owner"]
                        + "/"
                        + response4.json()["objects"][0]["repository"]
                    )
            else:
                dockerhub_url = "https://hub.docker.com/r/_/" + docker_image

            docker_images[url]["images"][name] = [github_url, dockerhub_url]

    return docker_images


def generate_images_text_rancher2(docker_images, image_checker):
    text = ""
    update_section = False

    for url, data in docker_images.items():
        _, msg = image_checker.compare_versions(
            data["chart_name"], [data["latest_version"]], data["chart_version"]
        )
        text += f"h4. Helm chart \"eea/{data['chart_name']}\":{url} | {msg}\n\n"
        images = data["images"]
        for name in sorted(images):
            text += '* *"' + name + '":' + images[name][1] + "*"
            if images[name][0]:
                text += ' | "Source code":' + images[name][0]
            update_needed, update_msg = image_checker.check_image_and_base_status(name)
            update_section |= update_needed
            text += " | " + update_msg + "\n"

        text = text + "\n"

    return update_section, text
