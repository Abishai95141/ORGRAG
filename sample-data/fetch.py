"""Download the sample files listed in files.json into upload/ and drive/.

    python3 sample-data/fetch.py

Every file is a GOV.UK publication under the Open Government Licence v3.0. Files
already downloaded are skipped, so it's safe to run again."""

import json
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ALLOWED_HOST = "assets.publishing.service.gov.uk"


def main() -> int:
    listing = json.loads((HERE / "files.json").read_text())
    failed = 0
    for item in listing["files"]:
        target = HERE / item["to"]
        if urlparse(item["url"]).hostname != ALLOWED_HOST:
            print(f"skip   {item['to']}: not a GOV.UK asset")
            failed += 1
            continue
        if target.exists() and target.stat().st_size > 0:
            print(f"have   {item['to']}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(item["url"], headers={"User-Agent": "strata-sample-data"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
        except OSError as exc:
            print(f"FAILED {item['to']}: {exc}")
            failed += 1
            continue
        target.write_bytes(data)
        print(f"got    {item['to']} ({len(data) // 1024} KB)")
    print("done" if not failed else f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
