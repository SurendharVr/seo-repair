"""crawl.py -- stage 0 of sitemend. Produces the single crawl.json that every
later stage reads.

Two modes:
  fs   : walk a repo on disk, map files to URLs (default)
  http : crawl a running dev server or live URL over HTTP (real status codes)

Usage:
  python crawl.py --root <path> [--base-url https://example.com] [--out crawl.json]
  python crawl.py --mode http --start http://localhost:8765/ [--out crawl.json]
"""
import argparse
import datetime
import json
import os
import re
import secrets
import sys
from collections import deque
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

import audit
import seolib as S

BOILERPLATE_TAGS = ("script", "style", "noscript", "template", "svg")


def infer_base_url(sm_urls, pages, robots_sitemaps):
    """Production origin from the sitemap, a canonical or the robots Sitemap line.

    A preview or placeholder host is skipped while a real one is on offer: a deploy that
    leaks *.vercel.app into the whole sitemap still names production in its canonicals,
    and taking the preview as the base made every correct canonical CANONICAL-OFFSITE.
    """
    cands = [u for u in (sm_urls[0] if sm_urls else None,
                         next((p["canonical"] for p in pages if p.get("canonical")), None),
                         robots_sitemaps[0] if robots_sitemaps else None)
             if u and u.startswith("http")]
    real = [u for u in cands if not audit.is_placeholder_host(urlsplit(u).hostname)
            and not (urlsplit(u).hostname or "").endswith(audit.PREVIEW_HOSTS)]
    if not (real or cands):
        return None
    sp = urlsplit((real or cands)[0])
    return "{0}://{1}".format(sp.scheme, sp.netloc)


