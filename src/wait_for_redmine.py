import os
from urllib.request import urlopen
from urllib.error import URLError
from time import sleep

retry_count = int(os.environ.get("WIKI_RETRY_COUNT", "5"))
retry_sleep = int(os.environ.get("WIKI_RETRY_SLEEP", "5"))
retry_timeout = int(os.environ.get("WIKI_RETRY_TIMEOUT", "30"))

url = os.environ.get("WIKI_SERVER")

if not url:
    raise RuntimeError("WIKI_SERVER environment variable not set")

print("Waiting for taskman to be up ...", flush=True)
while True:
    try:
        urlopen(url, timeout=retry_timeout)
    except (URLError, TimeoutError) as e:
        if retry_count > 0:
            retry_count -= 1
            print(e, flush=True)
            sleep(retry_sleep)
            continue
        else:
            raise
    else:
        print(url, "ok", flush=True)
        break
