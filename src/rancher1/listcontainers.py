import json
import urllib.request
import urllib.parse
import urllib.error
import urllib.request
import urllib.error
import urllib.parse
import os
import sys
import logging
import time
import getopt
from operator import itemgetter
from urllib.parse import urlparse

from redminelib import Redmine

from image_checker import ImageChecker


def getKey(instance):
    """Return the key to sort on"""
    return instance["name"]


class Discover(object):

    def __init__(self, image_checker):
        self.num_containers = 0
        self.containers = {}
        self.hosts = {}
        self.hosts_size = {}
        self.projectName = os.getenv("WIKI_PROJECT", "")
        self.pageName = os.getenv("WIKI_CONTAINERS_PAGE", "")
        self.image_checker = image_checker

    def _redmine(self):
        server = os.getenv("WIKI_SERVER", "")
        apikey = os.getenv("WIKI_APIKEY", "")
        return Redmine(server, key=apikey, requests={"verify": True})

    def write_page(self, content):
        server = self._redmine()
        server.wiki_page.update(
            self.pageName,
            project_id=self.projectName,
            text=content,
        )

    def write_stdout(self, content):
        # pass
        print(content)

    def has_changed(self, new):
        server = self._redmine()
        page = server.wiki_page.get(self.pageName, project_id=self.projectName)
        old = page.text
        marker = "_Do not update this page manually._"
        return old.split(marker)[1] != new.split(marker)[1]

    def get_operation(self, rancherUrl, rancherAccessKey, rancherSecretKey, url):
        realm = "Enter API access key and secret key as username and password"

        auth_handler = urllib.request.HTTPBasicAuthHandler()
        auth_handler.add_password(
            realm=realm, uri=rancherUrl, user=rancherAccessKey, passwd=rancherSecretKey
        )
        opener = urllib.request.build_opener(auth_handler)
        urllib.request.install_opener(opener)
        f = urllib.request.urlopen(url)
        rawdata = f.read()
        f.close()
        return json.loads(rawdata)

    def load_containers(self, rancherUrl, rancherAccessKey, rancherSecretKey, url):
        self.containers = {}
        self.num_containers = 0
        structdata = self.get_operation(
            rancherUrl,
            rancherAccessKey,
            rancherSecretKey,
            url + "/containers?limit=1000",
        )
        for instance in structdata["data"]:
            imageUuid = instance["imageUuid"]
            if imageUuid.startswith("docker:rancher/") and not imageUuid.startswith(
                "docker:rancher/lb-service-haproxy"
            ):
                continue
            imageUuid = imageUuid[7:]
            hostId = instance["hostId"]
            if instance["name"] is None:
                instance["name"] = "-"
            if hostId is None:
                logging.info(
                    "Container "
                    + instance["name"]
                    + " does not have host, will skip it"
                )
                continue
            containerStruct = self.containers.setdefault(hostId, [])
            containerLink = (
                url.replace("v2-beta/projects", "env")
                + "/infra/containers/"
                + instance["id"]
            )
            instance["containerLink"] = containerLink
            containerStruct.append(instance)
            self.num_containers = self.num_containers + 1
            # print instance

    def load_hosts(self, rancherUrl, rancherAccessKey, rancherSecretKey, url):
        self.hosts = {}
        self.host_size = {}
        structdata = self.get_operation(
            rancherUrl, rancherAccessKey, rancherSecretKey, url + "/hosts"
        )

        for instance in structdata["data"]:
            self.hosts[instance["id"]] = instance["hostname"]
            self.hosts_size[instance["id"]] = instance["info"]["memoryInfo"]["memTotal"]

    def buildgraph(self, content):

        envReserved = 0
        envLimit = 0
        envTotal = 0
        envText = []
        for hostId, containers in sorted(self.containers.items()):
            host = self.hosts[hostId]
            totalReserved = 0
            totalLimit = 0
            total = self.hosts_size[hostId]
            envText.append("h4. {}\n".format(host))
            envText.append(
                "|_. Image |_. Container |_. Stack |_. State |_. Reservation |_. Limit |_. Upgrade |"
            )
            for container in sorted(containers, key=itemgetter("name")):
                imageName = container["imageUuid"]
                # if container['imageUuid'].startswith("docker:rancher/"): continue
                contName = container["name"]
                try:
                    stackName = container["labels"]["io.rancher.stack.name"]
                except KeyError:
                    stackName = ""
                memoryRes = container.get("memoryReservation", 0)
                if memoryRes is None:
                    memoryRes = 0
                memoryRes = memoryRes / 1048576
                totalReserved = totalReserved + memoryRes

                memoryLim = container.get("memory", 0)
                if memoryLim is None:
                    memoryLim = 0
                memoryLim = memoryLim / 1048576
                totalLimit = totalLimit + memoryLim

                host = self.hosts[container["hostId"]]

                update_status, update_msg = (
                    self.image_checker.check_image_and_base_status(imageName[7:])
                )

                envText.append(
                    '| {} | "{}":{} | {} | {} |>. {} |>. {} |>. {} |'.format(
                        imageName,
                        contName,
                        container["containerLink"],
                        stackName,
                        container["state"],
                        memoryRes,
                        memoryLim,
                        update_msg,
                    )
                )
            envText.append("\nTotal    RAM on host: {:.1f} GiB".format(total / 1024))
            envText.append(
                "\nReserved RAM on host: {:.1f} GiB, {:.1f}% used or {:.1f} GiB available".format(
                    totalReserved / 1024,
                    totalReserved * 100 / total,
                    (total - totalReserved) / 1024,
                )
            )
            envText.append(
                "\nLimit    RAM on host: {:.1f} GiB, {:.1f}% used or {:.1f} GiB available".format(
                    totalLimit / 1024,
                    totalLimit * 100 / total,
                    (total - totalLimit) / 1024,
                )
            )
            envText.append("\n")
            envReserved = envReserved + totalReserved
            envLimit = envLimit + totalLimit
            envTotal = envTotal + total
        content.append(
            "\nTotal    RAM in environment: {:.1f} GiB".format(envTotal / 1024)
        )
        content.append(
            "\nReserved RAM in environment: {:.1f} GiB, {:.1f}% used or {:.1f} GiB available".format(
                envReserved / 1024,
                envReserved * 100 / envTotal,
                (envTotal - envReserved) / 1024,
            )
        )
        content.append(
            "\nLimit    RAM in environment: {:.1f} GiB, {:.1f}% used or {:.1f} GiB available".format(
                envLimit / 1024, envLimit * 100 / envTotal, (envTotal - envLimit) / 1024
            )
        )
        content.append("\n")
        content.extend(envText)


