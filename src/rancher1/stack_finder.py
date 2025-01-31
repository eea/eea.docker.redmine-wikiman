import re


class StackFinder:
    def __init__(self, stack_wiki_text):
        self.stacks = stack_wiki_text.splitlines()

    def find(self, url):
        stack = ""
        url_no_protocol = url.replace("http://", "").replace("https://", "")
        # remove \
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
