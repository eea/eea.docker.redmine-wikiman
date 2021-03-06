import os
import sys
import logging
import time
import getopt
from urllib.parse import urlparse

import applytemplate
import listcontainers
import liststacks
import backupstacks

from listhosts import RancherInstances
from image_checker import ImageChecker

logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)
log = logging.getLogger('')

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
        log.info("Content is the same, not saving")
    log.info("Done list hosts")

def run_list_containers(image_checker, dry_run):
    listcontainers.main(image_checker, dry_run)

def run_list_stacks(dry_run):
    liststacks.main(dry_run)

def run_backup_stacks(dry_run):
    backupstacks.main(dry_run)



if __name__ == "__main__":
    dry_run = False
    environments = None
    page = None
    image_checker = ImageChecker()

    log.setLevel(logging.INFO)
    try:
        opts, args = getopt.getopt(sys.argv[1:], ":p:dvn")
    except getopt.GetoptError as err:
        print('run_all.py -p template_page_name')
        sys.exit(2)
    for o, a in opts:
        if o == "-v":
            log.setLevel(logging.DEBUG)
        if o == "-n":
            dry_run = True
        if o == "-p":
            page = a
    if len(args) > 0:
        environments = args
        
    log.info('Running list stacks')
    run_list_stacks(dry_run)

    
    if os.getenv('GITLAB_CONFIG'):
        log.info('Running backup stacks')
        run_backup_stacks(dry_run)

    log.info('Running list hosts')
    run_list_hosts(dry_run, environments)        

    log.info('Running apply template')
    run_apply_template(page, image_checker, dry_run)

    log.info('Running list containers')
    run_list_containers(image_checker, dry_run)
