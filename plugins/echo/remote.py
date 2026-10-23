"""echo over the HTTP binding (§28.1): a separate process with its own service token; no DB credentials.
CODERAG_URL=http://... CODERAG_CLIENT_ID=... CODERAG_CLIENT_SECRET=... python -m plugins.echo.remote"""
import os, time, urllib.parse, urllib.request, json
from coderag_core.worker import Worker, HttpBinding
from plugins.echo import main


def token(url, cid, secret):
    data = urllib.parse.urlencode({"client_id": cid, "client_secret": secret}).encode()
    with urllib.request.urlopen(url + "/core/v1/token", data) as r: return json.loads(r.read())["access_token"]


if __name__ == "__main__":
    url = os.environ["CODERAG_URL"]
    w = Worker("echo", "0.1.0", main.HANDLERS, HttpBinding(url, token(url, os.environ["CODERAG_CLIENT_ID"], os.environ["CODERAG_CLIENT_SECRET"])))
    while True:
        if not w.run_once(): time.sleep(2)
