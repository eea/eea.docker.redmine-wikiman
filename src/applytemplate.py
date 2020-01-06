# encoding: utf8
"""
Apply the factsheet template to wiki pages

https://taskman.eionet.europa.eu/projects/netpub/wiki/IT_service_factsheet_template
"""

import os
import re
from collections import OrderedDict, defaultdict
import logging
import io
import tempfile
import subprocess
import argparse

from redminelib import Redmine
from redminelib.exceptions import ResourceNotFoundError
from more_itertools import peekable

log = logging.getLogger(__name__)

config = dict(
    factsheet_project=os.getenv("FACTSHEET_PROJECT", "infrastructure"),
    template_project=os.getenv("TEMPLATE_PROJECT", "netpub"),
    template_name=os.getenv("TEMPLATE_NAME", "IT_service_factsheet_template"),
    todolist_name=os.getenv("TODOLIST_NAME", "IT_service_factsheet_ToDo_list"),
    wiki_server=os.getenv("WIKI_SERVER", ""),
    wiki_apikey=os.getenv("WIKI_APIKEY", ""),
)

TODOLIST_DEFAULT_TEXT = """\
h1. IT service factsheet ToDo list

"""
OK_TEXT = "&#x1F44D;"


def remove_extensions_header(text):
    header_pattern = (
        r'<div id="wiki_extentions_header">\s*'
        r'{{last_updated_at}} _by_ {{last_updated_by}}\s*'
        r'</div>'
    )
    return re.sub(header_pattern, "", text).lstrip()


def print_diff(text1, text2):
    with tempfile.TemporaryDirectory() as tmp:
        with open(f"{tmp}/text1", "w", encoding="utf8") as f:
            f.write(text1.replace("\r\n", "\n"))
        with open(f"{tmp}/text2", "w", encoding="utf8") as f:
            f.write(text2.replace("\r\n", "\n"))
        subprocess.run(["diff", "-U3", f"{tmp}/text1", f"{tmp}/text2"])


class Taskman:

    def __init__(self, url, key):
        self.redmine = Redmine(url, key=key, requests={"verify": True})

    def get_wiki(self, project_id, name):
        page = self.redmine.wiki_page.get(name, project_id=project_id)
        return remove_extensions_header(page.text)

    def save_wiki(self, project_id, name, text):
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


class Wikipage:

    def __init__(self, text):
        lines = peekable(text.splitlines())

        line0 = next(lines)
        line0match = re.match(r"h1\. (?P<title>.*)$", line0)
        assert line0match is not None, "Wikipage must start with h1"
        self.title = line0match.group("title")

        self.intro = []
        while lines and not lines.peek().startswith("h2. "):
            self.intro.append(next(lines))

        self.sections = []
        current = None
        for line in lines:
            heading = re.match(r"h2\.\s+(?P<title>.*)$", line)
            if heading:
                if current:
                    self.sections.append(current)

                current = {
                    "title": heading.group("title").strip(),
                    "lines": [],
                }

            else:
                current["lines"].append(line)

        if current:
            self.sections.append(current)

        for section in self.sections:
            if not section["lines"] or section["lines"][-1] != "":
                section["lines"].append("")

    def render(self):
        out = io.StringIO()

        print(f"h1. {self.title}", file=out)

        for line in self.intro:
            print(line, file=out)

        for section in self.sections:
            print(f"h2. {section['title']}", file=out)
            for line in section["lines"]:
                print(line, file=out)

        return out.getvalue()


