# Demo site — what a correct run reports

This site has defects planted on purpose, one per check family. It exists so you can
confirm the tool works before pointing it at anything you care about, and so you can
see what the output looks like.

Nothing here is a real business. `demo.example.com` is not a real domain.

## Run it

From `scripts/`:

```bash
python crawl.py        --root ../examples/demo-site --out /tmp/crawl.json
python schema_check.py --crawl /tmp/crawl.json      --out /tmp/schema.json
python audit.py        --crawl /tmp/crawl.json --schema /tmp/schema.json --out /tmp/findings.json
```

## What it should find

**24 findings — 1 critical, 7 high, 7 medium, 9 low**, and this gate state:

| Gate | Status | Why |
| --- | --- | --- |
| A — Index before content | CLEAR | every page is indexable |
| B — Architecture before schema | BLOCKED | `/old-home.html` and `/reviews.html` are orphaned |
| C — Everything before authority | BLOCKED | 8 critical/high findings open in stages 1–5 |

Gate B blocking is the point of the demo: `/old-home.html` is a duplicate awaiting
consolidation, so writing schema for it would be discarded along with the page.

### The planted defects

| # | Defect | Where | Reported as |
| --- | --- | --- | --- |
| 1 | Byte-identical copy of the homepage | `/old-home.html` | `S3-DUPLICATE-EXACT` (critical) |
| 2 | …which nothing links to | `/old-home.html`, `/reviews.html` | `S2-ORPHAN`, `S2-UNREACHABLE` |
| 3 | …canonical'd to `/` instead of removed | `/old-home.html` | `S1-CANONICAL-CROSS` |
| 4 | …and absent from the sitemap | `/old-home.html` | `S1-SITEMAP-GAP` |
| 5 | …sharing the homepage's title and description | `/`, `/old-home.html` | `S3-TITLE-DUPLICATE`, `S3-DESC-DUPLICATE` |
| 6 | Sitemap lists a URL that does not exist | `/gone.html` | `S1-SITEMAP-ORPHAN` |
| 7 | No canonical | `/services.html` | `S1-CANONICAL-MISSING` |
| 8 | Title 8 chars, description 6 | `/services.html` | `S3-TITLE-SHORT`, `S3-DESC-SHORT` |
| 9 | 320 KB image behind `<picture><source>` | `/services.html` | `S3-IMG-HEAVY` |
| 10 | 5 words of content | `/about.html` | `S3-THIN` |
| 11 | Image with no `alt` and no dimensions | `/about.html` | `S3-IMG-ALT-MISSING`, `S3-IMG-DIMS-MISSING` |
| 12 | Heading jumps H1 → H3, plus an H5 footer | `/reviews.html` and site-wide | `S2-HEADING-SKIP` |
| 13 | Review markup for a person the page never names | `/reviews.html` | `S5-SCHEMA-UNSUPPORTED-CLAIM` (high) |
| 14 | 260 KB image referenced by nothing | `assets/unused-banner.jpg` | `S3-ASSET-UNUSED` |
| 15 | Canonicals, sitemap and robots `Sitemap:` line all use `demo.example.com`, a reserved placeholder origin | site-wide | `S1-PLACEHOLDER-DOMAIN` (high) |
| 16 | No `og:title`, `og:description` or `og:image` | every page | `S3-OG-MISSING` (low, one grouped finding) |

### What it should NOT report

Just as important — these are correct and must stay silent:

- **`/index.html` is not a duplicate of `/`.** Same file, two paths. In http mode they
  collapse to one page.
- **The `<source>` element has no `alt` or dimensions**, and is not flagged for either —
  only its bytes are checked.
- **`address` and `telephone` in the schema are corroborated**, because they appear in
  the footer. Only the review, which appears nowhere, is flagged.
- **The three pages over 250 words are not thin.** Only `/about.html` is.

If you see findings beyond the table above, either the tool has regressed or you have
changed the fixture. Both are worth understanding before trusting a run on a real site.

## What the demo cannot show

Stage 4 (Core Web Vitals) needs a running server and a local Chrome, so it produces
nothing here. Stage 6 (authority) is advisory in every run — the tool cannot measure a
backlink profile and does not pretend to.
