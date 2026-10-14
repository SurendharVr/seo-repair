"""report.py -- merges every stage's output into the deliverables.

Emits:
  SEO-REPORT-<date>.md   the report written into the audited repo
  report-data.json       merged data, injected into assets/dashboard.html

Every claim carries a capability tier (MEASURED / INFERRED / HANDOFF). Anything this
toolchain cannot observe becomes a HANDOFF action naming the exact report to open --
never an invented number. See references/capability-tiers.md.

Usage:
  python report.py --crawl crawl.json --findings findings.json
                   [--schema schema.json] [--vitals vitals.json]
                   [--outdir .] [--site-name "My Site"]
"""
import argparse
import datetime
import os

import seolib as S

STAGE_NAMES = {
    1: "Crawl & Indexing",
    2: "Site Architecture",
    3: "Content & On-Page",
    4: "Performance & Mobile",
    5: "Structured Data",
    6: "Authority & Off-Page",
    7: "QA & Monitoring",
}

SEV_ORDER = ["critical", "high", "medium", "low"]
SEV_LABEL = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM", "low": "LOW"}

# Work this environment cannot do. Each becomes an explicit human action with the
# exact place to go -- the honesty contract in practice.
HANDOFFS = [
    ("Index coverage", "Search Console > Indexing > Pages",
     "Confirm which URLs Google has actually indexed, and why any are excluded. "
     "A local crawl proves a page is crawlable, never that it is indexed."),
    ("Field Core Web Vitals", "Search Console > Experience > Core Web Vitals",
     "Real-user LCP/CLS/INP from CrUX. The Lighthouse numbers in this report are lab "
     "measurements taken against a local server and will differ from production."),
    ("Search performance", "Search Console > Performance",
     "Actual queries, impressions, CTR and position per page. This is the only way to "
     "confirm the keyword-to-page mapping below reflects how the site really ranks."),
    ("Keyword volume & difficulty", "Ahrefs / Semrush / Keyword Planner",
     "Search volume and KD cannot be measured from a codebase. No volume or difficulty "
     "figure appears anywhere in this report because none could be observed."),
    ("Backlink profile", "Ahrefs / Search Console > Links",
     "Referring domains, anchor distribution, and toxic-link review. Off-page work is "
     "stage 6 and is gated until stages 1-5 verify clean."),
    ("Rich result eligibility", "Google Rich Results Test",
     "Paste each template URL to confirm Google grants the rich result. Local schema "
     "validation checks structure and honesty, not eligibility."),
]


def load_findings(findings_path, schema_path=None):
    """findings.json is the complete, already-merged truth.

    Schema findings are merged by audit.py (via --schema) rather than here, so that
    the gates are computed over the same set the report prints. Merging in both places
    double-reported every schema finding.
    """
    doc = S.jload(findings_path)
    if schema_path:
        raise SystemExit(
            "report.py no longer merges --schema; pass it to audit.py instead:\n"
            "  python audit.py --crawl crawl.json --schema schema.json --out findings.json")
    return doc, list(doc["findings"])


def gate_status(fdoc):
    """Gates come from audit.py -- the single definition. Re-deriving them here is
    what let the report print a status that contradicted the rule beside it."""
    gates = fdoc.get("gates")
    if gates:
        return gates
    raise SystemExit(
        "findings.json has no 'gates' block. Re-run audit.py -- this file predates "
        "gate centralisation and the report will not invent gate statuses.")


def batches(findings):
    """Group findings into stage-ordered, approvable batches."""
    out = []
    for stage in sorted(STAGE_NAMES):
        items = [f for f in findings if f["stage"] == stage]
        if not items:
            continue
        items.sort(key=lambda f: SEV_ORDER.index(f["severity"]))
        out.append({
            "id": "B{0}".format(stage),
            "stage": stage,
            "name": STAGE_NAMES[stage],
            "findings": items,
            "worst": items[0]["severity"],
        })
    return out


def md_escape(s):
    return (s or "").replace("|", "\\|").replace("\n", " ")


