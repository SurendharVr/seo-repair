"""verify_helper.py -- Task 6 of the evidence plan: pre-fill bench/ledger.csv and show the evidence.

  python bench/verify_helper.py                       # bench/replay/results.csv, audits beside it
  python bench/verify_helper.py --results bench/replay/pilot/results.csv --audits DIR

Adds one ledger row per blocking finding (the gate's regressions) that is not in the
ledger yet, then prints the raw evidence for every row that still has no verdict: the
finding as audited, how each page it names changed between base and head in the crawl,
robots.txt and sitemap state for rules about them, and the GitHub compare link for the
source diff.

It never writes a verdict. Precision is the product, and whether a finding is real is a
person's call: fill in verdict (true_regression, false_positive, true_but_preexisting or
unclear), verified_by and verification_method by hand.
"""
import argparse
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FIELDS = ["repo", "head_sha", "rule_id", "severity", "url", "evidence_excerpt", "verdict",
          "verified_by", "verification_method", "notes"]
# Page fields worth comparing; text bodies and fingerprints are too large to read.
PAGE_FIELDS = ["status", "title", "meta_description", "canonical", "robots_meta", "noindex",
               "robots_allowed", "robots_rule", "h1", "lang", "hreflang", "jsonld_types",
               "jsonld_errors", "word_count", "og"]


def key(row):
    """One ledger row per blocking finding. The same rule on the same page with different
    evidence (seven broken language links) is seven findings, and precision counts them."""
    return row["repo"], row["head_sha"], row["rule_id"], row["url"], row["evidence_excerpt"]


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def page_diff(base, head, url):
    """Lines describing how one page's crawl record changed from base to head."""
    find = lambda crawl: next((p for p in (crawl or {}).get("pages", []) if p.get("url") == url), None)
    b, h = find(base), find(head)
    if h is None:
        return ["  {0}: not a page in head".format(url)]
    if b is None:
        return ["  {0}: new in head (not in base)".format(url)]
    lines = ["  {0}: {1}: {2!r} -> {3!r}".format(url, k, b.get(k), h.get(k))
             for k in PAGE_FIELDS if b.get(k) != h.get(k)]
    links = lambda p: {l.get("resolved") or l.get("href") for l in p.get("links") or [] if isinstance(l, dict)}
    gone, added = links(b) - links(h), links(h) - links(b)
    if gone or added:
        lines.append("  {0}: links removed {1}, added {2}".format(url, sorted(gone)[:10], sorted(added)[:10]))
    return lines or ["  {0}: no difference in the compared fields".format(url)]


def evidence(row, finding, base, head, base_sha):
    out = ["=" * 78,
           "{0} {1}  {2} ({3})  {4}".format(row["repo"], row["head_sha"][:10], row["rule_id"],
                                            row["severity"], row["url"]),
           "  message:  " + str(finding.get("message", "")),
           "  evidence: " + str(finding.get("evidence", ""))]
    if finding.get("detail"):
        out.append("  detail:   " + json.dumps(finding["detail"])[:500])
    for url in finding.get("pages", []):
        out += page_diff(base, head, url)
    if any(k in row["rule_id"] for k in ("ROBOTS", "SITEMAP", "INDEX")):
        for key in ("robots", "sitemap"):
            b, h = (base or {}).get(key), (head or {}).get(key)
            out.append("  {0}: {1}".format(key, "unchanged" if b == h else
                                           "base {0} -> head {1}".format(json.dumps(b)[:300], json.dumps(h)[:300])))
    out.append("  source diff: https://github.com/{0}/compare/{1}...{2}".format(
        row["repo"], base_sha, row["head_sha"]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", default=os.path.join(HERE, "replay", "results.csv"))
    ap.add_argument("--audits", help="folder that holds out/ (default: beside --results)")
    ap.add_argument("--ledger", default=os.path.join(HERE, "ledger.csv"))
    args = ap.parse_args(argv)
    audits = args.audits or os.path.dirname(os.path.abspath(args.results))

    ledger = []
    if os.path.exists(args.ledger):
        with open(args.ledger, encoding="utf-8", newline="") as fh:
            ledger = list(csv.DictReader(fh))
    known = {key(r) for r in ledger}

    with open(args.results, encoding="utf-8", newline="") as fh:
        failed = [r for r in csv.DictReader(fh) if r["gate_result"] == "fail"]
    shown, added = [], 0
    for r in failed:
        verify_path = os.path.join(audits, r["new_findings_json_path"])
        out_dir = os.path.dirname(os.path.dirname(verify_path))
        crawl = lambda sha: (load(os.path.join(out_dir, sha, "crawl.json"))
                             if os.path.exists(os.path.join(out_dir, sha, "crawl.json")) else None)
        base, head = crawl(r["base_sha"]), crawl(r["head_sha"])
        for f in load(verify_path)["regressions"]:
            row = {"repo": r["repo"], "head_sha": r["head_sha"], "rule_id": f["id"],
                   "severity": f["severity"], "url": " ".join(f.get("pages", [])),
                   "evidence_excerpt": " ".join(str(f.get("evidence") or "").split())[:200]}
            k = key(row)
            if k not in known:
                ledger.append(dict({x: "" for x in FIELDS}, **row))
                known.add(k)
                added += 1
            if not next(x for x in ledger if key(x) == k)["verdict"]:
                shown += evidence(row, f, base, head, r["base_sha"])

    with open(args.ledger, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows({k: x.get(k, "") for k in FIELDS} for x in ledger)
    print("\n".join(shown))
    pending = sum(1 for x in ledger if not x["verdict"])
    print("\n{0} row(s) added; {1} of {2} still need a verdict in {3}".format(
        added, pending, len(ledger), args.ledger))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