def read_text(path):
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, LookupError):
            continue
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def extract_page(url, html, soup=None):
    """Parse one HTML document into the per-page record. Shared by fs and http modes."""
    soup = soup or BeautifulSoup(html, "lxml")

    def meta(name=None, prop=None):
        if name:
            t = soup.find("meta", attrs={"name": re.compile("^" + re.escape(name) + "$", re.I)})
        else:
            t = soup.find("meta", attrs={"property": re.compile("^" + re.escape(prop) + "$", re.I)})
        return (t.get("content") or "").strip() if t else None

    title_tag = soup.find("title")
    title = S.normalize_text(title_tag.get_text()) if title_tag else None
    description = meta(name="description")

    canonical_tag = soup.find("link", rel=lambda v: v and "canonical" in [x.lower() for x in (v if isinstance(v, list) else [v])])
    canonical = (canonical_tag.get("href") or "").strip() if canonical_tag else None

    robots_meta = meta(name="robots")
    robots_directives = [d.strip().lower() for d in (robots_meta or "").split(",") if d.strip()]

    # One pass over all six levels so headings stay in DOCUMENT order. Collecting
    # them level-by-level silently sorts them, which turns "H2 followed by H5" into
    # "the page uses h3 and h5" -- a different, weaker check that misnames the culprit.
    headings = []
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        txt = S.normalize_text(h.get_text())
        if txt:
            headings.append({"level": int(h.name[1]), "text": txt[:200]})

    # Body text with boilerplate stripped. Keep a separate "main" measure when the
    # document marks its primary content, since word-count thresholds should judge
    # the content, not the shared nav/footer chrome.
    body_soup = BeautifulSoup(str(soup), "lxml")
    for tag in body_soup(list(BOILERPLATE_TAGS)):
        tag.decompose()
    full_text = S.normalize_text(body_soup.get_text(" "))

    main_el = body_soup.find("main") or body_soup.find(attrs={"role": "main"})
    if main_el:
        main_text = S.normalize_text(main_el.get_text(" "))
    else:
        chrome = BeautifulSoup(str(body_soup), "lxml")
        for tag in chrome(["nav", "header", "footer", "aside"]):
            tag.decompose()
        main_text = S.normalize_text(chrome.get_text(" "))

    links = []
    for a in soup.find_all("a"):
        href = a.get("href")
        kind, resolved = S.resolve_href(url, href)
        rel = a.get("rel") or []
        rel = [r.lower() for r in (rel if isinstance(rel, list) else [rel])]
        links.append({
            "href": href,
            "kind": kind,
            "resolved": resolved,
            "anchor": S.normalize_text(a.get_text())[:120],
            "rel": rel,
            "nofollow": "nofollow" in rel,
            "target_blank": a.get("target") == "_blank",
        })

    images = []
    for img in soup.find_all("img"):
        images.append({
            "src": img.get("src"),
            "alt": img.get("alt"),
            "has_alt_attr": img.has_attr("alt"),
            "width": img.get("width"),
            "height": img.get("height"),
            "loading": img.get("loading"),
            "decoding": img.get("decoding"),
            "kind": "img",
        })
        # srcset candidates are real downloads too -- a heavy .webp behind a
        # <picture> costs the user exactly as much as a heavy <img src>.
        for cand in srcset_urls(img.get("srcset")):
            images.append({"src": cand, "alt": img.get("alt"),
                           "has_alt_attr": True, "width": img.get("width"),
                           "height": img.get("height"), "loading": img.get("loading"),
                           "decoding": None, "kind": "srcset"})
    for src in soup.find_all("source"):
        for cand in srcset_urls(src.get("srcset")) or ([src.get("src")] if src.get("src") else []):
            images.append({"src": cand, "alt": None, "has_alt_attr": True,
                           "width": None, "height": None, "loading": None,
                           "decoding": None, "kind": "source"})

    jsonld, jsonld_errors = [], []
    for sc in soup.find_all("script", attrs={"type": re.compile("application/ld\\+json", re.I)}):
        raw = sc.string or sc.get_text() or ""
        try:
            jsonld.append(json.loads(raw))
        except Exception as e:
            jsonld_errors.append(str(e)[:200])

    html_tag = soup.find("html")
    viewport = meta(name="viewport")

    hreflang = []
    for t in soup.find_all("link", hreflang=True):
        if "alternate" in [x.lower() for x in (t.get("rel") or [])]:
            hreflang.append({"lang": (t.get("hreflang") or "").strip(),
                             "href": (t.get("href") or "").strip()})

    # http:// subresources. "active" ones (script, stylesheet, iframe...) are blocked
    # outright by browsers on an https page; passive ones (images, media) are upgraded or
    # warned about. Whether the page IS https is the auditor's call -- it knows the origin.
    insecure = []
    for tag, attr, active in (("script", "src", True), ("iframe", "src", True),
                              ("embed", "src", True), ("object", "data", True),
                              ("img", "src", False), ("source", "src", False),
                              ("video", "src", False), ("audio", "src", False)):
        for el in soup.find_all(tag):
            v = (el.get(attr) or "").strip()
            if v.lower().startswith("http://"):
                insecure.append({"url": v, "tag": tag, "active": active})
    for el in soup.find_all("link"):
        v = (el.get("href") or "").strip()
        if v.lower().startswith("http://") and "stylesheet" in [x.lower() for x in (el.get("rel") or [])]:
            insecure.append({"url": v, "tag": "link", "active": True})

    # A client-rendered shell: an empty mount point and next to no text in the source.
    # Only a guess from static HTML -- a rendered crawl (--render) is what proves it.
    mount_empty = any(
        (el := soup.find(id=i)) is not None and not el.get_text(strip=True)
        for i in ("root", "app", "__next", "__nuxt", "___gatsby", "svelte"))
    js_shell = mount_empty and len(full_text.split()) < 30

    uncrawlable = uncrawlable_links(soup)

    og = {}
    for t in soup.find_all("meta", attrs={"property": re.compile("^og:", re.I)}):
        og[t.get("property").lower()] = (t.get("content") or "").strip()
    tw = {}
    for t in soup.find_all("meta", attrs={"name": re.compile("^twitter:", re.I)}):
        tw[t.get("name").lower()] = (t.get("content") or "").strip()

    return {
        "url": url,
        "title": title,
        "title_len": len(title) if title else 0,
        "meta_description": description,
        "desc_len": len(description) if description else 0,
        "canonical": canonical,
        "robots_meta": robots_meta,
        "noindex": "noindex" in robots_directives,
        "nofollow_page": "nofollow" in robots_directives,
        "headings": headings,
        "h1": [h["text"] for h in headings if h["level"] == 1],
        "word_count": len(main_text.split()),
        "full_word_count": len(full_text.split()),
        "text_fingerprint": S.text_fingerprint(main_text),
        "minhash": S.minhash(main_text),
        "static_text_len": len(full_text),
        "links": links,
        "images": images,
        "jsonld": jsonld,
        "jsonld_errors": jsonld_errors,
        "jsonld_types": sorted({t for b in jsonld for t in _types_of(b)}),
        "lang": (html_tag.get("lang") if html_tag else None),
        "viewport": viewport,
        "hreflang": hreflang,
        "insecure_refs": insecure[:20],
        "js_shell": js_shell,
        "uncrawlable_links": uncrawlable[:20],
        "og": og,
        "twitter": tw,
        "html_bytes": len(html.encode("utf-8", "replace")),
        # Squashed visible text + tel:/mailto: targets, for stage-5 claim corroboration.
        # Carried in crawl.json so schema_check.py works identically in fs and http mode.
        "asset_refs": referenced_assets(soup, url),
        "asset_basenames": sorted(referenced_basenames(html)),
        "visible_text": S.normalize_text(full_text).casefold(),
        "claim_text": S.claim_norm(full_text + " " + " ".join(
            l["href"].split(":", 1)[1] for l in links
            if l["kind"] == "nonhttp" and l["href"]
            and l["href"].lower().startswith(("tel:", "mailto:")))),
    }


