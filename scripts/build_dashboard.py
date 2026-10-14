"""build_dashboard.py -- injects report-data.json into assets/dashboard.html.

Produces a self-contained page ready to publish as an Artifact: no external requests
except Google Fonts, which the Artifact CSP allows.

Usage:
  python build_dashboard.py --data report-data.json --out dashboard.html
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
# In the source tree the template is ../assets; in an installed wheel it ships as the
# `assets` subpackage next to this file.
TEMPLATE = next((p for p in (os.path.join(HERE, "..", "assets", "dashboard.html"),
                             os.path.join(HERE, "assets", "dashboard.html"))
                 if os.path.isfile(p)), os.path.join(HERE, "..", "assets", "dashboard.html"))
TOKEN = "/*__DATA__*/ null"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--template", default=TEMPLATE)
    args = ap.parse_args()

    with open(args.template, encoding="utf-8") as f:
        html = f.read()
    if TOKEN not in html:
        raise SystemExit("template is missing the {0} placeholder".format(TOKEN))

    with open(args.data, encoding="utf-8") as f:
        data = json.load(f)

    # ensure_ascii escapes every non-ASCII character from the crawled site's own
    # content (em dashes in meta descriptions, non-Latin script, currency symbols) to
    # \uXXXX. That makes the page render identically no matter what charset the host
    # declares -- without it, a host that omits `charset=utf-8` mojibakes the evidence
    # strings. `</` is broken up so a closing tag inside the data cannot end the block.
    payload = json.dumps(data, ensure_ascii=True).replace("</", "<\\/")
    html = html.replace(TOKEN, payload)

    # Name the page after the site. The static <title> is what the artifact gallery and
    # the browser tab use -- setting document.title from JS is too late for those, so a
    # generic template title would leave every dashboard indistinguishable in a gallery.
    site = str(data.get("site") or "").strip()
    if site:
        safe = (site.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    .encode("ascii", "xmlcharrefreplace").decode("ascii"))
        html = html.replace("<title>SEO Repair Dashboard</title>",
                            "<title>{0} SEO Repair</title>".format(safe), 1)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote {0} ({1:.0f} KB)".format(args.out, os.path.getsize(args.out) / 1024))


if __name__ == "__main__":
    main()
