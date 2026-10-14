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
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import audit          # noqa: E402
import crawl          # noqa: E402
import report         # noqa: E402
import schema_check   # noqa: E402
import seo_repair     # noqa: E402
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
        self.assertIn('seo_repair.py audit "https://x.test/"', md)
        self.assertNotIn("--root", md)


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

    def test_output_folder_ignores_itself(self):
        """REGRESSION found by the v1.0.3 review: `audit .` left a full-text crawl.json
        in the audited repo with nothing stopping `git add -A`."""
        self.fx.write("index.html", page_html())
        out = os.path.join(tempfile.mkdtemp(prefix="seo-repair-out-"), "o")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        seo_repair.audit(self.fx.root, out, 200, None)
        with open(os.path.join(out, ".gitignore")) as f:
            self.assertEqual(f.read(), "*\n")

    def _verify_args(self):
        d = tempfile.mkdtemp(prefix="seo-repair-verify-")
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
        r = seo_repair.run("verify.py", *self._verify_args(), ok=(0, 1))
        self.assertEqual(r.returncode, 1)
        self.assertIn("REGRESSION GATE: FAIL", r.stdout)

    def test_unexpected_exit_codes_still_abort(self):
        with self.assertRaises(SystemExit):
            seo_repair.run("verify.py", *self._verify_args())


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

        cls.root = tempfile.mkdtemp(prefix="seo-repair-redirect-test-")
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
    Found when a plain prompt noticed it and seo-repair did not."""

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
    The Chrome half is opt-in (SEO_REPAIR_RENDER_TEST=1), like the Lighthouse test: CI
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

    @unittest.skipUnless(os.environ.get("SEO_REPAIR_RENDER_TEST") == "1" and S.find_chrome(),
                         "set SEO_REPAIR_RENDER_TEST=1 (needs Chrome, Chromium or Edge)")
    def test_render_crawl_sees_the_javascript_content(self):
        page = crawl.crawl_http(self.base, render=True, render_limit=3)[0][0]
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
                      self.cmd(nt=False, root=False, env={"SEO_REPAIR_NO_SANDBOX": "1"}))

    def test_windows_never_gets_the_flag(self):
        self.assertNotIn("--no-sandbox",
                         self.cmd(nt=True, root=True, env={"SEO_REPAIR_NO_SANDBOX": "1"}))


class TestIntlDemoSite(unittest.TestCase):
    """End-to-end guard for the v1.2.0 rules: crawl the fixture on disk through the real
    CLI path and require exactly the planted findings, so a rule that stops firing, or
    starts firing on clean pages, fails here."""

    ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples", "demo-site-intl")

    def test_planted_defects_are_found_and_nothing_else_leaks(self):
        out = os.path.join(tempfile.mkdtemp(prefix="seo-repair-intl-"), "o")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        findings = seo_repair.audit(self.ROOT, out, 200, None)
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

    def test_the_wheel_ships_every_script_and_the_dashboard_template(self):
        """pyproject maps scripts/ and assets/ into the wheel by directory, so a new script
        is picked up automatically -- but only if it is a module in scripts/."""
        scripts = {f for f in os.listdir(os.path.join(self.ROOT, "scripts")) if f.endswith(".py")}
        self.assertLessEqual({"seo_repair.py", "crawl.py", "audit.py", "report.py", "verify.py",
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
        d = tempfile.mkdtemp(prefix="seo-repair-kill-")
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


@unittest.skipUnless(os.environ.get("SEO_REPAIR_LIGHTHOUSE_TEST") == "1",
                     "set SEO_REPAIR_LIGHTHOUSE_TEST=1 (needs npx, Lighthouse and Chrome)")
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
        wd = tempfile.mkdtemp(prefix="seo-repair-lh-test-")
        self.addCleanup(shutil.rmtree, wd, True)
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        res, err = vitals.run_one(base + "/?q=a&lang=en", "desktop", wd, timeout=150)
        self.assertIsNone(err)
        self.assertIn("/?q=a&lang=en", seen)


if __name__ == "__main__":
    unittest.main(verbosity=2)
