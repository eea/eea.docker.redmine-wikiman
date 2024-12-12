import os
import sys
import logging
import getopt
from urllib.parse import urlparse

import rancher1.applytemplate as applytemplate
import rancher1.listcontainers as listcontainers
import rancher1.liststacks as liststacks
import rancher1.backupstacks as backupstacks

from rancher1.listhosts import RancherInstances
from rancher1.image_checker import ImageChecker


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

    applytemplate.main(page, config, image_checker)


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
    listcontainers.main(image_checker, dry_run)


def run_list_stacks(dry_run):
    liststacks.main(dry_run)


def run_backup_stacks(dry_run):
    backupstacks.main(dry_run)


def reset_logs():
    log_dir = "/logs/"
    files = sorted(os.listdir(log_dir), reverse=True)
    for item in files:
        if item.endswith(".9"):
            os.remove(os.path.join(log_dir, item))
        else:
            ext = os.path.splitext(item)[1]
            if ext == ".log":
                new_count = "log.1"
            else:
                new_count = str(int(ext[1:]) + 1)
            new_name = os.path.splitext(item)[0] + "." + new_count
            os.rename(os.path.join(log_dir, item), os.path.join(log_dir, new_name))


if __name__ == "__main__":
    dry_run = False
    environments = None
    page = None
    image_checker = ImageChecker()

    log_level = logging.INFO
    try:
        opts, args = getopt.getopt(sys.argv[1:], ":p:dvn")
    except getopt.GetoptError as err:
        print("run_all.py -p template_page_name")
        sys.exit(2)
    for o, a in opts:
        if o == "-v":
            log_level = logging.DEBUG
        if o == "-n":
            dry_run = True
        if o == "-p":
            page = a
    if len(args) > 0:
        environments = args

    reset_logs()

    log = logging.getLogger("")

    logging.root.handlers = []
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("/logs/list_stacks.log"),
            logging.StreamHandler(),
        ],
    )

    logging.info("Running list stacks")
    run_list_stacks(dry_run)

    if os.getenv("GITLAB_CONFIG"):
        logging.root.handlers = []
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler("/logs/backup_stacks.log"),
                logging.StreamHandler(),
            ],
        )
        log.info("Running backup stacks")
        run_backup_stacks(dry_run)

    logging.root.handlers = []
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler("/logs/list_hosts.log"), logging.StreamHandler()],
    )

    log.info("Running list hosts")
    run_list_hosts(dry_run, environments)

    logging.root.handlers = []
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("/logs/apply_template.log"),
            logging.StreamHandler(),
        ],
    )

    log.info("Running apply template")
    run_apply_template(page, image_checker, dry_run)

    logging.root.handlers = []
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("/logs/list_containers.log"),
            logging.StreamHandler(),
        ],
    )

    log.info("Running list containers")
    run_list_containers(image_checker, dry_run)

    log.info("Finished running all scripts")
