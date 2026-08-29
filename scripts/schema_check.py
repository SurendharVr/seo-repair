"""schema_check.py -- stage 5. Validates JSON-LD structure AND cross-checks its claims
against what the page actually says.

The second half is the point. Structured data that asserts a rating, a price, or an
address the page never shows is misleading markup -- it risks a manual action, and it
is the exact failure mode the checklist means by "avoid misleading or unsupported
structured data". A validator that only checks required properties will never catch it.

Usage:
  python schema_check.py --crawl crawl.json [--out schema.json]
"""
import argparse
import json
import re
from collections import defaultdict

import seolib as S

# Required / recommended properties for the types that actually earn rich results.
# Keep this list short and defensible rather than mirroring all of schema.org.
SPEC = {
    "Organization":      {"required": ["name"], "recommended": ["url", "logo"]},
    "LocalBusiness":     {"required": ["name", "address"], "recommended": ["telephone", "openingHoursSpecification", "url", "image"]},
    "FoodEstablishment": {"required": ["name", "address"], "recommended": ["telephone", "servesCuisine", "priceRange", "openingHoursSpecification"]},
    "Caterer":           {"required": ["name", "address"], "recommended": ["telephone", "areaServed", "priceRange"]},
    "Restaurant":        {"required": ["name", "address"], "recommended": ["telephone", "servesCuisine", "priceRange"]},
    "Product":           {"required": ["name"], "recommended": ["image", "description", "offers"]},
    "Article":           {"required": ["headline"], "recommended": ["author", "datePublished", "image"]},
    "BlogPosting":       {"required": ["headline"], "recommended": ["author", "datePublished", "image"]},
    "NewsArticle":       {"required": ["headline"], "recommended": ["author", "datePublished", "image"]},
    "FAQPage":           {"required": ["mainEntity"], "recommended": []},
    "Question":          {"required": ["name", "acceptedAnswer"], "recommended": []},
    "BreadcrumbList":    {"required": ["itemListElement"], "recommended": []},
    "Event":             {"required": ["name", "startDate", "location"], "recommended": ["offers", "image"]},
    "Recipe":            {"required": ["name"], "recommended": ["image", "recipeIngredient", "recipeInstructions"]},
    "Service":           {"required": ["name"], "recommended": ["provider", "areaServed"]},
    "Menu":              {"required": ["name"], "recommended": ["hasMenuSection"]},
    "WebSite":           {"required": ["name", "url"], "recommended": []},
    "Person":            {"required": ["name"], "recommended": ["jobTitle", "url"]},
}

# Claims corroborated by a token-coverage heuristic (formatting varies legitimately).
VERIFY_CLAIMS = ["aggregateRating", "offers", "priceRange", "telephone", "address"]

# `review` is deliberately NOT in that list. Review markup gets a dedicated, stricter
# check below: Google requires review rich results to correspond to reviews actually
# visible on the page, and a token bag hides the violation because generic words in a
# testimonial ("good", "order", "100") match elsewhere on the site while the reviewer's
# name -- the thing that identifies it as real -- does not.


def walk(node, path="$"):
    """Yield (path, dict) for every schema object, descending @graph and nested values."""
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            if k.startswith("@") and k != "@graph":
                continue
            yield from walk(v, "{0}.{1}".format(path, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, "{0}[{1}]".format(path, i))


def types_of(node):
    t = node.get("@type")
    if isinstance(t, str):
        return [t]
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str)]
    return []


