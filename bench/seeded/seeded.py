"""seeded.py -- Task 7 of the evidence plan: seeded-regression recall.

Replay shows only what sitemend flagged, never what it missed. Here the ground truth is
known: build each repository once (its last commit before the cutoff, with the replay's
own build code), apply one known regression at a time to a copy of the built output, and
check that the gate catches it, base against mutated head. An unmutated copy (the control)
must pass, or the harness itself is producing failures.

  python bench/seeded/seeded.py                 # the first 5 repositories in repos.json
  python bench/seeded/seeded.py --only vuejs/docs
  python bench/seeded/seeded.py --list          # the selection as JSON, for a CI matrix
  python bench/seeded/seeded.py --report --results a.csv b.csv   # merge runs, write recall.md

Writes results.csv (repo, mutation, expected_rule, expected_severity, target, fired,
gate_failed) and recall.md beside it. fired means the expected rule is among the head's new
or worsened findings; gate_failed means verify.py failed the check. A mutation that has no
eligible page on a site is "n/a"; a site sitemend cannot audit is "tool_failure" (Addendum 1).

Each mutation declares, before it runs, the rule it expects; the expectations were read
from audit.py and schema_check.py, not from what fired. Mutations change the built output,
not the source, so they do not test whether a framework would produce that output.
"""
import argparse
import csv
import html
import json
import os
import re
import shutil
import sys
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "replay"))
import replay  # noqa: E402

REPOS = 5   # the first 5 in repos.json, decided before any recall data (2026-10-17)
FIELDS = ["repo", "mutation", "expected_rule", "expected_severity", "target", "fired", "gate_failed"]
PREVIEW = "https://seeded-git-preview.vercel.app"
CANONICAL_TAG = re.compile(r"<link\b(?=[^>]*\brel=[\"']?canonical\b)[^>]*>", re.I)


# ---------------------------------------------------------------- page selection

def indexable(crawl):
    """Pages a search engine may index, non-home first, in a fixed order."""
    pages = [p for p in crawl["pages"] if not p.get("noindex") and p.get("robots_allowed") is not False
             and p.get("file")]
    return sorted(pages, key=lambda p: (p["url"] == "/", p["url"]))


def in_sitemap(crawl, p):
    paths = {urlsplit(u).path or "/" for u in crawl["sitemap"].get("urls") or []}
    return p["url"] in paths or any(a in paths for a in p.get("aliases") or [])


def edit(site, p, fn):
    path = os.path.join(site, p["file"].replace("/", os.sep))
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    new = fn(text)
    if new == text:
        return None
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    return p["url"]


def in_head(text, tag):
    """Insert tag right after <head>; unchanged text if there is no <head>."""
    return re.sub(r"(<head\b[^>]*>)", lambda m: m.group(1) + tag, text, count=1, flags=re.I)


# ---------------------------------------------------------------- the mutations

def canonical_removed(site, crawl):
    p = next((p for p in indexable(crawl) if p.get("canonical")), None)
    return p and edit(site, p, lambda t: CANONICAL_TAG.sub("", t))


def noindex_added(site, crawl):
    p = next((p for p in indexable(crawl) if in_sitemap(crawl, p)), None)
    meta = re.compile(r"<meta\b(?=[^>]*\bname=[\"']?robots\b)[^>]*>", re.I)
    return p and edit(site, p, lambda t: in_head(meta.sub("", t), '<meta name="robots" content="noindex">'))


def canonical_preview(site, crawl):
    p = next((p for p in indexable(crawl) if (p.get("canonical") or "").startswith("http")), None)

    def fn(t):
        return CANONICAL_TAG.sub(lambda m: re.sub(r"href=([\"']?)https?://[^/\"'\s>]+",
                                                  lambda h: "href=" + h.group(1) + PREVIEW, m.group(0)), t, count=1)
    return p and edit(site, p, fn)


def sitemap_preview(site, crawl):
    for root, _, files in sorted(os.walk(site)):
        for f in sorted(files):
            if not f.endswith(".xml"):
                continue
            path = os.path.join(root, f)
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if "<urlset" not in text:
                continue
            new = re.sub(r"<loc>\s*https?://[^/<\s]+", "<loc>" + PREVIEW, text, count=1)
            if new != text:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new)
                return "/" + os.path.relpath(path, site).replace(os.sep, "/")
    return None


