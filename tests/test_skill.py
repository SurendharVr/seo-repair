"""Regression tests for the sitemend skill.

These exist because this tool's only value is that its findings can be trusted. Every
test below pins down a calibration decision that was made deliberately after a real
false positive or false negative -- the kind of thing a well-meaning "improvement"
silently undoes. If a test fails, the question is not "how do I make it pass" but
"was this behaviour meant to change".

Fixtures are generated into a temp directory, so there are no fixture files to drift.

Run:  python tests/test_skill.py            (from the skill root)
      python -m unittest discover -s tests  (equivalent)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import audit          # noqa: E402
import crawl          # noqa: E402
import report         # noqa: E402
import schema_check   # noqa: E402
import sitemend     # noqa: E402
import seolib as S    # noqa: E402
import verify as V    # noqa: E402
import vitals          # noqa: E402


def page_html(title="A Perfectly Reasonable Page Title Here",
              desc=("A meta description of entirely unremarkable length that sits "
                    "comfortably inside the seventy to one hundred sixty character band."),
              body="<h1>Heading</h1><p>" + ("word " * 300) + "</p>",
              head_extra="", lang="en", viewport=True):
    vp = '<meta name="viewport" content="width=device-width, initial-scale=1">' if viewport else ""
    return (
        '<!doctype html><html lang="{lang}"><head><meta charset="utf-8">{vp}'
        "<title>{title}</title>"
        '<meta name="description" content="{desc}">{head_extra}</head>'
        "<body><main>{body}</main></body></html>"
    ).format(lang=lang, vp=vp, title=title, desc=desc, head_extra=head_extra, body=body)


class Fixture:
    """A throwaway site on disk."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="sitemend-test-")

    def write(self, relpath, content):
        full = os.path.join(self.root, relpath.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def write_bytes(self, relpath, nbytes):
        full = os.path.join(self.root, relpath.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(b"\0" * nbytes)
        return full

    def crawl(self):
        (pages, assets, robots, sitemap, extras, excluded,
         skipped, refs, names) = crawl.crawl_fs(self.root)
        rb = S.Robots(robots)
        for p in pages:
            allowed, rule = rb.check(p["url"])
            p["robots_allowed"] = allowed
            p["robots_rule"] = rule
        sm_urls, _ = S.parse_sitemap(sitemap)
        return {
            "meta": {"source": self.root, "mode": "fs", "page_count": len(pages),
                     "base_url": None, "excluded_dirs_present": excluded},
            "robots": {"found": bool(robots), "sitemaps": rb.sitemaps, "groups": [], "raw": robots},
            "sitemap": {"found": bool(sitemap), "urls": sm_urls, "notes": [],
                        "lastmods": S.parse_sitemap_lastmods(sitemap)},
            "assets": assets, "pages": pages, "extras": {},
            "referenced_assets": refs, "referenced_basenames": names,
        }

    def audit(self):
        return audit.audit(self.crawl())

    def ids(self):
        return [f["id"] for f in self.audit()["findings"]]

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)


# --------------------------------------------------------------- URL handling

class TestUrlMapping(Base):
    def test_both_conventions(self):
        """Clean-URL directories and flat .html files must both map correctly.
        Real sites in use here do it both ways."""
        self.assertEqual(S.file_to_urls("index.html")[0], "/")
        self.assertEqual(S.file_to_urls("about/index.html")[0], "/about/")
        self.assertEqual(S.file_to_urls("services.html")[0], "/services.html")

    def test_aliases_let_clean_links_resolve_to_flat_files(self):
        """A link written href="/services" must not report as broken on a site
        whose file is services.html."""
        _, aliases = S.file_to_urls("services.html")
        self.assertIn("/services", aliases)

    def test_index_collapsing(self):
        """REGRESSION: http mode fetched / and /index.html as two pages and called
        them a critical duplicate. Relative href="index.html" links are ordinary."""
        self.assertEqual(S.canonical_http_path("/index.html"), "/")
        self.assertEqual(S.canonical_http_path("/foo/index.html"), "/foo/")
        self.assertEqual(S.canonical_http_path("/services.html"), "/services.html")
        self.assertEqual(S.canonical_http_path("/"), "/")

    def test_no_broken_link_for_clean_url_to_flat_file(self):
        self.fx.write("index.html", page_html(
            body='<h1>H</h1><p><a href="/services">Services</a></p>' + "word " * 300))
        self.fx.write("services.html", page_html(title="Services Page With A Fine Title Here"))
        self.assertNotIn("S1-BROKEN-LINK", self.fx.ids())

    def test_any_non_http_scheme_is_not_a_page_link(self):
        """REGRESSION found by the 2026-10-16 audit: skype:, viber:, geo: and ftp: links
        resolved to /skype:echo123 and were reported as high S1-BROKEN-LINK."""
        for href in ("skype:echo123?call", "viber://chat?number=1", "geo:37.7,-122.4",
                     "ftp://files.x.org/a.zip", "mailto:a@b.c", "tel:+1"):
            with self.subTest(href=href):
                self.assertEqual(S.link_kind(href), "nonhttp")
        for href in ("https://x.org/", "http://x.org/", "/a", "page.html", "./a:b"):
            with self.subTest(href=href):
                self.assertEqual(S.link_kind(href), "link")

    def test_no_broken_link_for_app_scheme_links(self):
        self.fx.write("index.html", page_html(
            body='<h1>H</h1><p><a href="skype:echo123?call">Skype</a> ' + "word " * 300 + "</p>"))
        self.assertNotIn("S1-BROKEN-LINK", self.fx.ids())


class TestAbsoluteInternalLinks(Base):
    """REGRESSION found by the 2026-10-16 audit: links written as absolute URLs on the
    site's own origin were ignored, so every page of a Hugo/Jekyll-style site was a high
    S2-ORPHAN and S2-UNREACHABLE, and its real broken links went unreported."""

    def _findings(self, other=False):
        host = "https://other.org" if other else "https://site.org"
        self.fx.write("index.html", page_html(
            body='<h1>H</h1><p><a href="https://site.org/about/">About</a> ' + "word " * 300 + "</p>"))
        self.fx.write("about/index.html", page_html(
            title="About Page With A Fine Long Title",
            body='<h1>A</h1><p><a href="https://site.org/">Home</a> <a href="{0}/gone/">x</a> '
                 .format(host) + "more " * 300 + "</p>"))
        doc = self.fx.crawl()
        doc["meta"]["base_url"] = "https://site.org"
        return audit.audit(doc)["findings"]

    def test_same_origin_absolute_links_are_inlinks(self):
        ids = [f["id"] for f in self._findings()]
        self.assertNotIn("S2-ORPHAN", ids)
        self.assertNotIn("S2-UNREACHABLE", ids)

    def test_same_origin_absolute_link_to_a_missing_page_is_broken(self):
        broken = [f for f in self._findings() if f["id"] == "S1-BROKEN-LINK"]
        self.assertEqual([f["detail"]["target"] for f in broken], ["/gone/"])

    def test_another_origin_is_still_external(self):
        self.assertNotIn("S1-BROKEN-LINK", [f["id"] for f in self._findings(other=True)])


# --------------------------------------------------------------- exclusions

class TestExclusions(Base):
    def test_build_output_excluded(self):
        """REGRESSION: a dist/ copy of the site made every real page look like a
        duplicate. This is the single highest-volume false positive available."""
        self.fx.write("index.html", page_html())
        self.fx.write("dist/index.html", page_html())
        self.fx.write("node_modules/pkg/readme.html", page_html())
        doc = self.fx.crawl()
        self.assertEqual([p["url"] for p in doc["pages"]], ["/"])
        self.assertNotIn("S3-DUPLICATE-EXACT", self.fx.ids())

    def test_noindex_404_is_not_a_defect(self):
        """A noindex 404 is correct. Reporting it as a finding trains the user to
        ignore the report."""
        self.fx.write("index.html", page_html())
        self.fx.write("404.html", page_html(title="Not Found Page Title Goes Right Here",
                                            head_extra='<meta name="robots" content="noindex">'))
        res = self.fx.audit()
        self.assertIn("/404.html", [i["url"] for i in res["intentional"]])
        self.assertNotIn("S1-NOT-INDEXABLE", [f["id"] for f in res["findings"]])

    def test_robots_disallowed_noindex_is_deliberate(self):
        self.fx.write("index.html", page_html())
        self.fx.write("studio/index.html", page_html(
            title="Internal Studio Tool Page Title", head_extra='<meta name="robots" content="noindex">'))
        self.fx.write("robots.txt", "User-agent: *\nAllow: /\nDisallow: /studio/\n")
        res = self.fx.audit()
        self.assertIn("/studio/", [i["url"] for i in res["intentional"]])

    def test_sitemap_plus_blocked_is_critical(self):
        """The one case that IS always a defect: the site contradicting itself."""
        self.fx.write("index.html", page_html())
        self.fx.write("secret.html", page_html(title="Secret Page Title That Is Long Enough",
                                               head_extra='<meta name="robots" content="noindex">'))
        self.fx.write("sitemap.xml",
                      "<urlset><url><loc>https://e.com/</loc></url>"
                      "<url><loc>https://e.com/secret.html</loc></url></urlset>")
        f = self.fx.audit()["findings"]
        crit = [x for x in f if x["id"] == "S1-INDEX-CONTRADICTION"]
        self.assertTrue(crit, "sitemap-listed blocked page must be critical")
        self.assertEqual(crit[0]["severity"], "critical")

    def test_robots_blocking_the_homepage_is_not_deliberate(self):
        """REGRESSION found by the v1.0.3 review: `Disallow: /` with a sitemap that
        lists nothing filed every page as a deliberate exclusion, so Gate A read
        CLEAR 'every crawled page is indexable'."""
        self.fx.write("index.html", page_html())
        self.fx.write("about.html", page_html(title="About Us Page Title That Is Long Enough"))
        self.fx.write("robots.txt", "User-agent: *\nDisallow: /\n")
        self.fx.write("sitemap.xml", "<urlset></urlset>")
        res = self.fx.audit()
        gate_a = [g for g in res["gates"] if g["gate"] == "A"][0]
        self.assertEqual(gate_a["status"], "BLOCKED")
        self.assertIn("/", gate_a["pages"])
        crit = [f for f in res["findings"]
                if f["id"] == "S1-NOT-INDEXABLE" and f["severity"] == "critical"]
        self.assertTrue(crit, "a robots-blocked homepage is never a deliberate exclusion")


class TestRobotsGroups(unittest.TestCase):
    """REGRESSION found by the 2026-10-16 audit: only the first User-agent: * group was
    read. RFC 9309 combines every group that matches the same agent."""

    TXT = ("User-agent: *\nDisallow: /admin/\n\nUser-agent: Googlebot\nDisallow: /tmp/\n\n"
           "User-agent: *\nDisallow: /private/\n")

    def test_groups_for_the_same_agent_are_merged(self):
        r = S.Robots(self.TXT)
        self.assertEqual(r.check("/private/x"), (False, "Disallow: /private/"))
        self.assertEqual(r.check("/admin/x"), (False, "Disallow: /admin/"))
        self.assertEqual(r.check("/tmp/x"), (True, None))

    def test_a_named_agent_still_ignores_the_star_groups(self):
        r = S.Robots(self.TXT)
        self.assertEqual(r.check("/admin/x", agent="Googlebot"), (True, None))
        self.assertEqual(r.check("/tmp/x", agent="Googlebot"), (False, "Disallow: /tmp/"))


# --------------------------------------------------------------- Gate A

class TestGateA(Base):
    def test_no_stage3_findings_on_nonindexable_page(self):
        """Gate A in code: optimising a title Google will never read is waste, and
        raising it buries the indexability problem that actually matters."""
        self.fx.write("index.html", page_html())
        self.fx.write("hidden.html",
                      '<!doctype html><html><head><meta name="robots" content="noindex">'
                      "<title>x</title></head><body><p>thin</p></body></html>")
        for f in self.fx.audit()["findings"]:
            if "/hidden.html" in f["pages"]:
                self.assertLessEqual(f["stage"], 1,
                                     "stage {0} finding raised on a non-indexable page: {1}"
                                     .format(f["stage"], f["id"]))


class TestIndexContradictionAliases(Base):
    """REGRESSION found by the 2026-10-16 audit: a noindex services.html listed in the
    sitemap as /services was medium S1-NOT-INDEXABLE, not the critical contradiction."""

    def test_clean_url_in_sitemap_for_a_noindex_flat_file(self):
        self.fx.write("index.html", page_html(
            body='<h1>H</h1><p><a href="/services">s</a> ' + "word " * 300 + "</p>"))
        self.fx.write("services.html", page_html(
            title="Services Page With A Fine Title Here",
            head_extra='<meta name="robots" content="noindex">'))
        self.fx.write("sitemap.xml", "<urlset><url><loc>https://x.org/</loc></url>"
                                     "<url><loc>https://x.org/services</loc></url></urlset>")
        hit = [f for f in self.fx.audit()["findings"] if f["id"] == "S1-INDEX-CONTRADICTION"]
        self.assertEqual([f["pages"] for f in hit], [["/services.html"]])


# --------------------------------------------------------------- grouping

class TestGrouping(Base):
    def test_shared_component_grouped_once(self):
        """REGRESSION: one heading skip in a shared footer was counted five times,
        making a single fix look like five."""
        body = "<h1>T</h1><h2>S</h2><h5>Shared Footer Heading</h5><p>" + "word " * 300 + "</p>"
        for name in ("index.html", "a.html", "b.html"):
            self.fx.write(name, page_html(title="Distinct Title For " + name + " Here Ok",
                                          body=body))
        skips = [f for f in self.fx.audit()["findings"] if f["id"] == "S2-HEADING-SKIP"]
        self.assertEqual(len(skips), 1, "identical evidence must group into one finding")
        self.assertGreaterEqual(skips[0].get("affects", 1), 3)


    def test_distinct_pairs_are_not_merged(self):
        """REGRESSION: near-duplicate findings are pairwise. Grouping them by evidence
        merged two unrelated pairs that scored the same similarity into one finding
        claiming all four pages duplicated each other."""
        a = {"id": "S3-NEAR-DUPLICATE", "pages": ["/a", "/b"], "evidence": "estimated Jaccard 0.88",
             "severity": "high", "stage": 3, "message": "m"}
        b = {"id": "S3-NEAR-DUPLICATE", "pages": ["/c", "/d"], "evidence": "estimated Jaccard 0.88",
             "severity": "high", "stage": 3, "message": "m"}
        grouped = audit.group_findings([a, b])
        self.assertEqual(len(grouped), 2, "distinct page pairs must stay separate")
        self.assertEqual([sorted(g["pages"]) for g in grouped], [["/a", "/b"], ["/c", "/d"]])

    def test_cannibalization_suppressed_when_duplicate(self):
        """Duplicate content and cannibalization on the same pair are one root
        cause; reporting both doubles the noise."""
        html = page_html(title="Exactly The Same Title On Both Of These Pages")
        self.fx.write("index.html", html)
        self.fx.write("copy.html", html)
        ids = self.fx.ids()
        self.assertIn("S3-DUPLICATE-EXACT", ids)
        self.assertNotIn("S3-CANNIBALIZATION", ids)

    def test_heading_skip_evidence_names_the_skipping_heading(self):
        """REGRESSION found by the v1.0.3 review: the evidence quoted the first H4
        after the first H2, which was a legitimate heading, not the one that skipped."""
        body = ("<h1>Top</h1><h2>Two A</h2><h3>Three</h3><h4>Four fine</h4>"
                "<h2>Two B</h2><h4>Skipped to four</h4><p>" + "word " * 300 + "</p>")
        self.fx.write("index.html", page_html(body=body))
        hits = [f for f in self.fx.audit()["findings"] if f["id"] == "S2-HEADING-SKIP"]
        self.assertEqual(len(hits), 1)
        self.assertIn("Skipped to four", hits[0]["evidence"])

    def _canonical_ids(self, canonical):
        self.fx.write("index.html", page_html(
            head_extra='<link rel="canonical" href="{0}">'.format(canonical)))
        doc = self.fx.crawl()
        doc["meta"]["base_url"] = "https://example.com"
        return [f["id"] for f in audit.audit(doc)["findings"]]

    def test_canonical_on_a_lookalike_domain_is_offsite(self):
        """REGRESSION found by the v1.0.3 review: a string-prefix test let
        https://example.community pass as https://example.com."""
        self.assertIn("S1-CANONICAL-OFFSITE", self._canonical_ids("https://example.community/"))

    def test_canonical_on_the_same_origin_is_not_offsite(self):
        self.assertNotIn("S1-CANONICAL-OFFSITE", self._canonical_ids("https://example.com/"))

    def _cannibal_pairs(self):
        pairs = set()
        for f in self.fx.audit()["findings"]:
            if f["id"] == "S3-CANNIBALIZATION":
                pairs.add(frozenset(f["pages"]))
        return pairs

    def _write(self, path, title, desc, tag):
        body = "<h1>{0}</h1><p>{1}</p>".format(tag, (tag + " ") * 300)
        self.fx.write(path, page_html(title=title, desc=desc, body=body))

    def test_templated_siblings_do_not_cannibalize(self):
        """REGRESSION from the first real-site run (a catering site): five menu pages
        sharing 'catering menu set dish' template words each flagged against the other
        four, ten high findings for pages that target five different occasions. Terms
        on a large share of the site are template words, not intent."""
        for occ in ("lunch", "dinner", "tiffin", "housewarming", "seemantham"):
            self._write("menu/%s/index.html" % occ,
                        "%s Catering Menu Pure Veg Sets Kitchen" % occ.title(),
                        "Every %s set menu dish listed for Pure Veg Catering in Kitchen "
                        "menus and sets across the city." % occ, occ)
        for n, t in enumerate(("Alpha Gallery Photos", "Bravo Contact Form", "Charlie Hiring")):
            self._write("p%d/index.html" % n, t, "Distinct %s page content here." % t, t.split()[0])
        self.assertEqual(self._cannibal_pairs(), set())

    def test_real_cannibal_pair_still_flagged_among_templated_siblings(self):
        """The template-word filter must not blind the check: two pages chasing the
        same distinguishing terms are still reported."""
        for occ in ("lunch", "dinner", "tiffin", "housewarming", "seemantham"):
            self._write("menu/%s/index.html" % occ,
                        "%s Catering Menu Pure Veg Sets Kitchen" % occ.title(),
                        "Every %s set menu dish listed for Pure Veg Catering in Kitchen "
                        "menus and sets across the city." % occ, occ)
        for p, tag in (("wedding-a", "alpha"), ("wedding-b", "bravo")):
            self._write(p + "/index.html", "Chennai Wedding Reception Hall Booking",
                        "Book a Chennai wedding reception hall with parking.", tag)
        self.assertEqual(self._cannibal_pairs(),
                         {frozenset(("/wedding-a/", "/wedding-b/"))})

    def test_grouping_a_site_wide_finding_is_linear(self):
        """A finding with the same evidence on every page (no breadcrumbs, no Open Graph
        tags) groups into one. Checking membership in the page list made that quadratic:
        17 s for 40,000 pages, and 9 s of a 10,000-page audit."""
        fs = [{"id": "S2-BREADCRUMB-MISSING", "pages": ["/p%d" % i],
               "evidence": "jsonld types: none", "severity": "low", "stage": 2,
               "message": "m"} for i in range(40000)]
        fs.append(dict(fs[0]))                     # the same page twice counts once
        t = time.perf_counter()
        grouped = audit.group_findings(fs)
        self.assertLess(time.perf_counter() - t, 3.0)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["affects"], 40000)
        self.assertEqual(len(grouped[0]["pages"]), 40000)


