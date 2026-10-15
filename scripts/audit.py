"""audit.py -- turns crawl.json into evidence-backed findings, tagged by stage.

Every finding carries: id, stage, severity, pages, evidence (the actual offending
string from the source), recipe (a fix-recipes.md key), and confidence. A finding
without evidence is a guess, and this file must not emit guesses.

Usage:
  python audit.py --crawl crawl.json [--out findings.json] [--vitals vitals.json]
"""
import argparse
import datetime
import itertools
import json
import re
from collections import defaultdict
from urllib.parse import urlsplit

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
        return S.norm_path(urlsplit(url_or_path).path or "/")
    return S.norm_path(url_or_path)


# Names that can never be a real site's own origin: the RFC 2606 / 6761 reserved names,
# loopback, and the template strings that ship in themes and starters.
PLACEHOLDER_NAMES = {"example.com", "example.org", "example.net", "example.edu", "localhost",
                     "127.0.0.1", "0.0.0.0", "::1", "yourdomain.com", "yoursite.com",
                     "yourwebsite.com"}
PLACEHOLDER_TLDS = (".example", ".test", ".invalid", ".localhost")
# Hosting platforms' per-deploy hostnames. One that differs from the base URL means a
# preview build's URLs leaked into production, which makes S1-HOST-MISMATCH critical.
PREVIEW_HOSTS = (".vercel.app", ".netlify.app", ".pages.dev", ".onrender.com",
                 ".herokuapp.com", ".web.app", ".firebaseapp.com", ".amplifyapp.com",
                 ".fly.dev", ".surge.sh")


def is_placeholder_host(host):
    h = (host or "").lower()
    return (h in PLACEHOLDER_NAMES or h.endswith(PLACEHOLDER_TLDS)
            or any(h.endswith("." + n) for n in PLACEHOLDER_NAMES))


def placeholder_host_of(url):
    """The placeholder hostname in an absolute URL, or None."""
    if not url or not url.startswith("http"):
        return None
    host = urlsplit(url).hostname
    return host if is_placeholder_host(host) else None


OG_REQUIRED = ("og:title", "og:description", "og:image")

# BCP 47 as hreflang uses it: language, optional script, optional region, or x-default.
HREFLANG_RE = re.compile(r"^(x-default|[a-z]{2,3}(-[a-z]{4})?(-([a-z]{2}|\d{3}))?)$", re.I)
# W3C Datetime, the only format the sitemap protocol allows in <lastmod>.
LASTMOD_RE = re.compile(
    r"^\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2}))?)?)?$")
LASTMOD_UNIFORM_MIN = 5   # fewer dated URLs than this can honestly share one date
JS_WORDS_MIN_GAP = 50     # words that exist only after JS, before it is worth a finding


def valid_lastmod(v):
    if not LASTMOD_RE.match(v):
        return False
    try:
        parts = [int(x) for x in v[:10].split("-")]
        datetime.date(*(parts + [1] * (3 - len(parts))))
    except ValueError:
        return False
    return True


def hreflang_target(href, page_url, alias, base):
    """(kind, page_url) for an hreflang href: 'internal' with the crawled page it names
    (None when no such page exists), or 'external' for another origin we cannot check."""
    if href.startswith("http") and base and not same_origin(href, base):
        return "external", None
    kind, resolved = S.resolve_href(page_url, href)
    if kind not in ("internal", "absolute"):
        return "internal", None
    path = path_of(href) if kind == "absolute" else resolved
    key = S.norm_path(path)
    found = alias.get(key)
    if found is None and "." not in key.rstrip("/").rsplit("/", 1)[-1]:
        # ponytail: /en and /en/ are one page behind a redirect. Google wants the final URL
        # in hreflang and this does not flag the redirecting form; add a low finding if it matters.
        found = alias.get(key[:-1] if key.endswith("/") else key + "/")
    return "internal", found


