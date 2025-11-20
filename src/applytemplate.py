# encoding: utf8
"""
Apply the factsheet template to wiki pages

https://taskman.eionet.europa.eu/projects/netpub/wiki/IT_service_factsheet_template
"""

import argparse
import io
import logging
import os
import re
import subprocess
import tempfile
import time

from collections import OrderedDict, defaultdict
from datetime import date, datetime

from more_itertools import peekable
from redminelib import Redmine
from redminelib.exceptions import ResourceNotFoundError, UnknownError

from image_checker import ImageChecker
from rancher1.addimageinfo import generate_images_text, get_docker_images
from rancher2.addimageinfo import (
    generate_images_text_rancher2,
    get_docker_images_rancher2,
)

log = logging.getLogger(__name__)

config = dict(
    factsheet_project=os.getenv("FACTSHEET_PROJECT", "infrastructure"),
    template_project=os.getenv("TEMPLATE_PROJECT", "netpub"),
    template_name=os.getenv("TEMPLATE_NAME", "IT_service_factsheet_template"),
    todolist_name=os.getenv("TODOLIST_NAME", "IT_service_factsheet_ToDo_list"),
    wiki_server=os.getenv("WIKI_SERVER", ""),
    wiki_apikey=os.getenv("WIKI_APIKEY", ""),
    stackwiki=os.getenv("WIKI_STACKS_PAGE", "Rancher_stacks"),
)

TODOLIST_DEFAULT_TEXT = """\
h1. IT service factsheet ToDo list

"""
OK_TEXT = "&#x1F44D;"
TOC_CODE = "{{>toc}}"


def remove_extensions_header(text):
    header_pattern = (
        r'<div id="wiki_extentions_header">\s*'
        r"{{last_updated_at}} _by_ {{last_updated_by}}\s*"
        r"</div>"
    )
    return re.sub(header_pattern, "", text).lstrip()


def print_diff(text1, text2):
    with tempfile.TemporaryDirectory() as tmp:
        with open(f"{tmp}/text1", "w", encoding="utf8") as f:
            f.write(text1.replace("\r\n", "\n"))
        with open(f"{tmp}/text2", "w", encoding="utf8") as f:
            f.write(text2.replace("\r\n", "\n"))
        subprocess.run(["diff", "-U3", f"{tmp}/text1", f"{tmp}/text2"])


def escape_html(text):
    return text.replace("]", "&#x5d;")


def unescape_html(text):
    return text.replace("&#x5d;", "]")


def get_deployment_info(urls, image_checker):
    # rancher1
    docker_images = get_docker_images(urls)
    if not docker_images:
        log.debug("No docker images extracted, will continue")
        return

    update_needed, text = generate_images_text(docker_images, image_checker)
    return update_needed, text


def get_deployment_info_rancher2(urls, image_checker):
    # rancher2
    docker_images = get_docker_images_rancher2(urls)
    if not docker_images:
        log.debug("No docker images extracted, will continue")
        return

    update_needed, text = generate_images_text_rancher2(
        docker_images, image_checker
    )
    return update_needed, text


class Taskman:

    def __init__(self, url, key):
        self.redmine = Redmine(url, key=key, requests={"verify": True})

    def get_wiki(self, project_id, name):
        page = self.redmine.wiki_page.get(name, project_id=project_id)
        return remove_extensions_header(page.text)

    def save_wiki(self, project_id, name, text):
        try:
            self.redmine.wiki_page.update(name, project_id=project_id, text=text)
        except UnknownError:
            time.sleep(30)
            self.redmine.wiki_page.update(name, project_id=project_id, text=text)

    def wiki_children(self, project_id, name):
        project = self.redmine.project.get(project_id)
        children_of = defaultdict(list)
        for page in project.wiki_pages:
            parent = getattr(page, "parent", "")
            if parent:
                children_of[parent.title].append(page.title)

        def children(title):
            yield title
            for c in children_of[title]:
                yield from children(c)

        yield from children(name)


class StackFinder:

    def __init__(self, stack_wiki_text):
        self.stacks = stack_wiki_text.splitlines()

    def find(self, url):
        stack = ""
        url_no_protocol = url.replace("http://", "").replace("https://", "")
        #remove \
        url_no_protocol = url_no_protocol.replace("\\", "")

        regex = re.compile(
            r" (https?://)?{}/?[^a-z/]".format(url_no_protocol.replace(".", r"\.")),
            re.IGNORECASE,
        )
        stack = list(filter(regex.search, self.stacks))
        if not stack:
            stack_name = url_no_protocol.replace(".europa.eu", "").replace(".", "-")
            stack = [x for x in self.stacks if '|"' + stack_name + '":' in x.lower()]

        regex = re.compile(r'^\|(".+?":.+?) \|.*$')
        rv = [regex.match(str(st)).group(1) for st in stack]
        return rv


