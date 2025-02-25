import os
import sys

import applytemplate as applytemplate
from image_checker import ImageChecker
from rancher2.listapps import Rancher2Apps, Rancher2MergeApps
from rancher2.listnodes import Rancher2MergeNodes, Rancher2Nodes
from rancher2.listpods import Rancher2MergePods, Rancher2Pods


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
    apps = Rancher2Apps(dry_run)
    apps.set_content()
    apps.write_page()


def run_list_nodes(dry_run=False):
    nodes = Rancher2Nodes(dry_run)
    nodes.set_content()
    nodes.write_page()


def run_list_pods(dry_run=False):
    pods = Rancher2Pods(dry_run)
    pods.set_content()
    pods.write_page()


def run_merge_apps(dry_run=False):
    merged_apps = Rancher2MergeApps(dry_run)
    merged_apps.set_content()
    merged_apps.write_page()


def run_merge_nodes(dry_run=False):
    merged_nodes = Rancher2MergeNodes(dry_run)
    merged_nodes.set_content()
    merged_nodes.write_page()


def run_merge_pods(dry_run=False):
    merged_pods = Rancher2MergePods(dry_run)
    merged_pods.set_content()
    merged_pods.write_page()


if __name__ == "__main__":
    if sys.argv[1] == "list":
        # run on each non-main instance (cluster)
        run_list_apps()
        run_list_nodes()
        run_list_pods()

    if sys.argv[1] == "merge":
        # run on main instance
        run_merge_apps()
        run_merge_nodes()
        run_merge_pods()
        # run_apply_template("Climate_Advisory_Board")