def same_origin(url, base):
    a, b = urlsplit(url), urlsplit(base)
    return (a.scheme, a.netloc.lower()) == (b.scheme, b.netloc.lower())


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

    home = "/" if "/" in by_url else (pages[0]["url"] if pages else None)

    # ---------------------------------------------------------- STAGE 1: index
    if not doc["robots"]["found"]:
        # low: Google treats a robots.txt that 404s as "crawl everything".
        out.append(finding("S1-ROBOTS-MISSING", 1, "low",
                           "No robots.txt found at site root.", ["/"],
                           evidence="robots.txt absent", recipe="robots-create"))
    if not doc["sitemap"]["found"]:
        out.append(finding("S1-SITEMAP-MISSING", 1, "medium",
                           "No sitemap found: none declared in robots.txt, none at "
                           "/sitemap.xml or /sitemap-index.xml.", ["/"],
                           evidence="sitemap absent", recipe="sitemap-create"))
    elif doc["robots"]["found"] and not doc["robots"]["sitemaps"]:
        out.append(finding("S1-ROBOTS-NO-SITEMAP", 1, "medium",
                           "robots.txt does not declare a Sitemap: line.", ["/"],
                           evidence=doc["robots"]["raw"][:200], recipe="robots-add-sitemap"))

    sitemap_paths = {path_of(u) for u in doc["sitemap"]["urls"]}
    # URLs an http crawl queued but never fetched because --max-pages ran out. Whether
    # they exist is unknown, so no finding may say they don't, and the link graph is
    # partial, so orphan and click-depth findings are withheld for the whole run.
    not_crawled = {S.canonical_http_path(s["url"]) for s in doc["meta"].get("skipped") or []
                   if s.get("not_crawled")}

    for sp in sorted(sitemap_paths):
        if sp not in alias and S.canonical_http_path(sp) not in not_crawled:
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
            deliberate = is_utility(p["url"]) or (p.get("robots_allowed") is False
                                                  and p["url"] != home)
            if in_sm:
                # Listed in the sitemap AND blocked: the site is contradicting itself.
                out.append(finding("S1-INDEX-CONTRADICTION", 1, "critical",
                                   "Page is in sitemap.xml but is not indexable: " + "; ".join(why),
                                   [p["url"]], evidence="; ".join(why), recipe="indexability-review"))
            elif not deliberate:
                out.append(finding("S1-NOT-INDEXABLE", 1,
                                   "critical" if p["url"] == home else "medium",
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
        if base and c.startswith("http") and not same_origin(c, base):
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

    # A placeholder origin in canonicals, og:url, the sitemap or the robots Sitemap line.
    # The base origin is inferred from those same URLs, so a site that is uniformly wrong
    # looks internally consistent and none of the canonical checks above can see it.
    where, hosts, on_pages = [], set(), set()
    for label, getter in (("canonical", lambda p: p.get("canonical")),
                          ("og:url", lambda p: (p.get("og") or {}).get("og:url"))):
        hit = [(p["url"], placeholder_host_of(getter(p))) for p in pages]
        hit = [(u, h) for u, h in hit if h]
        if hit:
            where.append("{0} on {1} page(s)".format(label, len(hit)))
            hosts.update(h for _, h in hit)
            on_pages.update(u for u, _ in hit)
    for label, urls in (("sitemap.xml", doc["sitemap"]["urls"]),
                        ("robots.txt Sitemap line", doc["robots"]["sitemaps"])):
        hit = [placeholder_host_of(u) for u in urls]
        hit = [h for h in hit if h]
        if hit:
            where.append("{0} ({1} URL(s))".format(label, len(hit)))
            hosts.update(hit)
    if where:
        out.append(finding("S1-PLACEHOLDER-DOMAIN", 1, "high",
                           "URLs point at a placeholder domain ({0}), not the site's own."
                           .format(", ".join(sorted(hosts))),
                           sorted(on_pages) or ["/"],
                           evidence="; ".join(where), recipe="domain-fix"))

    # Host consistency: the URLs a site publishes about itself should all name one host.
    # A preview deploy's host (*.vercel.app) baked into a production build, or www in the
    # canonicals and the apex in the sitemap, splits signals across hosts. Placeholder
    # hosts are left to S1-PLACEHOLDER-DOMAIN above.
    # The base URL only counts when it is a production host: an http crawl of localhost or
    # of a preview deploy takes the crawled origin as its base, and that preview's correct
    # production canonicals are not a mismatch.
    base_host = (urlsplit(base).hostname or "") if base else ""
    if is_placeholder_host(base_host) or base_host.endswith(PREVIEW_HOSTS):
        base_host = ""
    seen_hosts = defaultdict(lambda: defaultdict(int))     # source -> host -> count
    off_pages = set()
    for label, getter in (("canonical", lambda p: p.get("canonical")),
                          ("og:url", lambda p: (p.get("og") or {}).get("og:url"))):
        for p in pages:
            v = getter(p) or ""
            h = urlsplit(v).hostname if v.startswith("http") else None
            if h and not is_placeholder_host(h):
                seen_hosts[label][h] += 1
                if base_host and h != base_host:
                    off_pages.add(p["url"])
    for label, urls in (("sitemap.xml", doc["sitemap"]["urls"]),
                        ("robots.txt Sitemap line", doc["robots"]["sitemaps"])):
        for u in urls:
            h = urlsplit(u).hostname if u.startswith("http") else None
            if h and not is_placeholder_host(h):
                seen_hosts[label][h] += 1
    all_hosts = {h for hs in seen_hosts.values() for h in hs} | ({base_host} if base_host else set())
    # Canonicals alone on another origin are already S1-CANONICAL-OFFSITE, page by page.
    canonical_only = base_host and all(set(hs) <= {base_host}
                                       for label, hs in seen_hosts.items() if label != "canonical")
    if len(all_hosts) > 1 and not canonical_only:
        preview = sorted(h for h in all_hosts if h != base_host and h.endswith(PREVIEW_HOSTS))
        parts = (["base URL " + base_host] if base_host else []) + [
            "{0}: {1}".format(label, ", ".join("{0} ({1})".format(h, n) for h, n in sorted(hs.items())))
            for label, hs in seen_hosts.items()]
        published = all_hosts - {base_host}
        if base_host and len(published) == 1 and not preview and not any(
                base_host in hs for hs in seen_hosts.values()):
            # Every published URL agrees on one host and only the crawled host differs
            # (tauri.app, whose canonicals, og:url, sitemap and robots all name
            # v2.tauri.app). That is a deliberate canonical host; the one thing to check
            # is that the crawled host redirects there. The per-page CANONICAL-OFFSITE
            # findings say the same thing 50 times at critical, so they fold into this.
            other = next(iter(published))
            out[:] = [f for f in out if not (f["id"] == "S1-CANONICAL-OFFSITE"
                                             and urlsplit(f["evidence"]).hostname == other)]
            out.append(finding("S1-HOST-MISMATCH", 1, "medium",
                               "Served on {0}, but every URL names {1}; check {0} redirects to {1}."
                               .format(base_host, other),
                               sorted(off_pages) or ["/"], evidence="; ".join(parts),
                               recipe="domain-fix", detail={"hosts": sorted(all_hosts)}))
        else:
            out.append(finding("S1-HOST-MISMATCH", 1, "critical" if preview else "high",
                               "The site's own URLs name {0} different hosts{1}.".format(
                                   len(all_hosts),
                                   ", including preview host " + ", ".join(preview) if preview else ""),
                               sorted(off_pages) or ["/"], evidence="; ".join(parts),
                               recipe="domain-fix", detail={"hosts": sorted(all_hosts)}))

    # broken internal links
    broken = defaultdict(list)
    for p in pages:
        for l in p["links"]:
            if l["kind"] != "internal":
                continue
            t = S.norm_path(l["resolved"])
            if t in alias or t in assets or S.canonical_http_path(t) in not_crawled:
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

    # redirects -- only an http crawl ever sees one
    for p in pages:
        hops = p.get("redirect_hops") or []
        if len(hops) >= 3:                      # N redirects = N+1 URLs
            out.append(finding("S1-REDIRECT-CHAIN", 1, "medium",
                               "{0} redirects in a row before this page is reached.".format(len(hops) - 1),
                               [p["url"]], evidence=" -> ".join(hops), recipe="redirect-fix"))
    for sk in doc["meta"].get("skipped") or []:
        reason = sk.get("reason") or ""
        if "redirect" in reason.lower() and "exceeded" in reason.lower():
            out.append(finding("S1-REDIRECT-LOOP", 1, "high",
                               "URL redirects in a loop and never resolves.", [sk["url"]],
                               evidence=reason, recipe="redirect-fix"))

    # soft 404: the http crawl requested one random path that cannot exist
    probe = doc["meta"].get("soft404_probe") or {}
    if doc["meta"].get("mode") == "http" and probe.get("status") == 200:
        via = " (after redirect to {0})".format(probe["final_url"]) if probe.get("redirects") else ""
        out.append(finding("S1-SOFT-404", 1, "high",
                           "A URL that does not exist returns 200, so missing pages look like real ones.",
                           ["/"], evidence="GET {0} -> 200{1}".format(probe["path"], via),
                           recipe="soft-404-fix"))

    # mixed content: http:// subresources on a page that is served over https
    for p in pages:
        refs = p.get("insecure_refs") or []
        if refs and ((p.get("canonical") or "").startswith("https://") or base.startswith("https://")):
            active = [r for r in refs if r["active"]]
            out.append(finding("S1-MIXED-CONTENT", 1, "high" if active else "medium",
                               "{0} http:// resource(s) on an https page{1}.".format(
                                   len(refs), " -- browsers block the scripts/styles outright" if active else ""),
                               [p["url"]],
                               evidence="; ".join("<{0}> {1}".format(r["tag"], r["url"]) for r in refs[:3]),
                               recipe="mixed-content-fix"))

    # security headers: response headers exist only in an http crawl, and HSTS only means
    # something on https. Judge the scheme that was actually crawled (meta.source), not
    # base_url: that is inferred from the canonicals, so a local http dev server of an
    # https site would be told to send a header it cannot send.
    if doc["meta"].get("mode") == "http" and str(doc["meta"].get("source") or "").startswith("https://"):
        seen = [p for p in pages if p.get("status") == 200 and "security_headers" in p]
        gaps = []
        for hdr, label in (("strict-transport-security", "Strict-Transport-Security"),
                           ("x-content-type-options", "X-Content-Type-Options")):
            n = sum(1 for p in seen if hdr not in p["security_headers"])
            if n:
                gaps.append("{0} missing on {1} of {2} pages".format(label, n, len(seen)))
        if gaps:
            out.append(finding("S1-SECURITY-HEADERS", 1, "low",
                               "Response security headers are missing.", ["/"],
                               evidence="; ".join(gaps), recipe="security-headers-add"))

    # sitemap <lastmod>
    lastmods = [(u, v) for u, v in (doc["sitemap"].get("lastmods") or []) if v]
    bad = [(u, v) for u, v in lastmods if not valid_lastmod(v)]
    if bad:
        out.append(finding("S1-SITEMAP-LASTMOD-INVALID", 1, "medium",
                           "{0} sitemap <lastmod> value(s) are not valid W3C dates.".format(len(bad)),
                           ["/"], evidence="; ".join("{0} -> {1}".format(u, v) for u, v in bad[:3]),
                           recipe="lastmod-fix"))
    elif len(lastmods) >= LASTMOD_UNIFORM_MIN and len({v for _, v in lastmods}) == 1:
        out.append(finding("S1-SITEMAP-LASTMOD-UNIFORM", 1, "low",
                           "Every sitemap <lastmod> is the same date, so crawlers learn to ignore them.",
                           ["/"], evidence="{0} URLs, all {1}".format(len(lastmods), lastmods[0][1]),
                           recipe="lastmod-fix", confidence="medium"))

    # hreflang: every alternate must name a real page, include itself, and be reciprocated
    h_targets = {}
    for p in pages:
        entries = p.get("hreflang") or []
        if not entries:
            continue
        resolved = {}
        for e in entries:
            if not HREFLANG_RE.match(e["lang"]):
                out.append(finding("S1-HREFLANG-INVALID", 1, "medium",
                                   "hreflang value is not a valid language code.", [p["url"]],
                                   evidence='hreflang="{0}"'.format(e["lang"]), recipe="hreflang-fix"))
                continue
            kind, target = hreflang_target(e["href"], p["url"], alias, base)
            if kind == "internal" and target is None:
                out.append(finding("S1-HREFLANG-BROKEN", 1, "high",
                                   "hreflang points at a page that does not exist.", [p["url"]],
                                   evidence='hreflang="{0}" href="{1}"'.format(e["lang"], e["href"]),
                                   recipe="hreflang-fix"))
            elif target:
                resolved[e["lang"].lower()] = target
        h_targets[p["url"]] = set(resolved.values())
        if p["url"] not in h_targets[p["url"]] and resolved:
            out.append(finding("S1-HREFLANG-NO-SELF", 1, "medium",
                               "hreflang set does not include the page itself.", [p["url"]],
                               evidence="lists " + ", ".join(sorted(h_targets[p["url"]])),
                               recipe="hreflang-fix"))
    for u, targets in sorted(h_targets.items()):
        silent = sorted(t for t in targets if t != u and u not in h_targets.get(t, ()))
        if silent:
            out.append(finding("S1-HREFLANG-NO-RETURN", 1, "high",
                               "hreflang alternates do not link back, so Google ignores the pair.",
                               [u], evidence="no return tag from " + ", ".join(silent),
                               recipe="hreflang-fix"))

    # JavaScript: what a crawler that does not run scripts sees vs. what a browser sees
    for p in pages:
        raw = p.get("raw")
        if p.get("rendered") and raw:
            gap = p["full_word_count"] - raw["words"]
            if gap >= JS_WORDS_MIN_GAP and raw["words"] < 0.5 * p["full_word_count"]:
                out.append(finding("S1-JS-DEPENDENT", 1, "medium",
                                   "Most of this page's text only exists after JavaScript runs.",
                                   [p["url"]],
                                   evidence="raw HTML {0} words; rendered DOM {1} words".format(
                                       raw["words"], p["full_word_count"]),
                                   recipe="js-render-fix", confidence="medium"))
            diffs = []
            if (raw["canonical"] or "") != (p.get("canonical") or ""):
                diffs.append("canonical {0!r} -> {1!r}".format(raw["canonical"], p.get("canonical")))
            raw_noindex = "noindex" in (raw["robots_meta"] or "").lower()
            if raw_noindex != bool(p.get("noindex")):
                diffs.append("meta robots {0!r} -> {1!r}".format(raw["robots_meta"], p.get("robots_meta")))
            if diffs:
                out.append(finding("S1-JS-HEAD-MISMATCH", 1, "high",
                                   "Canonical or robots directive changes when JavaScript runs.",
                                   [p["url"]], evidence="; ".join(diffs), recipe="js-render-fix"))
        elif p.get("js_shell") and indexable[p["url"]][0]:
            out.append(finding("S1-JS-SHELL", 1, "medium",
                               "Source HTML is an empty client-rendered shell; re-run with --render "
                               "to see what a rendering crawler sees.", [p["url"]],
                               evidence="empty app mount point, {0} words in the source".format(
                                   p["full_word_count"]),
                               recipe="js-render-fix", confidence="medium"))

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
        if u == home or not_crawled:
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
            if p.get("js_shell"):
                continue       # an unrendered shell has no headings to judge; S1-JS-SHELL says so
            out.append(finding("S2-H1-MISSING", 2, "high", "Page has no H1.", [p["url"]],
                               evidence=p["file"] or p["url"], recipe="heading-fix"))
        elif len(h1s) > 1:
            out.append(finding("S2-H1-MULTIPLE", 2, "medium",
                               "Page has {0} H1 elements.".format(len(h1s)), [p["url"]],
                               evidence=" | ".join(h1s[:3]), recipe="heading-fix"))
        levels = [h["level"] for h in p["headings"]]
        for i, (a, b) in enumerate(zip(levels, levels[1:])):
            if b > a + 1:
                out.append(finding("S2-HEADING-SKIP", 2, "low",
                                   "Heading level jumps from H{0} to H{1}.".format(a, b), [p["url"]],
                                   evidence="H{0} -> H{1}: {2}".format(a, b, p["headings"][i + 1]["text"][:80]),
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

    # navigation with no real href: crawlers follow URLs, not click handlers
    for p in live:
        bad = p.get("uncrawlable_links") or []
        if bad:
            out.append(finding("S2-UNCRAWLABLE-LINK", 2, "medium",
                               "{0} link(s) have no URL a crawler can follow.".format(len(bad)),
                               [p["url"]], evidence="; ".join(bad[:3]),
                               recipe="crawlable-link-fix", confidence="medium"))

    # ------------------------------------------------- STAGE 3: content/on-page
    # exact + near duplicates
    by_fp = defaultdict(list)
    for p in live:
        if p["word_count"] > 0:
            by_fp[p["text_fingerprint"]].append(p["url"])
    # Pages that name each other in hreflang are alternates of one page, not duplicates.
    alt_pairs = {frozenset((u, t)) for u, ts in h_targets.items()
                 for t in ts if t != u and u in h_targets.get(t, ())}
    for fp, urls in by_fp.items():
        if len(urls) > 1:
            if all(frozenset(pair) in alt_pairs for pair in itertools.combinations(urls, 2)):
                continue
            out.append(finding("S3-DUPLICATE-EXACT", 3, "critical",
                               "{0} pages have byte-identical main content.".format(len(urls)),
                               sorted(urls), evidence="identical text fingerprint " + fp[:12],
                               recipe="consolidate-pages"))
    seen_pairs = {frozenset(u) for u in by_fp.values() if len(u) > 1} | alt_pairs
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
    # Terms on a large share of the site ("catering", "menu", the brand) are template
    # words: they say nothing about which page targets what. Left in, they make every
    # sibling in a templated section (lunch/dinner/tiffin menus) look like it competes
    # with every other. Compare on the remaining, distinguishing terms only.
    # ponytail: a family of 5+ near-identical pages on a 13-page site is read as a
    # template and goes unreported; use the duplicate checks for those.
    df = defaultdict(int)
    for u in urls:
        for w in sigs[u]:
            df[w] += 1
    boiler = {w for w, n in df.items() if n >= max(4, 0.35 * len(urls))}
    for u in urls:
        sigs[u] -= boiler
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
        if not is_utility(p["url"]):
            og = p.get("og") or {}
            missing = [k for k in OG_REQUIRED if not og.get(k)]
            if missing:
                out.append(finding("S3-OG-MISSING", 3, "low",
                                   "Open Graph tags are incomplete, so shared links get a poor preview.",
                                   [p["url"]], evidence="missing " + ", ".join(missing),
                                   recipe="og-add"))

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
            "schema_checked": schema_findings is not None,
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
        "meta": dict(doc["meta"], findings=len(findings),
                      schema_checked=result["schema_checked"]),
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
