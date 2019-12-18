"""
Apply the factsheet template to wiki pages

https://taskman.eionet.europa.eu/projects/netpub/wiki/IT_service_factsheet_template
"""

import os
import re

from redminelib import Redmine


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


def get_sections(text):
    current = None
    for line in text.splitlines():
        if line.startswith("h2."):
            if current:
                yield current

            m = re.match(
                r"h2\.\s+(?P<title>[^*]+)\s*(?P<mandatory>\*)?\s*$",
                line,
            )
            current = {
                "title": m.group("title"),
                "mandatory": bool(m.group("mandatory")),
                "lines": [],
            }

            continue

        else:
            if not current:
                raise RuntimeError("Template must begin with `h2.`")
            current["lines"].append(line)

    if current:
        yield current


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
        template_marker = (
            r"\*Copy the template from below:\*\s+"
            r"[-]+\s+"
        )
        (_ignore, template_text) = re.split(template_marker, text)

        self.sections = list(get_sections(template_text))

        assert self.sections[0]["title"] == "Structured fields"
        section0 = self.sections.pop(0)
        self.fields = list(get_fields_from_section(section0["lines"]))


def main(config):
    taskman = Taskman(config["wiki_server"], config["wiki_apikey"])
    template_text = taskman.get_wiki("netpub", "IT_service_factsheet_template")
    template = Template(template_text)
    print(template)


if __name__ == "__main__":
    config = {
        "wiki_server": os.getenv("WIKI_SERVER", ""),
        "wiki_apikey": os.getenv("WIKI_APIKEY", ""),
    }
    main(config)