class Wikipage:

    def __init__(self, text):
        lines = iter(text.splitlines())

        line0 = next(lines)
        line0match = re.match(r"h1\. (?P<title>.*)$", line0)
        assert line0match is not None, "Wikipage must start with h1"
        self.title = line0match.group("title")

        self.intro, self.sections = self._split_headings(lines, "h2. ")
        for s in self.sections:
            s["lines"], s["h3"] = self._split_headings(s["lines"], "h3. ")

    def _split_headings(self, lines, hprefix):
        lines = peekable(lines)
        intro = []
        while lines and not lines.peek().startswith(hprefix):
            intro.append(next(lines))

        sections = []
        current = None
        for line in lines:
            if line.startswith(hprefix):
                if current:
                    sections.append(current)

                current = {
                    "title": line[len(hprefix) :].strip(),
                    "lines": [],
                }

            else:
                current["lines"].append(line)

        if current:
            sections.append(current)

        for section in sections:
            if not section["lines"] or section["lines"][-1] != "":
                section["lines"].append("")

        return intro, sections

    def render(self):
        out = io.StringIO()

        print(f"h1. {self.title}", file=out)

        for line in self.intro:
            print(line, file=out)

        for section in self.sections:
            print(f"h2. {section['title']}", file=out)
            for line in section["lines"]:
                print(line, file=out)

            for h3 in section.get("h3", []):
                print(f"h3. {h3['title']}", file=out)
                for line in h3["lines"]:
                    print(line, file=out)

        return out.getvalue()


