"""
Apply the factsheet template to wiki pages

https://taskman.eionet.europa.eu/projects/netpub/wiki/IT_service_factsheet_template
"""

import os
import re
import sys

from redminelib import Redmine
from more_itertools import peekable


def remove_extensions_header(text):
    header_pattern = (
        r'<div id="wiki_extentions_header">\s*'
        r'{{last_updated_at}} _by_ {{last_updated_by}}\s*'
        r'</div>'
    )
    return re.sub(header_pattern, "", text).lstrip()


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


def get_fields_from_section(section_lines):
    for line in section_lines:
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


class Template:

    def __init__(self, text):
        wikipage = Wikipage(text)

        assert wikipage.sections[0]["title"] == "Structured fields"
        section0 = wikipage.sections[0]
        self.fields = list(get_fields_from_section(section0["lines"]))

        self.sections = []
        for section in wikipage.sections[1:]:
            title = section["title"]
            if title.endswith("*"):
                section["mandatory"] = True
                section["title"] = title.rstrip("*").strip()
            else:
                section["mandatory"] = False
            self.sections.append(section)

    def apply(self, page_text):
        page = Wikipage(page_text)


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
    main(config)