def build_markdown(crawl, fdoc, findings, vitals, site_name):
    m = crawl["meta"]
    today = datetime.date.today().isoformat()
    gates = gate_status(fdoc)
    bs = batches(findings)
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEV_ORDER}

    L = []
    A = L.append
    A("# SEO Report -- {0}".format(site_name))
    A("")
    A("**Generated** {0} · **Source** `{1}` · **Pages crawled** {2} · **Mode** {3}".format(
        today, m["source"], m["page_count"], m["mode"]))
    A("")
    if m.get("render"):
        rendered = m.get("rendered_pages", 0)
        failed = sum(1 for p in crawl.get("pages", []) if p.get("render_error"))
        A("**JavaScript rendering** {0} of {1} pages were parsed from Chrome's post-JavaScript DOM "
          "(limit {2}); the other {3} use the raw HTML{4}.".format(
              rendered, m["page_count"], m.get("render_limit"), m["page_count"] - rendered,
              ", {0} of them because the render failed".format(failed) if failed else ""))
        A("")
    A("> Every statement below is tagged **MEASURED** (observed directly), **INFERRED** "
      "(reasoned from observation), or **HANDOFF** (not observable here). No search "
      "volume, keyword difficulty, backlink, or field-CWV figure appears in this report, "
      "because none can be measured from a codebase.")
    A("")

    A("## Health summary")
    A("")
    A("| Severity | Count |")
    A("| --- | --- |")
    for s in SEV_ORDER:
        A("| {0} | {1} |".format(SEV_LABEL[s], counts[s]))
    A("| **Total** | **{0}** |".format(len(findings)))
    A("")

    A("### Repair gates")
    A("")
    A("The stages are ordered because the work depends on itself: there is no point "
      "optimising a title on a page Google cannot index, or building links to a page "
      "that is about to be merged away.")
    A("")
    A("| Gate | Rule | Status |")
    A("| --- | --- | --- |")
    for g in gates:
        A("| **{0}** -- {1} | {2} | {3} |".format(g["gate"], g["name"], g["rule"], g["status"]))
    A("")
    for g in gates:
        A("- **Gate {0}** ({1}): {2}".format(g["gate"], g["status"], g["detail"]))
    A("")

    intentional = fdoc.get("intentional_exclusions", [])
    if intentional:
        A("### Deliberate exclusions (not defects)")
        A("")
        for i in intentional:
            A("- `{0}` -- {1}".format(i["url"], "; ".join(i["why"])))
        A("")

    A("## Findings by stage")
    A("")
    for b in bs:
        A("### Stage {0} -- {1}".format(b["stage"], b["name"]))
        A("")
        A("Batch `{0}` · {1} finding(s) · worst severity **{2}**".format(
            b["id"], len(b["findings"]), SEV_LABEL[b["worst"]]))
        A("")
        A("| Sev | Rule | Pages | Finding | Evidence |")
        A("| --- | --- | --- | --- | --- |")
        for f in b["findings"]:
            pages = ", ".join("`{0}`".format(p) for p in f["pages"][:4])
            if len(f["pages"]) > 4:
                pages += " +{0} more".format(len(f["pages"]) - 4)
            A("| {0} | `{1}` | {2} | {3} | {4} |".format(
                SEV_LABEL[f["severity"]], f["id"], pages,
                md_escape(f["message"]),
                md_escape((f.get("evidence") or "")[:100])))
        A("")

    if vitals and vitals.get("runs"):
        A("## Core Web Vitals (MEASURED -- lab)")
        A("")
        A("> {0}".format(vitals["caveat"]))
        A("")
        A("| Page | Profile | LCP | CLS | TBT | Perf score |")
        A("| --- | --- | --- | --- | --- | --- |")
        for r in vitals["runs"]:
            mm = r["metrics"]
            A("| `{0}` | {1} | {2} | {3} | {4} | {5} |".format(
                r["page"], r["profile"],
                _ms(mm.get("largest-contentful-paint")),
                _n(mm.get("cumulative-layout-shift")),
                _ms(mm.get("total-blocking-time")),
                _pct(mm.get("_score"))))
        A("")
        opps = {}
        for r in vitals["runs"]:
            for o in r.get("opportunities", []):
                opps[o["title"]] = max(opps.get(o["title"], 0), o["savings_ms"])
        if opps:
            A("**Top opportunities** (estimated savings, lab):")
            A("")
            for t, sv in sorted(opps.items(), key=lambda kv: -kv[1])[:6]:
                A("- {0} -- ~{1} ms".format(t, sv))
            A("")

    A("## Keyword to page map")
    A("")
    A("One primary keyword and one search intent per important page. Fill the two right "
      "columns from live SERP research (see `references/serp-research.md`) or from Search "
      "Console. **Volume and difficulty are deliberately absent** -- they cannot be "
      "measured here, and a guessed number is worse than no number.")
    A("")
    A("| Page | Current title | Primary keyword | Intent |")
    A("| --- | --- | --- | --- |")
    for p in crawl["pages"]:
        if p.get("noindex") or p.get("robots_allowed") is False:
            continue
        A("| `{0}` | {1} | _to confirm_ | _to confirm_ |".format(
            p["url"], md_escape((p.get("title") or "(none)")[:60])))
    A("")

    A("## Human action list (HANDOFF)")
    A("")
    A("These require tools this environment cannot reach. Each names exactly where to go.")
    A("")
    for name, where, why in HANDOFFS:
        A("- **{0}** -- {1}".format(name, where))
        A("  - {0}".format(why))
    A("")

    A("## How to re-run")
    A("")
    A("```bash")
    A("python sitemend.py audit \"{0}\" --out rerun".format(m["source"]))
    A("python sitemend.py verify \"{0}\" --baseline findings.json".format(m["source"]))
    A("```")
    A("")
    A("_Generated by the `sitemend` skill. Findings are evidence-backed; nothing in "
      "this report was inferred from data the tool could not observe._")
    return "\n".join(L)


