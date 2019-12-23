"""
Apply the factsheet template to wiki pages

https://taskman.eionet.europa.eu/projects/netpub/wiki/IT_service_factsheet_template
"""

import os
import re
import sys
from collections import OrderedDict
import logging
import io
import tempfile
import subprocess

from redminelib import Redmine
from more_itertools import peekable

LOG_LEVEL = logging.DEBUG
log = logging.getLogger(__name__)
log.setLevel(LOG_LEVEL)


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
            if title.endswith("*"):
                mandatory = True
                title = title.rstrip("*").strip()
            else:
                mandatory = False

            yield {
                "title": title,
                "mandatory": mandatory,
                "lines": section["lines"],
            }

    def _merge_fields(self, intro_lines):
        fields = OrderedDict()
        for line in intro_lines:
            line = line.strip()
            if not line:
                continue
            [label, value] = line.split(":", 1)
            label = label.strip().capitalize()
            value = value.strip()
            fields.setdefault(label, []).append(value)

        new_fields = OrderedDict()
        for field in self.fields:
            label = field["label"]
            try:
                value = fields.pop(label)
            except KeyError:
                if field["mandatory"]:
                    placeholder = "_%{color:lightgray}" + field["desc"] + "%_"
                    value = [placeholder]
                else:
                    log.debug(f"Skipping non-mandatory field {label}")
                    continue
            new_fields[label] = value

        for label, value in fields.items():
            new_fields[label] = value

        return new_fields

    def apply(self, page_text):
        page = Wikipage(page_text)

        new_fields = self._merge_fields(page.intro)
        new_intro = [""]
        for label, values in new_fields.items():
            for value in values:
                new_intro.append(f"{label}: {value}")
        new_intro.append("")
        page.intro = new_intro

        print_diff(page_text, page.render())


def main(config):
    taskman = Taskman(config["wiki_server"], config["wiki_apikey"])
    template_text = taskman.get_wiki("netpub", "IT_service_factsheet_template")
    template = Template(template_text)

    [page] = sys.argv[1:]
    orig = taskman.get_wiki("infrastructure", page)
    template.apply(orig)


if __name__ == "__main__":
    config = {
        "wiki_server": os.getenv("WIKI_SERVER", ""),
        "wiki_apikey": os.getenv("WIKI_APIKEY", ""),
    }
    logging.basicConfig(level=LOG_LEVEL)
    main(config)