class TestPairwiseCandidates(unittest.TestCase):
    """The near-duplicate and cannibalization checks compare only candidate pairs. The
    filters must be lossless at the current thresholds: a pair the exhaustive loop would
    report and the filter drops is a silent false negative, the worst kind for a tool
    whose findings have to be trusted."""

    def _sig_pages(self, seed=11):
        import random
        rnd = random.Random(seed)
        pages = []
        for _ in range(12):                       # 12 clusters of 25 around a base
            base = [rnd.getrandbits(61) for _ in range(S.MINHASH_N)]
            for _ in range(25):
                sig = list(base)
                for pos in rnd.sample(range(S.MINHASH_N), rnd.randint(0, 30)):
                    sig[pos] = rnd.getrandbits(61)
                pages.append({"minhash": sig, "word_count": 400})
        return pages

    def test_near_duplicate_candidates_include_every_pair_at_the_threshold(self):
        pages = self._sig_pages()
        cand = set(audit.near_dup_candidates(pages))
        hits = 0
        for i in range(len(pages)):
            for j in range(i + 1, len(pages)):
                sim = S.minhash_similarity(pages[i]["minhash"], pages[j]["minhash"])
                if sim >= audit.NEAR_DUP:
                    hits += 1
                    self.assertIn((i, j), cand, "pair at {0:.3f} not compared".format(sim))
        self.assertGreater(hits, 100, "fixture must put pairs on both sides of 0.80")

    def test_worst_case_near_duplicate_is_a_candidate(self):
        """12 differing values, one in each of the first 12 bands: the most a pair at
        0.80 can differ. Only the last four bands still match."""
        a = list(range(S.MINHASH_N))
        b = list(a)
        r = S.MINHASH_N // audit.BANDS
        for band in range(12):
            b[band * r] = -1
        self.assertGreaterEqual(S.minhash_similarity(a, b), audit.NEAR_DUP)
        pages = [{"minhash": a, "word_count": 400}, {"minhash": b, "word_count": 400}]
        self.assertEqual(audit.near_dup_candidates(pages), [(0, 1)])

    def test_unrelated_pages_are_not_candidates(self):
        """The point of the filter: 300 unrelated pages give no candidates."""
        import random
        rnd = random.Random(5)
        pages = [{"minhash": [rnd.getrandbits(61) for _ in range(S.MINHASH_N)],
                  "word_count": 400} for _ in range(300)]
        self.assertEqual(audit.near_dup_candidates(pages), [])

    def test_short_pages_are_not_candidates(self):
        sig = list(range(S.MINHASH_N))
        pages = [{"minhash": sig, "word_count": 400}, {"minhash": sig, "word_count": 49}]
        self.assertEqual(audit.near_dup_candidates(pages), [])

    def _jaccard(self, a, b):
        return len(a & b) / len(a | b)

    def _term_sets(self, seed=3, n=600):
        """Template words on a quarter of the pages each (under the 35% boiler cut),
        clusters sharing 6 to 11 of 12 terms, Zipf filler."""
        import random
        rnd = random.Random(seed)
        vocab = ["t%d" % i for i in range(800)]
        weights = [1 / (i + 1) for i in range(800)]
        sigs, urls = {}, []
        for i in range(n):
            s = {"section%d" % (i % 4)}
            if i < 240:
                core = random.Random(i // 12).sample(vocab[100:], 12)
                s |= set(core[:rnd.randint(6, 11)])
            while len(s) < 12:
                s.add(rnd.choices(vocab, weights)[0])
            urls.append("/%d" % i)
            sigs[urls[-1]] = s
        return urls, sigs

    def test_cannibal_candidates_include_every_pair_at_the_threshold(self):
        urls, sigs = self._term_sets()
        cand = set(audit.cannibal_candidates(urls, sigs))
        hits = 0
        for i in range(len(urls)):
            for j in range(i + 1, len(urls)):
                if self._jaccard(sigs[urls[i]], sigs[urls[j]]) >= audit.CANNIBAL_TERMS:
                    hits += 1
                    self.assertIn((i, j), cand)
        self.assertGreater(hits, 100, "fixture must put pairs on both sides of 0.60")

    def test_cannibal_candidates_handle_short_and_empty_sets(self):
        """After template words are removed a page can keep 1 or 0 terms. One shared
        term out of one is Jaccard 1.0 and must be compared; an empty set never is."""
        urls = ["/a", "/b", "/c"]
        sigs = {"/a": {"widget"}, "/b": {"widget"}, "/c": set()}
        self.assertEqual(audit.cannibal_candidates(urls, sigs), [(0, 1)])

    def test_templated_terms_do_not_make_every_pair_a_candidate(self):
        """Guard against the rejected design: indexing every term makes 71% of these
        pairs candidates (prefix filtering: 4%), and on a 4,000-page templated set it
        ran slower than comparing every pair."""
        urls, sigs = self._term_sets()
        total = len(urls) * (len(urls) - 1) // 2
        self.assertLess(len(audit.cannibal_candidates(urls, sigs)), total // 10)


# --------------------------------------------------------------- thresholds

class TestThresholds(Base):
    def _title_ids(self, title):
        self.fx.write("index.html", page_html(title=title))
        return self.fx.ids()

    def test_62_char_title_not_flagged(self):
        """50-60 is a target, not a cliff -- the real constraint is pixel width.
        Flagging 62 chars trains the user to ignore low findings."""
        t = "A" * 62
        self.assertNotIn("S3-TITLE-LONG", self._title_ids(t))

    def test_70_char_title_flagged(self):
        self.assertIn("S3-TITLE-LONG", self._title_ids("A" * 70))


# --------------------------------------------------------------- headings

class TestHeadingOrder(Base):
    def test_document_order_preserved(self):
        """REGRESSION: headings were collected level-by-level, silently sorting them.
        That turned 'H2 then H5' into 'the page uses h3 and h5'."""
        self.fx.write("index.html", page_html(
            body="<h1>Alpha</h1><h2>Bravo</h2><h3>Charlie</h3><h2>Delta</h2><h5>Echo</h5>"
                 "<p>" + "word " * 300 + "</p>"))
        heads = self.fx.crawl()["pages"][0]["headings"]
        self.assertEqual([h["text"] for h in heads],
                         ["Alpha", "Bravo", "Charlie", "Delta", "Echo"])
        self.assertEqual([h["level"] for h in heads], [1, 2, 3, 2, 5])

    def test_skip_names_the_real_pair(self):
        """The reported jump must be the actual adjacent pair (H2->H5), not an
        artefact of sorted levels (H3->H5)."""
        self.fx.write("index.html", page_html(
            body="<h1>Alpha</h1><h2>Bravo</h2><h3>Charlie</h3><h2>Delta</h2><h5>Echo</h5>"
                 "<p>" + "word " * 300 + "</p>"))
        skips = [f for f in self.fx.audit()["findings"] if f["id"] == "S2-HEADING-SKIP"]
        self.assertTrue(skips)
        self.assertIn("H2 -> H5", skips[0]["evidence"])
        self.assertIn("Echo", skips[0]["evidence"])

    def test_well_formed_headings_not_flagged(self):
        self.fx.write("index.html", page_html(
            body="<h1>A</h1><h2>B</h2><h3>C</h3><h2>D</h2><p>" + "word " * 300 + "</p>"))
        self.assertNotIn("S2-HEADING-SKIP", self.fx.ids())


# --------------------------------------------------------------- images

class TestImages(Base):
    def test_relative_image_size_is_checked(self):
        """REGRESSION: the size lookup only fired for root-relative src, so a site
        using assets/x.png was never checked -- and said nothing rather than
        admitting it could not check."""
        self.fx.write_bytes("assets/big.jpg", 300 * 1024)
        self.fx.write("services.html", page_html(
            title="Services Page With Heavy Imagery Here",
            body='<h1>S</h1><img src="assets/big.jpg" alt="big" width="10" height="10">'
                 "<p>" + "word " * 300 + "</p>"))
        heavy = [f for f in self.fx.audit()["findings"] if f["id"] == "S3-IMG-HEAVY"]
        self.assertTrue(heavy, "relative image src must be resolved and size-checked")
        self.assertIn("assets/big.jpg", heavy[0]["evidence"])

    def test_small_image_not_flagged(self):
        self.fx.write_bytes("assets/small.jpg", 10 * 1024)
        self.fx.write("index.html", page_html(
            body='<h1>S</h1><img src="assets/small.jpg" alt="s" width="10" height="10">'
                 "<p>" + "word " * 300 + "</p>"))
        self.assertNotIn("S3-IMG-HEAVY", self.fx.ids())

    def test_empty_alt_is_not_missing_alt(self):
        """alt="" on a decorative image is correct, and is not the same as no alt."""
        self.fx.write("index.html", page_html(
            body='<h1>S</h1><img src="/d.png" alt="" width="1" height="1">'
                 "<p>" + "word " * 300 + "</p>"))
        self.assertNotIn("S3-IMG-ALT-MISSING", self.fx.ids())


# --------------------------------------------------------------- gates

class TestGates(Base):
    def test_gates_are_emitted_by_audit(self):
        self.fx.write("index.html", page_html())
        gates = self.fx.audit()["gates"]
        self.assertEqual([g["gate"] for g in gates], ["A", "B", "C"])

    def test_gate_a_clear_when_every_page_indexable(self):
        """REGRESSION: the report re-derived Gate A from stage-1 severity, so a site
        with no robots.txt (stage-1 high) but every page indexable printed
        'Gate A: BLOCKED' beside a rule about non-indexable pages."""
        self.fx.write("index.html", page_html())   # no robots.txt, no sitemap
        res = self.fx.audit()
        ids = [f["id"] for f in res["findings"]]
        self.assertIn("S1-ROBOTS-MISSING", ids)
        gate_a = [g for g in res["gates"] if g["gate"] == "A"][0]
        self.assertEqual(gate_a["status"], "CLEAR",
                         "Gate A is about non-indexable pages, not stage-1 severity")

    def test_gate_a_blocked_names_the_pages(self):
        self.fx.write("index.html", page_html())
        self.fx.write("blocked.html", page_html(
            title="A Blocked Page Title Long Enough Here",
            head_extra='<meta name="robots" content="noindex">'))
        self.fx.write("robots.txt", "User-agent: *\nAllow: /\n")   # not deliberate
        gate_a = [g for g in self.fx.audit()["gates"] if g["gate"] == "A"][0]
        self.assertEqual(gate_a["status"], "BLOCKED")
        self.assertIn("/blocked.html", gate_a["pages"])

    def test_gate_c_blocks_on_open_critical(self):
        html = page_html(title="Identical Title Across Two Different Pages")
        self.fx.write("index.html", html)
        self.fx.write("dupe.html", html)
        gate_c = [g for g in self.fx.audit()["gates"] if g["gate"] == "C"][0]
        self.assertEqual(gate_c["status"], "BLOCKED")


class TestReport(Base):
    GATES = {"gates": [{"gate": "A", "name": "n", "rule": "r", "status": "CLEAR", "detail": "d"}]}

    def test_report_states_how_many_pages_were_rendered(self):
        """A rendered crawl that silently rendered 50 of 200 pages is a coverage claim the
        reader cannot check; the honesty contract says to state it."""
        meta = {"source": "https://x.test/", "page_count": 5, "mode": "http",
                "render": True, "rendered_pages": 2, "render_limit": 2}
        md = report.build_markdown({"meta": meta, "pages": []}, self.GATES, [], None, "x.test")
        self.assertIn("2 of 5 pages", md)
        self.assertIn("limit 2", md)

    def test_plain_report_says_nothing_about_rendering(self):
        meta = {"source": "https://x.test/", "page_count": 5, "mode": "http"}
        md = report.build_markdown({"meta": meta, "pages": []}, self.GATES, [], None, "x.test")
        self.assertNotIn("JavaScript rendering", md)

    def test_audit_records_whether_schema_was_checked(self):
        self.fx.write("index.html", page_html())
        doc = self.fx.crawl()
        self.assertIs(audit.audit(doc, None, [])["schema_checked"], True)
        self.assertIs(audit.audit(doc)["schema_checked"], False)

    def test_clean_schema_run_counts_as_stage_5_assessed(self):
        """REGRESSION found by the v1.0.3 review: a schema check that ran and found
        nothing was reported 'not-measured'."""
        self.assertTrue(report.stage5_ran({"meta": {"schema_checked": True}}, []))
        self.assertFalse(report.stage5_ran({"meta": {}}, []))
        self.assertTrue(report.stage5_ran({"meta": {}}, [{"stage": 5}]))

    def test_rerun_block_uses_the_cli_for_both_modes(self):
        """REGRESSION found by the v1.0.3 review: the block omitted schema_check and
        passed a URL to crawl.py --root."""
        crawl_doc = {"meta": {"source": "https://x.test/", "page_count": 0, "mode": "http"},
                     "pages": []}
        fdoc = {"gates": [{"gate": "A", "name": "n", "rule": "r", "status": "CLEAR",
                           "detail": "d"}]}
        md = report.build_markdown(crawl_doc, fdoc, [], None, "x.test")
        self.assertIn('sitemend.py audit "https://x.test/"', md)
        self.assertNotIn("--root", md)


# --------------------------------------------------------------- cli

class TestCli(Base):
    def test_audit_writes_every_artifact_and_edits_nothing(self):
        self.fx.write("index.html", page_html())
        before = sorted(os.listdir(self.fx.root))
        out = os.path.join(tempfile.mkdtemp(prefix="sitemend-out-"), "o")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        findings = sitemend.audit(self.fx.root, out, 200, None)
        names = os.listdir(out)
        for want in ("crawl.json", "schema.json", "findings.json", "report-data.json",
                     "dashboard.html"):
            self.assertIn(want, names)
        self.assertTrue(any(n.startswith("SEO-REPORT-") for n in names))
        self.assertEqual(sorted(os.listdir(self.fx.root)), before, "audit must not touch the site")
        self.assertGreaterEqual(sitemend.worst_open(findings), -1)

    def test_verify_with_the_baseline_inside_out_still_compares(self):
        """REGRESSION found by the 2026-10-16 audit: README's `verify --baseline
        sitemend-out/findings.json` uses the default --out sitemend-out, so the audit
        overwrote the baseline before verify.py read it, and the gate always passed."""
        self.fx.write("index.html", page_html())
        out = os.path.join(tempfile.mkdtemp(prefix="sitemend-out-"), "o")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        sitemend.audit(self.fx.root, out, 200, None)
        self.fx.write("index.html", page_html(
            body='<h1>H</h1><p><a href="/gone/">x</a> ' + "word " * 300 + "</p>"))
        r = subprocess.run(
            [sys.executable, os.path.join(sitemend.HERE, "sitemend.py"), "verify", self.fx.root,
             "--baseline", os.path.join(out, "findings.json"), "--out", out],
            capture_output=True, text=True, encoding="utf-8",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertIn("REGRESSION GATE: FAIL", r.stdout)
        self.assertNotEqual(r.returncode, 0)

    def test_rerun_does_not_audit_its_own_report(self):
        """REGRESSION: with --out inside the audited folder (the default when run as
        `audit .`), the second run crawled the first run's dashboard.html and reported
        7 extra findings on the demo site."""
        self.fx.write("index.html", page_html())
        out = os.path.join(self.fx.root, "sitemend-out")
        counts = []
        for _ in range(2):
            findings = sitemend.audit(self.fx.root, out, 200, None)
            counts.append(len(json.load(open(findings, encoding="utf-8"))["findings"]))
        self.assertEqual(counts[0], counts[1])

    def test_worst_open_ranks_severity(self):
        path = os.path.join(self.fx.root, "f.json")
        with open(path, "w") as f:
            json.dump({"findings": [{"severity": "low"}, {"severity": "high"}]}, f)
        self.assertEqual(sitemend.SEVERITY[sitemend.worst_open(path)], "high")

    def test_output_folder_ignores_itself(self):
        """REGRESSION found by the v1.0.3 review: `audit .` left a full-text crawl.json
        in the audited repo with nothing stopping `git add -A`."""
        self.fx.write("index.html", page_html())
        out = os.path.join(tempfile.mkdtemp(prefix="sitemend-out-"), "o")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        sitemend.audit(self.fx.root, out, 200, None)
        with open(os.path.join(out, ".gitignore")) as f:
            self.assertEqual(f.read(), "*\n")

    def _verify_args(self):
        d = tempfile.mkdtemp(prefix="sitemend-verify-")
        self.addCleanup(shutil.rmtree, d, True)
        base, cur = os.path.join(d, "b.json"), os.path.join(d, "c.json")
        with open(base, "w") as f:
            json.dump({"findings": []}, f)
        with open(cur, "w") as f:
            json.dump({"findings": [{"id": "X", "severity": "high", "pages": ["/"],
                                     "evidence": "e", "message": "m"}]}, f)
        return ["--baseline", base, "--current", cur, "--out", os.path.join(d, "v.json")]

    def test_failed_regression_gate_is_a_result_not_a_crash(self):
        """REGRESSION found by the v1.0.3 review: verify.py exits 1 on a FAILED gate and
        run() reported that as 'verify.py failed:'."""
        r = sitemend.run("verify.py", *self._verify_args(), ok=(0, 1))
        self.assertEqual(r.returncode, 1)
        self.assertIn("REGRESSION GATE: FAIL", r.stdout)

    def test_unexpected_exit_codes_still_abort(self):
        with self.assertRaises(SystemExit):
            sitemend.run("verify.py", *self._verify_args())

    def test_fail_on_ignores_judgement_calls(self):
        path = os.path.join(self.fx.root, "f.json")
        with open(path, "w") as f:
            json.dump({"findings": [{"severity": "high", "confidence": "medium"},
                                    {"severity": "low"}]}, f)
        self.assertEqual(sitemend.SEVERITY[sitemend.worst_open(path)], "low")

    def test_adding_a_sibling_reference_page_passes_the_gate(self):
        """The 2026-10-16 review's reproduction, end to end: a docs site with hx-get,
        hx-post and hx-put reference pages gains hx-patch. The new cannibalization
        findings are advisory and the gate passes."""
        import random
        rnd = random.Random(3)
        words = ["w%d" % i for i in range(4000)]
        topics = ["install", "config", "routing", "caching", "testing", "deploy", "plugins",
                  "themes", "cli", "faq", "security", "migration"]
        verbs = ["get", "post", "put"]

        def nav():
            return " ".join('<a href="/{0}/">{0}</a>'.format(t)
                            for t in topics + ["hx-" + v for v in verbs])

        def write(slug, title, desc):
            body = "<h1>{0}</h1><p>{1}</p><nav>{2}</nav>".format(
                title, " ".join(rnd.choice(words) for _ in range(400)), nav())
            self.fx.write((slug + "/" if slug else "") + "index.html",
                          page_html(title=title, desc=desc, body=body))

        def verb(v):
            write("hx-" + v, "hx-%s attribute reference for x" % v,
                  "The hx-%s attribute issues a %s request to the specified url and swaps "
                  "the returned element." % (v, v.upper()))

        write("", "X framework documentation home",
              "Documentation for the x framework: guides, reference and every attribute it supports.")
        for t in topics:
            write(t, "%s guide for the x framework docs" % t.title(),
                  "How %s works in the x framework, with examples and every option explained "
                  "in one place." % t)
        for v in verbs:
            verb(v)
        out = os.path.join(tempfile.mkdtemp(prefix="sitemend-out-"), "o")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        sitemend.audit(self.fx.root, out, 200, None)
        verbs.append("patch")
        verb("patch")
        write("", "X framework documentation home",
              "Documentation for the x framework: guides, reference and every attribute it supports.")
        r = subprocess.run(
            [sys.executable, os.path.join(sitemend.HERE, "sitemend.py"), "verify", self.fx.root,
             "--baseline", os.path.join(out, "findings.json"), "--out", out + "2"],
            capture_output=True, text=True, encoding="utf-8",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.addCleanup(shutil.rmtree, out + "2", True)
        self.assertIn("REGRESSION GATE: PASS", r.stdout)
        self.assertEqual(r.returncode, 0)
        with open(os.path.join(out + "2", "verify.json"), encoding="utf-8") as fh:
            new = [f["id"] for f in json.load(fh)["new"]]
        self.assertIn("S3-CANNIBALIZATION", new)


# --------------------------------------------------------------- schema

class TestSchemaReferences(unittest.TestCase):
    """REGRESSION from the first real-site run: a nested {"@id": ".../#business", "name"}
    on eight Menu/Service pages was reported as a FoodEstablishment missing its address,
    though the full node lives on another page. A reference is not a definition."""

    FULL = {"@type": "FoodEstablishment", "@id": "https://x.test/#b", "name": "X",
            "address": "1 Some Street"}
    STUB = {"@type": "Menu", "name": "Lunch",
            "provider": {"@type": "FoodEstablishment", "@id": "https://x.test/#b", "name": "X"}}

    def _required(self, known):
        page = {"url": "/menu/", "jsonld": [self.STUB], "jsonld_errors": []}
        f, _ = schema_check.check_page(page, "", known)
        return [x for x in f if x["id"] == "S5-SCHEMA-REQUIRED-MISSING"]

    def test_stub_resolved_to_a_full_definition_elsewhere_is_not_flagged(self):
        known = schema_check.known_props([{"jsonld": [self.FULL]}, {"jsonld": [self.STUB]}])
        self.assertEqual(self._required(known), [])

    def test_stub_with_no_definition_anywhere_is_still_flagged(self):
        known = schema_check.known_props([{"jsonld": [self.STUB]}])
        self.assertTrue(self._required(known))


class TestSchemaClaims(unittest.TestCase):
    REVIEW = {"@type": "LocalBusiness", "name": "X", "address": "Some Street",
              "review": [{"@type": "Review",
                          "author": {"@type": "Person", "name": "Jane Doe"},
                          "reviewBody": "Great service."}]}

    def _check(self, claim_text):
        page = {"url": "/", "jsonld": [self.REVIEW], "jsonld_errors": []}
        if claim_text is not None:
            page["claim_text"] = S.claim_norm(claim_text)
            page["visible_text"] = claim_text.casefold()
        f, _ = schema_check.check_page(page, page.get("claim_text", ""))
        return [x for x in f if x["id"] == "S5-SCHEMA-UNSUPPORTED-CLAIM"]

    def _review_by(self, author, page_text):
        biz = dict(self.REVIEW, review=[{"@type": "Review", "reviewBody": "ok",
                                         "author": {"@type": "Person", "name": author}}])
        page = {"url": "/", "jsonld": [biz], "jsonld_errors": [],
                "claim_text": S.claim_norm(page_text), "visible_text": page_text.casefold()}
        f, _ = schema_check.check_page(page, page["claim_text"])
        return [x for x in f if x["id"] == "S5-SCHEMA-UNSUPPORTED-CLAIM"]

    def test_name_inside_a_longer_word_is_not_a_match(self):
        """REGRESSION found by the v1.0.3 review: 'ann' matched inside 'channel'."""
        self.assertTrue(self._review_by("Ann Lee", "Our channel partners. Some Street"))

    def test_short_names_are_checked(self):
        self.assertTrue(self._review_by("Li Wu", "Nothing about them here. Some Street"))
        self.assertFalse(self._review_by("Li Wu", "Li Wu said it was great. Some Street"))

    def test_non_latin_names_are_checked(self):
        tamil = "ராஜேஷ்"
        self.assertTrue(self._review_by(tamil, "Nothing about them here. Some Street"))
        self.assertFalse(self._review_by(tamil, "Review by " + tamil + ". Some Street"))

    def test_initial_only_tokens_are_ignored(self):
        self.assertFalse(self._review_by("Varsha B.", "Varsha said it was lovely. Some Street"))

    def test_visible_review_not_flagged(self):
        self.assertEqual(self._check("Jane Doe said: Great service. Some Street"), [])

    def test_absent_review_flagged(self):
        hits = self._check("This page never mentions any reviewer at all. Some Street")
        self.assertTrue(hits)
        self.assertIn("Jane Doe", hits[0]["evidence"])

    def test_no_page_text_means_skip_not_accuse(self):
        """REGRESSION and the worst bug this tool had: with no text to compare
        against, every author read as absent, fabricating the exact policy-violation
        accusation the tool exists to make honestly. Silence is the only honest
        output when corroboration is impossible."""
        self.assertEqual(self._check(None), [])
        self.assertEqual(self._check(""), [])

    def test_nested_reference_not_flagged_for_recommended(self):
        """REGRESSION: every Service inside makesOffer[] was flagged for a missing
        provider, producing 128 findings on a 5-page site."""
        page = {"url": "/", "jsonld_errors": [], "claim_text": "",
                "jsonld": [{"@type": "Organization", "name": "N", "url": "u", "logo": "l",
                            "makesOffer": [{"@type": "Offer",
                                            "itemOffered": {"@type": "Service", "name": "S"}}]}]}
        f, _ = schema_check.check_page(page, "")
        self.assertEqual([x for x in f if x["id"] == "S5-SCHEMA-RECOMMENDED-MISSING"], [])

    def test_top_level_missing_required_is_flagged(self):
        page = {"url": "/", "jsonld_errors": [], "claim_text": "",
                "jsonld": [{"@type": "LocalBusiness", "name": "N"}]}   # no address
        f, _ = schema_check.check_page(page, "")
        self.assertTrue([x for x in f if x["id"] == "S5-SCHEMA-REQUIRED-MISSING"])


# --------------------------------------------------------------- verify

class TestVerify(unittest.TestCase):
    @staticmethod
    def f(fid, pages, evidence="e", severity="high", stage=2):
        return {"id": fid, "pages": pages, "evidence": evidence,
                "severity": severity, "stage": stage, "message": fid}

    def test_a_judgement_call_is_advisory_and_never_fails_the_gate(self):
        """REGRESSION found by the 2026-10-16 review: adding an hx-patch reference page
        next to hx-get/hx-post/hx-put failed the PR with three high S3-CANNIBALIZATION
        findings. Cannibalization is a judgement (confidence medium); it is reported
        as advisory and only measured findings fail the gate."""
        d = tempfile.mkdtemp(prefix="sitemend-verify-")
        self.addCleanup(shutil.rmtree, d, True)
        guess = dict(self.f("S3-CANNIBALIZATION", ["/hx-get/", "/hx-patch/"]), confidence="medium")
        paths = {}
        for name, items in (("b", []), ("c", [guess])):
            paths[name] = os.path.join(d, name + ".json")
            with open(paths[name], "w") as fh:
                json.dump({"findings": items}, fh)
        r = sitemend.run("verify.py", "--baseline", paths["b"], "--current", paths["c"],
                         "--out", os.path.join(d, "v.json"), ok=(0, 1))
        self.assertEqual(r.returncode, 0)
        self.assertIn("REGRESSION GATE: PASS", r.stdout)
        with open(os.path.join(d, "v.md"), encoding="utf-8") as fh:
            self.assertIn("| high (advisory) | S3-CANNIBALIZATION", fh.read())

    def test_a_measured_high_finding_still_fails_the_gate(self):
        self.assertTrue(V.blocks(self.f("S1-BROKEN-LINK", ["/"])))
        self.assertTrue(V.blocks(dict(self.f("S1-BROKEN-LINK", ["/"]), confidence="high")))
        self.assertFalse(V.blocks(dict(self.f("S3-NEAR-DUPLICATE", ["/a", "/b"]), confidence="medium")))
        self.assertFalse(V.blocks(self.f("S3-THIN", ["/a"], severity="medium")))

    def test_shrinking_page_list_is_changed_not_new(self):
        """REGRESSION: when a grouped finding's page list shrank after a partial fix,
        it read as one finding closed plus one brand-new finding -- wrong and
        alarming on every re-run."""
        base = [self.f("S2-HEADING-SKIP", ["/a", "/b", "/c"])]
        cur = [self.f("S2-HEADING-SKIP", ["/a", "/b"])]
        self.assertTrue(V.related(base[0], cur[0]))

    def test_new_broken_target_on_the_same_page_is_new(self):
        """REGRESSION found by the 2026-10-16 audit: fixing href="/a" while adding a
        broken href="/b" on the same page read as one 'changed' finding and passed."""
        a = dict(self.f("S1-BROKEN-LINK", ["/"], evidence='href="/a" -> /a'), detail={"target": "/a"})
        b = dict(self.f("S1-BROKEN-LINK", ["/"], evidence='href="/b" -> /b'), detail={"target": "/b"})
        self.assertFalse(V.related(a, b))

    def test_same_broken_target_spelled_differently_is_still_changed(self):
        a = dict(self.f("S1-BROKEN-LINK", ["/"], evidence='href="/a" -> /a'), detail={"target": "/a"})
        b = dict(self.f("S1-BROKEN-LINK", ["/", "/x"], evidence='href="a" -> /a'), detail={"target": "/a"})
        self.assertTrue(V.related(a, b))

    def test_finding_that_gets_worse_fails_the_gate(self):
        """REGRESSION found by the 2026-10-16 audit: S1-HOST-MISMATCH going high ->
        critical (a preview host appeared) paired as 'changed' and passed."""
        d = tempfile.mkdtemp(prefix="sitemend-verify-")
        self.addCleanup(shutil.rmtree, d, True)
        paths = {}
        for name, sev, ev in (("b", "high", "canonical: www.x.org (3)"),
                              ("c", "critical", "canonical: x-git-a.vercel.app (3)")):
            paths[name] = os.path.join(d, name + ".json")
            with open(paths[name], "w") as fh:
                json.dump({"findings": [self.f("S1-HOST-MISMATCH", ["/"], evidence=ev, severity=sev)]}, fh)
        r = sitemend.run("verify.py", "--baseline", paths["b"], "--current", paths["c"],
                         "--out", os.path.join(d, "v.json"), ok=(0, 1))
        self.assertEqual(r.returncode, 1)
        self.assertIn("REGRESSION GATE: FAIL", r.stdout)
        with open(os.path.join(d, "v.json"), encoding="utf-8") as fh:
            self.assertEqual([f["id"] for f in json.load(fh)["escalated"]], ["S1-HOST-MISMATCH"])

    def test_unrelated_finding_is_new(self):
        self.assertFalse(V.related(self.f("S2-ORPHAN", ["/a"]), self.f("S2-ORPHAN", ["/z"])))
        self.assertFalse(V.related(self.f("S2-ORPHAN", ["/a"]), self.f("S3-THIN", ["/a"])))

    def test_markdown_quotes_evidence_and_counts_only(self):
        """The PR comment quotes each finding's evidence verbatim (bar the pipe that
        would break the table) and its only numbers are counts from verify.json."""
        new = self.f("S1-NOT-INDEXABLE", ["/"], evidence="<meta> noindex | nofollow",
                     severity="critical")
        doc = {"gate": "FAIL", "counts": {"closed": 1, "new": 1, "changed": 0, "persisting": 7},
               "new": [new], "closed": [self.f("S3-THIN", ["/about"], severity="low")],
               "changed": [], "regressions": [new]}
        md = V.markdown(doc)
        self.assertTrue(md.startswith(V.MARKER))
        self.assertIn("regression gate FAIL", md)
        self.assertIn("1 new, 1 closed, 0 changed, 7 unchanged", md)
        self.assertIn("| critical | S1-NOT-INDEXABLE: S1-NOT-INDEXABLE | / | "
                      "&lt;meta> noindex \\| nofollow |", md)
        self.assertIn("### Closed", md)
        self.assertNotIn("### New (critical", V.markdown(dict(doc, regressions=[])))


# --------------------------------------------------------------- determinism

class TestDeterminism(unittest.TestCase):
    def test_minhash_is_stable_across_processes(self):
        """verify diffs a later crawl against a baseline, so hashing must not depend
        on Python's per-process hash salt."""
        text = "the quick brown fox jumps over the lazy dog and keeps on running"
        code = ("import sys; sys.path.insert(0, r'{0}'); import seolib;"
                "print(seolib.minhash({1!r})[:4])").format(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"), text)
        import subprocess
        outs = {subprocess.run([sys.executable, "-c", code], capture_output=True,
                               text=True).stdout.strip() for _ in range(2)}
        self.assertEqual(len(outs), 1, "minhash differed between processes: " + str(outs))
        self.assertEqual(S.minhash(text), S.minhash(text))

    def test_identical_text_is_perfectly_similar(self):
        a = S.minhash("alpha beta gamma delta epsilon zeta eta theta iota kappa")
        b = S.minhash("alpha beta gamma delta epsilon zeta eta theta iota kappa")
        self.assertEqual(S.minhash_similarity(a, b), 1.0)


# --------------------------------------------------------------- robustness

class TestRobustness(Base):
    def test_empty_site_does_not_crash(self):
        res = self.fx.audit()
        self.assertIsInstance(res["findings"], list)

    def test_single_page_no_robots_no_sitemap(self):
        self.fx.write("index.html", page_html())
        ids = self.fx.ids()
        self.assertIn("S1-ROBOTS-MISSING", ids)
        self.assertIn("S1-SITEMAP-MISSING", ids)

    def test_malformed_jsonld_reported_not_fatal(self):
        """The crawl records the parse error and keeps going; schema_check.py is what
        turns it into a finding, since it owns stage 5."""
        self.fx.write("index.html", page_html(
            head_extra='<script type="application/ld+json">{not json}</script>'))
        doc = self.fx.crawl()
        self.assertTrue(doc["pages"][0]["jsonld_errors"])
        f, _ = schema_check.check_page(doc["pages"][0], "")
        self.assertIn("S5-SCHEMA-INVALID-JSON", [x["id"] for x in f])


# --------------------------------------------------- srcset & unused assets

class TestSrcsetAndAssets(Base):
    def test_source_srcset_is_size_checked(self):
        """REGRESSION: only <img src> was size-checked, so a heavy .webp behind a
        <picture> was invisible -- yet it is exactly what the browser downloads."""
        self.fx.write_bytes("assets/hero.webp", 300 * 1024)
        self.fx.write_bytes("assets/hero.jpg", 10 * 1024)
        self.fx.write("index.html", page_html(
            body='<h1>H</h1><picture><source srcset="assets/hero.webp" type="image/webp">'
                 '<img src="assets/hero.jpg" alt="hero" width="9" height="9"></picture>'
                 "<p>" + "word " * 300 + "</p>"))
        heavy = [f for f in self.fx.audit()["findings"] if f["id"] == "S3-IMG-HEAVY"]
        self.assertTrue(heavy, "<source srcset> must be size-checked")
        self.assertIn("hero.webp", heavy[0]["evidence"])

    def test_source_does_not_trigger_alt_or_dims(self):
        """A <source> has no alt and inherits layout from its <img>; flagging it for
        either is a false positive."""
        self.fx.write_bytes("assets/a.webp", 1024)
        self.fx.write("index.html", page_html(
            body='<h1>H</h1><picture><source srcset="assets/a.webp">'
                 '<img src="assets/a.jpg" alt="x" width="9" height="9"></picture>'
                 "<p>" + "word " * 300 + "</p>"))
        ids = self.fx.ids()
        self.assertNotIn("S3-IMG-ALT-MISSING", ids)
        self.assertNotIn("S3-IMG-DIMS-MISSING", ids)

    def test_unused_media_is_flagged(self):
        self.fx.write_bytes("assets/orphan.jpg", 50 * 1024)
        self.fx.write("index.html", page_html())
        found = [f for f in self.fx.audit()["findings"] if f["id"] == "S3-ASSET-UNUSED"]
        self.assertTrue(found)
        self.assertIn("orphan.jpg", found[0]["evidence"])

    def test_absolute_url_reference_suppresses_unused(self):
        """REGRESSION: og:image and JSON-LD logo use absolute URLs on the production
        origin, which the path resolver does not treat as internal. They were flagged
        for deletion. Recommending removal of a file that IS used is the worst
        failure this check can have, so basename matching backstops it."""
        self.fx.write_bytes("assets/og-card.png", 50 * 1024)
        self.fx.write("index.html", page_html(
            head_extra='<meta property="og:image" content="https://example.com/assets/og-card.png">'))
        found = [f for f in self.fx.audit()["findings"] if f["id"] == "S3-ASSET-UNUSED"]
        self.assertEqual(found, [], "absolute-URL reference must suppress the finding")

    def test_jsonld_reference_suppresses_unused(self):
        self.fx.write_bytes("assets/logo-square.png", 50 * 1024)
        self.fx.write("index.html", page_html(
            head_extra='<script type="application/ld+json">'
                       '{"@type":"Organization","name":"N",'
                       '"logo":"https://example.com/assets/logo-square.png"}</script>'))
        found = [f for f in self.fx.audit()["findings"] if f["id"] == "S3-ASSET-UNUSED"]
        self.assertEqual(found, [])

    def test_css_reference_suppresses_unused(self):
        self.fx.write_bytes("assets/bg.jpg", 50 * 1024)
        self.fx.write("styles.css", ".hero{background-image:url('assets/bg.jpg')}")
        self.fx.write("index.html", page_html(head_extra='<link rel="stylesheet" href="/styles.css">'))
        found = [f for f in self.fx.audit()["findings"] if f["id"] == "S3-ASSET-UNUSED"]
        self.assertEqual(found, [])

    def test_favicon_never_flagged(self):
        """Hosts and crawlers fetch these by convention, never by an in-page link."""
        self.fx.write_bytes("favicon.ico", 50 * 1024)
        self.fx.write("index.html", page_html())
        found = [f for f in self.fx.audit()["findings"] if f["id"] == "S3-ASSET-UNUSED"]
        self.assertEqual(found, [])


# --------------------------------------------------- stage 5 ownership

class TestStageFiveOwnership(Base):
    def test_audit_merges_schema_findings(self):
        """Schema findings must be merged by audit.py so the GATES see them. Merging
        only in report.py left Gate C blind to stage-5 problems."""
        self.fx.write("index.html", page_html())
        doc = self.fx.crawl()
        schema = [{"id": "S5-SCHEMA-UNSUPPORTED-CLAIM", "severity": "high", "page": "/",
                   "message": "made up claim", "evidence": "e"}]
        res = audit.audit(doc, None, schema)
        self.assertIn("S5-SCHEMA-UNSUPPORTED-CLAIM", [f["id"] for f in res["findings"]])
        gate_c = [g for g in res["gates"] if g["gate"] == "C"][0]
        self.assertEqual(gate_c["status"], "BLOCKED",
                         "a high stage-5 finding must block gate C")

    def test_audit_emits_no_schema_findings_of_its_own(self):
        """audit.py's inline schema check could not corroborate anything and
        double-reported through the merge. schema_check.py owns stage 5."""
        self.fx.write("index.html", page_html(
            head_extra='<script type="application/ld+json">'
                       '{"@type":"LocalBusiness","name":"N","aggregateRating":'
                       '{"ratingValue":"5","reviewCount":"9"}}</script>'))
        stage5 = [f for f in self.fx.audit()["findings"] if f["stage"] == 5]
        self.assertEqual(stage5, [], "stage 5 must come only from schema_check.py")


class TestSchemaRespectsGateA(Base):
    """REGRESSION found by the 2026-10-16 audit: a noindex page's broken JSON-LD was a
    high S5 finding and blocked Gate C. Gate A covers stage 5 like every stage after 1."""

    def _s5(self, robots_meta):
        self.fx.write("index.html", page_html(
            body='<h1>H</h1><p><a href="/draft.html">d</a> ' + "word " * 300 + "</p>"))
        self.fx.write("draft.html", page_html(
            title="Draft Page With A Long Enough Title",
            head_extra=robots_meta + '<script type="application/ld+json">{"@type": bad}</script>'))
        doc = self.fx.crawl()
        schema = [f for p in doc["pages"] for f in schema_check.check_page(p)[0]]
        return [f["id"] for f in audit.audit(doc, None, schema)["findings"] if f["stage"] == 5]

    def test_noindex_page_gets_no_schema_findings(self):
        self.assertEqual(self._s5('<meta name="robots" content="noindex">'), [])

    def test_indexable_page_still_does(self):
        self.assertEqual(self._s5(""), ["S5-SCHEMA-INVALID-JSON"])


# ------------------------------------------------------- http mode: discovery

class TestGateBIsModeAware(Base):
    """A gate may only be CLEARED by a check that could actually run.

    http mode discovers pages via links and the sitemap, so it can never prove a site
    has no orphans -- a page neither linked nor listed is invisible to it. It once
    printed 'CLEAR -- no orphan or duplicate pages' on a site with two orphans, which
    also unblocked schema work on pages that were merge candidates.
    """

    def _gate_b(self, mode):
        self.fx.write("index.html", page_html(
            body='<h1>H</h1><p><a href="/about.html">About</a> ' + ("word " * 300) + "</p>"))
        self.fx.write("about.html", page_html(title="An About Page With A Fine Title"))
        doc = self.fx.crawl()
        doc["meta"]["mode"] = mode
        return [g for g in audit.audit(doc)["gates"] if g["gate"] == "B"][0]

    def test_fs_mode_can_clear_gate_b(self):
        self.assertEqual(self._gate_b("fs")["status"], "CLEAR")

    def test_http_mode_cannot_clear_gate_b(self):
        gate_b = self._gate_b("http")
        self.assertEqual(gate_b["status"], "NOT ASSESSED",
                         "silence from a check that could not run is not a pass")
        self.assertIn("fs mode", gate_b["detail"], "say what would clear it")

    def test_positive_findings_still_block_in_http_mode(self):
        """BLOCKED outranks NOT ASSESSED -- an orphan the http walk *did* see is real."""
        self.fx.write("index.html", page_html())
        self.fx.write("stray.html", page_html(title="A Stray Page Nothing Links To"))
        doc = self.fx.crawl()
        doc["meta"]["mode"] = "http"
        gate_b = [g for g in audit.audit(doc)["gates"] if g["gate"] == "B"][0]
        self.assertEqual(gate_b["status"], "BLOCKED")
        self.assertIn("/stray.html", gate_b["pages"])


class TestHttpSitemapSeeding(unittest.TestCase):
    """REGRESSION: crawl_http only followed links, so a page that was unlinked but
    listed in sitemap.xml was never fetched -- and was then reported as
    S1-SITEMAP-ORPHAN ('does not exist in the crawl') despite serving a 200."""

    @classmethod
    def setUpClass(cls):
        try:
            import requests  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("requests not installed (http mode is optional)")

        import functools
        import http.server
        import threading

        cls.root = tempfile.mkdtemp(prefix="sitemend-http-test-")
        body = "<h1>H</h1><p>" + ("word " * 300) + "</p>"

        def w(name, html):
            with open(os.path.join(cls.root, name), "w", encoding="utf-8") as f:
                f.write(html)

        w("index.html", page_html(body='<h1>H</h1><p><a href="/linked.html">L</a> ' +
                                       ("word " * 300) + "</p>"))
        w("linked.html", page_html(title="A Linked Page With An Adequate Title", body=body))
        # Listed in the sitemap, linked from nowhere. The whole point of the fixture.
        w("stray.html", page_html(title="A Stray Page With An Adequate Title", body=body))
        w("sitemap.xml",
          '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
          "<url><loc>https://example.com/</loc></url>"
          "<url><loc>https://example.com/linked.html</loc></url>"
          "<url><loc>https://example.com/stray.html</loc></url>"
          "<url><loc>https://example.com/gone.html</loc></url>"
          "</urlset>")

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=cls.root)
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.httpd.RequestHandlerClass.log_message = lambda *a, **k: None
        cls.base = "http://127.0.0.1:{0}/".format(cls.httpd.server_address[1])
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

        pages, _, robots, sitemap, _, _, cls.skipped, _, _ = crawl.crawl_http(cls.base)
        cls.paths = sorted(p["url"] for p in pages)
        for p in pages:
            p["robots_allowed"], p["robots_rule"] = True, None
        sm_urls, _ = S.parse_sitemap(sitemap)
        doc = {
            "meta": {"source": cls.base, "mode": "http", "page_count": len(pages),
                     "base_url": None, "excluded_dirs_present": []},
            "robots": {"found": bool(robots), "sitemaps": [], "groups": [], "raw": robots},
            "sitemap": {"found": bool(sitemap), "urls": sm_urls, "notes": []},
            "assets": [], "pages": pages, "extras": {},
            "referenced_assets": [], "referenced_basenames": [],
        }
        cls.found = [(f["id"], tuple(f["pages"])) for f in audit.audit(doc)["findings"]]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.root, ignore_errors=True)

    def ids_for(self, path):
        return [i for i, pgs in self.found if pgs == (path,)]

    def test_unlinked_sitemap_page_is_crawled(self):
        self.assertIn("/stray.html", self.paths,
                      "a sitemap entry must seed the crawl frontier")

    def test_live_unlinked_page_is_not_called_a_dead_sitemap_url(self):
        self.assertNotIn("S1-SITEMAP-ORPHAN", self.ids_for("/stray.html"),
                         "/stray.html returns 200; it is an orphan, not a dead sitemap URL")

    def test_live_unlinked_page_is_reported_as_an_orphan(self):
        self.assertIn("S2-ORPHAN", self.ids_for("/stray.html"))

    def test_genuinely_missing_sitemap_url_still_reported(self):
        """The fix must not silence the finding it was confused with: /gone.html is a
        404, so it must stay out of `pages` and keep firing S1-SITEMAP-ORPHAN."""
        self.assertNotIn("/gone.html", self.paths)
        self.assertIn("S1-SITEMAP-ORPHAN", self.ids_for("/gone.html"))


class TestHttpRedirects(unittest.TestCase):
    """REGRESSION found by the v1.0.3 review: /about 301s to /about/, the crawl stored
    the same content under both paths, S3-DUPLICATE-EXACT fired (critical) and the
    redirect itself was never visible."""

    # Home links to the redirecting path first, so the redirect target is NOT yet crawled.
    HOME_LINKS = '<a href="/about">About</a>'
    CONTACT_LINKS = ""

    @classmethod
    def setUpClass(cls):
        try:
            import requests  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("requests not installed (http mode is optional)")
        import functools
        import http.server
        import threading

        cls.root = tempfile.mkdtemp(prefix="sitemend-redirect-test-")
        os.makedirs(os.path.join(cls.root, "about"))
        os.makedirs(os.path.join(cls.root, "contact"))
        with open(os.path.join(cls.root, "index.html"), "w", encoding="utf-8") as f:
            f.write(page_html(body="<h1>Home</h1><p>" + cls.HOME_LINKS + " " +
                                   ("home " * 300) + "</p>"))
        with open(os.path.join(cls.root, "about", "index.html"), "w", encoding="utf-8") as f:
            f.write(page_html(title="About This Site And Its Maker Title",
                              body="<h1>About</h1><p>" + ("about " * 300) + "</p>"))
        with open(os.path.join(cls.root, "contact", "index.html"), "w", encoding="utf-8") as f:
            f.write(page_html(title="Contact The Team Behind This Site Title",
                              body="<h1>Contact</h1><p>" + cls.CONTACT_LINKS + " " +
                                   ("contact " * 300) + "</p>"))
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=cls.root)
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.httpd.RequestHandlerClass.log_message = lambda *a, **k: None
        base = "http://127.0.0.1:{0}/".format(cls.httpd.server_address[1])
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

        cls.pages, _, robots, sitemap, _, _, cls.skipped, _, _ = crawl.crawl_http(base)
        for p in cls.pages:
            p["robots_allowed"], p["robots_rule"] = True, None
        doc = {"meta": {"source": base, "mode": "http", "page_count": len(cls.pages),
                        "base_url": None, "excluded_dirs_present": []},
               "robots": {"found": False, "sitemaps": [], "groups": [], "raw": ""},
               "sitemap": {"found": False, "urls": [], "notes": []},
               "assets": [], "pages": cls.pages, "extras": {},
               "referenced_assets": [], "referenced_basenames": []}
        cls.ids = [f["id"] for f in audit.audit(doc)["findings"]]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_redirect_target_is_stored_once_under_its_final_path(self):
        self.assertEqual(sorted(p["url"] for p in self.pages),
                         ["/", "/about/"] + (["/contact/"] if self.CONTACT_LINKS else []))

    def test_requested_path_becomes_an_alias(self):
        about = [p for p in self.pages if p["url"] == "/about/"][0]
        self.assertIn("/about", about["aliases"])

    def test_no_false_duplicate_or_broken_link(self):
        self.assertNotIn("S3-DUPLICATE-EXACT", self.ids)
        self.assertNotIn("S1-BROKEN-LINK", self.ids)


class TestHttpRedirectToCrawledTarget(TestHttpRedirects):
    """The redirect target is crawled BEFORE the redirecting URL is fetched: home links
    /about/ directly, then /contact/ links the redirecting /about. The second fetch must
    not create a page, yet /about must still resolve (no S1-BROKEN-LINK)."""

    HOME_LINKS = '<a href="/about/">About</a> <a href="/contact/">Contact</a>'
    CONTACT_LINKS = '<a href="/about">About</a>'


class TestDashboardTemplate(unittest.TestCase):
    """REGRESSION found while regenerating the README screenshot: gateClass() returned
    'gate-block', the pill class was derived from it as 'block', and the stylesheet
    defined '.pill.blocked', so the BLOCKED pill had no styling from v1.0.1 on."""

    def test_every_gate_state_has_matching_pill_and_mark_styles(self):
        import re
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets",
                            "dashboard.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        body = re.search(r"const gateClass.*?;", html, re.S).group(0)
        classes = re.findall(r'"(gate-[a-z]+)"', body)
        self.assertEqual(len(set(classes)), 3, "expected blocked, clear and unassessed")
        for cls in set(classes):
            pill = cls.replace("gate-", "")        # how render() derives the pill class
            self.assertIn(".pill." + pill + "{", html, "no pill style for " + cls)
            self.assertIn("." + cls + " span{", html, "no mark style for " + cls)


class TestPlaceholderDomain(Base):
    """A site whose canonicals or sitemap point at example.com, localhost or similar tells
    search engines to index a domain that is not its own. It was missed because the base
    origin is inferred from those same URLs, so a uniformly wrong site looked consistent.
    Found when a plain prompt noticed it and sitemend did not."""

    def _ids(self, canonical=None, sitemap_host=None, robots_sitemap=None):
        extra = '<link rel="canonical" href="{0}">'.format(canonical) if canonical else ""
        self.fx.write("index.html", page_html(head_extra=extra))
        if sitemap_host:
            self.fx.write("sitemap.xml",
                          "<urlset><url><loc>https://{0}/</loc></url></urlset>".format(sitemap_host))
        if robots_sitemap:
            self.fx.write("robots.txt", "User-agent: *\nAllow: /\nSitemap: {0}\n".format(robots_sitemap))
        return self.fx.audit()["findings"]

    def _hit(self, findings):
        return [f for f in findings if f["id"] == "S1-PLACEHOLDER-DOMAIN"]

    def test_canonical_on_example_com_is_flagged(self):
        hit = self._hit(self._ids(canonical="https://example.com/"))
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["severity"], "high")
        self.assertIn("canonical", hit[0]["evidence"])

    def test_subdomain_of_a_reserved_name_is_flagged(self):
        self.assertTrue(self._hit(self._ids(sitemap_host="demo.example.org")))

    def test_localhost_in_robots_sitemap_line_is_flagged(self):
        self.assertTrue(self._hit(self._ids(robots_sitemap="http://localhost:3000/sitemap.xml")))

    def test_reserved_tld_is_flagged(self):
        self.assertTrue(self._hit(self._ids(canonical="https://shop.test/")))

    def test_a_real_domain_is_not_flagged(self):
        self.assertEqual(self._hit(self._ids(canonical="https://www.vaavkitchenandcaterers.com/",
                                             sitemap_host="www.vaavkitchenandcaterers.com")), [])

    def test_lookalike_names_are_not_flagged(self):
        """Suffix matching must be on whole labels: notexample.com and example.community
        are not example.com."""
        self.assertEqual(self._hit(self._ids(canonical="https://notexample.com/")), [])
        self.assertEqual(self._hit(self._ids(sitemap_host="example.community")), [])


class TestOpenGraph(Base):
    def _og(self, head_extra="", **kw):
        self.fx.write("index.html", page_html(head_extra=head_extra, **kw))
        return [f for f in self.fx.audit()["findings"] if f["id"] == "S3-OG-MISSING"]

    def test_page_with_no_open_graph_tags_is_flagged_low(self):
        hit = self._og()
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["severity"], "low")
        for tag in ("og:title", "og:description", "og:image"):
            self.assertIn(tag, hit[0]["evidence"])

    def test_complete_open_graph_is_not_flagged(self):
        og = ('<meta property="og:title" content="T"><meta property="og:description" content="D">'
              '<meta property="og:image" content="https://x.test/i.png">')
        self.assertEqual(self._og(head_extra=og), [])

    def test_partial_open_graph_names_only_what_is_missing(self):
        hit = self._og(head_extra='<meta property="og:title" content="T">')
        self.assertEqual(len(hit), 1)
        self.assertNotIn("og:title", hit[0]["evidence"])
        self.assertIn("og:image", hit[0]["evidence"])

    def test_noindex_and_utility_pages_are_not_flagged(self):
        self.fx.write("index.html", page_html(
            head_extra='<meta property="og:title" content="T"><meta property="og:description" '
                       'content="D"><meta property="og:image" content="https://x.test/i.png">'))
        self.fx.write("hidden.html", page_html(head_extra='<meta name="robots" content="noindex">'))
        self.fx.write("404.html", page_html())
        hits = [f for f in self.fx.audit()["findings"] if f["id"] == "S3-OG-MISSING"]
        self.assertEqual(hits, [], "noindex and 404 pages are never shared or indexed")


