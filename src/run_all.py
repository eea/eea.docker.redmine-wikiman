import os
import sys
import logging
import time
import getopt
from urllib.parse import urlparse

from applytemplate import main
from listhosts import RancherInstances
from listcontainers import Discover, getKey
from image_checker import ImageChecker

log = logging.getLogger(__name__)

def run_apply_template(page, image_checker, dry_run):
    assert page, "Please provide a template"

    config = dict(
        factsheet_project=os.getenv("FACTSHEET_PROJECT", "infrastructure"),
        template_project=os.getenv("TEMPLATE_PROJECT", "netpub"),
        template_name=os.getenv("TEMPLATE_NAME", "IT_service_factsheet_template"),
        todolist_name=os.getenv("TODOLIST_NAME", "IT_service_factsheet_ToDo_list"),
        wiki_server=os.getenv("WIKI_SERVER", ""),
        wiki_apikey=os.getenv("WIKI_APIKEY", ""),
        stackwiki=os.getenv("WIKI_STACKS_PAGE", "Rancher_stacks"),
        dry_run=dry_run,
    )

    assert config["wiki_server"], "Please set WIKI_SERVER env var"
    assert config["wiki_apikey"], "Please set WIKI_APIKEY env var"

    main(page, config, image_checker)


def run_list_hosts(dry_run, environments):
    obj = RancherInstances(environments)
    if obj.has_changed():
        if dry_run == True:
            obj.write_stdout()
        else:
            obj.write_page()
    else:
        logging.info("Content is the same, not saving")
    logging.info("Done list hosts")

def run_list_containers(image_checker, dry_run):
    pageTitle = os.getenv('WIKI_CONTAINERSPAGETITLE', 'Rancher Containers')

    content = []
    content.append('{{>toc}}\n\n')
    content.append('h1. ' + pageTitle + '\n\n')
    content.append(
        'Automatically discovered on ' +
        time.strftime('%d %B %Y') +
        '. _Do not update this page manually._')

    disc = Discover(image_checker)
    rancher_configs = os.getenv('RANCHER_CONFIG')

    for rancher_config in rancher_configs.split():

        rancher_configuration = rancher_config.split(",")

        rancherUrl = rancher_configuration[0]
        logging.info(rancherUrl)
        rancherApiUrl = rancherUrl + "/v2-beta"
        rancherAccessKey = rancher_configuration[1]
        rancherSecretKey = rancher_configuration[2]

        content.append(
            '\nh2. {}\n'.format(
                urlparse(rancherUrl).netloc.upper()))

        try:
            envstruct = disc.get_operation(
                rancherApiUrl,
                rancherAccessKey,
                rancherSecretKey,
                rancherApiUrl + "/projects")
        except BaseException:
            logging.error("There was a problem reading from Rancher")
            logging.error(sys.exc_info())
            continue


        for project in sorted(envstruct['data'], key=getKey):

            if project['state'] != 'active':
                continue
            environment = project['id']
            envURL = rancherUrl + "/env/" + environment
            logging.info("Retrieving %s - %s", environment, project['name'])
            envLabel = project['name']
            content.append('\nh3. "{}":{}\n'.format(envLabel, envURL))
            description = project.get('description')
            if description is None:
                description = ''
            content.append('{}\n'.format(description))

            content.append('\n')
            disc.load_hosts(
                rancherApiUrl,
                rancherAccessKey,
                rancherSecretKey,
                rancherApiUrl +
                "/projects/" +
                environment)
            disc.load_containers(
                rancherApiUrl,
                rancherAccessKey,
                rancherSecretKey,
                rancherApiUrl +
                "/projects/" +
                environment)
            content.append(
                'Number of containers: {}\n'.format(
                    disc.num_containers))
            disc.buildgraph(content)
            content.append('\n')

    new_content = "\n".join(content)

    if not disc.has_changed(new_content):
        logging.info("Content is the same, not saving")

    else:
        if dry_run:
            disc.write_stdout(new_content)
        else:
            disc.write_page(new_content)


if __name__ == "__main__":
    dry_run = False
    environments = None
    page = None
    image_checker = ImageChecker()

    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)
    try:
        opts, args = getopt.getopt(sys.argv[1:], ":p:dvn")
    except getopt.GetoptError as err:
        print('run_all.py -p template_page_name')
        sys.exit(2)
    for o, a in opts:
        if o == "-v":
            logging.basicConfig(
                format='%(levelname)s:%(message)s',
                level=logging.DEBUG)
        if o == "-n":
            dry_run = True
        if o == "-p":
            page = a
    if len(args) > 0:
        environments = args

    logging.info('Running list containers')
    run_list_containers(image_checker, dry_run)

    logging.info('Running apply template')
    run_apply_template(page, image_checker, dry_run)

    logging.info('Running list hosts')
    run_list_hosts(dry_run, environments)