class Template:

    def __init__(
        self, text, template_project, template_name, stack_wiki_text, image_checker, rancher2=False
    ):
        wikipage = Wikipage(text)
        self.template_project = template_project
        self.template_name = template_name
        self.image_checker = image_checker

        assert wikipage.sections[1]["title"] == "Structured fields"
        section0 = wikipage.sections[1]

        self.fields = list(self._parse_fields(section0["lines"]))

        self.sections = []
        for s in wikipage.sections[2:]:
            mapped_s = self._map_section(s)
            mapped_s["h3"] = [self._map_section(h3) for h3 in s["h3"]]
            self.sections.append(mapped_s)

        self.stack_finder = StackFinder(stack_wiki_text)
        self.rancher2 = rancher2

    def _parse_fields(self, intro_lines):
        for line in intro_lines:
            line = line.strip("| \xa0")
            if not line:
                continue

            (label, desc) = line.split("|")
            label = label.strip(": \xa0")
            desc = desc.strip()
            mandatory = False
            if label.endswith("*"):
                mandatory = True
                label = label.rstrip("* ")

            yield {
                "label": label,
                "mandatory": mandatory,
                "desc": desc,
            }

    _begin_todo = "_%{color:red}ToDo:% %{color:gray}"
    _end_todo = "%_"

    def _todo(self, text):
        return self._begin_todo + text + self._end_todo

    def _is_todo(self, text):
        m = re.match(r"^_%{color:[^}]+}ToDo:.*%_$", text.strip())
        return m is not None

    def _map_section(self, section):
        title = section["title"]

        link_hash = title.rstrip("*").replace(" ", "-")
        prj = self.template_project
        tmpl = self.template_name
        link = f"Refer to [[{prj}:{tmpl}#{link_hash}]]"

        if title.endswith("*"):
            mandatory = True
            title = title.rstrip("*").strip()
        else:
            mandatory = False

        for line in section["lines"]:
            alt = re.match(r"\*alternate titles\*\s*:(.*)$", line.lower())
            if alt:
                alt_titles = [a.strip() for a in alt.group(1).split(";")]
                break
        else:
            alt_titles = []

        return {
            "title": title,
            "alt_titles": alt_titles,
            "mandatory": mandatory,
            "lines": ["", self._todo(link), ""],
        }

    def _merge_fields(self, intro_lines):
        original_case = {}
        fields = OrderedDict()
        extra_lines = []
        fields_finished = False

        existing_fields = []
        for field in self.fields:
            existing_fields.append(field["label"])

        for line in intro_lines:
            if line.strip() == TOC_CODE:
                continue

            if line.strip() and ":" not in line:
                fields_finished = True

            if fields_finished:
                extra_lines.append(line)
                continue

            if not line.strip():
                continue

            [label, value] = line.strip().split(":", 1)
            label = label.strip()
            value = value.strip()

            if "{color:red}ToDo" in value and label.lower() not in existing_fields:
                log.debug(f"Not keeping the old mandatory field {label!r}")
                continue
            fields.setdefault(label.lower(), []).append(value)
            original_case[label.lower()] = label

        new_fields = OrderedDict()
        for field in self.fields:
            label = field["label"]
            try:
                value = fields.pop(label.lower())
            except KeyError:
                if field["mandatory"]:
                    value = [self._todo(field["desc"])]
                else:
                    log.debug(f"Skipping optional field {label!r}")
                    continue
            else:
                if all(self._is_todo(i) for i in value):
                    value = [self._todo(field["desc"])]
            new_fields[label] = value

        for label, value in fields.items():
            new_fields[original_case[label]] = value

        new_stacks = []
        for location in new_fields["Service location"]:
            if not location.strip():
                continue
            url = location.strip().split()[0].strip("/")
            if "(" in url:
                log.warning(
                    f"Could not extract url from {url}, check 'Service location'"
                )
                continue
            new_stacks.extend(self.stack_finder.find(url))

        if new_stacks:
            existing_values = [
                v for v in new_fields.get("Rancher Stack URL", []) if v.strip()
            ]
            combined = []
            seen = set()
            for value in existing_values + new_stacks:
                if value in seen:
                    continue
                combined.append(value)
                seen.add(value)
            new_fields["Rancher Stack URL"] = combined

        return (new_fields, extra_lines)

    def _merge_sections(self, template_sections, original_sections):
        old_sections = OrderedDict()
        for section in original_sections:
            if not self._is_todo("\n".join(section["lines"])):
                old_sections[section["title"].lower()] = section

        new_sections = []
        for section_template in template_sections:
            title = section_template["title"]
            search_titles = [title.lower()] + section_template["alt_titles"]

            for s in search_titles:
                if s in old_sections:
                    section = old_sections.pop(s)
                    section["title"] = title
                    break

            else:
                if section_template["mandatory"]:
                    section = {
                        "title": title,
                        "lines": section_template["lines"],
                    }
                else:
                    log.debug(f"Skipping optional section {title!r}")
                    continue

            new_sections.append(section)

        for section in old_sections.values():
            new_sections.append(section)

        return new_sections

    def _add_image_info(self, page, urls):
        today = date.today().strftime("%Y-%m-%d")
        comment = (
            f"??This section of the wiki was generated automatically "
            f"from the DeploymentRepoURL on {today}, "
            f"please don't edit it manually.??"
        )

        if self._is_todo(urls[0]):
            return

        section_map = {s["title"]: s for s in page.sections}
        source_code_section = section_map.get("Components and source code")
        if source_code_section is None:
            return

        deployment_info = (
            get_deployment_info_rancher2(urls, self.image_checker)
            if self.rancher2
            else get_deployment_info(urls, self.image_checker)
        )
        if deployment_info is None:
            return

        update_needed, text = deployment_info
        marker = "please don't edit it manually.??"
        old = "\n".join(source_code_section["lines"]).strip()
        if marker in old:
            if old.split(marker)[1].strip() == text.strip():
                return update_needed

        source_code_section["lines"] = ["", comment, ""]
        source_code_section["lines"] += text.splitlines()
        source_code_section["lines"] += [""]

        old_section = section_map.get("Source code information")
        if old_section is not None:
            page.sections.remove(old_section)

        return update_needed

    def apply(self, page_text):
        page = Wikipage(page_text)

        (new_fields, extra_lines) = self._merge_fields(page.intro)
        new_intro = [""]
        todo_list = []
        for label, values in new_fields.items():
            for value in values:
                new_intro.append(f"{label}: {value}")
                if self._is_todo(value):
                    todo_list.append(f'Field "{label}"')
        new_intro.append("")
        if extra_lines:
            new_intro += extra_lines
            if new_intro[-1] != "":
                new_intro.append("")
        page.intro = new_intro

        if TOC_CODE not in "\n".join(page.intro):
            page.intro[0:0] = ["", TOC_CODE]

        page.sections = self._merge_sections(self.sections, page.sections)
        todo_components_and_source = False
        for section in page.sections:
            content = "\n".join(section["lines"])
            if self._is_todo(content):
                todo_list.append(f"Section \"{section['title']}\"")
                if section["title"] == "Components and source code":
                    todo_components_and_source = True

            h3_template = []
            for s in self.sections:
                if s["title"] == section["title"]:
                    h3_template = s["h3"]
            section_h3 = section.get("h3", [])
            section["h3"] = self._merge_sections(h3_template, section_h3)

        update_needed = self._add_image_info(page, new_fields["DeploymentRepoURL"])
        if update_needed and not todo_components_and_source:
            todo_list.append(
                'Section "Components and source code" **(upgrade available)**'
            )

        return (page.render(), todo_list)


