"""audit.py -- turns crawl.json into evidence-backed findings, tagged by stage.

Every finding carries: id, stage, severity, pages, evidence (the actual offending
string from the source), recipe (a fix-recipes.md key), and confidence. A finding
without evidence is a guess, and this file must not emit guesses.

Usage:
  python audit.py --crawl crawl.json [--out findings.json] [--vitals vitals.json]
"""
import argparse
import json
import re
from collections import defaultdict

import seolib as S

# Thresholds. Deliberately conservative: a false positive costs the user's trust,
# and every one of these is defensible from public guidance rather than folklore.
TITLE_MIN, TITLE_MAX = 30, 60
TITLE_FLAG = 65          # target is 50-60; only flag past the point Google actually truncates
DESC_MIN, DESC_MAX = 70, 160
THIN_WORDS = 250
NEAR_DUP = 0.80          # minhash Jaccard estimate
CANNIBAL_TERMS = 0.60
IMG_HEAVY_BYTES = 200 * 1024
MAX_DEPTH = 3

SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3}

STOP = set("""a an the and or but of for to in on at by with from as is are was were be been being
this that these those it its it's you your we our they their he she his her i me my will can could
would should has have had do does did not no if then than so such about into over under more most
very just also only own same too s t don now here there all any both each few other some what which
who whom why how when where new best top get got make made use used using one two three""".split())


def finding(fid, stage, severity, message, pages, evidence=None, recipe=None,
            confidence="high", detail=None):
    return {
        "id": fid, "stage": stage, "severity": severity, "message": message,
        "pages": pages if isinstance(pages, list) else [pages],
        "evidence": evidence, "recipe": recipe, "confidence": confidence,
        "detail": detail or {},
    }


def top_terms(text, n=15):
    words = [w for w in re.findall(r"[a-z][a-z0-9\-]{2,}", (text or "").lower()) if w not in STOP]
    freq = defaultdict(int)
    for w in words:
        freq[w] += 1
    return {w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:n]}


UTILITY_RE = re.compile(r"/(404|403|500|offline|thank-?you|thanks)(\.html?)?/?$", re.I)


def is_utility(url):
    """404/error/thank-you pages. A noindex here is correct, not a defect."""
    return bool(UTILITY_RE.search(url or ""))


def path_of(url_or_path):
    if url_or_path and url_or_path.startswith("http"):
        from urllib.parse import urlsplit
        return S.norm_path(urlsplit(url_or_path).path or "/")
    return S.norm_path(url_or_path)


def build_index(doc):
    """alias -> canonical page url, plus the asset path set."""
    alias = {}
    for p in doc["pages"]:
        for a in p.get("aliases") or [p["url"]]:
            alias.setdefault(S.norm_path(a), p["url"])
        alias.setdefault(S.norm_path(p["url"]), p["url"])
    assets = {S.norm_path("/" + a["path"]): a for a in doc.get("assets", [])}
    return alias, assets


