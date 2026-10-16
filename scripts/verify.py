"""verify.py -- stage 7. Diffs a fresh findings.json against the baseline.

The regression gate: a batch is only "done" when the findings it targeted are closed
AND no new findings appeared. Fixing a title while silently orphaning a page is not a
win, and only a re-crawl can tell the difference.

Usage:
  python verify.py --baseline baseline/findings.json --current current/findings.json

Next to --out it also writes a .md summary of the new and closed findings, for a
pull-request comment.
"""
import argparse
import os
import sys

import seolib as S

SEV_ORDER = ["critical", "high", "medium", "low"]


def key(f):
    """Identity of a finding across runs: same rule, same evidence, same pages."""
    return (f["id"], f.get("evidence"), tuple(sorted(f.get("pages", []))))


# Rules whose evidence names the defect itself: the broken target, the canonical URL.
# The same rule on the same page with different evidence is a different defect, so
# fixing href="/a" while adding href="/b" is one closed and one new, never one changed.
EVIDENCE_IS_IDENTITY = {"S1-CANONICAL-OFFSITE", "S1-CANONICAL-BROKEN", "S1-CANONICAL-CROSS",
                        "S1-HREFLANG-BROKEN"}


def identity(f):
    """What makes this finding the defect it is, beyond its rule and pages. None for
    rules whose evidence is a measurement (counts, similarity) that may drift."""
    if f["id"] == "S1-BROKEN-LINK":
        # One finding per target; its evidence quotes whichever href was seen first.
        return (f.get("detail") or {}).get("target") or f.get("evidence")
    if f["id"] in EVIDENCE_IS_IDENTITY:
        return f.get("evidence")
    return None


def related(a, b):
    """Same rule, overlapping pages, same defect. Matches a finding across runs even when
    its evidence or page list changed -- which happens routinely, because a grouped
    finding's page list shrinks as pages get fixed or removed. Without this, every
    partial fix reports as one finding closed plus one brand-new finding, which is
    both wrong and alarming."""
    return (a["id"] == b["id"] and bool(set(a.get("pages", [])) & set(b.get("pages", [])))
            and identity(a) == identity(b))


def worse(f, than):
    """f is critical/high and more severe than the baseline finding it matched."""
    return (f["severity"] in ("critical", "high")
            and SEV_ORDER.index(f["severity"]) < SEV_ORDER.index(than["severity"]))


def blocks(f):
    """Whether a new or worsened finding may fail the gate: critical or high, and
    measured. A finding audit.py marks below high confidence is a judgement call --
    two docs pages "competing" for the same terms, two blog posts "near-duplicate" --
    and adding an ordinary sibling page must not fail a pull request over one. Those
    are reported in the comment as advisory, never as the reason the check failed."""
    return f["severity"] in ("critical", "high") and f.get("confidence", "high") == "high"


MARKER = "<!-- sitemend-verify -->"


def cell(v, limit=200):
    """One markdown table cell, quoting the value as-is apart from what breaks a table
    or would render as HTML (evidence is often markup, like `<source>`)."""
    t = " ".join(str(v if v is not None else "").split())
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace("|", "\\|")
    return t if len(t) <= limit else t[:limit - 1] + "…"


def markdown(doc, rows=50):
    """verify.json as a PR comment. Every number is a count of findings in doc; the
    evidence column quotes what audit.py recorded."""
    c = doc["counts"]
    out = [MARKER, "## sitemend: regression gate {0}".format(doc["gate"]), "",
           "{new} new, {closed} closed, {changed} changed, {persisting} unchanged "
           "compared with the base branch.".format(**c)]

    def table(title, items, note=""):
        if not items:
            return
        items = sorted(items, key=lambda f: SEV_ORDER.index(f["severity"]))
        out.extend(["", "### " + title + note, "",
                    "| Severity | Finding | Pages | Evidence |", "|---|---|---|---|"])
        for f in items[:rows]:
            pages = f.get("pages", [])
            p = ", ".join(pages[:5]) + (" +{0} more".format(len(pages) - 5) if len(pages) > 5 else "")
            sev = f["severity"] + (" (advisory)" if f in doc.get("advisory", ()) else "")
            out.append("| {0} | {1} | {2} | {3} |".format(
                sev, cell(f["id"] + ": " + f.get("message", "")), cell(p),
                cell(f.get("evidence"))))
        if len(items) > rows:
            out.append("\n...and {0} more in verify.json.".format(len(items) - rows))

    table("New", doc["new"], " (measured critical and high block the merge)" if doc["regressions"] else "")
    table("Got worse", doc.get("escalated", []), " (measured ones block the merge)")
    if doc.get("advisory"):
        out.extend(["", "Advisory findings are judgement calls (for example two pages that look "
                        "like they target the same terms). They are worth a look but never fail "
                        "the check."])
    table("Closed", doc["closed"])
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--current", required=True)
    ap.add_argument("--out", default="verify.json")
    args = ap.parse_args()

    base = S.jload(args.baseline)["findings"]
    cur = S.jload(args.current)["findings"]

    bk = {key(f): f for f in base}
    ck = {key(f): f for f in cur}

    persisting = [f for k, f in ck.items() if k in bk]
    escalated = [f for k, f in ck.items() if k in bk and worse(f, bk[k])]
    cur_unmatched = [f for k, f in ck.items() if k not in bk]
    base_unmatched = [f for k, f in bk.items() if k not in ck]

    # Pair up what's left by rule + page overlap: those are the same finding in a
    # changed form, not a closure plus a new problem.
    changed, new = [], []
    paired_base = set()
    for f in cur_unmatched:
        match = next((i for i, b in enumerate(base_unmatched)
                      if i not in paired_base and related(b, f)), None)
        if match is None:
            new.append(f)
        else:
            paired_base.add(match)
            changed.append(f)
            if worse(f, base_unmatched[match]):
                escalated.append(f)
    closed = [b for i, b in enumerate(base_unmatched) if i not in paired_base]

    def bysev(items):
        return {s: sum(1 for f in items if f["severity"] == s) for s in SEV_ORDER}

    regressions = [f for f in new + escalated if blocks(f)]
    advisory = [f for f in new + escalated if f["severity"] in ("critical", "high") and not blocks(f)]
    gate = "PASS" if not regressions else "FAIL"

    doc = {
        "gate": gate,
        "counts": {"closed": len(closed), "new": len(new),
                   "changed": len(changed), "persisting": len(persisting)},
        "closed": closed, "new": new, "changed": changed,
        "escalated": escalated,
        "regressions": regressions,
        "advisory": advisory,
    }
    S.jdump(doc, args.out)
    md_path = os.path.splitext(args.out)[0] + ".md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown(doc))

    print("REGRESSION GATE: " + gate)
    print("  closed:     {0:3d}  {1}".format(len(closed), bysev(closed)))
    print("  new:        {0:3d}  {1}".format(len(new), bysev(new)))
    print("  changed:    {0:3d}".format(len(changed)))
    print("  persisting: {0:3d}  {1}".format(len(persisting), bysev(persisting)))
    if closed:
        print("\nClosed:")
        for f in sorted(closed, key=lambda x: SEV_ORDER.index(x["severity"]))[:20]:
            print("  [{0}] {1} {2}".format(f["severity"], f["id"], ", ".join(f["pages"])[:50]))
    if regressions:
        print("\nREGRESSIONS introduced (gate blocks on these):")
        for f in regressions:
            print("  [{0}] {1} {2}".format(f["severity"], f["id"], ", ".join(f["pages"])[:50]))
            print("       {0}".format(f["message"][:100]))
    print("\nwrote " + args.out)
    return 0 if gate == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