def alt(*pairs):
    return "".join('<link rel="alternate" hreflang="{0}" href="{1}">'.format(*p) for p in pairs)


class TestHreflang(Base):
    def _ids(self, en, fr, home=""):
        self.fx.write("index.html", page_html(title="Home Page With An Adequate Title", head_extra=home))
        self.fx.write("en/index.html", page_html(title="English Page With An Adequate Title", head_extra=en))
        self.fx.write("fr/index.html", page_html(title="French Page With An Adequate Title", head_extra=fr))
        return [(f["id"], tuple(f["pages"]), f["evidence"]) for f in self.fx.audit()["findings"]
                if f["id"].startswith("S1-HREFLANG")]

    def test_reciprocal_set_with_self_references_is_clean(self):
        both = alt(("en", "/en/"), ("fr", "/fr/"), ("x-default", "/en/"))
        self.assertEqual(self._ids(both, both), [])

    def test_missing_return_tag_names_the_silent_page(self):
        hits = self._ids(alt(("en", "/en/"), ("fr", "/fr/")), "")
        self.assertEqual([h[:2] for h in hits], [("S1-HREFLANG-NO-RETURN", ("/en/",))])
        self.assertIn("/fr/", hits[0][2])

    def test_page_missing_from_its_own_set_is_flagged(self):
        hits = self._ids(alt(("fr", "/fr/")), alt(("en", "/en/"), ("fr", "/fr/")))
        self.assertIn(("S1-HREFLANG-NO-SELF", ("/en/",)), [h[:2] for h in hits])

    def test_alternate_that_does_not_exist_is_broken(self):
        hits = self._ids(alt(("en", "/en/"), ("de", "/de/")), "")
        self.assertIn(("S1-HREFLANG-BROKEN", ("/en/",)), [h[:2] for h in hits])

    def test_invalid_codes_flagged_valid_codes_are_not(self):
        bad = self._ids(alt(("en_GB", "/en/")), "")
        self.assertIn("S1-HREFLANG-INVALID", [h[0] for h in bad])
        for code in ("en-GB", "x-default", "zh-Hant-TW", "es-419", "fil"):
            self.assertTrue(audit.HREFLANG_RE.match(code), code)

    def test_other_origin_alternates_are_not_checked(self):
        """A cross-domain alternate cannot be verified from this crawl, so it is not 'broken'."""
        self.fx.write("index.html", page_html(head_extra=alt(
            ("en", "https://acme-widgets.com/"), ("fr", "https://acme-widgets.fr/"))))
        doc = self.fx.crawl()
        doc["meta"]["base_url"] = "https://acme-widgets.com"
        ids = [f["id"] for f in audit.audit(doc)["findings"]]
        self.assertNotIn("S1-HREFLANG-BROKEN", ids)

    def test_slashless_href_resolves_against_an_http_mode_alias_set(self):
        """An http crawl records a directory page as {/en/, /en/index.html}, with no
        slash-less alias, so /en used to be reported as a page that does not exist.
        (fs crawls already alias /en, which is why this is not a Fixture test.)"""
        alias = {"/en/": "/en/", "/en/index.html": "/en/"}
        self.assertEqual(audit.hreflang_target("/en", "/", alias, None), ("internal", "/en/"))
        self.assertEqual(audit.hreflang_target("https://acme-widgets.com/en", "/", alias, None),
                         ("internal", "/en/"))
        self.assertEqual(audit.hreflang_target("/en/", "/", {"/en": "/en"}, None), ("internal", "/en"))

    def test_wrong_case_and_file_paths_stay_broken(self):
        """Guard: the tolerance is for directory slashes only. Paths are case-sensitive
        and /about.html is not /about.html/."""
        hits = self._ids(alt(("en", "/en/"), ("fr", "/FR/")), alt(("fr", "/fr/"), ("en", "/en/")))
        self.assertIn(("S1-HREFLANG-BROKEN", ("/en/",)), [h[:2] for h in hits])
        self.assertIsNone(audit.hreflang_target("/about.html/", "/", {"/about.html": "/about.html"}, None)[1])

    def _variants(self, en_head, us_head):
        home = "<h1>Home</h1><p>" + "other " * 300 + "</p>"
        # A distinct description keeps the home page out of the cannibalization check,
        # so the assertions below are about the two alternates only.
        self.fx.write("index.html", page_html(
            title="Home Page With An Adequate Title", body=home,
            desc="Welcome to the company front page, where you can find our latest news and contact details."))
        self.fx.write("en/index.html", page_html(title="British English Page Title Here Ok", head_extra=en_head))
        self.fx.write("us/index.html", page_html(title="American English Page Title Here Ok", head_extra=us_head))
        return [f["id"] for f in self.fx.audit()["findings"]]

    def test_reciprocal_regional_variants_are_not_duplicates(self):
        """en-GB and en-US pages are meant to be near-identical; hreflang is how the site
        says so, and reporting them as duplicates would tell the owner to merge them."""
        both = alt(("en-GB", "/en/"), ("en-US", "/us/"))
        ids = self._variants(both, both)
        for dup in ("S3-DUPLICATE-EXACT", "S3-NEAR-DUPLICATE", "S3-CANNIBALIZATION"):
            self.assertNotIn(dup, ids)

    def test_identical_pages_without_a_return_tag_are_still_duplicates(self):
        ids = self._variants(alt(("en-GB", "/en/"), ("en-US", "/us/")), "")
        self.assertIn("S3-DUPLICATE-EXACT", ids)