def robots_disallow_all(site, crawl):
    if not any(in_sitemap(crawl, p) for p in indexable(crawl)):
        return None
    lines = [l for l in (crawl["robots"].get("raw") or "").splitlines() if l.lower().startswith("sitemap:")]
    with open(os.path.join(site, "robots.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(["User-agent: *", "Disallow: /"] + lines) + "\n")
    return "/robots.txt"


def linked_page_deleted(site, crawl):
    alias = {a: p["url"] for p in crawl["pages"] for a in [p["url"]] + (p.get("aliases") or [])}
    linked = {alias.get(l.get("resolved")) for p in crawl["pages"] for l in p.get("links") or []
              if l.get("kind") == "internal" and alias.get(l.get("resolved")) != p["url"]}
    p = next((p for p in indexable(crawl) if p["url"] != "/" and p["url"] in linked), None)
    if not p:
        return None
    os.remove(os.path.join(site, p["file"].replace("/", os.sep)))
    return p["url"]


def title_removed(site, crawl):
    p = next((p for p in indexable(crawl) if p.get("title")), None)
    return p and edit(site, p, lambda t: re.sub(r"<title\b[^>]*>.*?</title>", "", t, count=1, flags=re.I | re.S))


def hreflang_no_return(site, crawl):
    plain = [p for p in indexable(crawl) if not p.get("hreflang")]
    if len(plain) < 2:
        return None
    a, b = plain[0], plain[1]
    tag = '<link rel="alternate" hreflang="de" href="{0}">'.format(b["url"])
    return edit(site, a, lambda t: in_head(t, tag))


def title_duplicated(site, crawl):
    pages = [p for p in indexable(crawl) if p.get("title")]
    counts = {}
    for p in pages:
        counts[p["title"]] = counts.get(p["title"], 0) + 1
    a = next((p for p in pages if counts[p["title"]] == 1), None)
    b = next((p for p in pages if a and p["url"] != a["url"]), None)
    if not (a and b):
        return None
    title = "<title>{0}</title>".format(html.escape(a["title"], quote=False))
    return edit(site, b, lambda t: re.sub(r"<title\b[^>]*>.*?</title>", lambda m: title, t, count=1, flags=re.I | re.S))


def rating_unsupported(site, crawl):
    p = next(iter(indexable(crawl)), None)
    tag = ('<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Product", '
           '"name": "Seeded product", "aggregateRating": {"@type": "AggregateRating", '
           '"ratingValue": "4.7", "reviewCount": "86413"}}</script>')
    return p and edit(site, p, lambda t: in_head(t, tag))


# name, function, the rule it must trigger, that rule's severity (from audit.py / schema_check.py)
MUTATIONS = [
    ("canonical-removed", canonical_removed, "S1-CANONICAL-MISSING", "medium"),
    ("noindex-added", noindex_added, "S1-INDEX-CONTRADICTION", "critical"),
    ("canonical-preview-host", canonical_preview, "S1-CANONICAL-OFFSITE", "critical"),
    ("sitemap-preview-host", sitemap_preview, "S1-HOST-MISMATCH", "critical"),
    ("robots-disallow-all", robots_disallow_all, "S1-INDEX-CONTRADICTION", "critical"),
    ("linked-page-deleted", linked_page_deleted, "S1-BROKEN-LINK", "high"),
    ("title-removed", title_removed, "S3-TITLE-MISSING", "critical"),
    ("hreflang-no-return", hreflang_no_return, "S1-HREFLANG-NO-RETURN", "high"),
    ("title-duplicated", title_duplicated, "S3-TITLE-DUPLICATE", "high"),
    ("rating-unsupported", rating_unsupported, "S5-SCHEMA-UNSUPPORTED-CLAIM", "high"),
]


# ---------------------------------------------------------------- running

def run_repo(entry, args, scripts, env):
    slug = entry["repo"].replace("/", "__")
    rows = lambda fired, target="", gate="": [
        dict(repo=entry["repo"], mutation=n, expected_rule=r, expected_severity=s,
             target=target, fired=fired, gate_failed=gate) for n, _, r, s in MUTATIONS] + [
        dict(repo=entry["repo"], mutation="control", expected_rule="", expected_severity="",
             target="", fired="", gate_failed=gate if fired != "tool_failure" else "")]
    path = replay.clone(entry, args.work)
    sha = replay.commits(path, entry["ref_cutoff"], 0)[-1]
    base_site = os.path.join(args.work, "sites", slug)
    outroot = os.path.join(args.out, slug)
    os.makedirs(outroot, exist_ok=True)
    if not os.path.isdir(base_site):
        site, _ = replay.build(entry, path, sha, os.path.join(outroot, "build.log"), env, args.timeout)
        if site is None:
            print(entry["repo"], "build failed")
            return rows("tool_failure")
        shutil.copytree(site, base_site)
    base_out = os.path.join(outroot, "base")
    os.makedirs(base_out, exist_ok=True)
    if not replay.audit(sys.executable, scripts, base_site, base_out, env)[0]:
        print(entry["repo"], "base audit failed:", replay.failure_reason({"status": "audit_failed"}, base_out))
        return rows("tool_failure")
    with open(os.path.join(base_out, "crawl.json"), encoding="utf-8") as fh:
        crawl = json.load(fh)

    out = []
    for name, fn, rule, sev in [("control", None, "", "")] + MUTATIONS:
        copy = os.path.join(args.work, "mutated", slug, name)
        shutil.rmtree(copy, ignore_errors=True)
        shutil.copytree(base_site, copy)
        row = dict(repo=entry["repo"], mutation=name, expected_rule=rule, expected_severity=sev,
                   target="", fired="", gate_failed="")
        target = fn(copy, crawl) if fn else ""
        if fn and not target:
            row["fired"] = "n/a"
        else:
            row["target"] = target
            head_out = os.path.join(outroot, name)
            os.makedirs(head_out, exist_ok=True)
            if not replay.audit(sys.executable, scripts, copy, head_out, env)[0]:
                row["fired"] = "tool_failure"
            else:
                gate, vpath = replay.compare(sys.executable, scripts, base_out, head_out, env)
                with open(vpath, encoding="utf-8") as fh:
                    v = json.load(fh)
                ids = {f["id"] for f in v["new"] + v.get("escalated", [])}
                row["gate_failed"] = "y" if gate == "fail" else "n"
                if fn:
                    row["fired"] = "y" if rule in ids else "n"
        shutil.rmtree(copy, ignore_errors=True)
        print(entry["repo"], name, row["target"], "fired", row["fired"] or "-", "gate_failed", row["gate_failed"], flush=True)
        out.append(row)
    return out


def recall_md(rows, sources):
    out = ["# Seeded-regression recall", "",
           "Generated by `bench/seeded/seeded.py` from {0}. Each mutation is applied to a copy of "
           "a repository's built output and compared with the unmutated build.".format(
               ", ".join("`{0}`".format(s) for s in sources)), "",
           "| Mutation | Expected rule | Severity | Runs | Fired | Recall | Gate failed | Not applicable | Tool failures |",
           "|---|---|---|---|---|---|---|---|---|"]
    pool_runs = pool_fired = 0
    for name, _, rule, sev in MUTATIONS:
        mine = [r for r in rows if r["mutation"] == name]
        runs = [r for r in mine if r["fired"] in ("y", "n")]
        fired = sum(r["fired"] == "y" for r in runs)
        if sev in ("critical", "high"):
            pool_runs += len(runs)
            pool_fired += fired
        out.append("| {0} | {1} | {2} | {3} | {4} | {5} | {6} | {7} | {8} |".format(
            name, rule, sev, len(runs), fired,
            "{0:.1f}%".format(100.0 * fired / len(runs)) if runs else "n/a",
            sum(r["gate_failed"] == "y" for r in runs),
            sum(r["fired"] == "n/a" for r in mine), sum(r["fired"] == "tool_failure" for r in mine)))
    out += ["", "**Recall, critical and high mutation types: {0} of {1} ({2})**".format(
        pool_fired, pool_runs, "{0:.1f}%".format(100.0 * pool_fired / pool_runs) if pool_runs else "n/a")]
    controls = [r for r in rows if r["mutation"] == "control" and r["gate_failed"] in ("y", "n")]
    out += ["", "Controls (base against an unmutated copy): {0} run, {1} passed the gate.".format(
        len(controls), sum(r["gate_failed"] == "n" for r in controls))]
    failed = sorted({r["repo"] for r in rows if r["fired"] == "tool_failure"})
    if failed:
        out += ["", "Tool failures (Addendum 1), not counted in recall: " + ", ".join(failed) + "."]
    out += ["", "Limitation: mutations change the built output, not the source, so they do not "
                "test whether a framework would produce that output."]
    return "\n".join(out) + "\n"


def write(rows, results, sources):
    with open(results, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    recall = os.path.join(os.path.dirname(os.path.abspath(results)), "recall.md")
    with open(recall, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(recall_md(rows, sources))
    print("wrote", results, "and", recall)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=os.path.join(os.path.dirname(HERE), "replay", "repos.json"))
    ap.add_argument("--work", default=os.path.join(HERE, "work"))
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--results", nargs="+", default=[os.path.join(HERE, "results.csv")])
    ap.add_argument("--only", help="comma-separated owner/name list")
    ap.add_argument("--scripts", help="sitemend's scripts folder (default: the installed package)")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds for the build")
    ap.add_argument("--list", action="store_true", help="print the selected repositories as JSON and exit")
    ap.add_argument("--report", action="store_true",
                    help="merge the --results files into the first one's folder and write recall.md")
    args = ap.parse_args(argv)

    if args.report:
        rows = []
        for p in args.results:
            with open(p, encoding="utf-8", newline="") as fh:
                rows += list(csv.DictReader(fh))
        merged = os.path.join(os.path.dirname(os.path.abspath(args.results[0])), "results.csv")
        write(rows, merged, [os.path.basename(p) for p in args.results])
        return 0

    with open(args.config, encoding="utf-8") as fh:
        repos = json.load(fh)
    repos = [e for e in repos if e["repo"] in set(args.only.split(","))] if args.only else repos[:REPOS]
    if args.list:
        print(json.dumps([{"repo": e["repo"], "slug": e["repo"].replace("/", "__"),
                           "node_version": str(e.get("node_version", ""))} for e in repos]))
        return 0
    scripts = args.scripts or replay.installed_scripts()
    os.makedirs(args.work, exist_ok=True)
    env = replay.tool_env(args.work)
    rows = []
    for entry in repos:
        rows += run_repo(entry, args, scripts, env)
    write(rows, args.results[0], [os.path.basename(args.results[0])])
    return 0


if __name__ == "__main__":
    sys.exit(main())