NAV_JS_RE = re.compile(r"location(\.href)?\s*=|location\.(assign|replace)\s*\(|window\.open\s*\(", re.I)
TOGGLE_ATTRS = ("aria-expanded", "aria-controls", "aria-haspopup", "data-toggle", "data-bs-toggle")
# Where a placeholder href="#" is a writer's mistake rather than UI: running text.
PROSE_TAGS = ("p", "li", "td", "dd")
# Inside these, a "#" link is a widget: pagination, a demo component, a login form.
NOT_PROSE_TAGS = ("nav", "figure", "form")


def uncrawlable_links(soup):
    """Navigation a crawler cannot follow, because there is no real URL in an href.

    Conservative on purpose. A bare <a> with no href is skipped (the current item in a
    breadcrumb or pagination has none, legitimately); it counts only when it is wired up
    as a click target (onclick or tabindex). href="#" is skipped on menu toggles and
    "back to top" links, which are not navigation at all.

    A bare href="#" (no onclick) counts only in prose: a <p>, <li>, <td> or <dd> inside
    <main>/<article> (or anywhere in the body when there is neither), and not inside a
    nav, figure or form. Docs sites render live demo components (mock pagination, login
    forms, cards) full of href="#" (tailwindcss.com, ui.shadcn.com), and a table of
    contents points its first entry at "#" (react.dev). "your Jest [config file](#)" in a
    docs paragraph is the defect this is for. Hidden elements (aria-hidden, tabindex=-1)
    are decorative duplicates of a real link and are skipped too.
    """
    out = []
    scoped = soup.find(["main", "article"]) or soup.find(attrs={"role": "main"})

    def snip(el):
        # The opening tag minus class (utility-CSS sites bury the triggering attribute
        # behind hundreds of characters of it), plus the text.
        attrs = "".join(' {0}="{1}"'.format(k, " ".join(v) if isinstance(v, list) else v)
                        for k, v in el.attrs.items() if k != "class")
        text = S.normalize_text(el.get_text(" "))[:80]
        return "<{0}{1}>{2}</{0}>".format(el.name, attrs, text)[:200]

    def hidden(el):
        return (el.get("tabindex") == "-1"
                or el.find_parent(attrs={"aria-hidden": "true"}) is not None
                or el.get("aria-hidden") == "true")

    def in_prose(el):
        if el.find_parent(PROSE_TAGS) is None or el.find_parent(NOT_PROSE_TAGS) is not None:
            return False
        return not scoped or el.find_parent(
            lambda t: t.name in ("main", "article") or t.get("role") == "main") is not None

    def in_toc(el):
        # Sibling links in the same list or nav are #fragments: "Overview" at the top of
        # an "On this page" list scrolls to the top.
        box = el.find_parent(["ul", "ol", "nav"])
        return box is not None and any(
            (o.get("href") or "").startswith("#") and len(o.get("href")) > 1
            for o in box.find_all("a") if o is not el)

    for a in soup.find_all("a"):
        if a.get("role") == "button" or any(a.has_attr(x) for x in TOGGLE_ATTRS) or hidden(a):
            continue
        href = a.get("href")
        h = (href or "").strip().lower()
        if href is None:
            if a.has_attr("onclick") or a.has_attr("tabindex"):
                out.append(snip(a))
        elif h.startswith("javascript:"):
            out.append(snip(a))
        elif h == "#" and not re.search(r"\btop\b", a.get_text(), re.I):
            if a.has_attr("onclick") or (in_prose(a) and not in_toc(a)):
                out.append(snip(a))
    for el in soup.find_all(onclick=NAV_JS_RE):
        if el.name != "a" and not hidden(el):
            out.append(snip(el))
    return out


REFRESH_RE = re.compile(r"\s*(\d+(?:\.\d*)?)\s*(?:[;,]\s*(?:url\s*=\s*)?(.*))?$", re.I | re.S)