class TestMixedContent(Base):
    def _hit(self, body_extra="", canonical="https://acme-widgets.com/"):
        head = '<link rel="canonical" href="{0}">'.format(canonical) if canonical else ""
        self.fx.write("index.html", page_html(head_extra=head,
                                              body="<h1>H</h1><p>" + "word " * 300 + "</p>" + body_extra))
        return [f for f in self.fx.audit()["findings"] if f["id"] == "S1-MIXED-CONTENT"]

    def test_http_image_on_https_page_is_medium(self):
        hit = self._hit('<img src="http://cdn.test/a.png" alt="a" width="1" height="1">')
        self.assertEqual([f["severity"] for f in hit], ["medium"])
        self.assertIn("http://cdn.test/a.png", hit[0]["evidence"])

    def test_http_script_is_high_because_browsers_block_it(self):
        hit = self._hit('<script src="http://cdn.test/a.js"></script>')
        self.assertEqual([f["severity"] for f in hit], ["high"])

    def test_https_resources_are_fine(self):
        self.assertEqual(self._hit('<script src="https://cdn.test/a.js"></script>'), [])

    def test_an_http_page_cannot_have_mixed_content(self):
        self.assertEqual(self._hit('<script src="http://cdn.test/a.js"></script>', canonical=None), [])


