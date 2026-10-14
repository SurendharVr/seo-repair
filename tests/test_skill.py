"""Regression tests for the seo-repair skill.

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
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import audit          # noqa: E402
import crawl          # noqa: E402
import schema_check   # noqa: E402
import seo_repair     # noqa: E402
import seolib as S    # noqa: E402
import verify as V    # noqa: E402


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
        self.root = tempfile.mkdtemp(prefix="seo-repair-test-")

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
            "sitemap": {"found": bool(sitemap), "urls": sm_urls, "notes": []},
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


# --------------------------------------------------------------- cli

class TestCli(Base):
    def test_audit_writes_every_artifact_and_edits_nothing(self):
        self.fx.write("index.html", page_html())
        before = sorted(os.listdir(self.fx.root))
        out = os.path.join(tempfile.mkdtemp(prefix="seo-repair-out-"), "o")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        findings = seo_repair.audit(self.fx.root, out, 200, None)
        names = os.listdir(out)
        for want in ("crawl.json", "schema.json", "findings.json", "report-data.json",
                     "dashboard.html"):
            self.assertIn(want, names)
        self.assertTrue(any(n.startswith("SEO-REPORT-") for n in names))
        self.assertEqual(sorted(os.listdir(self.fx.root)), before, "audit must not touch the site")
        self.assertGreaterEqual(seo_repair.worst_open(findings), -1)

    def test_rerun_does_not_audit_its_own_report(self):
        """REGRESSION: with --out inside the audited folder (the default when run as
        `audit .`), the second run crawled the first run's dashboard.html and reported
        7 extra findings on the demo site."""
        self.fx.write("index.html", page_html())
        out = os.path.join(self.fx.root, "seo-repair-out")
        counts = []
        for _ in range(2):
            findings = seo_repair.audit(self.fx.root, out, 200, None)
            counts.append(len(json.load(open(findings, encoding="utf-8"))["findings"]))
        self.assertEqual(counts[0], counts[1])

    def test_worst_open_ranks_severity(self):
        path = os.path.join(self.fx.root, "f.json")
        with open(path, "w") as f:
            json.dump({"findings": [{"severity": "low"}, {"severity": "high"}]}, f)
        self.assertEqual(seo_repair.SEVERITY[seo_repair.worst_open(path)], "high")


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

    def test_shrinking_page_list_is_changed_not_new(self):
        """REGRESSION: when a grouped finding's page list shrank after a partial fix,
        it read as one finding closed plus one brand-new finding -- wrong and
        alarming on every re-run."""
        base = [self.f("S2-HEADING-SKIP", ["/a", "/b", "/c"])]
        cur = [self.f("S2-HEADING-SKIP", ["/a", "/b"])]
        self.assertTrue(V.related(base[0], cur[0]))

    def test_unrelated_finding_is_new(self):
        self.assertFalse(V.related(self.f("S2-ORPHAN", ["/a"]), self.f("S2-ORPHAN", ["/z"])))
        self.assertFalse(V.related(self.f("S2-ORPHAN", ["/a"]), self.f("S3-THIN", ["/a"])))


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

        cls.root = tempfile.mkdtemp(prefix="seo-repair-http-test-")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