def refresh_target(html):
    """The URL a <meta http-equiv="refresh"> sends the visitor to at once (delay 0 or 1),
    or None. Such a page is a redirect, not a page: docs.astro.build's home is one, and
    judging it as a page reported a missing title, viewport, H1 and the rest."""
    if not re.search(r"http-equiv\s*=\s*['\"]?refresh", html or "", re.I):
        return None
    tag = BeautifulSoup(html, "lxml").find(
        "meta", attrs={"http-equiv": re.compile("^refresh$", re.I)})
    m = REFRESH_RE.match((tag.get("content") or "") if tag else "")
    if not m or float(m.group(1)) > 1:
        return None
    return (m.group(2) or "").strip().strip("'\"").strip() or None


def srcset_urls(value):
    """URLs out of a srcset attribute ("a.webp 1x, b.webp 2x" / "a.jpg 400w")."""
    if not value:
        return []
    out = []
    for part in value.split(","):
        part = part.strip()
        if part:
            out.append(part.split()[0])
    return out


ASSET_REF_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)", re.I)


MEDIA_NAME_RE = re.compile(
    r"[\w\-.]+\.(?:png|jpe?g|webp|avif|gif|svg|ico|mp4|webm|pdf|woff2?)", re.I)


def referenced_basenames(text):
    """Every media-looking filename in a blob of source, matched by basename only.

    Blunt on purpose. It catches absolute URLs on the production origin, JSON-LD
    image/logo fields, inline scripts and data attributes -- all places a precise
    path resolver misses -- and it can only ever suppress an unused-asset finding,
    never create one.
    """
    return {m.lower() for m in MEDIA_NAME_RE.findall(text or "")}


def referenced_assets(soup, page_url, extra_text=""):
    """Every asset path a page pulls in: img/source/srcset, stylesheets, scripts,
    social images, and url() inside inline styles or <style> blocks.

    Used to tell a genuinely unused file from one this crawler simply didn't look for
    -- the difference between a useful finding and a dangerous one.
    """
    refs = set()

    def add(v):
        if not v:
            return
        kind, resolved = S.resolve_href(page_url, v.strip())
        if kind == "internal":
            refs.add(resolved)

    for tag, attr in (("img", "src"), ("script", "src"), ("link", "href"),
                      ("source", "src"), ("video", "poster"), ("embed", "src")):
        for el in soup.find_all(tag):
            add(el.get(attr))
    for el in soup.find_all(["img", "source"]):
        for c in srcset_urls(el.get("srcset")):
            add(c)
    for el in soup.find_all(attrs={"style": True}):
        for m in ASSET_REF_RE.findall(el.get("style") or ""):
            add(m)
    for el in soup.find_all("style"):
        for m in ASSET_REF_RE.findall(el.get_text() or ""):
            add(m)
    for el in soup.find_all("meta"):
        key = (el.get("property") or el.get("name") or "").lower()
        if key in ("og:image", "twitter:image", "og:image:secure_url"):
            add(el.get("content"))
    for m in ASSET_REF_RE.findall(extra_text or ""):
        add(m)
    return sorted(refs)


def _types_of(block):
    """Collect @type values from a JSON-LD block, including @graph children."""
    out = []
    if isinstance(block, dict):
        t = block.get("@type")
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, list):
            out.extend(x for x in t if isinstance(x, str))
        for key in ("@graph", "itemListElement", "mainEntity"):
            child = block.get(key)
            if isinstance(child, list):
                for c in child:
                    out.extend(_types_of(c))
            elif isinstance(child, dict):
                out.extend(_types_of(child))
    elif isinstance(block, list):
        for c in block:
            out.extend(_types_of(c))
    return out


# ------------------------------------------------------------------- fs mode

