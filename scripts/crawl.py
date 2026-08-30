"""crawl.py -- stage 0 of seo-repair. Produces the single crawl.json that every
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
import sys
from collections import deque
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

import seolib as S

BOILERPLATE_TAGS = ("script", "style", "noscript", "template", "svg")


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
        "og": og,
        "twitter": tw,
        "html_bytes": len(html.encode("utf-8", "replace")),
        # Squashed visible text + tel:/mailto: targets, for stage-5 claim corroboration.
        # Carried in crawl.json so schema_check.py works identically in fs and http mode.
        "asset_refs": referenced_assets(soup, url),
        "asset_basenames": sorted(referenced_basenames(html)),
        "claim_text": S.claim_norm(full_text + " " + " ".join(
            l["href"].split(":", 1)[1] for l in links
            if l["kind"] == "nonhttp" and l["href"]
            and l["href"].lower().startswith(("tel:", "mailto:")))),
    }


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

    robots_txt = ""
    rp = os.path.join(root, "robots.txt")
    if os.path.isfile(rp):
        robots_txt = read_text(rp)
    sitemap_xml = ""
    sp = os.path.join(root, "sitemap.xml")
    if os.path.isfile(sp):
        sitemap_xml = read_text(sp)

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

def fetch_site_files(sess, origin, timeout):
    """robots.txt and sitemap.xml, fetched before the walk because the sitemap seeds it."""
    robots_txt = sitemap_xml = ""
    try:
        rr = sess.get(origin + "/robots.txt", timeout=timeout)
        if rr.status_code == 200 and "html" not in rr.headers.get("Content-Type", "").lower():
            robots_txt = rr.text
    except Exception:
        pass
    try:
        sr = sess.get(origin + "/sitemap.xml", timeout=timeout)
        if sr.status_code == 200:
            sitemap_xml = sr.text
    except Exception:
        pass
    return robots_txt, sitemap_xml


def crawl_http(start, max_pages=300, timeout=20):
    import requests

    sess = requests.Session()
    sess.headers["User-Agent"] = "seo-repair-crawler/1.0 (+local audit)"
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

        rec = extract_page(path, r.text)
        rec["file"] = None
        rec["aliases"] = sorted({path, path + "index.html"} if path.endswith("/") else {path})
        rec["status"] = r.status_code
        rec["bytes"] = len(r.content)
        rec["final_url"] = r.url
        rec["redirected"] = len(r.history) > 0
        rec["redirect_chain"] = [h.status_code for h in r.history]
        rec["x_robots_tag"] = r.headers.get("X-Robots-Tag")
        pages.append(rec)

        if r.status_code == 200:
            for l in rec["links"]:
                if l["kind"] != "internal":
                    continue
                nxt = S.canonical_http_path(l["resolved"])
                if nxt not in seen:
                    queue.append(nxt)

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
    args = ap.parse_args()

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
         skipped, refs, names) = crawl_http(args.start, args.max_pages)
        source = args.start

    robots = S.Robots(robots_txt)
    sm_urls, sm_errs = S.parse_sitemap(sitemap_xml)

    # Infer production origin from sitemap/canonical when not supplied.
    base_url = args.base_url
    if not base_url:
        for cand in (sm_urls[0] if sm_urls else None,
                     next((p["canonical"] for p in pages if p.get("canonical")), None)):
            if cand and cand.startswith("http"):
                sp = urlsplit(cand)
                base_url = "{0}://{1}".format(sp.scheme, sp.netloc)
                break

    # Mark robots.txt crawl-blocking per page.
    for p in pages:
        allowed, rule = robots.check(p["url"])
        p["robots_allowed"] = allowed
        p["robots_rule"] = rule

    doc = {
        "meta": {
            "tool": "seo-repair/crawl.py",
            "version": 1,
            "mode": args.mode,
            "source": source,
            "base_url": base_url,
            "crawled_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "page_count": len(pages),
            "excluded_dirs_present": excluded,
            "skipped": skipped,
        },
        "robots": {
            "found": robots.found,
            "sitemaps": robots.sitemaps,
            "groups": [{"agents": g["agents"], "rules": [[a, p] for a, p in g["rules"]]} for g in robots.groups],
            "raw": robots.raw[:2000],
        },
        "sitemap": {"found": bool(sitemap_xml), "urls": sm_urls, "notes": sm_errs},
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
    if excluded:
        print("excluded build dirs: " + ", ".join("{0} ({1} files)".format(e["dir"], e["files"]) for e in excluded))
    if skipped:
        print("skipped {0} files".format(len(skipped)))
    print("wrote " + args.out)


if __name__ == "__main__":
    sys.exit(main())