class TestSitemapLastmod(Base):
    def _ids(self, dates):
        urls = "".join("<url><loc>https://acme-widgets.com/p{0}</loc>{1}</url>".format(
            i, "<lastmod>{0}</lastmod>".format(d) if d else "") for i, d in enumerate(dates))
        self.fx.write("index.html", page_html())
        self.fx.write("sitemap.xml", '<?xml version="1.0"?><urlset xmlns='
                      '"http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + "</urlset>")
        return [f["id"] for f in self.fx.audit()["findings"] if "LASTMOD" in f["id"]]

    def test_identical_dates_on_many_urls_are_flagged(self):
        self.assertEqual(self._ids(["2026-01-01"] * 5), ["S1-SITEMAP-LASTMOD-UNIFORM"])

    def test_few_urls_may_honestly_share_a_date(self):
        self.assertEqual(self._ids(["2026-01-01"] * 4), [])

    def test_distinct_valid_dates_are_clean(self):
        self.assertEqual(self._ids(["2026-01-0%d" % i for i in range(1, 6)]), [])
        self.assertEqual(self._ids(["2026-05-01T10:00:00Z", "2026-05-02T10:00:00+05:30"]), [])

    def test_missing_lastmod_is_not_an_error(self):
        self.assertEqual(self._ids([None] * 6), [])

    def test_invalid_dates_are_flagged(self):
        for bad in ("2026-13-45", "2026-05-01 10:00", "yesterday", "01/05/2026"):
            self.assertEqual(self._ids(["2026-01-01", bad]), ["S1-SITEMAP-LASTMOD-INVALID"], bad)


class TestSecurityHeaders(Base):
    def _ids(self, base, headers, mode="http", source=None):
        self.fx.write("index.html", page_html())
        doc = self.fx.crawl()
        doc["meta"].update(mode=mode, base_url=base, source=base if source is None else source)
        for p in doc["pages"]:
            p["status"] = 200
            if headers is not None:
                p["security_headers"] = headers
        return [f for f in audit.audit(doc)["findings"] if f["id"] == "S1-SECURITY-HEADERS"]

    def test_missing_headers_on_https_are_low(self):
        hit = self._ids("https://acme-widgets.com", {})
        self.assertEqual([f["severity"] for f in hit], ["low"])
        self.assertIn("Strict-Transport-Security", hit[0]["evidence"])
        self.assertIn("X-Content-Type-Options", hit[0]["evidence"])

    def test_present_headers_are_clean(self):
        self.assertEqual(self._ids("https://acme-widgets.com", {
            "strict-transport-security": "max-age=1", "x-content-type-options": "nosniff"}), [])

    def test_https_canonicals_on_a_plain_http_crawl_are_never_nagged(self):
        """REGRESSION found by the PR #12 review: base_url is inferred from the canonicals,
        so crawling a local http dev server of an https site reported 'HSTS missing' --
        a header that cannot be sent over http, so the finding could never be fixed."""
        self.assertEqual(self._ids("https://acme-widgets.com", {}, source="http://127.0.0.1:8000/"), [])

    def test_plain_http_dev_server_is_never_nagged(self):
        self.assertEqual(self._ids("http://127.0.0.1:8000", {}), [])

    def test_fs_mode_and_old_crawls_say_nothing(self):
        self.assertEqual(self._ids("https://acme-widgets.com", {}, mode="fs"), [])
        self.assertEqual(self._ids("https://acme-widgets.com", None), [])


class TestJsShellAndRenderedDiff(Base):
    SHELL = '<div id="root"></div>'

    def _ids(self, body, mutate=None):
        self.fx.write("index.html", page_html(body=body))
        doc = self.fx.crawl()
        if mutate:
            mutate(doc["pages"][0])
        return {f["id"]: f for f in audit.audit(doc)["findings"] if f["id"].startswith("S1-JS")}

    def test_empty_mount_point_is_flagged_as_a_shell(self):
        self.assertIn("S1-JS-SHELL", self._ids(self.SHELL))

    def test_a_shell_is_not_also_called_h1_less(self):
        """Its headings only exist after JS runs; the shell finding is the honest one."""
        self.fx.write("index.html", page_html(body=self.SHELL))
        ids = [f["id"] for f in self.fx.audit()["findings"]]
        self.assertIn("S1-JS-SHELL", ids)
        self.assertNotIn("S2-H1-MISSING", ids)

    def test_mount_point_with_server_rendered_text_is_not(self):
        self.assertEqual(self._ids('<div id="root"><h1>Hi</h1><p>' + "word " * 80 + "</p></div>"), {})

    def test_a_normal_page_is_not_a_shell(self):
        self.assertEqual(self._ids("<h1>H</h1><p>" + "word " * 300 + "</p>"), {})

    def _rendered(self, raw_words=5, canonical=None, robots=None, **page):
        def m(p):
            p.update(rendered=True, **page)
            p["raw"] = {"words": raw_words, "title": p["title"], "canonical": canonical,
                        "robots_meta": robots, "h1": 0}
        return m

    def test_text_that_only_exists_after_js_is_flagged(self):
        ids = self._ids("<h1>H</h1><p>" + "word " * 300 + "</p>", self._rendered(raw_words=5))
        self.assertIn("S1-JS-DEPENDENT", ids)
        self.assertIn("raw HTML 5 words", ids["S1-JS-DEPENDENT"]["evidence"])

    def test_server_rendered_text_is_not_js_dependent(self):
        ids = self._ids("<h1>H</h1><p>" + "word " * 300 + "</p>", self._rendered(raw_words=300))
        self.assertNotIn("S1-JS-DEPENDENT", ids)
        self.assertNotIn("S1-JS-SHELL", ids)

    def test_js_that_injects_noindex_or_a_canonical_is_high(self):
        body = "<h1>H</h1><p>" + "word " * 300 + "</p>"
        ids = self._ids(body, self._rendered(raw_words=300, noindex=True, robots_meta="noindex"))
        self.assertEqual(ids["S1-JS-HEAD-MISMATCH"]["severity"], "high")
        ids = self._ids(body, self._rendered(raw_words=300, canonical="https://acme-widgets.com/old"))
        self.assertIn("canonical", ids["S1-JS-HEAD-MISMATCH"]["evidence"])


class _JsServer:
    """A site whose only page is an empty shell that JavaScript fills in."""

    @classmethod
    def start(cls):
        import http.server
        import threading
        html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width"><title>Shell</title></head>'
                '<body><div id="root"></div><script>document.getElementById("root").innerHTML='
                '"<h1>Rendered by JS</h1><p>" + "word ".repeat(150) + "</p>";</script></body></html>'
                ).encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                ok = self.path == "/"
                body = html if ok else b"nope"
                self.send_response(200 if ok else 404)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, "http://127.0.0.1:%d/" % srv.server_address[1]


class TestRenderCrawl(unittest.TestCase):
    """The plain crawl cannot see JS-built content and says so; --render shows it.
    The Chrome half is opt-in (SITEMEND_RENDER_TEST=1), like the Lighthouse test: CI
    cannot be assumed to have a usable headless Chrome, and a skipped test is honest."""

    @classmethod
    def setUpClass(cls):
        try:
            import requests  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("requests not installed (http mode is optional)")
        cls.srv, cls.base = _JsServer.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_plain_crawl_sees_an_empty_shell(self):
        page = crawl.crawl_http(self.base)[0][0]
        self.assertTrue(page["js_shell"])
        self.assertLess(page["full_word_count"], 5)     # just the <title>
        self.assertNotIn("rendered", page)

    @unittest.skipUnless(os.environ.get("SITEMEND_RENDER_TEST") == "1" and S.find_chrome(),
                         "set SITEMEND_RENDER_TEST=1 (needs Chrome, Chromium or Edge)")
    def test_render_crawl_sees_the_javascript_content(self):
        page = crawl.crawl_http(self.base, render=True, render_limit=3)[0][0]
        self.assertIsNone(page.get("render_error"), page.get("render_error"))
        self.assertTrue(page["rendered"])
        self.assertEqual(page["h1"], ["Rendered by JS"])
        self.assertLess(page["raw"]["words"], 5)
        self.assertGreaterEqual(page["full_word_count"], 150)
        self.assertFalse(page["js_shell"])

    def test_render_without_chrome_stops_with_a_clear_message(self):
        real, S.find_chrome = S.find_chrome, lambda: None
        self.addCleanup(setattr, S, "find_chrome", real)
        with self.assertRaises(SystemExit) as cm:
            crawl.crawl_http(self.base, render=True)
        self.assertIn("Chrome", str(cm.exception))