def crawl_fs(root, base_url=None):
    root = os.path.abspath(root)
    pages, assets = [], []
    skipped = []
    stubs = []   # (file, its aliases, the path its meta refresh sends visitors to)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in S.EXCLUDE_DIRS and not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        for fn in filenames:
            rel = (rel_dir + "/" + fn if rel_dir else fn)
            full = os.path.join(dirpath, fn)
            if fn.lower().endswith(S.HTML_EXT):
                url, aliases = S.file_to_urls(rel)
                try:
                    html = read_text(full)
                except Exception as e:
                    skipped.append({"file": rel, "reason": str(e)[:200]})
                    continue
                target = refresh_target(html)
                if target:
                    kind, resolved = S.resolve_href(url, target)
                    if kind == "absolute" and base_url and urlsplit(target).netloc == urlsplit(base_url).netloc:
                        kind, resolved = "internal", S.norm_path(urlsplit(target).path)
                    if kind == "internal":
                        stubs.append((rel, aliases, resolved))
                    else:
                        skipped.append({"file": rel, "reason": "meta refresh off-site to " + target})
                    continue
                rec = extract_page(url, html)
                rec["file"] = rel
                rec["aliases"] = aliases
                rec["status"] = 200
                rec["bytes"] = os.path.getsize(full)
                pages.append(rec)
            else:
                try:
                    assets.append({"path": rel, "bytes": os.path.getsize(full)})
                except OSError:
                    pass

    # Note excluded dirs that actually exist -- material for the report.
    excluded_present = []
    for d in sorted(S.EXCLUDE_DIRS):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            n = sum(len(f) for _, _, f in os.walk(p))
            excluded_present.append({"dir": d, "files": n})

    # Redirect stubs become aliases of the page they send visitors to.
    for rel, aliases, target in stubs:
        page = next((p for p in pages if target in p["aliases"]), None)
        if page:
            page["aliases"] = sorted(set(page["aliases"]) | set(aliases))
        else:
            skipped.append({"file": rel, "reason": "meta refresh to " + target + ", not a page here"})

    robots_txt = ""
    rp = os.path.join(root, "robots.txt")
    if os.path.isfile(rp):
        robots_txt = read_text(rp)

    def read_site_file(path):
        if ".." in path.split("/"):
            return ""
        fp = os.path.join(root, path.lstrip("/").replace("/", os.sep))
        return read_text(fp) if os.path.isfile(fp) else ""

    sitemap_xml = discover_sitemap(robots_txt, read_site_file)

    extras = {}
    for name in ("llms.txt", "_headers", "_redirects", "netlify.toml", "vercel.json", ".htaccess"):
        fp = os.path.join(root, name)
        if os.path.isfile(fp):
            extras[name] = read_text(fp)[:4000]

    refs = set()
    basenames = set()
    for p in pages:
        refs.update(p.get("asset_refs") or [])
        basenames.update(p.get("asset_basenames") or [])
    for a in assets:
        if not a["path"].lower().endswith((".css", ".js", ".mjs")):
            continue
        try:
            text = read_text(os.path.join(root, a["path"].replace("/", os.sep)))
        except Exception:
            continue
        base = "/" + a["path"].rsplit("/", 1)[0] + "/" if "/" in a["path"] else "/"
        for m in ASSET_REF_RE.findall(text):
            kind, resolved = S.resolve_href(base, m.strip())
            if kind == "internal":
                refs.add(resolved)
        # bare quoted paths in JS ("assets/hero.jpg")
        basenames.update(referenced_basenames(text))

    return (pages, assets, robots_txt, sitemap_xml, extras, excluded_present,
            skipped, sorted(refs), sorted(basenames))


# ----------------------------------------------------------------- http mode

SITEMAP_FILES_MAX = 50   # sitemap files read per site, index children included


def is_sitemap(text):
    low = (text or "").lower()
    return "<urlset" in low or "<sitemapindex" in low


def discover_sitemap(robots_txt, read, cap=SITEMAP_FILES_MAX):
    """Every <url> the site's sitemaps list, as one blob for parse_sitemap and
    parse_sitemap_lastmods (both regex over the text, so concatenated files read fine).

    Where a search engine looks: the robots.txt Sitemap: lines, else /sitemap.xml, else
    /sitemap-index.xml (Astro's default, undeclared on docs.astro.build, starlight and
    biomejs.dev). <sitemapindex> files are followed to their children. A declared URL
    contributes its path only, read from the origin being audited, as sitemap <loc>s do.
    read(path) returns the file's text or "". Shared by fs and http mode.
    """
    declared = [S.norm_path(urlsplit(u).path) for u in S.Robots(robots_txt).sitemaps]
    roots = []
    for group in (declared, ["/sitemap.xml"], ["/sitemap-index.xml"]):
        roots = [(p, t) for p, t in ((p, read(p)) for p in dict.fromkeys(group)) if is_sitemap(t)]
        if roots:
            break
    seen = {p for p, _ in roots}
    queue, urlsets = deque(roots), []
    while queue:
        _, text = queue.popleft()
        if "<sitemapindex" not in text.lower():
            urlsets.append(text)
            continue
        for loc in S.parse_sitemap(text)[0]:
            child = S.norm_path(urlsplit(loc).path)
            if child in seen or len(seen) >= cap:
                continue
            seen.add(child)
            t = read(child)
            if is_sitemap(t):
                queue.append((child, t))
    # An index whose children are all unreadable is still a sitemap the site publishes.
    return "\n".join(urlsets or [t for _, t in roots])