class Template:

    def __init__(self, text, template_project, template_name):
        wikipage = Wikipage(text)
        self.template_project = template_project
        self.template_name = template_name

        assert wikipage.sections[0]["title"] == "Structured fields"
        section0 = wikipage.sections[0]

        self.fields = list(self._parse_fields(section0["lines"]))

        self.sections = list(self._map_sections(wikipage.sections[1:]))

    def _parse_fields(self, intro_lines):
        for line in intro_lines:
            line = line.strip("| ")
            if not line:
                continue

            (label, desc) = line.split("|")
            label = label.strip(": ")
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

    _begin_todo = "_%{color:lightgray}ToDo: "
    _end_todo = "%_"

    def _todo(self, text):
        return self._begin_todo + text + self._end_todo

    def _is_todo(self, text):
        t = text.strip()
        return (t.startswith(self._begin_todo) and t.endswith(self._end_todo))

    def _map_sections(self, sections):
        for section in sections:
            title = section["title"]

            link_hash = title.rstrip("*").replace(" ", "-")
            prj = self.template_project
            tmpl = self.template_name
            link = f"[[{prj}:{tmpl}#{link_hash}]]"

            if title.endswith("*"):
                mandatory = True
                title = title.rstrip("*").strip()
            else:
                mandatory = False

            yield {
                "title": title,
                "mandatory": mandatory,
                "lines": ["", self._todo(link), ""],
            }

    def _merge_fields(self, intro_lines):
        original_case = {}
        fields = OrderedDict()

        extra_lines = []
        fields_finished = False
        for line in intro_lines:
            if line.strip() and ':' not in line:
                fields_finished = True

            if fields_finished:
                extra_lines.append(line)
                continue

            if not line.strip():
                continue

            [label, value] = line.strip().split(":", 1)
            label = label.strip()
            value = value.strip()
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

        return (new_fields, extra_lines)

    def _merge_sections(self, original_sections):
        old_sections = OrderedDict()
        for section in original_sections:
            old_sections[section["title"].lower()] = section

        new_sections = []
        for section_template in self.sections:
            title = section_template["title"]
            try:
                section = old_sections.pop(title.lower())
            except KeyError:
                if section_template["mandatory"]:
                    section = {
                        "title": title,
                        "lines": section_template["lines"],
                    }
                else:
                    log.debug(f"Skipping optional section {title!r}")
                    continue
            else:
                section["title"] = title

            new_sections.append(section)

        for section in old_sections.values():
            new_sections.append(section)

        return new_sections

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

        page.sections = self._merge_sections(page.sections)
        for section in page.sections:
            content = "\n".join(section["lines"])
            if self._is_todo(content):
                todo_list.append(f"Section \"{section['title']}\"")

        return (page.render(), todo_list)


class FactsheetUpdater:

    def __init__(self, taskman, dry_run, factsheet_project, template_project,
                 template_name, todolist_name):
        self.taskman = taskman
        self.dry_run = dry_run
        template_text = self.taskman.get_wiki(template_project, template_name)
        self.template = Template(
            template_text,
            template_project,
            template_name,
        )
        self.todo_map = defaultdict(dict)
        self.factsheet_project = factsheet_project
        self.template_project = template_project
        self.template_name = template_name
        self.todolist_name = todolist_name

    def save_page(self, project, page, orig, new):
        if new != orig:
            log.info(f"Saving page {project!r}:{page!r}")
            if self.dry_run:
                print_diff(orig, new)
            else:
                self.taskman.save_wiki(project, page, new)
        else:
            log.debug(f"No changes for page {project!r}:{page!r}")

    def update(self, page):
        log.debug(f"Processing page {page!r}")
        orig = self.taskman.get_wiki(self.factsheet_project, page)

        owner_match = re.search(r"Product Owner:\s*(.*)", orig, re.IGNORECASE)
        if not owner_match:
            log.debug(f"Page {page!r} is not a factsheet, skipping")
            return

        (new, todo_list) = self.template.apply(orig)

        self.save_page(self.factsheet_project, page, orig, new)

        owner = owner_match.group(1).strip()
        self.todo_map[owner][page] = ', '.join(todo_list) or OK_TEXT

    def recursive_update(self, start_page):
        prj = self.factsheet_project
        for name in self.taskman.wiki_children(prj, start_page):
            self.update(name)

    def save_todo_list(self):
        try:
            orig = self.taskman.get_wiki(
                self.template_project,
                self.todolist_name,
            )
        except ResourceNotFoundError:
            orig = TODOLIST_DEFAULT_TEXT

        todo_page = Wikipage(orig)

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
                    merged_todos[owner][m.group("page")] = m.group("summary")

        for owner, page_map in self.todo_map.items():
            for page, summary in page_map.items():
                merged_todos[owner][page] = summary

        todo_page.sections = []
        for owner, page_map in sorted(merged_todos.items()):
            lines = [""]
            for page, summary in sorted(page_map.items()):
                prj = self.factsheet_project
                link = f"[[{prj}:{page}]]"
                lines.append(f"* {link}: {summary}")
            lines.append("")

            todo_page.sections.append({
                "title": owner,
                "lines": lines,
            })

        new = todo_page.render()
        self.save_page(self.template_project, self.todolist_name, orig, new)


def main(page, config):
    taskman = Taskman(config["wiki_server"], config["wiki_apikey"])
    updater = FactsheetUpdater(
        taskman=taskman,
        dry_run=config["dry_run"],
        factsheet_project=config["factsheet_project"],
        template_project=config["template_project"],
        template_name=config["template_name"],
        todolist_name=config["todolist_name"],
    )
    updater.recursive_update(page)
    updater.save_todo_list()


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

    config["dry_run"] = options.dry_run

    main(options.page, config)
