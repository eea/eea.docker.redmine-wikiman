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
from more_itertools import peekable

log = logging.getLogger(__name__)

TEMPLATE_PROJECT = "netpub"
TEMPLATE_NAME = "IT_service_factsheet_template"


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
            f.write(text1)
        with open(f"{tmp}/text2", "w", encoding="utf8") as f:
            f.write(text2)
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

    def __init__(self, text):
        wikipage = Wikipage(text)

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

    def _map_sections(self, sections):
        for section in sections:
            title = section["title"]

            link_hash = title.rstrip("*").replace(" ", "-")
            link = f"[[{TEMPLATE_PROJECT}:{TEMPLATE_NAME}#{link_hash}]]"

            if title.endswith("*"):
                mandatory = True
                title = title.rstrip("*").strip()
            else:
                mandatory = False

            yield {
                "title": title,
                "mandatory": mandatory,
                "lines": [
                    "",
                    "_%{color:lightgray}" + f"ToDo: {link}" + "%_",
                    "",
                ],
            }

    def _merge_fields(self, intro_lines):
        original_case = {}
        fields = OrderedDict()

        for line in intro_lines:
            line = line.strip()
            if not line:
                continue
            [label, value] = line.split(":", 1)
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
                    placeholder = "_%{color:lightgray}" + field["desc"] + "%_"
                    value = [placeholder]
                else:
                    log.debug(f"Skipping optional field {label!r}")
                    continue
            new_fields[label] = value

        for label, value in fields.items():
            new_fields[original_case[label]] = value

        return new_fields

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

        new_fields = self._merge_fields(page.intro)
        new_intro = [""]
        for label, values in new_fields.items():
            for value in values:
                new_intro.append(f"{label}: {value}")
        new_intro.append("")
        page.intro = new_intro

        page.sections = self._merge_sections(page.sections)

        return page.render()


class FactsheetUpdater:

    def __init__(self, taskman, dry_run):
        self.taskman = taskman
        self.dry_run = dry_run
        template_text = self.taskman.get_wiki(TEMPLATE_PROJECT, TEMPLATE_NAME)
        self.template = Template(template_text)

    def update(self, page):
        log.debug(f"Processing page {page!r}")
        orig = self.taskman.get_wiki("infrastructure", page)

        if "product owner:" not in orig.lower():
            log.debug(f"Page {page!r} is not a factsheet, skipping")
            return

        new = self.template.apply(orig)

        if new != orig:
            log.info(f"Saving page {page!r}")
            if self.dry_run:
                print_diff(orig, new)
            else:
                self.taskman.save_wiki("infrastructure", page, new)
        else:
            log.debug(f"No changes for page {page!r}")

    def work(self, start_page):
        for name in self.taskman.wiki_children('infrastructure', start_page):
            self.update(name)


def main(page, wiki_server, wiki_apikey, dry_run):
    taskman = Taskman(wiki_server, wiki_apikey)
    updater = FactsheetUpdater(taskman, dry_run)
    updater.work(page)


if __name__ == "__main__":
    config = {
        "wiki_server": os.getenv("WIKI_SERVER", ""),
        "wiki_apikey": os.getenv("WIKI_APIKEY", ""),
    }

    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-n", "--dry-run", action="store_true")
    parser.add_argument("page")
    options = parser.parse_args()

    log_level = logging.DEBUG if options.verbose else logging.INFO
    logging.basicConfig(level=log_level)

    config["dry_run"] = options.dry_run

    main(options.page, **config)