def main(image_checker, dry_run):

    logging.info("List containers script started")

    pageTitle = os.getenv("WIKI_CONTAINERSPAGETITLE", "Rancher Containers")

    content = []
    content.append("{{>toc}}\n\n")
    content.append("h1. " + pageTitle + "\n\n")
    content.append(
        "Automatically discovered on "
        + time.strftime("%d %B %Y")
        + ". _Do not update this page manually._"
    )

    disc = Discover(image_checker)
    rancher_configs = os.getenv("RANCHER_CONFIG")

    for rancher_config in rancher_configs.split():

        rancher_configuration = rancher_config.split(",")

        rancherUrl = rancher_configuration[0]
        logging.info(rancherUrl)
        rancherApiUrl = rancherUrl + "/v2-beta"
        rancherAccessKey = rancher_configuration[1]
        rancherSecretKey = rancher_configuration[2]

        content.append("\nh2. {}\n".format(urlparse(rancherUrl).netloc.upper()))

        try:
            envstruct = disc.get_operation(
                rancherApiUrl,
                rancherAccessKey,
                rancherSecretKey,
                rancherApiUrl + "/projects",
            )
        except BaseException:
            raise RuntimeError("There was a problem reading from Rancher")

        for project in sorted(envstruct["data"], key=getKey):

            if project["state"] != "active":
                continue
            environment = project["id"]
            envURL = rancherUrl + "/env/" + environment
            logging.info("Retrieving %s - %s", environment, project["name"])
            envLabel = project["name"]
            content.append('\nh3. "{}":{}\n'.format(envLabel, envURL))
            description = project.get("description")
            if description is None:
                description = ""
            content.append("{}\n".format(description))

            content.append("\n")
            disc.load_hosts(
                rancherApiUrl,
                rancherAccessKey,
                rancherSecretKey,
                rancherApiUrl + "/projects/" + environment,
            )
            disc.load_containers(
                rancherApiUrl,
                rancherAccessKey,
                rancherSecretKey,
                rancherApiUrl + "/projects/" + environment,
            )
            content.append("Number of containers: {}\n".format(disc.num_containers))
            disc.buildgraph(content)
            content.append("\n")

    new_content = "\n".join(content)

    if not disc.has_changed(new_content):
        logging.info("Content is the same, not saving")

    else:
        if dry_run:
            disc.write_stdout(new_content)
        else:
            disc.write_page(new_content)

    logging.info("List containers script finished succesfully")


if __name__ == "__main__":
    dryrun = False
    logging.basicConfig(format="%(levelname)s:%(message)s", level=logging.INFO)
    try:
        opts, args = getopt.getopt(sys.argv[1:], "vn")
    except getopt.GetoptError as err:
        sys.exit(2)
    for o, a in opts:
        if o == "-v":
            logging.basicConfig(format="%(levelname)s:%(message)s", level=logging.DEBUG)
        if o == "-n":
            dryrun = True

    image_checker = ImageChecker()
    main(image_checker, dryrun)
