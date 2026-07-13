import logging
import os
import sys

import applytemplate as applytemplate
from image_checker import ImageChecker
from rancher2.listapps import Rancher2Apps, Rancher2MergeApps
from rancher2.listnodes import Rancher2MergeNodes, Rancher2Nodes
from rancher2.listpods import Rancher2MergePods, Rancher2Pods

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

log = logging.getLogger(__name__)


def run_apply_template(page, dry_run=False):
    assert page, "Please provide a template"

    config = dict(
        factsheet_project=os.getenv("FACTSHEET_PROJECT", "infrastructure"),
        template_project=os.getenv("TEMPLATE_PROJECT", "netpub"),
        template_name=os.getenv("TEMPLATE_NAME", "IT_service_factsheet_template"),
        todolist_name=os.getenv("TODOLIST_NAME", "IT_service_factsheet_ToDo_list"),
        wiki_server=os.getenv("WIKI_SERVER", ""),
        wiki_apikey=os.getenv("WIKI_APIKEY", ""),
        stackwiki=os.getenv("WIKI_APPS_PAGE", "Rancher2_apps"),
        dry_run=dry_run,
        rancher2=True,
    )

    assert config["wiki_server"], "Please set WIKI_SERVER env var"
    assert config["wiki_apikey"], "Please set WIKI_APIKEY env var"

    applytemplate.main(page, config, ImageChecker())


def run_list_apps(dry_run=False):
    log.info("Starting list apps")
    apps = Rancher2Apps(dry_run)
    apps.set_content()
    apps.write_page()
    log.info("Completed list apps")


def run_list_nodes(dry_run=False):
    log.info("Starting list nodes")
    nodes = Rancher2Nodes(dry_run)
    nodes.set_content()
    nodes.write_page()
    log.info("Completed list nodes")


def run_list_pods(dry_run=False):
    log.info("Starting list pods")
    pods = Rancher2Pods(dry_run)
    pods.set_content()
    pods.write_page()
    log.info("Completed list pods")


def run_merge_apps(dry_run=False):
    log.info("Starting merge apps")
    merged_apps = Rancher2MergeApps(dry_run)
    merged_apps.set_content()
    merged_apps.write_page()
    log.info("Completed merge apps")


def run_merge_nodes(dry_run=False):
    log.info("Starting merge nodes")
    merged_nodes = Rancher2MergeNodes(dry_run)
    merged_nodes.set_content()
    merged_nodes.write_page()
    log.info("Completed merge nodes")


def run_merge_pods(dry_run=False):
    log.info("Starting merge pods")
    merged_pods = Rancher2MergePods(dry_run)
    merged_pods.set_content()
    merged_pods.write_page()
    log.info("Completed merge pods")


if __name__ == "__main__":
    if sys.argv[1] == "list":
        log.info("=== Rancher2 list pipeline starting ===")
        for step in [run_list_apps, run_list_nodes, run_list_pods]:
            try:
                step()
            except Exception:
                log.exception("Step %s failed, continuing with next step", step.__name__)
        log.info("=== Rancher2 list pipeline finished ===")

    if sys.argv[1] == "merge":
        log.info("=== Rancher2 merge pipeline starting ===")
        for step in [run_merge_apps, run_merge_nodes, run_merge_pods]:
            try:
                step()
            except Exception:
                log.exception("Step %s failed, continuing with next step", step.__name__)

        try:
            run_apply_template(os.getenv("WIKI_PAGE", "Applications"))
        except Exception:
            log.exception("Apply template step failed")
        log.info("=== Rancher2 merge pipeline finished ===")