def _ms(v):
    return "-" if v is None else "{0:.0f} ms".format(v)


def _n(v):
    return "-" if v is None else "{0:.3f}".format(v)


def _pct(v):
    return "-" if v is None else "{0:.0f}".format(v * 100)


def stage5_ran(fdoc, findings):
    """Stage 5 ran if audit.py was given schema_check output (recorded in meta), or,
    for a findings.json from before that field existed, if any stage-5 finding is
    present. A clean schema run produces no findings and was once shown as unmeasured."""
    return bool(fdoc.get("meta", {}).get("schema_checked")) or \
        any(f["stage"] == 5 for f in findings)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crawl", required=True)
    ap.add_argument("--findings", required=True)
    ap.add_argument("--schema")
    ap.add_argument("--vitals")
    ap.add_argument("--outdir", default=".", help="where SEO-REPORT-<date>.md goes (the repo)")
    ap.add_argument("--data-out", help="where report-data.json goes (default: alongside "
                                       "--findings, i.e. the scratchpad -- NOT the repo)")
    ap.add_argument("--site-name", default=None)
    args = ap.parse_args()

    crawl = S.jload(args.crawl)
    fdoc, findings = load_findings(args.findings, args.schema)
    vitals = S.jload(args.vitals) if args.vitals and os.path.isfile(args.vitals) else None
    site = args.site_name or os.path.basename(crawl["meta"]["source"].rstrip("\\/"))

    md = build_markdown(crawl, fdoc, findings, vitals, site)
    today = datetime.date.today().isoformat()
    md_path = os.path.join(args.outdir, "SEO-REPORT-{0}.md".format(today))
    os.makedirs(args.outdir, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    # Which stages were actually evaluated in this run. A stage that produced no
    # findings because it never ran is NOT "clear", and labelling it so would be the
    # same unearned confidence this tool refuses everywhere else.
    s5 = stage5_ran(fdoc, findings)
    assessed = {
        1: {"state": "assessed"},
        2: {"state": "assessed"},
        3: {"state": "assessed"},
        4: {"state": "assessed"} if (vitals and vitals.get("runs"))
           else {"state": "not-measured", "note": "Lighthouse was not run"},
        5: {"state": "assessed"} if s5
           else {"state": "not-measured", "note": "schema_check.py output was not supplied"},
        6: {"state": "advisory", "note": "off-page work cannot be measured from a codebase"},
        7: {"state": "not-run", "note": "populated by verify against a baseline"},
    }

    data = {
        "site": site,
        "assessed": {str(k): v for k, v in assessed.items()},
        "generated": today,
        "meta": crawl["meta"],
        "counts": {s: sum(1 for x in findings if x["severity"] == s) for s in SEV_ORDER},
        "gates": gate_status(fdoc),
        "batches": [{k: v for k, v in b.items()} for b in batches(findings)],
        "findings": findings,
        "intentional": fdoc.get("intentional_exclusions", []),
        "pages": [{"url": p["url"], "title": p.get("title"), "words": p["word_count"],
                   "indexable": not (p.get("noindex") or p.get("robots_allowed") is False),
                   "jsonld": p.get("jsonld_types", [])}
                  for p in crawl["pages"]],
        "vitals": vitals,
        "handoffs": [{"name": n, "where": w, "why": y} for n, w, y in HANDOFFS],
    }
    # The report is the only artifact that belongs in the user's repo; the data file
    # is a run artifact and defaults to the scratchpad next to findings.json.
    data_path = args.data_out or os.path.join(
        os.path.dirname(os.path.abspath(args.findings)), "report-data.json")
    S.jdump(data, data_path)

    print("wrote {0}".format(md_path))
    print("wrote {0}".format(data_path))
    print("{0} findings across {1} batches".format(len(findings), len(data["batches"])))
    for g in data["gates"]:
        print("  gate {0} ({1}): {2}".format(g["gate"], g["name"], g["status"]))


if __name__ == "__main__":
    main()