def audit(doc, vitals=None, schema_findings=None):
    pages = doc["pages"]
    by_url = {p["url"]: p for p in pages}
    alias, assets = build_index(doc)
    base = (doc["meta"].get("base_url") or "").rstrip("/")
    out = []
    intentional = []   # deliberate exclusions, reported as confirmations not defects

    indexable = {}
    for p in pages:
        reasons = []
        if p.get("noindex"):
            reasons.append("meta robots noindex")
        if p.get("robots_allowed") is False:
            reasons.append("robots.txt " + str(p.get("robots_rule")))
        if p.get("x_robots_tag") and "noindex" in str(p["x_robots_tag"]).lower():
            reasons.append("X-Robots-Tag noindex")
        indexable[p["url"]] = (not reasons, reasons)

    # ---------------------------------------------------------- STAGE 1: index
    if not doc["robots"]["found"]:
        out.append(finding("S1-ROBOTS-MISSING", 1, "high",
                           "No robots.txt found at site root.", ["/"],
                           evidence="robots.txt absent", recipe="robots-create"))
    if not doc["sitemap"]["found"]:
        out.append(finding("S1-SITEMAP-MISSING", 1, "high",
                           "No sitemap.xml found at site root.", ["/"],
                           evidence="sitemap.xml absent", recipe="sitemap-create"))
    elif doc["robots"]["found"] and not doc["robots"]["sitemaps"]:
        out.append(finding("S1-ROBOTS-NO-SITEMAP", 1, "medium",
                           "robots.txt does not declare a Sitemap: line.", ["/"],
                           evidence=doc["robots"]["raw"][:200], recipe="robots-add-sitemap"))

    sitemap_paths = {path_of(u) for u in doc["sitemap"]["urls"]}

    for sp in sorted(sitemap_paths):
        if sp not in alias:
            out.append(finding("S1-SITEMAP-ORPHAN", 1, "high",
                               "URL listed in sitemap.xml does not exist in the crawl.", [sp],
                               evidence="<loc>{0}</loc>".format(sp), recipe="sitemap-prune"))
    for p in pages:
        ok, why = indexable[p["url"]]
        if ok and p["url"] not in sitemap_paths and doc["sitemap"]["found"]:
            if not any(a in sitemap_paths for a in (p.get("aliases") or [])):
                out.append(finding("S1-SITEMAP-GAP", 1, "medium",
                                   "Indexable page is missing from sitemap.xml.", [p["url"]],
                                   evidence=p["file"] or p["url"], recipe="sitemap-add"))
        if not ok:
            in_sm = p["url"] in sitemap_paths
            deliberate = is_utility(p["url"]) or p.get("robots_allowed") is False
            if in_sm:
                # Listed in the sitemap AND blocked: the site is contradicting itself.
                out.append(finding("S1-INDEX-CONTRADICTION", 1, "critical",
                                   "Page is in sitemap.xml but is not indexable: " + "; ".join(why),
                                   [p["url"]], evidence="; ".join(why), recipe="indexability-review"))
            elif not deliberate:
                out.append(finding("S1-NOT-INDEXABLE", 1, "medium",
                                   "Page is not indexable: " + "; ".join(why), [p["url"]],
                                   evidence="; ".join(why), recipe="indexability-review"))
            else:
                intentional.append({"url": p["url"], "why": why})

    # canonicals -- only meaningful for pages meant to be indexed
    for p in pages:
        if not indexable[p["url"]][0]:
            continue
        c = p.get("canonical")
        if not c:
            out.append(finding("S1-CANONICAL-MISSING", 1, "medium",
                               "No canonical link element.", [p["url"]],
                               evidence=p["file"] or p["url"], recipe="canonical-add"))
            continue
        cpath = path_of(c)
        if base and c.startswith("http") and not c.startswith(base):
            out.append(finding("S1-CANONICAL-OFFSITE", 1, "critical",
                               "Canonical points to a different origin.", [p["url"]],
                               evidence=c, recipe="canonical-fix"))
        elif cpath not in alias:
            out.append(finding("S1-CANONICAL-BROKEN", 1, "critical",
                               "Canonical points to a URL that does not exist.", [p["url"]],
                               evidence=c, recipe="canonical-fix"))
        elif alias[cpath] != p["url"]:
            out.append(finding("S1-CANONICAL-CROSS", 1, "high",
                               "Canonical points at a different page (self-canonical expected).",
                               [p["url"]], evidence=c, recipe="canonical-fix",
                               detail={"points_to": alias[cpath]}))

    # broken internal links
    broken = defaultdict(list)
    for p in pages:
        for l in p["links"]:
            if l["kind"] != "internal":
                continue
            t = S.norm_path(l["resolved"])
            if t in alias or t in assets:
                continue
            if re.search(r"\.(css|js|png|jpe?g|webp|svg|ico|pdf|txt|xml|woff2?|mp4|webm)$", t, re.I):
                continue
            broken[t].append({"from": p["url"], "anchor": l["anchor"], "href": l["href"]})
    for target, refs in sorted(broken.items()):
        out.append(finding("S1-BROKEN-LINK", 1, "high",
                           "Internal link points to a URL that does not exist ({0} reference(s)).".format(len(refs)),
                           sorted({r["from"] for r in refs}),
                           evidence='href="{0}" -> {1}'.format(refs[0]["href"], target),
                           recipe="broken-link-fix", detail={"target": target, "refs": refs[:10]}))

    # URL hygiene
    conventions = set()
    for p in pages:
        u = p["url"]
        if u == "/" or is_utility(u):
            continue
        conventions.add("flat" if u.endswith(".html") else "clean")
        bad = []
        if re.search(r"[A-Z]", u):
            bad.append("uppercase")
        if "_" in u:
            bad.append("underscore")
        if re.search(r"\s|%20", u):
            bad.append("space")
        if bad:
            out.append(finding("S1-URL-UNCLEAN", 1, "medium",
                               "URL is not clean/descriptive: " + ", ".join(bad), [u],
                               evidence=u, recipe="url-rename"))
    if len(conventions) > 1:
        out.append(finding("S1-URL-MIXED-CONVENTION", 1, "medium",
                           "Site mixes clean-URL and .html URL conventions.",
                           sorted(p["url"] for p in pages if p["url"] != "/" and not is_utility(p["url"])),
                           evidence=", ".join(sorted(conventions)), recipe="url-normalize",
                           confidence="medium"))

    # ---------------------------------------------------- STAGE 2: architecture
    # GATE A: from here on, only pages stage 1 says are indexable are examined.
    # Reporting a missing H1 on a noindex page is noise that buries the one finding
    # that matters for it -- why it is not indexable.
    live = [p for p in pages if indexable[p["url"]][0]]

    inlinks = defaultdict(set)
    for p in pages:
        for l in p["links"]:
            if l["kind"] != "internal":
                continue
            t = S.norm_path(l["resolved"])
            if t in alias and alias[t] != p["url"]:
                inlinks[alias[t]].add(p["url"])

    home = "/" if "/" in by_url else (pages[0]["url"] if pages else None)

    # click depth from home
    depth = {home: 0} if home else {}
    frontier = [home] if home else []
    while frontier:
        nxt = []
        for u in frontier:
            for l in by_url[u]["links"]:
                if l["kind"] != "internal":
                    continue
                t = S.norm_path(l["resolved"])
                if t in alias and alias[t] not in depth:
                    depth[alias[t]] = depth[u] + 1
                    nxt.append(alias[t])
        frontier = nxt

    for p in pages:
        u = p["url"]
        if u == home:
            continue
        ok, _ = indexable[u]
        if not inlinks[u] and ok:
            out.append(finding("S2-ORPHAN", 2, "high",
                               "Orphan page: no internal links point to it.", [u],
                               evidence="0 inbound internal links", recipe="internal-link-add"))
        elif ok and len(inlinks[u]) == 1 and len(pages) > 3:
            out.append(finding("S2-LOW-INLINKS", 2, "low",
                               "Only one internal link points to this page.", [u],
                               evidence="1 inbound link from " + list(inlinks[u])[0],
                               recipe="internal-link-add", confidence="medium"))
        if u not in depth and ok:
            out.append(finding("S2-UNREACHABLE", 2, "high",
                               "Page is not reachable by following links from the homepage.", [u],
                               evidence="no path from " + str(home), recipe="internal-link-add"))
        elif depth.get(u, 0) > MAX_DEPTH:
            out.append(finding("S2-DEEP", 2, "medium",
                               "Page is {0} clicks from the homepage.".format(depth[u]), [u],
                               evidence="click depth {0}".format(depth[u]), recipe="internal-link-add"))

    # headings
    for p in live:
        h1s = p["h1"]
        if len(h1s) == 0:
            out.append(finding("S2-H1-MISSING", 2, "high", "Page has no H1.", [p["url"]],
                               evidence=p["file"] or p["url"], recipe="heading-fix"))
        elif len(h1s) > 1:
            out.append(finding("S2-H1-MULTIPLE", 2, "medium",
                               "Page has {0} H1 elements.".format(len(h1s)), [p["url"]],
                               evidence=" | ".join(h1s[:3]), recipe="heading-fix"))
        levels = [h["level"] for h in p["headings"]]
        for a, b in zip(levels, levels[1:]):
            if b > a + 1:
                idx = levels.index(b, levels.index(a) if a in levels else 0)
                out.append(finding("S2-HEADING-SKIP", 2, "low",
                                   "Heading level jumps from H{0} to H{1}.".format(a, b), [p["url"]],
                                   evidence="H{0} -> H{1}: {2}".format(a, b, p["headings"][min(idx, len(p['headings'])-1)]["text"][:80]),
                                   recipe="heading-fix"))
                break

    # breadcrumbs
    for p in live:
        if p["url"] == home:
            continue
        has_bc = "BreadcrumbList" in p["jsonld_types"]
        if not has_bc and depth.get(p["url"], 0) >= 1:
            out.append(finding("S2-BREADCRUMB-MISSING", 2, "low",
                               "No BreadcrumbList structured data on a non-home page.", [p["url"]],
                               evidence="jsonld types: " + (", ".join(p["jsonld_types"]) or "none"),
                               recipe="breadcrumb-add", confidence="medium"))

    # ------------------------------------------------- STAGE 3: content/on-page
    # exact + near duplicates
    by_fp = defaultdict(list)
    for p in live:
        if p["word_count"] > 0:
            by_fp[p["text_fingerprint"]].append(p["url"])
    for fp, urls in by_fp.items():
        if len(urls) > 1:
            out.append(finding("S3-DUPLICATE-EXACT", 3, "critical",
                               "{0} pages have byte-identical main content.".format(len(urls)),
                               sorted(urls), evidence="identical text fingerprint " + fp[:12],
                               recipe="consolidate-pages"))
    seen_pairs = {frozenset(u) for u in by_fp.values() if len(u) > 1}
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if frozenset((a["url"], b["url"])) in seen_pairs:
                continue
            if a["word_count"] < 50 or b["word_count"] < 50:
                continue
            sim = S.minhash_similarity(a.get("minhash"), b.get("minhash"))
            if sim >= NEAR_DUP:
                seen_pairs.add(frozenset((a["url"], b["url"])))
                out.append(finding("S3-NEAR-DUPLICATE", 3, "high",
                                   "Pages are {0}% similar in main content.".format(int(sim * 100)),
                                   [a["url"], b["url"]],
                                   evidence="estimated Jaccard {0:.2f}".format(sim),
                                   recipe="consolidate-pages", confidence="medium"))

    # cannibalization: same intent signals on two pages
    sigs = {}
    for p in live:
        blob = " ".join([p.get("title") or "", " ".join(p["h1"]),
                         p.get("meta_description") or ""])
        sigs[p["url"]] = top_terms(blob, 12)
    urls = [p["url"] for p in live]
    for i, a in enumerate(urls):
        for b in urls[i + 1:]:
            if frozenset((a, b)) in seen_pairs:
                continue          # already reported as a duplicate; same root cause
            if not sigs[a] or not sigs[b]:
                continue
            inter = sigs[a] & sigs[b]
            union = sigs[a] | sigs[b]
            j = len(inter) / len(union) if union else 0
            if j >= CANNIBAL_TERMS:
                out.append(finding("S3-CANNIBALIZATION", 3, "high",
                                   "Two pages target near-identical head terms ({0}% overlap).".format(int(j * 100)),
                                   [a, b], evidence="shared terms: " + ", ".join(sorted(inter)[:8]),
                                   recipe="keyword-map", confidence="medium"))

    titles, descs = defaultdict(list), defaultdict(list)
    for p in live:
        u = p["url"]
        t, d = p.get("title"), p.get("meta_description")
        if not t:
            out.append(finding("S3-TITLE-MISSING", 3, "critical", "Page has no <title>.", [u],
                               evidence=p["file"] or u, recipe="title-write"))
        else:
            titles[t].append(u)
            if p["title_len"] > TITLE_FLAG:
                out.append(finding("S3-TITLE-LONG", 3, "low",
                                   "Title is {0} chars (target {1}-{2}).".format(p["title_len"], TITLE_MIN, TITLE_MAX),
                                   [u], evidence=t, recipe="title-write"))
            elif p["title_len"] < TITLE_MIN:
                out.append(finding("S3-TITLE-SHORT", 3, "medium",
                                   "Title is only {0} chars.".format(p["title_len"]), [u],
                                   evidence=t, recipe="title-write"))
        if not d:
            out.append(finding("S3-DESC-MISSING", 3, "high", "Page has no meta description.", [u],
                               evidence=p["file"] or u, recipe="description-write"))
        else:
            descs[d].append(u)
            if p["desc_len"] > DESC_MAX:
                out.append(finding("S3-DESC-LONG", 3, "low",
                                   "Meta description is {0} chars (target {1}-{2}).".format(p["desc_len"], DESC_MIN, DESC_MAX),
                                   [u], evidence=d[:180], recipe="description-write"))
            elif p["desc_len"] < DESC_MIN:
                out.append(finding("S3-DESC-SHORT", 3, "low",
                                   "Meta description is only {0} chars.".format(p["desc_len"]), [u],
                                   evidence=d, recipe="description-write"))
    for t, us in titles.items():
        if len(us) > 1:
            out.append(finding("S3-TITLE-DUPLICATE", 3, "high",
                               "{0} pages share the same <title>.".format(len(us)), sorted(us),
                               evidence=t, recipe="title-write"))
    for d, us in descs.items():
        if len(us) > 1:
            out.append(finding("S3-DESC-DUPLICATE", 3, "medium",
                               "{0} pages share the same meta description.".format(len(us)), sorted(us),
                               evidence=d[:180], recipe="description-write"))

    for p in live:
        if 0 < p["word_count"] < THIN_WORDS:
            out.append(finding("S3-THIN", 3, "medium",
                               "Main content is only {0} words.".format(p["word_count"]), [p["url"]],
                               evidence="{0} words in main content".format(p["word_count"]),
                               recipe="content-expand", confidence="medium"))
        if not p.get("lang"):
            out.append(finding("S3-LANG-MISSING", 3, "low", "No lang attribute on <html>.", [p["url"]],
                               evidence="<html> without lang", recipe="lang-add"))
        if not p.get("viewport"):
            out.append(finding("S3-VIEWPORT-MISSING", 3, "critical",
                               "No viewport meta tag -- page will not be mobile-friendly.", [p["url"]],
                               evidence="meta viewport absent", recipe="viewport-add"))

        for img in p["images"]:
            src = img.get("src") or "(no src)"
            is_img = img.get("kind", "img") == "img"
            if is_img and not img["has_alt_attr"]:
                out.append(finding("S3-IMG-ALT-MISSING", 3, "medium",
                                   "Image has no alt attribute.", [p["url"]],
                                   evidence='<img src="{0}">'.format(src), recipe="alt-write"))
            if is_img and (not img.get("width") or not img.get("height")):
                out.append(finding("S3-IMG-DIMS-MISSING", 3, "low",
                                   "Image lacks width/height (layout shift risk).", [p["url"]],
                                   evidence='<img src="{0}">'.format(src), recipe="img-dimensions"))
            kind, ap = S.resolve_href(p["url"], src)
            ap = ap if kind == "internal" else None
            if ap and ap in assets and assets[ap]["bytes"] > IMG_HEAVY_BYTES:
                out.append(finding("S3-IMG-HEAVY", 3, "medium",
                                   "Image is {0} KB.".format(assets[ap]["bytes"] // 1024), [p["url"]],
                                   evidence="{0} ({1} KB, via <{2}>)".format(
                                       src, assets[ap]["bytes"] // 1024, img.get("kind", "img")),
                                   recipe="img-optimize"))

    # unused assets -- only meaningful in fs mode, where the full file set is known
    if doc["meta"].get("mode") == "fs" and doc.get("referenced_assets") is not None:
        refs = set(doc["referenced_assets"])
        names = set(doc.get("referenced_basenames") or [])
        media_re = re.compile(r"\.(png|jpe?g|webp|avif|gif|svg|mp4|webm|pdf|woff2?)$", re.I)
        # Files a host or crawler fetches by convention, never by an in-page link.
        conventional = re.compile(
            r"(^/(favicon|apple-touch-icon|android-chrome|mstile|safari-pinned-tab|"
            r"og-|twitter-|logo|manifest|browserconfig|sitemap|robots)"
            r"|/(favicon|apple-touch-icon|site\.webmanifest))", re.I)
        unused = []
        for a in doc.get("assets", []):
            path = S.norm_path("/" + a["path"])
            if (not media_re.search(path) or path in refs
                    or path.rsplit("/", 1)[-1].lower() in names
                    or conventional.search(path)):
                continue
            unused.append((a["bytes"], path))
        unused.sort(reverse=True)
        if unused:
            total_kb = sum(b for b, _ in unused) // 1024
            out.append(finding(
                "S3-ASSET-UNUSED", 3, "low",
                "{0} media file(s) totalling {1} KB are not referenced by any page, "
                "stylesheet or script.".format(len(unused), total_kb),
                ["/"],
                evidence="; ".join("{0} ({1} KB)".format(p_, b // 1024)
                                   for b, p_ in unused[:6]),
                recipe="asset-prune", confidence="medium",
                detail={"files": [{"path": p_, "bytes": b} for b, p_ in unused],
                        "total_bytes": sum(b for b, _ in unused)}))

    # ------------------------------------------------------- STAGE 5: schema
    # Owned entirely by schema_check.py, whose findings are merged in via --schema.
    # An inline copy here could not corroborate claims against page text and simply
    # double-reported through report.py's merge.
    for f in (schema_findings or []):
        out.append(finding(f["id"], 5, f["severity"], f["message"], [f["page"]],
                           evidence=f.get("evidence"), recipe="schema-fix"))

    # --------------------------------------------------- STAGE 4: performance
    if vitals:
        for entry in vitals.get("runs", []):
            u = entry.get("page", "/")
            m = entry.get("metrics", {})
            for key, label, good, poor, sev in (
                ("largest-contentful-paint", "LCP", 2500, 4000, "high"),
                ("cumulative-layout-shift", "CLS", 0.1, 0.25, "high"),
                ("total-blocking-time", "TBT", 200, 600, "medium"),
            ):
                v = m.get(key)
                if v is None:
                    continue
                if v > poor:
                    out.append(finding("S4-CWV-POOR", 4, sev,
                                       "{0} is {1} on {2} ({3} profile) -- poor.".format(
                                           label, _fmt(key, v), u, entry.get("profile")),
                                       [u], evidence="{0}={1}".format(label, _fmt(key, v)),
                                       recipe="cwv-" + label.lower(),
                                       detail={"profile": entry.get("profile"), "tier": "MEASURED-lab"}))
                elif v > good:
                    out.append(finding("S4-CWV-NEEDS-WORK", 4, "low",
                                       "{0} is {1} on {2} ({3} profile) -- needs improvement.".format(
                                           label, _fmt(key, v), u, entry.get("profile")),
                                       [u], evidence="{0}={1}".format(label, _fmt(key, v)),
                                       recipe="cwv-" + label.lower(),
                                       detail={"profile": entry.get("profile"), "tier": "MEASURED-lab"}))

    out = group_findings(out)
    out.sort(key=lambda f: (f["stage"], SEV.get(f["severity"], 9), f["id"]))
    withheld = sorted(u for u, (ok, _) in indexable.items()
                      if not ok and u not in {i["url"] for i in intentional})
    return {"findings": out, "intentional": intentional,
            "gates": compute_gates(out, withheld, doc["meta"].get("mode", "fs"))}


GATE_B_IDS = {"S2-ORPHAN", "S2-UNREACHABLE", "S3-DUPLICATE-EXACT", "S3-NEAR-DUPLICATE"}

GATE_B_UNASSESSED = (
    "orphan detection needs a complete page list; an http crawl discovers pages only via "
    "links and sitemap.xml, so a page that is neither linked nor listed cannot be seen. "
    "Re-run in fs mode against the codebase to clear this gate.")


def compute_gates(findings, withheld, mode="fs"):
    """The three repair gates. This is the ONLY definition -- the report and the
    dashboard consume it rather than re-deriving it, so the status can never
    contradict the rule printed beside it.

    Each gate mirrors references/pipeline.md exactly:
      A  blocked while a page that should be indexable is not (stage 3+ work on it
         is being withheld), or the site contradicts itself by listing a blocked
         page in its sitemap.
      B  blocked while a page's very existence is unsettled -- orphaned, unreachable,
         or a merge candidate. Schema on such a page may be discarded with the page.
      C  blocked while any critical/high finding remains open in stages 1-5.

    Gate B has a third state. A gate may only be *cleared* by a check that could
    actually run: fs mode enumerates the filesystem and so can prove a site has no
    orphans, but an http walk cannot -- a page that is neither linked nor listed in
    the sitemap is invisible to it, so silence there is absence of evidence, not
    evidence of absence. Reporting CLEAR from it once told a user "no orphan or
    duplicate pages" on a site with two orphans, and unblocked schema work on pages
    that were merge candidates. Positive findings are trustworthy in either mode, so
    BLOCKED still wins over NOT ASSESSED. This is the same principle as the report's
    per-stage `assessed` state: a check nobody could run is not a check that passed.
    """
    contradiction = [f for f in findings if f["id"] == "S1-INDEX-CONTRADICTION"]
    gate_b_pages = sorted({u for f in findings if f["id"] in GATE_B_IDS for u in f["pages"]})
    blockers_c = [f for f in findings
                  if f["stage"] <= 5 and f["severity"] in ("critical", "high")]

    a_blocked = bool(withheld or contradiction)
    return [
        {"gate": "A", "name": "Index before content",
         "status": "BLOCKED" if a_blocked else "CLEAR",
         "rule": "No stage 2+ work on pages stage 1 says are not indexable.",
         "detail": ("{0} page(s) not indexable: {1}".format(len(withheld), ", ".join(withheld[:4]))
                    if withheld else
                    ("sitemap lists a blocked page" if contradiction
                     else "every crawled page is indexable")),
         "pages": withheld},
        {"gate": "B", "name": "Architecture before schema",
         "status": ("BLOCKED" if gate_b_pages
                    else "CLEAR" if mode == "fs" else "NOT ASSESSED"),
         "rule": "No schema work on orphaned or merge-candidate pages.",
         "detail": ("{0} page(s) orphaned or pending consolidation: {1}".format(
                        len(gate_b_pages), ", ".join(gate_b_pages[:4]))
                    if gate_b_pages
                    else "no orphan or duplicate pages" if mode == "fs"
                    else GATE_B_UNASSESSED),
         "pages": gate_b_pages},
        {"gate": "C", "name": "Everything before authority",
         "status": "BLOCKED" if blockers_c else "CLEAR",
         "rule": "Stage 6 off-page work is refused until stages 1-5 verify clean.",
         "detail": ("{0} critical/high finding(s) open in stages 1-5: {1}".format(
                        len(blockers_c), ", ".join(sorted({f["id"] for f in blockers_c})[:4]))
                    if blockers_c else "stages 1-5 clear of critical/high findings"),
         "pages": sorted({u for f in blockers_c for u in f["pages"]})},
    ]


def group_findings(findings):
    """Collapse findings that share an id AND identical evidence into one.

    A heading skip inside a shared footer is one defect in one shared component,
    not five defects because it renders on five pages. Grouping keeps the fix list
    honest about how much work there actually is.
    """
    grouped, order = {}, []
    for f in findings:
        # Only single-page findings may merge. A finding that already names several
        # pages is inherently ABOUT that specific set -- two unrelated page pairs that
        # happen to score the same similarity are not one defect, and merging them
        # produces a finding claiming four pages duplicate each other when only two
        # pairs do.
        if len(f["pages"]) != 1:
            out_key = ("__pairwise__", id(f))
            grouped[out_key] = dict(f, pages=list(f["pages"]))
            order.append(out_key)
            continue
        key = (f["id"], f["evidence"])
        if key in grouped:
            g = grouped[key]
            for u in f["pages"]:
                if u not in g["pages"]:
                    g["pages"].append(u)
        else:
            g = dict(f, pages=list(f["pages"]))
            grouped[key] = g
            order.append(key)
    out = []
    for key in order:
        g = grouped[key]
        g["pages"].sort()
        if len(g["pages"]) > 1 and not g["message"].endswith(")"):
            g["affects"] = len(g["pages"])
        out.append(g)
    return out


def _fmt(key, v):
    return "{0:.3f}".format(v) if "shift" in key else "{0:.0f}ms".format(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crawl", required=True)
    ap.add_argument("--vitals")
    ap.add_argument("--schema", help="schema.json from schema_check.py. Merged here (not in "
                                     "report.py) so the gates see the complete finding set.")
    ap.add_argument("--out", default="findings.json")
    args = ap.parse_args()

    doc = S.jload(args.crawl)
    vitals = S.jload(args.vitals) if args.vitals else None
    schema = S.jload(args.schema)["findings"] if args.schema else None
    if args.schema is None:
        print("NOTE: no --schema given; stage 5 findings are absent from this run "
              "and from the gates. Run schema_check.py first for a complete audit.")
    result = audit(doc, vitals, schema)
    findings, intentional = result["findings"], result["intentional"]

    counts = defaultdict(int)
    for f in findings:
        counts[f["severity"]] += 1
    stage_counts = defaultdict(int)
    for f in findings:
        stage_counts[f["stage"]] += 1

    S.jdump({
        "meta": dict(doc["meta"], findings=len(findings)),
        "summary": {"by_severity": dict(counts), "by_stage": dict(stage_counts)},
        "intentional_exclusions": intentional,
        "gates": result["gates"],
        "findings": findings,
    }, args.out)

    print("{0} findings: {1} critical, {2} high, {3} medium, {4} low".format(
        len(findings), counts["critical"], counts["high"], counts["medium"], counts["low"]))
    for st in sorted(stage_counts):
        print("  stage {0}: {1}".format(st, stage_counts[st]))
    for g in result["gates"]:
        print("  gate {0} ({1}): {2} -- {3}".format(g["gate"], g["name"], g["status"], g["detail"]))
    if intentional:
        print("deliberate exclusions (not defects): " +
              ", ".join(i["url"] for i in intentional))
    print("wrote " + args.out)


if __name__ == "__main__":
    main()