def fetch_site_files(sess, origin, timeout):
    """robots.txt and the sitemap, fetched before the walk because the sitemap seeds it."""
    def get(path):
        try:
            r = sess.get(origin + path, timeout=timeout)
            return r.text if r.status_code == 200 else ""
        except Exception:
            return ""

    robots_txt = ""
    try:
        rr = sess.get(origin + "/robots.txt", timeout=timeout)
        if rr.status_code == 200 and "html" not in rr.headers.get("Content-Type", "").lower():
            robots_txt = rr.text
    except Exception:
        pass
    return robots_txt, discover_sitemap(robots_txt, get)


def probe_soft404(start, timeout=20):
    """Request one path that cannot exist. A site that answers it with 200 serves
    "not found" as a real page, and every mistyped or deleted URL becomes an indexable
    duplicate of the error page."""
    import requests

    origin = "{0}://{1}".format(*urlsplit(start)[:2])
    path = "/sitemend-404-probe-" + secrets.token_hex(6)
    try:
        r = requests.get(origin + path, timeout=timeout, allow_redirects=True,
                         headers={"User-Agent": "sitemend-crawler/1.0 (+local audit)"})
    except Exception as e:
        return {"path": path, "status": None, "error": str(e)[:200]}
    return {"path": path, "status": r.status_code, "final_url": r.url,
            "redirects": [h.status_code for h in r.history]}


SECURITY_HEADERS = ("strict-transport-security", "x-content-type-options")


