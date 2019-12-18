"""
Apply the factsheet template to wiki pages

https://taskman.eionet.europa.eu/projects/netpub/wiki/IT_service_factsheet_template
"""

import os
import re

from redminelib import Redmine


class Taskman:

    def __init__(self, url, key):
        self.redmine = Redmine(url, key=key, requests={"verify": True})

    def get_wiki(self, project_id, name):
        page = self.redmine.wiki_page.get(name, project_id=project_id)
        return page.text


def parse_template(text):
    noise_pattern = (
        r'<div id="wiki_extentions_header">\s*'
        r'{{last_updated_at}} _by_ {{last_updated_by}}\s*'
        r'</div>'
    )
    text = re.sub(noise_pattern, "", text).lstrip()

    template_marker = (
        r"\*Copy the template from below:\*\s+"
        r"[-]+\s+"
    )
    (_ignore, template) = re.split(template_marker, text)

    current = None
    sections = []
    for line in template.splitlines():
        if line.startswith("h2."):
            if current:
                sections.append(current)

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
        sections.append(current)

    assert sections[0]["title"] == "Structured fields"
    section0 = sections.pop(0)
    structured_fields = []
    for line in section0["lines"]:
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

        structured_fields.append({
            "label": label,
            "mandatory": mandatory,
            "desc": desc,
        })

    return {
        "structured_fields": structured_fields,
        "sections": sections,
    }


def main(config):
    taskman = Taskman(config["wiki_server"], config["wiki_apikey"])
    template_text = taskman.get_wiki("IT_service_factsheet_template", "netpub")
    template = parse_template(template_text)
    print(template)


if __name__ == "__main__":
    config = {
        "wiki_server": os.getenv("WIKI_SERVER", ""),
        "wiki_apikey": os.getenv("WIKI_APIKEY", ""),
    }
    main(config)