class TestHttpRedirectChains(unittest.TestCase):
    """/a -> /b -> /c is two hops; /x -> /d is one and is fine; /loop1 <-> /loop2 never ends."""

    @classmethod
    def setUpClass(cls):
        try:
            import requests  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("requests not installed (http mode is optional)")
        import http.server
        import threading
        redirects = {"/a": "/b", "/b": "/c", "/x": "/d", "/loop1": "/loop2", "/loop2": "/loop1"}
        home = page_html(title="Home Page With An Adequate Title", body=(
            '<h1>H</h1><p><a href="/a">a</a> <a href="/x">x</a> <a href="/loop1">l</a> '
            + "word " * 300 + "</p>"))
        pages = {"/": home,
                 "/c": page_html(title="Chain Target Page With A Fine Title"),
                 "/d": page_html(title="Single Hop Target With A Fine Title")}

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in redirects:
                    self.send_response(301)
                    self.send_header("Location", redirects[self.path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = pages.get(self.path, "nope").encode()
                self.send_response(200 if self.path in pages else 404)
                self.send_header("Content-Type", "text/html")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d/" % cls.srv.server_address[1]
        cls.pages, _, _, _, _, _, skipped, _, _ = crawl.crawl_http(base)
        for p in cls.pages:
            p["robots_allowed"], p["robots_rule"] = True, None
        doc = {"meta": {"source": base, "mode": "http", "page_count": len(cls.pages),
                        "base_url": None, "excluded_dirs_present": [], "skipped": skipped},
               "robots": {"found": False, "sitemaps": [], "groups": [], "raw": ""},
               "sitemap": {"found": False, "urls": [], "notes": []},
               "assets": [], "pages": cls.pages, "extras": {},
               "referenced_assets": [], "referenced_basenames": []}
        cls.findings = audit.audit(doc)["findings"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def of(self, fid):
        return [f for f in self.findings if f["id"] == fid]

    def test_two_hop_chain_is_flagged_with_every_hop_in_the_evidence(self):
        hit = self.of("S1-REDIRECT-CHAIN")
        self.assertEqual([f["pages"] for f in hit], [["/c"]])
        for hop in ("/a", "/b", "/c"):
            self.assertIn(hop, hit[0]["evidence"])

    def test_a_single_redirect_is_not_a_chain(self):
        self.assertNotIn(["/d"], [f["pages"] for f in self.of("S1-REDIRECT-CHAIN")])

    def test_redirect_loop_is_flagged_high(self):
        hit = self.of("S1-REDIRECT-LOOP")
        self.assertEqual([f["pages"] for f in hit], [["/loop1"]])
        self.assertEqual(hit[0]["severity"], "high")

    def test_response_security_headers_are_recorded(self):
        home = next(p for p in self.pages if p["url"] == "/")
        self.assertEqual(home["security_headers"], {"x-content-type-options": "nosniff"})


class TestChromeCommand(unittest.TestCase):
    """--no-sandbox runs a site's JavaScript with Chrome's sandbox off, so it is only
    added when Chrome would not start otherwise (root) or the user asks for it."""

    def cmd(self, **kw):
        return S.chrome_cmd("chrome", "http://x.test/", "/tmp/p", **kw)

    def test_sandbox_stays_on_for_a_normal_posix_user(self):
        self.assertNotIn("--no-sandbox", self.cmd(nt=False, root=False, env={}))

    def test_root_needs_the_sandbox_off(self):
        self.assertIn("--no-sandbox", self.cmd(nt=False, root=True, env={}))

    def test_env_var_opts_in_explicitly(self):
        self.assertIn("--no-sandbox",
                      self.cmd(nt=False, root=False, env={"SITEMEND_NO_SANDBOX": "1"}))

    def test_windows_never_gets_the_flag(self):
        self.assertNotIn("--no-sandbox",
                         self.cmd(nt=True, root=True, env={"SITEMEND_NO_SANDBOX": "1"}))


class TestIntlDemoSite(unittest.TestCase):
    """End-to-end guard for the v1.2.0 rules: crawl the fixture on disk through the real
    CLI path and require exactly the planted findings, so a rule that stops firing, or
    starts firing on clean pages, fails here."""

    ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples", "demo-site-intl")

    def test_planted_defects_are_found_and_nothing_else_leaks(self):
        out = os.path.join(tempfile.mkdtemp(prefix="sitemend-intl-"), "o")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        findings = sitemend.audit(self.ROOT, out, 200, None)
        with open(findings, encoding="utf-8") as f:
            ids = sorted(x["id"] for x in json.load(f)["findings"])
        self.assertEqual(ids, sorted([
            "S1-HREFLANG-BROKEN", "S1-HREFLANG-INVALID", "S1-HREFLANG-NO-RETURN",
            "S1-JS-SHELL", "S1-MIXED-CONTENT", "S1-SITEMAP-LASTMOD-INVALID",
            "S2-BREADCRUMB-MISSING", "S3-THIN", "S3-THIN", "S3-THIN", "S3-THIN"]))


class TestPackaging(unittest.TestCase):
    ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

    def _read(self, *parts):
        with open(os.path.join(self.ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_every_manifest_carries_the_same_version(self):
        """Three places name the version; releasing with one stale is how a PyPI upload
        and a plugin tag end up disagreeing."""
        import re
        py = re.search(r'^version = "([^"]+)"', self._read("pyproject.toml"), re.M).group(1)
        plugin = json.loads(self._read(".claude-plugin", "plugin.json"))["version"]
        skill = re.search(r"^version: (\S+)", self._read("SKILL.md"), re.M).group(1)
        self.assertEqual((py, plugin), (skill, skill))

    def test_the_example_workflows_pin_this_version(self):
        """The examples pin @vX.Y.Z so a release never changes a user's gate unasked;
        they must name the version being released, not the last one."""
        import re
        py = re.search(r'^version = "([^"]+)"', self._read("pyproject.toml"), re.M).group(1)
        for parts in (("examples", "github-action", "sitemend.yml"),
                      ("examples", "github-action", "preview.yml"), ("README.md",)):
            pins = re.findall(r"SurendharVr/sitemend@v([0-9.]+)", self._read(*parts))
            self.assertEqual(set(pins), {py}, "/".join(parts))

    def test_the_wheel_ships_every_script_and_the_dashboard_template(self):
        """pyproject maps scripts/ and assets/ into the wheel by directory, so a new script
        is picked up automatically -- but only if it is a module in scripts/."""
        scripts = {f for f in os.listdir(os.path.join(self.ROOT, "scripts")) if f.endswith(".py")}
        self.assertLessEqual({"sitemend.py", "crawl.py", "audit.py", "report.py", "verify.py",
                              "schema_check.py", "build_dashboard.py", "seolib.py", "__init__.py"},
                             scripts)
        self.assertTrue(os.path.isfile(os.path.join(self.ROOT, "assets", "dashboard.html")))

    def test_dashboard_template_resolves_in_the_source_tree(self):
        import build_dashboard
        self.assertTrue(os.path.isfile(build_dashboard.TEMPLATE))

    def test_license_metadata_is_not_the_deprecated_form(self):
        """setuptools drops the table form of `license` and license classifiers on
        2027-02-18. SPDX string + setuptools>=77 is the supported spelling."""
        text = self._read("pyproject.toml")
        self.assertIn('license = "MIT"', text)
        self.assertNotIn("license = {", text)
        self.assertNotIn("License ::", text)
        self.assertIn("setuptools>=77", text)


class TestHttpRedirectChainOrder(unittest.TestCase):
    """REGRESSION found by the PR #12 review: when the redirect target had already been
    crawled directly, the chain leading to it was discarded and S1-REDIRECT-CHAIN never
    fired. Home links to /c first, then to /a, and /a -> /b -> /c."""

    @classmethod
    def setUpClass(cls):
        try:
            import requests  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("requests not installed (http mode is optional)")
        import http.server
        import threading
        redirects = {"/a": "/b", "/b": "/c"}
        pages = {"/": page_html(title="Home Page With An Adequate Title", body=(
                     '<h1>H</h1><p><a href="/c">c</a> <a href="/a">a</a> ' + "word " * 300 + "</p>")),
                 "/c": page_html(title="Chain Target Page With A Fine Title")}

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in redirects:
                    self.send_response(301)
                    self.send_header("Location", redirects[self.path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = pages.get(self.path, "nope").encode()
                self.send_response(200 if self.path in pages else 404)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d/" % cls.srv.server_address[1]
        cls.pages, _, _, _, _, _, skipped, _, _ = crawl.crawl_http(base)
        for p in cls.pages:
            p["robots_allowed"], p["robots_rule"] = True, None
        doc = {"meta": {"source": base, "mode": "http", "page_count": len(cls.pages),
                        "base_url": None, "excluded_dirs_present": [], "skipped": skipped},
               "robots": {"found": False, "sitemaps": [], "groups": [], "raw": ""},
               "sitemap": {"found": False, "urls": [], "notes": []},
               "assets": [], "pages": cls.pages, "extras": {},
               "referenced_assets": [], "referenced_basenames": []}
        cls.chains = [f for f in audit.audit(doc)["findings"] if f["id"] == "S1-REDIRECT-CHAIN"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_chain_is_reported_even_though_the_target_was_crawled_first(self):
        self.assertEqual([f["pages"] for f in self.chains], [["/c"]])
        for hop in ("/a", "/b", "/c"):
            self.assertIn(hop, self.chains[0]["evidence"])


class TestVitalsCommand(unittest.TestCase):
    """REGRESSION found by the v1.0.3 review: on Windows the Lighthouse command ran
    with shell=True from a list, so cmd.exe saw an unquoted '&' in a page path and cut
    the command there (`/?q=a&lang=en` was measured as `/?q=a`, and the rest, including
    --output-path, was run as a separate command)."""

    def test_url_with_cmd_metacharacters_is_quoted(self):
        line = vitals._nt_cmdline(["npx", "lighthouse", "http://h/?q=a&lang=en", "--quiet"])
        self.assertEqual(line, 'npx lighthouse "http://h/?q=a&lang=en" --quiet')

    def test_arguments_with_spaces_are_quoted(self):
        line = vitals._nt_cmdline(["x", "--chrome-flags=--headless=new --no-sandbox"])
        self.assertEqual(line, 'x "--chrome-flags=--headless=new --no-sandbox"')

    def test_percent_encoding_is_allowed_and_quoted(self):
        self.assertEqual(vitals._nt_cmdline(["x", "http://h/a%20b"]), 'x "http://h/a%20b"')

    def test_quote_and_variable_expansion_are_refused(self):
        for bad in ('http://h/"&calc', "http://h/%PATH%", "http://h/%USERNAME%x"):
            with self.assertRaises(ValueError):
                vitals._nt_cmdline(["x", bad])

    def test_timeout_kills_grandchildren_too(self):
        """REGRESSION: the timeout used to kill only the shell, leaving node/Chrome
        running. Here the child spawns a grandchild that ticks into a file; after the
        timeout the ticking must stop."""
        d = tempfile.mkdtemp(prefix="sitemend-kill-")
        self.addCleanup(shutil.rmtree, d, True)
        tick = os.path.join(d, "tick.txt")
        grand = os.path.join(d, "grand.py")
        child = os.path.join(d, "child.py")
        with open(grand, "w") as f:
            f.write("import time\n"
                    "while True:\n"
                    "    open(%r, 'a').write('x')\n"
                    "    time.sleep(0.2)\n" % tick)
        with open(child, "w") as f:
            f.write("import subprocess, sys, time\n"
                    "subprocess.Popen([sys.executable, %r])\n"
                    "time.sleep(60)\n" % grand)
        import time
        with self.assertRaises(subprocess.TimeoutExpired):
            vitals.run_command([sys.executable, child], timeout=3)
        before = os.path.getsize(tick)
        time.sleep(1.2)
        self.assertEqual(os.path.getsize(tick), before, "the grandchild is still running")


class TestLighthouseSandbox(unittest.TestCase):
    """REGRESSION found by the 2026-10-16 audit: Lighthouse's Chrome always ran with
    --no-sandbox; the render path drops the sandbox only as root or on opt-out."""

    def flags(self, **kw):
        cmd = vitals.lighthouse_cmd("http://h/", "o.json", "mobile", **kw)
        return next(a for a in cmd if a.startswith("--chrome-flags="))

    def test_sandbox_is_kept_by_default(self):
        self.assertNotIn("--no-sandbox", self.flags(nt=False, root=False, env={}))
        self.assertNotIn("--no-sandbox", self.flags(nt=True, root=False, env={}))

    def test_root_or_the_opt_out_drops_it(self):
        self.assertIn("--no-sandbox", self.flags(nt=False, root=True, env={}))
        self.assertIn("--no-sandbox", self.flags(nt=False, root=False,
                                                 env={"SITEMEND_NO_SANDBOX": "1"}))


@unittest.skipUnless(os.environ.get("SITEMEND_LIGHTHOUSE_TEST") == "1",
                     "set SITEMEND_LIGHTHOUSE_TEST=1 (needs npx, Lighthouse and Chrome)")
class TestVitalsLive(unittest.TestCase):
    """Opt-in: runs real Lighthouse against a local server and checks the URL Chrome
    actually requested. CI has no Chrome, so this is run by hand before touching vitals.py."""

    def test_ampersand_in_page_path_reaches_the_server_intact(self):
        import http.server
        import threading
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                body = (b"<!doctype html><html lang=en><head><meta charset=utf-8><title>t</title>"
                        b"<meta name=viewport content='width=device-width'></head>"
                        b"<body><h1>hi</h1></body></html>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        wd = tempfile.mkdtemp(prefix="sitemend-lh-test-")
        self.addCleanup(shutil.rmtree, wd, True)
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        res, err = vitals.run_one(base + "/?q=a&lang=en", "desktop", wd, timeout=150)
        self.assertIsNone(err)
        self.assertIn("/?q=a&lang=en", seen)


class TestHostMismatch(Base):
    """A preview deploy's host (*.vercel.app) baked into a production build was invisible:
    the base origin is inferred from the same URLs, and only canonicals were compared."""

    def _hit(self, canonical=None, og_url=None, sitemap_host=None, robots_sitemap=None,
             base="https://acme-widgets.com"):
        extra = ""
        if canonical:
            extra += '<link rel="canonical" href="{0}">'.format(canonical)
        if og_url:
            extra += '<meta property="og:url" content="{0}">'.format(og_url)
        self.fx.write("index.html", page_html(head_extra=extra))
        if sitemap_host:
            self.fx.write("sitemap.xml",
                          "<urlset><url><loc>https://{0}/</loc></url></urlset>".format(sitemap_host))
        if robots_sitemap:
            self.fx.write("robots.txt", "User-agent: *\nAllow: /\nSitemap: {0}\n".format(robots_sitemap))
        doc = self.fx.crawl()
        doc["meta"]["base_url"] = base
        return [f for f in audit.audit(doc)["findings"] if f["id"] == "S1-HOST-MISMATCH"]

    def test_preview_host_in_sitemap_is_critical(self):
        hit = self._hit(canonical="https://acme-widgets.com/", sitemap_host="acme-git-main.vercel.app")
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["severity"], "critical")
        self.assertIn("acme-git-main.vercel.app", hit[0]["evidence"])
        self.assertIn("base URL acme-widgets.com", hit[0]["evidence"])

    def test_preview_host_in_og_url_is_flagged_on_the_page(self):
        hit = self._hit(canonical="https://acme-widgets.com/", og_url="https://acme.netlify.app/")
        self.assertEqual(hit[0]["pages"], ["/"])
        self.assertEqual(hit[0]["severity"], "critical")

    def test_www_against_apex_is_high(self):
        hit = self._hit(canonical="https://acme-widgets.com/", sitemap_host="www.acme-widgets.com",
                        robots_sitemap="https://acme-widgets.com/sitemap.xml")
        self.assertEqual([f["severity"] for f in hit], ["high"])

    def test_inconsistent_sources_without_a_base_url(self):
        self.assertTrue(self._hit(canonical="https://acme-widgets.com/",
                                  sitemap_host="acme.pages.dev", base=None))

    def test_consistent_site_is_clean(self):
        self.assertEqual(self._hit(canonical="https://acme-widgets.com/",
                                   og_url="https://acme-widgets.com/",
                                   sitemap_host="acme-widgets.com",
                                   robots_sitemap="https://acme-widgets.com/sitemap.xml"), [])

    def test_production_on_a_platform_host_is_clean(self):
        """A site whose real home IS acme.netlify.app is not a leak."""
        self.assertEqual(self._hit(canonical="https://acme.netlify.app/",
                                   sitemap_host="acme.netlify.app",
                                   base="https://acme.netlify.app"), [])

    def test_crawling_localhost_or_a_preview_is_not_a_mismatch(self):
        """`sitemend audit <url>` makes the crawled origin the base URL. A dev server or a
        preview deploy whose URLs all correctly name production must stay clean."""
        for base in ("http://127.0.0.1:8000", "https://acme-git-feat.vercel.app"):
            with self.subTest(base=base):
                self.assertEqual(self._hit(canonical="https://acme-widgets.com/",
                                           sitemap_host="acme-widgets.com", base=base), [])

    def test_preview_crawl_still_catches_a_preview_host_in_its_sitemap(self):
        """VERCEL_URL is the per-deploy host even in production, so a sitemap built from
        it is a real bug wherever it is crawled."""
        hit = self._hit(canonical="https://acme-widgets.com/", sitemap_host="acme-abc123.vercel.app",
                        base="https://acme-abc123.vercel.app")
        self.assertEqual([f["severity"] for f in hit], ["critical"])

    def test_relative_urls_and_schemes_are_not_hosts(self):
        self.assertEqual(self._hit(canonical="/", sitemap_host="acme-widgets.com",
                                   robots_sitemap="http://acme-widgets.com/sitemap.xml"), [])

    def test_canonical_alone_offsite_is_left_to_canonical_offsite(self):
        """Not reported twice: S1-CANONICAL-OFFSITE already names the page."""
        self.assertEqual(self._hit(canonical="https://acme.vercel.app/",
                                   sitemap_host="acme-widgets.com"), [])

    def test_placeholder_host_is_left_to_placeholder_domain(self):
        self.assertEqual(self._hit(canonical="https://acme-widgets.com/",
                                   sitemap_host="demo.example.com"), [])

    def test_preview_host_in_whole_sitemap_does_not_become_the_base(self):
        """fs mode infers the base URL. A sitemap that names only the preview host must
        not win over production canonicals, or every correct canonical is OFFSITE."""
        prod, preview = "https://sitemend-demo.dev", "https://x-git-y.vercel.app"
        for path in ("", "about/", "blog/"):
            self.fx.write(path + "index.html", page_html(
                head_extra='<link rel="canonical" href="{0}/{1}">'.format(prod, path)))
        self.fx.write("sitemap.xml", "<urlset>{0}</urlset>".format("".join(
            "<url><loc>{0}/{1}</loc></url>".format(preview, p) for p in ("", "about/", "blog/"))))
        doc = self.fx.crawl()
        doc["meta"]["base_url"] = crawl.infer_base_url(
            doc["sitemap"]["urls"], doc["pages"], doc["robots"]["sitemaps"])
        self.assertEqual(doc["meta"]["base_url"], prod)
        hits = [(f["id"], f["severity"]) for f in audit.audit(doc)["findings"]
                if f["id"] in ("S1-HOST-MISMATCH", "S1-CANONICAL-OFFSITE")]
        self.assertEqual(hits, [("S1-HOST-MISMATCH", "critical")])

    def test_preview_only_site_still_infers_its_own_host(self):
        """With no production host on offer, inference falls back to the first URL."""
        self.assertEqual(crawl.infer_base_url(["https://acme.netlify.app/"], [], []),
                         "https://acme.netlify.app")

    def _offsite(self, base):
        self.fx.write("index.html", page_html(
            head_extra='<link rel="canonical" href="https://acme-widgets.com/">'))
        doc = self.fx.crawl()
        doc["meta"]["base_url"] = base
        return [f for f in audit.audit(doc)["findings"] if f["id"] == "S1-CANONICAL-OFFSITE"]

    def test_dev_server_or_preview_crawl_does_not_flag_production_canonicals(self):
        """REGRESSION found by the 2026-10-16 audit: `sitemend audit http://127.0.0.1:8000/`
        reported every correct production canonical as critical S1-CANONICAL-OFFSITE."""
        for base in ("http://127.0.0.1:8000", "http://localhost:3000", "http://app.localhost:5173",
                     "https://acme-git-feat.vercel.app"):
            with self.subTest(base=base):
                self.assertEqual(self._offsite(base), [])

    def test_a_production_base_still_flags_an_offsite_canonical(self):
        self.assertEqual(len(self._offsite("https://acme-widgets.org")), 1)


class TestXRobotsTagNoindex(Base):
    """X-Robots-Tag: noindex on the production homepage (a preview-deploy header that
    shipped) is already the critical S1-NOT-INDEXABLE. Pinned so it stays that way."""

    def test_header_noindex_on_home_is_critical(self):
        self.fx.write("index.html", page_html())
        doc = self.fx.crawl()
        doc["meta"].update(mode="http", base_url="https://acme-widgets.com")
        doc["pages"][0]["x_robots_tag"] = "noindex"
        hit = [f for f in audit.audit(doc)["findings"] if f["id"] == "S1-NOT-INDEXABLE"]
        self.assertEqual([(f["severity"], f["evidence"]) for f in hit],
                         [("critical", "X-Robots-Tag noindex")])


class TestSoft404(unittest.TestCase):
    """The probe requests one random path at the origin root. A real server answering
    it 404 is clean; a catch-all answering 200 (SPA rewrite, redirect to home) is not."""

    def _serve(self, catch_all):
        try:
            import requests  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("requests not installed (http mode is optional)")
        import http.server
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/" and catch_all == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                code = 200 if self.path == "/" or catch_all == "spa" else 404
                body = b"<html><body>hi</body></html>"
                self.send_response(code)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return crawl.probe_soft404("http://127.0.0.1:%d/" % srv.server_address[1])

    def _hit(self, probe, mode="http"):
        doc = {"meta": {"mode": mode, "base_url": None, "soft404_probe": probe},
               "robots": {"found": True, "sitemaps": [], "groups": [], "raw": ""},
               "sitemap": {"found": True, "urls": [], "notes": [], "lastmods": []},
               "pages": [], "assets": []}
        return [f for f in audit.audit(doc)["findings"] if f["id"] == "S1-SOFT-404"]

    def test_proper_404_is_clean(self):
        probe = self._serve(None)
        self.assertEqual(probe["status"], 404)
        self.assertTrue(probe["path"].startswith("/sitemend-404-probe-"))
        self.assertEqual(self._hit(probe), [])

    def test_spa_catch_all_is_flagged(self):
        hit = self._hit(self._serve("spa"))
        self.assertEqual([f["severity"] for f in hit], ["high"])
        self.assertIn("-> 200", hit[0]["evidence"])
        self.assertNotIn("redirect", hit[0]["evidence"])

    def test_redirect_to_home_is_flagged_and_says_so(self):
        hit = self._hit(self._serve("redirect"))
        self.assertIn("after redirect", hit[0]["evidence"])

    def test_probe_is_random(self):
        self.assertNotEqual(self._serve(None)["path"], self._serve(None)["path"])

    def test_unreachable_probe_and_fs_mode_say_nothing(self):
        self.assertEqual(self._hit({"path": "/x", "status": None, "error": "refused"}), [])
        self.assertEqual(self._hit({"path": "/x", "status": 200}, mode="fs"), [])
        self.assertEqual(self._hit(None), [])


class TestUncrawlableLinks(Base):
    def _hit(self, body):
        self.fx.write("index.html", page_html(body="<h1>H</h1><p>" + "word " * 300 + "</p>" + body))
        return [f for f in self.fx.audit()["findings"] if f["id"] == "S2-UNCRAWLABLE-LINK"]

    def test_js_navigation_is_flagged(self):
        for body in ('<p>See <a href="#">Pricing</a></p>',
                     '<a href="javascript:void(0)" onclick="go()">Pricing</a>',
                     '<a onclick="go(\'/pricing\')">Pricing</a>',
                     '<a tabindex="0">Pricing</a>',
                     '<div onclick="location.href=\'/pricing\'">Pricing</div>',
                     '<button onclick="window.location = \'/pricing\'">Pricing</button>',
                     '<span onclick="window.open(\'/pricing\')">Pricing</span>'):
            with self.subTest(body=body):
                hit = self._hit(body)
                self.assertEqual(len(hit), 1, body)
                self.assertEqual(hit[0]["severity"], "medium")
                self.assertIn("Pricing", hit[0]["evidence"])

    def test_things_that_are_not_navigation_are_clean(self):
        for body in ('<a href="/pricing" onclick="track()">Pricing</a>',
                     '<a>Current crumb</a>',
                     '<a name="section"></a>',
                     '<a href="#">Back to top</a>',
                     '<a href="#features">Features</a>',
                     '<a href="#" role="button">Menu</a>',
                     '<a href="#" aria-expanded="false">Menu</a>',
                     '<a href="#" data-bs-toggle="dropdown">Menu</a>',
                     '<button onclick="openMenu()">Menu</button>',
                     '<div onclick="toggle()">Show more</div>'):
            with self.subTest(body=body):
                self.assertEqual(self._hit(body), [], body)

    def test_shared_nav_is_one_finding(self):
        nav = '<ul><li><a href="#">Pricing</a></li></ul>'
        self.fx.write("index.html", page_html(body='<h1>H</h1><a href="/b.html">b</a>' + nav))
        self.fx.write("b.html", page_html(title="Another Perfectly Reasonable Title",
                                          body='<h1>B</h1><a href="/">h</a>' + nav))
        hit = [f for f in self.fx.audit()["findings"] if f["id"] == "S2-UNCRAWLABLE-LINK"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["pages"], ["/", "/b.html"])


# ------------------------------------------- false positives from the 20-site benchmark

def serve(test, routes):
    """A throwaway http server for one test: routes maps path -> (status, content type,
    body, extra headers). Returns its base URL."""
    try:
        import requests  # noqa: F401
    except ImportError:
        raise unittest.SkipTest("requests not installed (http mode is optional)")
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, ctype, body, headers = routes.get(self.path, (404, "text/html", "nope", {}))
            body = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    test.addCleanup(srv.server_close)
    test.addCleanup(srv.shutdown)
    return "http://127.0.0.1:%d/" % srv.server_address[1]


def http_doc(base, max_pages=300):
    """crawl_http plus the robots/sitemap blocks crawl.main() would build."""
    (pages, _, robots, sitemap, _, _, skipped, _, _) = crawl.crawl_http(base, max_pages)
    rb = S.Robots(robots)
    for p in pages:
        p["robots_allowed"], p["robots_rule"] = rb.check(p["url"])
    sm_urls, _ = S.parse_sitemap(sitemap)
    return {"meta": {"source": base, "mode": "http", "page_count": len(pages),
                     "base_url": None, "excluded_dirs_present": [], "skipped": skipped},
            "robots": {"found": bool(robots), "sitemaps": rb.sitemaps, "groups": [], "raw": robots},
            "sitemap": {"found": bool(sitemap), "urls": sm_urls, "notes": []},
            "assets": [], "pages": pages, "extras": {},
            "referenced_assets": [], "referenced_basenames": []}


class TestHttpStartRedirect(unittest.TestCase):
    """REGRESSION found by the 2026-10-16 audit: a start URL redirecting to another host
    (apex -> www, http -> https) skipped every page as off-site, crawl.py exited 0 with 0
    pages, and every sitemap URL was reported as not existing."""

    def _sites(self):
        target = serve(self, {
            "/": (200, "text/html", page_html(
                body='<h1>H</h1><p><a href="/about/">a</a> ' + "word " * 300 + "</p>"), {}),
            "/about/": (200, "text/html", page_html(title="About Page With A Fine Long Title"), {}),
        })
        apex = serve(self, {"/": (301, "text/html", "", {"Location": target})})
        return apex, target

    def test_crawl_follows_the_start_redirect(self):
        apex, _ = self._sites()
        pages = crawl.crawl_http(apex)[0]
        self.assertEqual(sorted(p["url"] for p in pages), ["/", "/about/"])

    def test_base_url_repeating_the_typed_origin_follows_it(self):
        apex, target = self._sites()
        d = tempfile.mkdtemp(prefix="sitemend-redir-")
        self.addCleanup(shutil.rmtree, d, True)
        out = os.path.join(d, "crawl.json")
        subprocess.run([sys.executable, os.path.join(sitemend.HERE, "crawl.py"), "--mode", "http",
                        "--start", apex, "--base-url", apex.rstrip("/"), "--out", out],
                       capture_output=True, check=True)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["meta"]["base_url"], target.rstrip("/"))

    def test_zero_pages_is_an_error_not_an_empty_audit(self):
        dead = serve(self, {})
        d = tempfile.mkdtemp(prefix="sitemend-dead-")
        self.addCleanup(shutil.rmtree, d, True)
        r = subprocess.run([sys.executable, os.path.join(sitemend.HERE, "crawl.py"), "--mode", "http",
                            "--start", dead, "--out", os.path.join(d, "crawl.json")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("crawled 0 pages", r.stderr)

    def test_http_crawl_follows_absolute_links_on_its_own_origin(self):
        routes = {"/": (200, "text/html", None, {}),
                  "/about/": (200, "text/html", page_html(title="About Page With A Fine Long Title"), {})}
        base = serve(self, routes)
        routes["/"] = (200, "text/html", page_html(
            body='<h1>H</h1><p><a href="{0}about/">a</a> '.format(base) + "word " * 300 + "</p>"), {})
        self.assertEqual(sorted(p["url"] for p in crawl.crawl_http(base)[0]), ["/", "/about/"])


STUB = ('<!doctype html><html><head><meta http-equiv="refresh" content="{0}">'
        '</head><body></body></html>')
ON_PAGE = {"S3-TITLE-MISSING", "S3-VIEWPORT-MISSING", "S2-H1-MISSING", "S3-DESC-MISSING",
           "S1-CANONICAL-MISSING", "S3-LANG-MISSING", "S3-OG-MISSING", "S3-THIN"}


class TestHttpMaxPagesTruncation(unittest.TestCase):
    """REGRESSION found by the 2026-10-16 audit: with --max-pages 5 on a 13-page site,
    the 8 pages never fetched were reported as high S1-SITEMAP-ORPHAN ("does not
    exist") and high S1-BROKEN-LINK. Not crawled is unknown, not missing."""

    def test_uncrawled_urls_are_not_reported_missing(self):
        links = "".join('<a href="/p{0}/">p{0}</a> '.format(i) for i in range(8))
        routes = {"/": (200, "text/html", page_html(
            body="<h1>H</h1><p>" + links + "word " * 300 + "</p>"), {})}
        for i in range(8):
            routes["/p%d/" % i] = (200, "text/html", page_html(
                title="Page %d With A Long Enough Title Here" % i,
                body='<h1>P</h1><p><a href="/">home</a> ' + ("w%d " % i) * 300 + "</p>"), {})
        routes["/sitemap.xml"] = (200, "application/xml", "<urlset>" + "".join(
            "<url><loc>https://x.org/p%d/</loc></url>" % i for i in range(8)) + "</urlset>", {})
        doc = http_doc(serve(self, routes), max_pages=3)
        self.assertTrue(any(s.get("not_crawled") for s in doc["meta"]["skipped"]))
        ids = [f["id"] for f in audit.audit(doc)["findings"]]
        for fid in ("S1-SITEMAP-ORPHAN", "S1-BROKEN-LINK", "S2-ORPHAN", "S2-UNREACHABLE"):
            self.assertNotIn(fid, ids)

    def _i18n(self, max_pages, missing=()):
        alt = ('<link rel="alternate" hreflang="en" href="/">'
               '<link rel="alternate" hreflang="fr" href="/fr/">'
               '<link rel="alternate" hreflang="de" href="/de/">')
        routes = {"/": (200, "text/html", page_html(
            head_extra=alt + '<link rel="canonical" href="/">',
            body='<h1>H</h1><p><a href="/p0/">p0</a> <a href="/fr/">fr</a> <a href="/de/">de</a> '
                 + "word " * 300 + "</p>"), {})}
        for lang in ("fr", "de"):
            if lang not in missing:
                routes["/%s/" % lang] = (200, "text/html", page_html(
                    title="%s home page with a long enough title" % lang,
                    head_extra=alt + '<link rel="canonical" href="/%s/">' % lang,
                    body="<h1>%s</h1><p>" % lang + ("%s " % lang) * 300 + "</p>"), {})
        for i in range(8):
            routes["/p%d/" % i] = (200, "text/html", page_html(
                title="Page %d With A Long Enough Title Here" % i,
                head_extra='<link rel="canonical" href="/p7/">' if i == 0 else "",
                body='<h1>P</h1><p><a href="/">home</a> ' + ("w%d " % i) * 300 + "</p>"), {})
        routes["/sitemap.xml"] = (200, "application/xml", "<urlset>" + "".join(
            "<url><loc>https://x.org/p%d/</loc></url>" % i for i in range(8)) + "</urlset>", {})
        return audit.audit(http_doc(serve(self, routes), max_pages=max_pages))["findings"]

    def test_hreflang_and_canonical_targets_it_never_reached_are_not_broken(self):
        """REGRESSION found on biomejs.dev: a 50-page crawl reported 241 high
        S1-HREFLANG-BROKEN for language pages that serve 200 but were never fetched.
        A canonical to an unfetched page is still a measured cross-canonical."""
        found = self._i18n(max_pages=3)
        ids = [f["id"] for f in found]
        self.assertNotIn("S1-HREFLANG-BROKEN", ids)
        self.assertNotIn("S1-CANONICAL-BROKEN", ids)
        cross = [f for f in found if f["id"] == "S1-CANONICAL-CROSS"]
        self.assertEqual([(f["pages"], f["detail"]["points_to"]) for f in cross], [(["/p0/"], "/p7/")])

    def test_hreflang_alternates_nothing_links_to_are_crawled(self):
        """REGRESSION found on pnpm.io: /it serves 200 but no crawled page links to it and
        the sitemap omits it, so a finished crawl still called it missing. Search engines
        discover pages through hreflang and the canonical; the crawl now does too."""
        alt = ('<link rel="alternate" hreflang="en" href="/">'
               '<link rel="alternate" hreflang="it" href="/it">')
        routes = {"/": (200, "text/html", page_html(head_extra=alt), {}),
                  "/it": (200, "text/html", page_html(
                      title="Pagina italiana con un titolo abbastanza lungo", head_extra=alt,
                      body="<h1>Ciao</h1><p>" + "parola " * 300 + "</p>"), {})}
        doc = http_doc(serve(self, routes))
        self.assertIn("/it", [p["url"] for p in doc["pages"]])
        self.assertNotIn("S1-HREFLANG-BROKEN", [f["id"] for f in audit.audit(doc)["findings"]])

    def test_a_site_that_negotiates_on_accept_is_crawled_as_html(self):
        """REGRESSION found on turborepo.dev: its home page answers the generic */* with
        text/markdown (Vary: Accept), so the crawl skipped it as non-HTML and reported
        "/" as a broken link and a sitemap orphan. Ask for HTML the way browsers do."""
        try:
            import requests  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("requests not installed (http mode is optional)")
        import http.server
        import threading
        home = page_html().encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                html = "text/html" in (self.headers.get("Accept") or "") and self.path == "/"
                body = home if html else b"# Home\n"
                self.send_response(200 if self.path == "/" else 404)
                self.send_header("Content-Type", "text/html" if html else "text/markdown")
                self.send_header("Vary", "Accept")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        pages = crawl.crawl_http("http://127.0.0.1:%d/" % srv.server_address[1])[0]
        self.assertEqual([p["url"] for p in pages], ["/"])

    def test_a_missing_hreflang_page_is_still_broken_when_the_crawl_finished(self):
        broken = [f for f in self._i18n(max_pages=300, missing=("de",))
                  if f["id"] == "S1-HREFLANG-BROKEN"]
        self.assertTrue(broken)
        self.assertTrue(all('hreflang="de"' in f["evidence"] for f in broken))


class TestHttpSkipsNonHtmlBodies(unittest.TestCase):
    """REGRESSION found by the 2026-10-16 audit: a link to a 60 MB file downloaded all of
    it just to learn it was not HTML."""

    def test_linked_binary_is_not_downloaded(self):
        try:
            import requests  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("requests not installed (http mode is optional)")
        import http.server
        import threading

        sent = [0]
        home = page_html(body='<h1>H</h1><p><a href="/big.bin">get</a> ' + "word " * 300 + "</p>").encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/big.bin":
                    body = home if self.path == "/" else b"nope"
                    self.send_response(200 if self.path == "/" else 404)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                size, chunk = 64 * 1024 * 1024, b"\0" * 65536
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                try:
                    for _ in range(size // len(chunk)):
                        self.wfile.write(chunk)
                        sent[0] += len(chunk)
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        pages = crawl.crawl_http("http://127.0.0.1:%d/" % srv.server_address[1])[0]
        self.assertEqual([p["url"] for p in pages], ["/"])
        self.assertLess(sent[0], 16 * 1024 * 1024)
    def test_a_body_that_breaks_mid_read_is_skipped_not_fatal(self):
        try:
            import requests  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("requests not installed (http mode is optional)")
        import http.server
        import threading

        home = page_html(body='<h1>H</h1><p><a href="/broken/">b</a> ' + "word " * 300 + "</p>").encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/broken/":
                    # Promise 100000 bytes, send 10, then hang up.
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", "100000")
                    self.end_headers()
                    self.wfile.write(b"<html><bo")
                    self.wfile.flush()
                    self.close_connection = True
                    return
                body = home if self.path == "/" else b"nope"
                self.send_response(200 if self.path == "/" else 404)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        pages, _, _, _, _, _, skipped, _, _ = crawl.crawl_http("http://127.0.0.1:%d/" % srv.server_address[1])
        self.assertEqual([p["url"] for p in pages], ["/"])
        self.assertIn("/broken/", [s["url"] for s in skipped])


class TestMetaRefresh(Base):
    """REGRESSION from the benchmark: docs.astro.build's home page is
    <meta http-equiv="refresh" content="0;url=/en/getting-started/">. It was judged as a
    page (critical missing title and viewport, plus H1, description, canonical, lang and
    OG) and the http crawl stopped there, at 1 page."""

    def test_fs_stub_is_an_alias_of_its_target(self):
        self.fx.write("index.html", STUB.format("0;url=/en/getting-started/"))
        self.fx.write("en/getting-started/index.html", page_html())
        doc = self.fx.crawl()
        self.assertEqual([p["url"] for p in doc["pages"]], ["/en/getting-started/"])
        self.assertIn("/", doc["pages"][0]["aliases"])
        f = audit.audit(doc)["findings"]
        self.assertEqual([x["id"] for x in f if x["id"] in ON_PAGE and "/" in x["pages"]], [])

    def test_quoted_url_and_one_second_delay_are_redirects_too(self):
        self.fx.write("old.html", STUB.format("1; URL='/new.html'"))
        self.fx.write("new.html", page_html())
        doc = self.fx.crawl()
        self.assertEqual([p["url"] for p in doc["pages"]], ["/new.html"])
        self.assertIn("/old.html", doc["pages"][0]["aliases"])

    def test_slow_refresh_is_a_page(self):
        """A 5-second refresh shows the visitor the page first. It is a page."""
        self.fx.write("index.html", page_html(
            head_extra='<meta http-equiv="refresh" content="5;url=/next.html">'))
        self.assertEqual([p["url"] for p in self.fx.crawl()["pages"]], ["/"])

    def test_offsite_stub_is_skipped_not_judged(self):
        self.fx.write("index.html", page_html())
        self.fx.write("away.html", STUB.format("0;url=https://elsewhere.org/"))
        self.assertEqual([p["url"] for p in self.fx.crawl()["pages"]], ["/"])

    def test_http_stub_is_followed_like_a_redirect(self):
        base = serve(self, {
            "/": (200, "text/html", STUB.format("0;url=/en/getting-started/"), {}),
            "/en/getting-started/": (200, "text/html", page_html(), {}),
        })
        doc = http_doc(base)
        self.assertEqual([p["url"] for p in doc["pages"]], ["/en/getting-started/"])
        page = doc["pages"][0]
        self.assertIn("/", page["aliases"])
        self.assertEqual(page["redirect_hops"], [base, base + "en/getting-started/"])
        f = audit.audit(doc)["findings"]
        self.assertEqual([x["id"] for x in f if x["id"] in ON_PAGE and "/" in x["pages"]], [])


class TestSitemapDiscovery(Base):
    """REGRESSION from the benchmark: only /sitemap.xml was read. astro.build declares
    `Sitemap: https://astro.build/sitemap-index.xml` in robots.txt and was told its sitemap
    was missing; docs.astro.build, starlight.astro.build and biomejs.dev publish
    /sitemap-index.xml without declaring it, which is S1-ROBOTS-NO-SITEMAP, not missing."""

    INDEX = ('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
             '<sitemap><loc>https://acme-widgets.com/sitemap-0.xml</loc></sitemap></sitemapindex>')
    URLSET = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
              '<url><loc>https://acme-widgets.com/</loc></url></urlset>')

    def _site(self, robots):
        self.fx.write("index.html", page_html(
            head_extra='<link rel="canonical" href="https://acme-widgets.com/">'))
        self.fx.write("robots.txt", robots)
        self.fx.write("sitemap-index.xml", self.INDEX)
        self.fx.write("sitemap-0.xml", self.URLSET)
        doc = self.fx.crawl()
        return doc, {f["id"] for f in audit.audit(doc)["findings"]}

    def test_declared_index_is_followed_to_its_urls(self):
        doc, ids = self._site("User-agent: *\nAllow: /\n"
                              "Sitemap: https://acme-widgets.com/sitemap-index.xml\n")
        self.assertEqual(doc["sitemap"]["urls"], ["https://acme-widgets.com/"])
        self.assertFalse(ids & {"S1-SITEMAP-MISSING", "S1-ROBOTS-NO-SITEMAP", "S1-SITEMAP-ORPHAN"})

    def test_undeclared_index_is_robots_no_sitemap(self):
        doc, ids = self._site("User-agent: *\nAllow: /\n")
        self.assertTrue(doc["sitemap"]["found"])
        self.assertIn("S1-ROBOTS-NO-SITEMAP", ids)
        self.assertNotIn("S1-SITEMAP-MISSING", ids)

    def test_http_mode_reads_the_declared_path_on_the_crawled_origin(self):
        """tauri.app's robots.txt names v2.tauri.app's sitemap-index.xml; only the path
        transfers, as for sitemap <loc>s."""
        xml, txt = "application/xml", "text/plain"
        base = serve(self, {
            "/": (200, "text/html", page_html(), {}),
            "/robots.txt": (200, txt, "User-agent: *\nSitemap: https://v2.acme.dev/sitemap-index.xml\n", {}),
            "/sitemap-index.xml": (200, xml, self.INDEX, {}),
            "/sitemap-0.xml": (200, xml, self.URLSET, {}),
        })
        doc = http_doc(base)
        self.assertEqual(doc["sitemap"]["urls"], ["https://acme-widgets.com/"])
        ids = {f["id"] for f in audit.audit(doc)["findings"]}
        self.assertFalse(ids & {"S1-SITEMAP-MISSING", "S1-ROBOTS-NO-SITEMAP"})

    def test_index_children_are_capped(self):
        reads = []
        index = "<sitemapindex>" + "".join(
            "<sitemap><loc>https://a.com/s{0}.xml</loc></sitemap>".format(i)
            for i in range(200)) + "</sitemapindex>"

        def read(path):
            reads.append(path)
            return index if path == "/sitemap.xml" else self.URLSET
        crawl.discover_sitemap("", read)
        self.assertEqual(len(reads), crawl.SITEMAP_FILES_MAX)

    def test_html_at_the_sitemap_path_is_not_a_sitemap(self):
        """A catch-all route answering /sitemap.xml with the app shell is no sitemap."""
        self.assertEqual(crawl.discover_sitemap("", lambda p: "<html><body>app</body></html>"), "")

    def test_missing_files_are_calibrated(self):
        """Google treats a 404 robots.txt as allow-all, so its absence is low; a missing
        sitemap only slows discovery on a linked site, so medium."""
        self.fx.write("index.html", page_html())
        sev = {f["id"]: f["severity"] for f in self.fx.audit()["findings"]}
        self.assertEqual(sev["S1-ROBOTS-MISSING"], "low")
        self.assertEqual(sev["S1-SITEMAP-MISSING"], "medium")


class TestSitemapStaysInsideRoot(Base):
    """REGRESSION found by the 2026-10-16 audit: on Windows a robots.txt Sitemap: path
    such as /C:/... or /..\\x was read from outside the audited folder."""

    def test_robots_sitemap_path_cannot_leave_the_folder(self):
        outside = tempfile.mkdtemp(prefix="sitemend-outside-")
        self.addCleanup(shutil.rmtree, outside, True)
        sm = os.path.join(outside, "sm.xml")
        with open(sm, "w") as fh:
            fh.write("<urlset><url><loc>https://x.org/LEAKED/</loc></url></urlset>")
        self.fx.write("index.html", page_html())
        for path in (sm.replace("\\", "/").lstrip("/"), os.path.relpath(sm, self.fx.root)):
            with self.subTest(path=path):
                self.fx.write("robots.txt", "User-agent: *\nSitemap: https://x.org/" + path + "\n")
                self.assertEqual(self.fx.crawl()["sitemap"]["urls"], [])


class TestUncrawlableBenchmark(Base):
    """REGRESSION from the benchmark: S2-UNCRAWLABLE-LINK fired on 5 sites and 3 were
    wrong. The 2 right ones must stay flagged."""

    def _hit(self, html):
        self.fx.write("index.html", html)
        return [f for f in self.fx.audit()["findings"] if f["id"] == "S2-UNCRAWLABLE-LINK"]

    def _main(self, body):
        return page_html(body="<h1>H</h1><p>" + "word " * 300 + "</p>" + body)

    def test_react_dev_hidden_thumbnail_is_clean(self):
        self.assertEqual(self._hit(self._main(
            '<article><a aria-hidden="true" tabindex="-1" target="_blank"><img src="t.jpg" alt=""></a>'
            '<div aria-hidden="true"><p><a href="#">x</a></p></div></article>')), [])

    def test_react_dev_toc_overview_is_clean(self):
        self.assertEqual(self._hit(self._main(
            '<ul><li><a href="#">Overview</a></li><li><a href="#install">Install</a></li></ul>')), [])

    def test_tailwind_demo_in_a_figure_without_main_is_clean(self):
        """tailwindcss.com has no <main>; its live examples sit in <figure>."""
        html = page_html().replace("<main>", "<div>").replace("</main>", "</div>")
        html = html.replace("</body>", '<figure><p>by <a href="#">planeteria.tech</a></p>'
                                       '<ul><li><a href="#">Call</a></li></ul></figure></body>')
        self.assertEqual(self._hit(html), [])

    def test_shadcn_components_are_clean(self):
        """ui.shadcn.com: pagination in a nav, a login form's "Forgot?", item cards."""
        self.assertEqual(self._hit(self._main(
            '<nav><ul><li><a href="#">Previous</a></li><li><a href="#">1</a></li></ul></nav>'
            '<div><div><a href="#">Forgot?</a></div></div>'
            '<form><p><a href="#">Forgot?</a></p></form>'
            '<a href="#" data-slot="item">Danger Zone</a>')), [])

    def test_placeholder_links_in_docs_text_stay_flagged(self):
        """pnpm.io: "ignored by the [pnpm audit](#) command"; jestjs.io: "your Jest
        [config file](#)". Both are <a href="#"> in a docs paragraph or list item."""
        for body in ('<article><p>ignored by the <a href="#"><code>pnpm audit</code></a> command</p></article>',
                     '<article><ul><li>your Jest <a href="#">config file</a></li></ul></article>'):
            with self.subTest(body=body):
                self.assertEqual(len(self._hit(self._main(body))), 1)

    def test_no_main_body_prose_is_still_checked(self):
        html = page_html(body='<h1>H</h1><p>see <a href="#">the guide</a></p>')
        html = html.replace("<main>", "<div>").replace("</main>", "</div>")
        self.assertEqual(len(self._hit(html)), 1)

    def test_evidence_shows_the_triggering_attribute(self):
        """The 120-character cut used to end inside a utility-class list, before href."""
        hit = self._hit(self._main('<p><a class="{0}" data-x="1" href="#">config file</a></p>'
                                   .format("text-link " * 30)))
        self.assertEqual(hit[0]["evidence"], '<a data-x="1" href="#">config file</a>')


class TestHostAgreement(Base):
    """REGRESSION from the benchmark: tauri.app serves the site, and every URL it
    publishes (canonical, og:url, sitemap, robots) names v2.tauri.app, which also serves
    it. That is a deliberate canonical host, not a leak: one medium finding asking for a
    redirect, instead of a high mismatch plus a critical CANONICAL-OFFSITE per page."""

    def _audit(self, host):
        url = "https://{0}/".format(host)
        self.fx.write("index.html", page_html(head_extra=(
            '<link rel="canonical" href="{0}"><meta property="og:url" content="{0}">').format(url)))
        self.fx.write("sitemap.xml", "<urlset><url><loc>{0}</loc></url></urlset>".format(url))
        self.fx.write("robots.txt", "User-agent: *\nSitemap: {0}sitemap.xml\n".format(url))
        doc = self.fx.crawl()
        doc["meta"]["base_url"] = "https://acme-widgets.com"
        return audit.audit(doc)["findings"]

    def test_every_url_naming_one_other_host_is_medium(self):
        f = self._audit("v2.acme-widgets.com")
        hit = [x for x in f if x["id"] == "S1-HOST-MISMATCH"]
        self.assertEqual([x["severity"] for x in hit], ["medium"])
        self.assertEqual(hit[0]["message"],
                         "Served on acme-widgets.com, but every URL names v2.acme-widgets.com; "
                         "check acme-widgets.com redirects to v2.acme-widgets.com.")
        self.assertNotIn("S1-CANONICAL-OFFSITE", [x["id"] for x in f])

    def test_agreeing_on_a_preview_host_stays_critical(self):
        f = self._audit("acme-git-main.vercel.app")
        self.assertEqual([x["severity"] for x in f if x["id"] == "S1-HOST-MISMATCH"], ["critical"])

class TestCheckCommand(unittest.TestCase):
    """`sitemend check` is what the GitHub Action runs: compare a change with its base and
    fail only on what the change broke. Folders for a static build, URLs for a preview."""

    def _site(self, broken=False):
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        link = '<a href="/gone/">x</a> ' if broken else ""
        fx.write("index.html", page_html(body="<h1>H</h1><p>" + link + "word " * 300 + "</p>"))
        return fx.root

    def _out(self):
        d = tempfile.mkdtemp(prefix="sitemend-check-")
        self.addCleanup(shutil.rmtree, d, True)
        return os.path.join(d, "out")

    def test_a_change_that_breaks_a_link_fails_and_leaves_a_comment(self):
        out = self._out()
        code = sitemend.check(self._site(broken=True), self._site(), out, 200)
        self.assertEqual(code, 1)
        with open(os.path.join(out, "verify.md"), encoding="utf-8") as fh:
            self.assertIn("S1-BROKEN-LINK", fh.read())

    def test_an_unchanged_site_passes(self):
        self.assertEqual(sitemend.check(self._site(), self._site(), self._out(), 200), 0)

    def test_without_a_base_it_fails_only_on_fail_on(self):
        broken = self._site(broken=True)          # one high finding, no critical
        self.assertEqual(sitemend.check(broken, None, self._out(), 200), 0)
        self.assertEqual(sitemend.check(broken, None, self._out(), 200, "high"), 1)

    def test_a_preview_url_compared_with_production_passes_when_nothing_changed(self):
        """The same site on two origins (production and a preview) is not a regression:
        no finding's evidence may depend on the origin it was crawled from."""
        routes = {"/": (200, "text/html", page_html(
                      body='<h1>H</h1><p><a href="/about/">a</a> ' + "word " * 300 + "</p>"), {}),
                  "/about/": (200, "text/html", page_html(title="About Page With A Fine Long Title"), {})}
        prod, preview = serve(self, routes), serve(self, routes)
        out = self._out()
        self.assertEqual(sitemend.check(preview, prod, out, 50), 0)
        with open(os.path.join(out, "verify.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["new"], [])

class TestExtraHeaders(unittest.TestCase):
    """A Vercel preview behind Deployment Protection answers 401 until the request carries
    its bypass header. The header comes from SITEMEND_HTTP_HEADERS, never the command line."""

    def test_parses_name_value_lines(self):
        env = {"SITEMEND_HTTP_HEADERS": "x-vercel-protection-bypass: s3cret\n\nbad line\nX-A:b:c\n"}
        self.assertEqual(crawl.extra_headers(env), {"x-vercel-protection-bypass": "s3cret", "X-A": "b:c"})
        self.assertEqual(crawl.extra_headers({}), {})

    def test_a_protected_preview_is_crawled_with_the_header(self):
        try:
            import requests  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("requests not installed (http mode is optional)")
        import http.server
        import threading
        home = page_html().encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                ok = self.headers.get("x-vercel-protection-bypass") == "s3cret" and self.path == "/"
                body = home if ok else b"nope"
                self.send_response(200 if ok else 401)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = "http://127.0.0.1:%d/" % srv.server_address[1]
        self.assertEqual(crawl.crawl_http(base)[0], [])
        old = os.environ.get("SITEMEND_HTTP_HEADERS")
        os.environ["SITEMEND_HTTP_HEADERS"] = "x-vercel-protection-bypass: s3cret"
        try:
            self.assertEqual([p["url"] for p in crawl.crawl_http(base)[0]], ["/"])
        finally:
            if old is None:
                os.environ.pop("SITEMEND_HTTP_HEADERS")
            else:
                os.environ["SITEMEND_HTTP_HEADERS"] = old


class TestGithubAction(unittest.TestCase):
    """action.yml is the product's front door. CI's `action` job runs it for real on every
    pull request; these tests catch the cheap mistakes first, on every platform."""

    ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

    def _action(self):
        try:
            import yaml
        except ImportError:
            raise unittest.SkipTest("pyyaml not installed")
        with open(os.path.join(self.ROOT, "action.yml"), encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def test_the_marketplace_accepts_the_metadata(self):
        """REGRESSION: GitHub Marketplace refused to publish 1.5.0 because the action's
        description was over its limit ("Description must be less than 125 characters")."""
        a = self._action()
        self.assertLess(len(a["description"]), 125)
        self.assertTrue(a["name"] and a["branding"]["icon"] and a["branding"]["color"])

    def test_the_metadata_parses_and_every_used_input_is_declared(self):
        import re
        a = self._action()
        self.assertEqual(a["runs"]["using"], "composite")
        with open(os.path.join(self.ROOT, "action.yml"), encoding="utf-8") as fh:
            used = set(re.findall(r"inputs\.([a-z-]+)", fh.read()))
        self.assertLessEqual(used, set(a["inputs"]))

    @staticmethod
    def _bash():
        """A bash that runs this filesystem's scripts. On Windows a bare "bash" resolves to
        System32's bash.exe, the WSL launcher, before anything on PATH (GitHub's Windows
        runners have it, so the step failed there), so use the Git Bash beside git.exe."""
        if os.name != "nt":
            return shutil.which("bash")
        git = shutil.which("git")
        for rel in (("..", "bin", "bash.exe"), ("..", "..", "bin", "bash.exe")):
            cand = os.path.normpath(os.path.join(os.path.dirname(git or ""), *rel))
            if git and os.path.isfile(cand):
                return cand
        return None

    def test_the_audit_step_runs_check_on_the_folders_it_checked_out(self):
        """Run the action's own shell step with bash against two prepared folders, the
        way the runner would after checkout and build."""
        bash = self._bash()
        if not bash:
            raise unittest.SkipTest("bash not available")
        step = next(s for s in self._action()["runs"]["steps"] if s.get("id") == "check")
        work = tempfile.mkdtemp(prefix="sitemend-action-")
        self.addCleanup(shutil.rmtree, work, True)
        for side, broken in (("head", True), ("base", False)):
            link = '<a href="/gone/">x</a> ' if broken else ""
            full = os.path.join(work, "_sitemend", side, "out", "index.html")
            os.makedirs(os.path.dirname(full))
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(page_html(body="<h1>H</h1><p>" + link + "word " * 300 + "</p>"))
        shim = os.path.join(work, "bin")
        os.makedirs(shim)
        with open(os.path.join(shim, "sitemend"), "w", newline="\n") as fh:
            fh.write('#!/bin/sh\nexec "{0}" "{1}" "$@"\n'.format(
                sys.executable.replace("\\", "/"),
                os.path.join(sitemend.HERE, "sitemend.py").replace("\\", "/")))
        os.chmod(os.path.join(shim, "sitemend"), 0o755)
        gh_out = os.path.join(work, "gh_output")
        env = dict(os.environ, SITE_DIR="out", URL="", BASELINE_URL="", FAIL_ON="",
                   MAX_PAGES="200", SITEMEND_HTTP_HEADERS="", GITHUB_OUTPUT=gh_out,
                   PATH=shim + os.pathsep + os.environ.get("PATH", ""), PYTHONIOENCODING="utf-8")
        script = os.path.join(work, "step.sh")
        with open(script, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(step["run"])
        r = subprocess.run([bash, script], cwd=work, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, "bash {0} failed:\n{1}{2}".format(
            bash, r.stdout[-2000:], r.stderr[-2000:]))
        with open(gh_out, encoding="utf-8") as fh:
            self.assertEqual(fh.read().split(), ["code=1", "result=fail"])
        self.assertTrue(os.path.isfile(os.path.join(work, "_sitemend", "out", "verify.md")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