def crawl_http(start, max_pages=300, timeout=20, render=False, render_limit=50):
    """render=True re-reads each page (up to render_limit) from Chrome's post-JavaScript DOM.
    Status, redirects and headers still come from the plain request; only the HTML that
    is parsed changes, and the raw HTML's head is kept so the auditor can compare the two.
    ponytail: one Chrome launch per page (~5 s); a persistent DevTools session if big
    sites need rendering past the limit.
    """
    import requests

    chrome = None
    if render:
        chrome = S.find_chrome()
        if not chrome:
            sys.exit("--render needs Chrome, Chromium or Edge (set CHROME_PATH to point at one)")
    rendered = 0

    sess = requests.Session()
    sess.headers["User-Agent"] = "sitemend-crawler/1.0 (+local audit)"
    # Crawl the origin the start URL lands on. apex -> www and http -> https are how a
    # site is served, not off-site links: taking the typed origin skipped every page as
    # "redirects off-site" and the crawl came back empty.
    try:
        landed = sess.get(start, timeout=timeout, allow_redirects=True, stream=True)
        landed.close()
        start = landed.url
    except Exception:
        pass
    origin = "{0}://{1}".format(*urlsplit(start)[:2])

    # The sitemap is fetched first because it seeds the frontier. A pure link walk cannot
    # reach a page nothing links to, so a page that is listed in the sitemap but unlinked
    # -- exactly the population most likely to have an indexing problem -- used to be
    # invisible, and was then reported as S1-SITEMAP-ORPHAN ("listed in sitemap.xml does
    # not exist in the crawl") despite serving a healthy 200. Seeding from the sitemap is
    # what Screaming Frog and Sitebulb both do by default, and it makes the frontier match
    # what a search engine actually discovers: links plus the sitemap.
    robots_txt, sitemap_xml = fetch_site_files(sess, origin, timeout)

    start_path = S.canonical_http_path(urlsplit(start).path or "/")
    seen, pages, queue = set(), [], deque([start_path])
    skipped = []
    redirect_aliases = {}
    chains = {}   # final path -> longest list of URLs that led to it, even when it was crawled first

    for loc in S.parse_sitemap(sitemap_xml)[0]:
        # Sitemap entries carry the production origin, which is not the origin being
        # crawled (localhost, staging). Only the path transfers.
        sp = S.canonical_http_path(urlsplit(loc).path or "/")
        if sp != start_path:
            queue.append(sp)

    while queue and len(pages) < max_pages:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        url = origin + path
        try:
            r = sess.get(url, timeout=timeout, allow_redirects=True)
        except Exception as e:
            skipped.append({"url": path, "reason": str(e)[:200]})
            continue

        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype.lower():
            continue

        if r.status_code >= 400:
            # A URL that does not resolve is not a page of the site. Recording it as one
            # would put it in the alias index and so silence both S1-SITEMAP-ORPHAN (for a
            # sitemap entry that really is dead) and S1-BROKEN-LINK (for a link pointing at
            # it). The status is kept as evidence instead.
            skipped.append({"url": path, "reason": "HTTP {0}".format(r.status_code),
                            "status": r.status_code})
            continue

        if r.history:
            # r.url is where the redirect landed. The content belongs to THAT path; the
            # requested path is an alias of it, not a second page with identical text.
            final = urlsplit(r.url)
            if "{0}://{1}".format(final.scheme, final.netloc) != origin:
                skipped.append({"url": path, "reason": "redirects off-site to " + r.url,
                                "status": r.history[0].status_code})
                continue
            target = S.canonical_http_path(final.path or "/")
            hops = [h.url for h in r.history] + [r.url]
            if len(hops) > len(chains.get(target, ())):
                chains[target] = hops
            if target != path:
                redirect_aliases.setdefault(target, set()).add(path)
                if target in seen:
                    continue
                seen.add(target)
                path = target

        refresh = refresh_target(r.text)
        if refresh:
            # A meta-refresh stub is a redirect: its path becomes an alias of the target,
            # and the refresh is one more hop. Never recorded as a page of its own.
            dest = urlsplit(urljoin(r.url, refresh))
            if "{0}://{1}".format(dest.scheme, dest.netloc) != origin:
                skipped.append({"url": path, "reason": "meta refresh off-site to " + dest.geturl()})
                continue
            target = S.canonical_http_path(dest.path or "/")
            if target != path:
                redirect_aliases.setdefault(target, set()).update(
                    {path} | redirect_aliases.pop(path, set()))
                hops = (chains.pop(path, None) or [r.url]) + [origin + target]
                if len(hops) > len(chains.get(target, ())):
                    chains[target] = hops
                if target not in seen:
                    queue.appendleft(target)
                continue

        html, raw, render_error = r.text, None, None
        if chrome and r.status_code == 200 and rendered < render_limit:
            dom, render_error = S.render_dom(chrome, r.url)
            if dom:
                raw = extract_page(path, r.text)
                html = dom
                rendered += 1

        rec = extract_page(path, html)
        if raw:
            rec["rendered"] = True
            rec["raw"] = {"words": raw["full_word_count"], "title": raw["title"],
                          "canonical": raw["canonical"], "robots_meta": raw["robots_meta"],
                          "h1": len(raw["h1"])}
        elif render_error:
            rec["render_error"] = render_error
        rec["file"] = None
        rec["aliases"] = sorted({path, path + "index.html"} if path.endswith("/") else {path})
        rec["status"] = r.status_code
        rec["bytes"] = len(r.content)
        rec["final_url"] = r.url
        rec["redirected"] = len(r.history) > 0
        rec["redirect_chain"] = [h.status_code for h in r.history]
        rec["redirect_hops"] = [h.url for h in r.history] + [r.url]
        rec["x_robots_tag"] = r.headers.get("X-Robots-Tag")
        rec["security_headers"] = {k: r.headers.get(k) for k in SECURITY_HEADERS
                                   if r.headers.get(k)}
        pages.append(rec)

        if r.status_code == 200:
            for l in rec["links"]:
                t = audit.internal_target(l, origin)
                if t is None:
                    continue
                nxt = S.canonical_http_path(t)
                if nxt not in seen:
                    queue.append(nxt)

    # --max-pages stopped the walk with URLs still queued. They were never fetched, so
    # whether they exist is unknown: audit.py must not report them as sitemap orphans or
    # broken links, and the link graph it builds is partial.
    for path in dict.fromkeys(q for q in queue if q not in seen):
        skipped.append({"url": path, "reason": "not crawled: --max-pages reached",
                        "not_crawled": True})

    for p in pages:
        extra = redirect_aliases.get(p["url"])
        if extra:
            p["aliases"] = sorted(set(p["aliases"]) | extra)
        chain = chains.get(p["url"])
        if chain and len(chain) > len(p.get("redirect_hops") or ()):
            p["redirect_hops"] = chain

    refs = sorted({r for p in pages for r in (p.get('asset_refs') or [])})
    names = sorted({n for p in pages for n in (p.get('asset_basenames') or [])})
    return pages, [], robots_txt, sitemap_xml, {}, [], skipped, refs, names


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", help="repo root (fs mode)")
    ap.add_argument("--mode", choices=["fs", "http"], default="fs")
    ap.add_argument("--start", help="start URL (http mode)")
    ap.add_argument("--base-url", help="production origin, e.g. https://example.com")
    ap.add_argument("--out", default="crawl.json")
    ap.add_argument("--max-pages", type=int, default=300)
    ap.add_argument("--render", action="store_true",
                    help="http mode: parse Chrome's post-JavaScript DOM (needs a local Chrome/Edge)")
    ap.add_argument("--render-limit", type=int, default=50,
                    help="max pages to render (~5 s each); the rest use the raw HTML")
    args = ap.parse_args()

    if args.render and args.mode != "http":
        ap.error("--render needs --mode http: fs mode has no running site to render")
    if args.mode == "fs":
        if not args.root:
            ap.error("--root is required in fs mode")
        (pages, assets, robots_txt, sitemap_xml, extras, excluded,
         skipped, refs, names) = crawl_fs(args.root, args.base_url)
        source = os.path.abspath(args.root)
    else:
        if not args.start:
            ap.error("--start is required in http mode")
        (pages, assets, robots_txt, sitemap_xml, extras, excluded,
         skipped, refs, names) = crawl_http(args.start, args.max_pages, render=args.render,
                                  render_limit=args.render_limit)
        source = args.start

    if not pages:
        reason = skipped[0]["reason"] if skipped else "no HTML pages found"
        print("ERROR: crawled 0 pages from {0} ({1}). Nothing to audit.".format(source, reason),
              file=sys.stderr)
        return 1
    soft404 = probe_soft404(pages[0]["final_url"]) if args.mode == "http" else None
    if args.mode == "http" and args.base_url and audit.same_origin(args.base_url, args.start):
        # A start URL that redirected is crawled where it landed (crawl_http), and a
        # --base-url that only repeats the typed origin (as sitemend.py passes) follows it.
        landed = urlsplit(pages[0]["final_url"])
        args.base_url = "{0}://{1}".format(landed.scheme, landed.netloc)

    robots = S.Robots(robots_txt)
    sm_urls, sm_errs = S.parse_sitemap(sitemap_xml)

    base_url = args.base_url or infer_base_url(sm_urls, pages, robots.sitemaps)

    # Mark robots.txt crawl-blocking per page.
    for p in pages:
        allowed, rule = robots.check(p["url"])
        p["robots_allowed"] = allowed
        p["robots_rule"] = rule

    doc = {
        "meta": {
            "tool": "sitemend/crawl.py",
            "version": 1,
            "mode": args.mode,
            "source": source,
            "base_url": base_url,
            "crawled_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "page_count": len(pages),
            "render": args.render,
            "render_limit": args.render_limit if args.render else None,
            "rendered_pages": sum(1 for p in pages if p.get("rendered")),
            "excluded_dirs_present": excluded,
            "skipped": skipped,
            "soft404_probe": soft404,
        },
        "robots": {
            "found": robots.found,
            "sitemaps": robots.sitemaps,
            "groups": [{"agents": g["agents"], "rules": [[a, p] for a, p in g["rules"]]} for g in robots.groups],
            "raw": robots.raw[:2000],
        },
        "sitemap": {"found": bool(sitemap_xml), "urls": sm_urls, "notes": sm_errs,
                    "lastmods": S.parse_sitemap_lastmods(sitemap_xml)},
        "extras": {k: True for k in extras},
        "extras_content": extras,
        "assets": assets,
        "referenced_assets": refs,
        "referenced_basenames": names,
        "pages": pages,
    }
    S.jdump(doc, args.out)
    print("crawled {0} pages from {1} ({2} mode)".format(len(pages), source, args.mode))
    print("robots.txt: {0} | sitemap.xml: {1} ({2} urls)".format(
        "found" if robots.found else "MISSING",
        "found" if sitemap_xml else "MISSING", len(sm_urls)))
    if args.render:
        print("rendered {0} of {1} pages in Chrome".format(
            sum(1 for p in pages if p.get("rendered")), len(pages)))
    if excluded:
        print("excluded build dirs: " + ", ".join("{0} ({1} files)".format(e["dir"], e["files"]) for e in excluded))
    not_crawled = sum(1 for s in skipped if s.get("not_crawled"))
    if not_crawled:
        print("stopped at --max-pages {0}: {1} queued URL(s) not crawled, and not reported "
              "as missing".format(args.max_pages, not_crawled))
    if len(skipped) > not_crawled:
        print("skipped {0} files".format(len(skipped) - not_crawled))
    print("wrote " + args.out)


if __name__ == "__main__":
    sys.exit(main())
