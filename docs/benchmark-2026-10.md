# Benchmark: sitemend on 20 open-source sites (October 2026)

We ran sitemend on 20 popular open-source framework and docs sites and checked every
headline finding by hand. This page gives totals only and names no sites.

## Method

- **Sample:** 20 sites of popular open-source projects: 5 Next.js, 1 SvelteKit,
  6 Docusaurus, 5 Astro/Starlight, 1 Hugo and 1 VitePress. 18 are hosted on Vercel or
  Netlify.
- **Crawl:** `sitemend audit https://<host>/ --max-pages 50`: http mode against the
  production URL, 50 pages per site, one site at a time with the single-threaded
  crawler. Each site also got one request for a made-up URL, to test for soft 404s.
- **Only checks that are valid on a partial crawl are counted.** With a 50-page cap,
  checks that need the whole site would report the cap itself as a defect, so these
  are **not measured** here: `S1-BROKEN-LINK`, `S1-HREFLANG-BROKEN`,
  `S1-SITEMAP-ORPHAN`, `S2-DEEP`, `S2-LOW-INLINKS`, `S2-ORPHAN`, `S2-UNREACHABLE`,
  `S3-CANNIBALIZATION`, `S3-DESC-DUPLICATE`, `S3-NEAR-DUPLICATE` and
  `S3-TITLE-DUPLICATE`.
- **Hand-verified:** every deploy and indexing finding below was checked with direct
  requests (curl and BeautifulSoup) before it was counted.
- **Two runs on 2026-10-15:** first with sitemend 1.4.0 from PyPI, then again on the
  same 20 sites after fixing the false positives that run exposed (released as 1.4.1).
  Every total below comes from the second run.

## What it found (verified)

- **1 site's sitemap lists 148 URLs its own robots.txt blocks** (148 of 361 sitemap
  URLs sit under a path robots.txt disallows). Google is asked to crawl pages it isn't
  allowed to crawl. `S1-INDEX-CONTRADICTION`, critical.
- **3 sites have no canonical tag on any page we crawled** (50 of 50 pages each), and
  2 more miss it on some pages (4 and 8 of 50). `S1-CANONICAL-MISSING`.
- **3 sites have no sitemap search engines can find**, at any common path or in
  robots.txt. `S1-SITEMAP-MISSING`.
- **6 more have a sitemap that robots.txt doesn't point to:** 5 have a robots.txt with
  no `Sitemap:` line (`S1-ROBOTS-NO-SITEMAP`) and 1 has no robots.txt at all.
- **2 docs sites have placeholder `href="#"` links in their documentation text**, such
  as a command name that links nowhere. `S2-UNCRAWLABLE-LINK`.
- 7 sites have no robots.txt (reported at low severity: Google treats a missing
  robots.txt as "crawl everything").

**Precision on the hand-checked findings in the second run:** `S1-INDEX-CONTRADICTION`
1 of 1, `S1-SITEMAP-MISSING` 3 of 3, `S1-ROBOTS-NO-SITEMAP` 5 of 5,
`S2-UNCRAWLABLE-LINK` 2 of 2. One `S1-HOST-MISMATCH` (medium) is accurate as worded but
isn't a defect: the site serves on one host while every URL it publishes deliberately
names another, so the finding asks you to check the redirect.

On-page checks (missing H1, description or Open Graph tags, image alt text, security
headers) were common but weren't verified one by one, so they aren't reported here.

## What it didn't find, and why that matters

- **0 of 20 sites served a soft 404.** All 20 returned a real 404 for a made-up URL.
- **0 sites leaked a preview URL** (`*.vercel.app`, `*.netlify.app` and similar) into
  their canonicals, `og:url` or sitemap.
- **0 sites published a placeholder domain** such as `example.com` or `localhost`.

These are mature, heavily maintained sites, and they've already fixed what would show
up here. Soft 404s and preview leaks don't sit in production for long: they happen on
the deploy that introduces them and get fixed once someone notices, often weeks later
in Search Console. That's why sitemend is built as a regression check on pull requests
rather than a production audit. The [demo PR](https://github.com/SurendharVr/sitemend-demo/pull/2)
shows the check failing on exactly that kind of change.

## The false positives it found in itself

The first run, with 1.4.0, found sitemend's own bugs before it found anyone else's.
All of these are fixed in [1.4.1](../CHANGELOG.md):

| Problem | 1.4.0 | 1.4.1 |
|---|---|---|
| A meta-refresh home page (`<meta http-equiv="refresh" content="0;url=…">`) was judged as a page, not followed as a redirect | 1 site crawled 1 page; 2 critical and 7 other findings, all wrong | Redirect followed; 50 pages crawled; 0 critical |
| Sitemaps declared in robots.txt, or published as `sitemap-index.xml`, were reported missing | `S1-SITEMAP-MISSING` (high) on 7 sites: 3 true, 3 misleading, 1 false | `S1-SITEMAP-MISSING` (medium) on 3 sites, all true; the undeclared ones are `S1-ROBOTS-NO-SITEMAP` (medium) on 5 sites, all true |
| `S2-UNCRAWLABLE-LINK` flagged a table of contents, decorative `aria-hidden` links and demo UI components | 5 sites, 2 true | 2 sites, both true; the evidence now shows the tag and link text, e.g. `<a href="#">…</a>` |
| A deliberate canonical host was treated as a mismatch | 1 high `S1-HOST-MISMATCH` plus 50 critical `S1-CANONICAL-OFFSITE` on one site | One medium `S1-HOST-MISMATCH`: "served on X, but every URL names Y; check X redirects to Y". Still critical when Y is a preview host |
| A missing robots.txt was rated high | high on 7 sites | low |

The critical sitemap/robots contradiction stayed critical in both runs.

Reading sitemap indexes changed which 50 pages some crawls reached. One redirect chain
found in the first run wasn't reached in the second, and the on-page counts moved a
little. Those weren't re-verified by hand and aren't used above.

## How to rerun it

On any list of sites, one at a time:

```bash
pip install sitemend==1.4.1
sitemend audit https://<host>/ --max-pages 50 --out out/<host>
curl -s -o /dev/null -w "%{http_code}\n" https://<host>/a-page-that-does-not-exist-123
```

Each run writes `findings.json`, a Markdown report and `dashboard.html` to its `--out`
folder. To compare like with like, count only the checks that are valid on a partial
crawl (leave out the ones listed under Method), then confirm each finding against the
raw HTML, robots.txt and sitemap before you trust a number. If you find a false
positive, please [open an issue](https://github.com/SurendharVr/sitemend/issues) with
the URL and the finding.