class FactsheetUpdater:

    def __init__(
        self,
        taskman,
        dry_run,
        factsheet_project,
        template_project,
        template_name,
        todolist_name,
        stackwiki,
        image_checker,
        rancher2=False,
    ):
        self.taskman = taskman
        self.dry_run = dry_run
        template_text = self.taskman.get_wiki(template_project, template_name)
        stack_wiki_text = self.taskman.get_wiki(factsheet_project, stackwiki)
        self.template = Template(
            template_text,
            template_project,
            template_name,
            stack_wiki_text,
            image_checker,
            rancher2,
        )
        self.todo_map = defaultdict(dict)
        self.seen_pages = set()
        self.all_pages = set()
        self.factsheet_project = factsheet_project
        self.template_project = template_project
        self.template_name = template_name
        self.todolist_name = todolist_name

    def save_page(self, project, page, orig, new):
        if new != orig:
            log.info(f"Saving page {project}:{page}")
            if self.dry_run:
                print_diff(orig, new)
            else:
                self.taskman.save_wiki(project, page, new)
        else:
            log.info(f"No changes for page {project}:{page}")

    def update(self, page):
        log.info(f"Processing page {page}")
        orig = self.taskman.get_wiki(self.factsheet_project, page)

        product_owner_match = re.search(r"Product owner:\s*(.*)", orig, re.I)
        if not product_owner_match:
            log.info(f"Page {page} is not a factsheet, skipping")
            return

        (new, todo_list) = self.template.apply(orig)

        self.save_page(self.factsheet_project, page, orig, new)

        owner = "Unspecified"
        system_owner_match = re.search(r"System owner:\s*(.*)", orig, re.I)
        if system_owner_match:
            value = system_owner_match.group(1).strip().strip("[]")
            if not self.template._is_todo(value):
                owner = value

        self.todo_map[owner][page] = ", ".join(todo_list) or OK_TEXT
        self.seen_pages.add(page)

    def recursive_update(self, start_page):
        prj = self.factsheet_project
        for name in self.taskman.wiki_children(prj, start_page):
            self.all_pages.add(name)
            self.update(name)

    def save_todo_list(self, start_page, start_time):
        try:
            orig = self.taskman.get_wiki(
                self.template_project,
                self.todolist_name,
            )
        except ResourceNotFoundError:
            orig = TODOLIST_DEFAULT_TEXT

        todo_page = Wikipage(orig)

        when = start_time.strftime("%Y-%m-%d %H:%M")
        link = f"[[{self.factsheet_project}:{start_page}]]"
        todo_page.intro = [
            "",
            TOC_CODE,
            "",
            f"Updated by wikiman at {when}, applied on {link}",
            "",
        ]

        merged_todos = defaultdict(dict)
        for section in todo_page.sections:
            owner = section["title"]
            for line in section["lines"]:
                m = re.match(
                    r"^\* \[\[(?P<project>\w+):(?P<page>[^]]*)\]\]: "
                    r"(?P<summary>.*)",
                    line,
                )
                if m:
                    assert m.group("project") == self.factsheet_project
                    page = unescape_html(m.group("page"))
                    if (page in self.all_pages) and (page not in self.seen_pages):
                        merged_todos[owner][page] = m.group("summary")

        for owner, page_map in self.todo_map.items():
            for page, summary in page_map.items():
                merged_todos[owner][page] = summary

        todo_page.sections = []
        for owner, page_map in sorted(merged_todos.items()):
            lines = [""]
            for page, summary in sorted(page_map.items()):
                prj = self.factsheet_project
                link = f"[[{prj}:{escape_html(page)}]]"
                lines.append(f"* {link}: {summary}")
            lines.append("")

            todo_page.sections.append(
                {
                    "title": owner,
                    "lines": lines,
                }
            )

        new = todo_page.render()

        self.save_page(self.template_project, self.todolist_name, orig, new)


def main(page, config, image_checker):
    start_time = datetime.now()
    taskman = Taskman(config["wiki_server"], config["wiki_apikey"])
    updater = FactsheetUpdater(
        taskman=taskman,
        dry_run=config["dry_run"],
        factsheet_project=config["factsheet_project"],
        template_project=config["template_project"],
        template_name=config["template_name"],
        todolist_name=config["todolist_name"],
        stackwiki=config["stackwiki"],
        image_checker=image_checker,
        rancher2=config.get("rancher2", False),
    )
    log.info("Starting at %s" % page)
    updater.recursive_update(page)
    updater.save_todo_list(page, start_time)
    log.info("Done")


if __name__ == "__main__":
    assert config["wiki_server"], "Please set WIKI_SERVER env var"
    assert config["wiki_apikey"], "Please set WIKI_APIKEY env var"

    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-n", "--dry-run", action="store_true")
    parser.add_argument("page")
    options = parser.parse_args()

    log_level = logging.DEBUG if options.verbose else logging.INFO
    logging.basicConfig(level=log_level)
    log.setLevel(log_level)

    config["dry_run"] = options.dry_run

    image_checker = ImageChecker()
    main(options.page, config, image_checker)
