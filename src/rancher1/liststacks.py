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
from redminelib import Redmine
from urllib.parse import urlparse


class Rancher_Stacks(object):

    def __init__(self):
        self.projectName = os.getenv("WIKI_PROJECT", "")
        self.pageName = os.getenv("WIKI_STACKS_PAGE", "")

    def _redmine(self):
        server = os.getenv("WIKI_SERVER", "")
        apikey = os.getenv("WIKI_APIKEY", "")
        return Redmine(server, key=apikey, requests={"verify": True})

    def write_page(self, content):
        server = self._redmine()
        server.wiki_page.update(
            self.pageName, project_id=self.projectName, text=content
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


def getKey(instance):
    """Return the key to sort on"""
    return instance["name"]


def main(dryrun):

    pageTitle = os.getenv("WIKI_STACKSPAGETITLE", "Rancher Stacks")

    content = []
    content.append("{{>toc}}\n\n")
    content.append("h1. " + pageTitle + "\n\n")
    content.append(
        "Automatically discovered on "
        + time.strftime("%d %B %Y")
        + ". _Do not update this page manually._"
    )

    disc = Rancher_Stacks()
    rancher_configs = os.getenv("RANCHER_CONFIG")
    for rancher_config in rancher_configs.split():
        rancher_configuration = rancher_config.split(",")
        rancherUrl = rancher_configuration[0]
        rancherApiUrl = rancherUrl + "/v2-beta"
        rancherAccessKey = rancher_configuration[1]
        rancherSecretKey = rancher_configuration[2]
        logging.info(rancherUrl)

        content.append("\nh2. {}\n".format(urlparse(rancherUrl).netloc.upper()))
        try:
            structdata = disc.get_operation(
                rancherApiUrl,
                rancherAccessKey,
                rancherSecretKey,
                rancherApiUrl + "/projects",
            )
        except BaseException:
            raise RuntimeError("There was a problem reading from Rancher")

        for project in sorted(structdata["data"], key=getKey):
            if project["state"] != "active":
                continue
            environment = project["id"]
            envURL = rancherUrl + "/env/" + environment
            logging.info("Retrieving %s - %s", environment, project["name"])
            content.append('\nh3. "{}":{}\n'.format(project["name"], envURL))
            description = project.get("description")
            if description is None:
                description = ""
            content.append("{}\n".format(description))
            content.append(
                "|_. Name |_. Created |_. State |_. Health |_. Catalog |_. Description |_. Tags |"
            )
            infraStacks = []
            userStacks = []

            stackUrl = rancherApiUrl + "/projects/" + environment
            structdata = disc.get_operation(
                rancherApiUrl, rancherAccessKey, rancherSecretKey, stackUrl + "/stacks"
            )

            for instance in sorted(structdata["data"], key=getKey):
                actions = instance["actions"]
                name = instance["name"]
                link = envURL + "/apps/stacks/" + instance["id"]
                created = instance["created"][:10]
                system = instance.get("system", False)
                if system is True:
                    name = ">. _" + name + "_"
                description = instance.get("description", "")
                if description is None:
                    description = ""
                if description.find("\n") >= 0:
                    description = description[: description.find("\n")]
                group = instance.get("group", "")
                if group is None:
                    group = ""
                logging.debug("%s", name)
                stack_line = '|"{}":{} | {} | {} | {} | {} | {} | {} |'.format(
                    name,
                    link,
                    created,
                    instance["state"],
                    instance["healthState"],
                    instance["externalId"],
                    description,
                    group,
                )
                if instance["system"]:
                    infraStacks.append(stack_line)
                else:
                    userStacks.append(stack_line)
            content.extend(infraStacks)
            content.extend(userStacks)

    new_content = "\n".join(content)

    if disc.has_changed(new_content):
        if dryrun:
            disc.write_stdout(new_content)
        else:
            disc.write_page(new_content)
    else:
        logging.info("Content is the same, not saving")


if __name__ == "__main__":
    dryrun = False

    try:
        opts, args = getopt.getopt(sys.argv[1:], "vn")
    except getopt.GetoptError as err:
        sys.exit(2)
    logging.basicConfig(format="%(levelname)s:%(message)s", level=logging.INFO)
    for o, a in opts:
        if o == "-v":
            logging.basicConfig(format="%(levelname)s:%(message)s", level=logging.DEBUG)
        if o == "-n":
            dryrun = True

    main(dryrun)