def flatten_strings(node):
    """All scalar string/number values inside a schema node, for text corroboration."""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k.startswith("@"):
                continue
            out.extend(flatten_strings(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(flatten_strings(v))
    elif isinstance(node, (str, int, float)):
        out.append(str(node))
    return out


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def check_page(page, site_text_norm=""):
    """Return a list of schema findings for one crawled page.

    Claim corroboration needs the page's visible text. When that is unavailable the
    checks are SKIPPED, never guessed -- an empty comparison string would make every
    claim look absent and produce false accusations of misleading markup.
    """
    findings = []
    url = page["url"]
    page_text_norm = page.get("claim_text") or norm(page.get("_text") or "")

    if page["jsonld_errors"]:
        for e in page["jsonld_errors"]:
            findings.append({"id": "S5-SCHEMA-INVALID-JSON", "severity": "high", "page": url,
                             "message": "JSON-LD block does not parse.", "evidence": e})

    seen_types = set()
    for block in page["jsonld"]:
        for path, node in walk(block):
            if not isinstance(node, dict):
                continue
            for t in types_of(node):
                seen_types.add(t)
                spec = SPEC.get(t)
                if not spec:
                    continue
                for prop in spec["required"]:
                    if prop not in node:
                        findings.append({
                            "id": "S5-SCHEMA-REQUIRED-MISSING", "severity": "high", "page": url,
                            "message": "{0} is missing required property '{1}'.".format(t, prop),
                            "evidence": "{0} at {1}".format(t, path)})
                # depth: "$" and "$.@graph[n]" are top level; anything deeper is a
                # nested reference and is not the page's primary entity.
                top_level = path.count(".") <= (1 if "@graph" in path else 0)
                for prop in (spec["recommended"] if top_level else []):
                    if prop not in node:
                        findings.append({
                            "id": "S5-SCHEMA-RECOMMENDED-MISSING", "severity": "low", "page": url,
                            "message": "{0} is missing recommended property '{1}'.".format(t, prop),
                            "evidence": "{0} at {1}".format(t, path)})

            # Review markup: check each review's author name against the page. An author
            # who appears nowhere on the page means the review is not visible there, which
            # is a rich-results policy violation regardless of whether the review is real.
            if "review" in node and page_text_norm:
                revs = node["review"]
                revs = revs if isinstance(revs, list) else [revs]
                absent = []
                for r in revs:
                    if not isinstance(r, dict):
                        continue
                    author = r.get("author")
                    name = author.get("name") if isinstance(author, dict) else author
                    if not isinstance(name, str) or not name.strip():
                        continue
                    parts = [p for p in re.findall(r"[A-Za-z]{3,}", name)]
                    if parts and not any(norm(p) in page_text_norm for p in parts):
                        absent.append(name.strip())
                if absent:
                    findings.append({
                        "id": "S5-SCHEMA-UNSUPPORTED-CLAIM", "severity": "high", "page": url,
                        "message": "{0} of {1} reviews in structured data have authors who do "
                                   "not appear anywhere on this page. Review rich results "
                                   "require the reviews to be visible on the page that marks "
                                   "them up.".format(len(absent), len(revs)),
                        "evidence": "authors not on page: " + ", ".join(absent[:6])})

            # Claim corroboration. Scored by token coverage rather than exact substring,
            # because markup legitimately reformats what the page renders ("St" vs
            # "Street", address split across elements). Severity depends on WHERE the
            # supporting text lives:
            #   - not anywhere on the site  -> high: the claim may be fabricated
            #   - elsewhere on the site     -> low: normal for a shared LocalBusiness block
            #   - on this page              -> no finding
            for claim in VERIFY_CLAIMS:
                if claim not in node or not page_text_norm:
                    continue
                toks = claim_tokens(node[claim])
                if not toks:
                    continue
                on_page = coverage(toks, page_text_norm)
                if on_page >= 0.7:
                    continue
                on_site = coverage(toks, site_text_norm)

                # Common words inflate coverage: a fabricated review still scores ~50%
                # on words like "order" and "short". The identifying terms -- proper
                # nouns and numbers -- are what actually corroborate a claim, so judge
                # on those when the claim has any.
                dist = [t for t in toks if t[:1].isupper() or t.isdigit()]
                dist_site = coverage(dist, site_text_norm) if dist else 1.0
                missing = [t for t in (dist or toks) if norm(t) not in site_text_norm][:5]

                if on_site < 0.4 or (dist and dist_site < 0.34):
                    findings.append({
                        "id": "S5-SCHEMA-UNSUPPORTED-CLAIM", "severity": "high", "page": url,
                        "message": "'{0}' asserts content that is not corroborated anywhere on "
                                   "the site ({1}% of terms, {2}% of identifying terms found). "
                                   "Structured data must reflect real, visible information."
                                   .format(claim, int(on_site * 100), int(dist_site * 100)),
                        "evidence": "{0} -- not found: {1}".format(claim, ", ".join(missing))[:220]})
                elif on_page < 0.4:
                    findings.append({
                        "id": "S5-SCHEMA-CLAIM-OFFPAGE", "severity": "low", "page": url,
                        "message": "'{0}' is marked up here but its content is shown on other "
                                   "pages, not this one. Usually fine for a shared business "
                                   "block; confirm it is intended.".format(claim),
                        "evidence": "{0} -- {1}% of terms on this page, {2}% site-wide".format(
                            claim, int(on_page * 100), int(on_site * 100))})

    return findings, sorted(seen_types)


def claim_tokens(value):
    """Significant tokens from a claim value: words of 3+ chars and any number."""
    out = []
    for v in flatten_strings(value):
        if v.lower() in ("true", "false"):
            continue
        for tok in re.findall(r"[A-Za-z]{3,}|\d+", v):
            if tok.lower() not in ("the", "and", "for", "with", "http", "https", "www", "com"):
                out.append(tok)
    seen, uniq = set(), []
    for t in out:
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
    return uniq


def coverage(tokens, text_norm):
    """Fraction of claim tokens present in the normalized text."""
    if not tokens:
        return 1.0
    return sum(1 for t in tokens if norm(t) in text_norm) / len(tokens)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crawl", required=True)
    ap.add_argument("--out", default="schema.json")
    args = ap.parse_args()

    doc = S.jload(args.crawl)
    all_findings, by_page = [], {}

    # Pass 1: obtain visible text for every page (needed for site-wide corroboration).
    # crawl.py now carries `claim_text`, which works in http mode too and does not
    # require the source tree to still be on disk. Re-reading is a fallback only.
    for p in doc["pages"]:
        if p.get("claim_text"):
            p["_text"] = None
            continue
        # Re-derive visible text for corroboration. crawl.json stores counts, not the
        # body text, so read the file back when we have it.
        text = ""
        if p.get("file"):
            import os
            fp = os.path.join(doc["meta"]["source"], p["file"].replace("/", os.sep))
            if os.path.isfile(fp):
                from bs4 import BeautifulSoup
                import crawl as C
                soup = BeautifulSoup(C.read_text(fp), "lxml")
                for tag in soup(["script", "style", "noscript", "template"]):
                    tag.decompose()
                text = soup.get_text(" ")
                # tel: and mailto: targets count as visible contact details
                for a in soup.find_all("a"):
                    href = a.get("href") or ""
                    if href.startswith("tel:") or href.startswith("mailto:"):
                        text += " " + href.split(":", 1)[1]
        p["_text"] = text

    site_text_norm = "".join(
        (p.get("claim_text") or norm(p.get("_text") or "")) for p in doc["pages"])

    uncorroborated = [p["url"] for p in doc["pages"]
                      if not (p.get("claim_text") or p.get("_text"))]

    # Pass 2: validate, scoring each claim against this page and against the whole site.
    for p in doc["pages"]:
        f, types = check_page(p, site_text_norm)
        all_findings.extend(f)
        by_page[p["url"]] = types

    seen, deduped = set(), []
    for f in all_findings:
        k = (f["page"], f["id"], f["evidence"])
        if k in seen:
            continue
        seen.add(k)
        deduped.append(f)
    all_findings = deduped

    counts = defaultdict(int)
    for f in all_findings:
        counts[f["id"]] += 1

    S.jdump({"meta": doc["meta"], "types_by_page": by_page,
             "summary": dict(counts), "claim_checks_skipped": uncorroborated,
             "findings": all_findings}, args.out)

    print("{0} schema findings across {1} pages".format(len(all_findings), len(doc["pages"])))
    if uncorroborated:
        print("  NOTE: claim verification skipped (no page text) for {0} page(s): {1}".format(
            len(uncorroborated), ", ".join(uncorroborated[:5])))
    for k in sorted(counts):
        print("  {0}: {1}".format(k, counts[k]))
    print("wrote " + args.out)


if __name__ == "__main__":
    main()
