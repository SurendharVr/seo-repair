# International demo site — what a correct run reports

A four-page fictional site (`acme-widgets.com` is never fetched) with one defect planted
per rule added in v1.2.0. `tests/test_skill.py::TestIntlDemoSite` requires exactly this
list; if you change a rule, change the fixture or this table in the same commit.

```bash
python scripts/seo_repair.py audit examples/demo-site-intl --out /tmp/intl-out
```

**11 findings — 0 critical, 3 high, 7 medium, 1 low.**

| Planted defect | Where | Reported as |
| --- | --- | --- |
| `/` lists `/fr/` as an alternate; `/fr/` does not list `/` back | `/` | `S1-HREFLANG-NO-RETURN` (high) |
| `hreflang="de"` points at `/de/about/`, which does not exist | `/about/` | `S1-HREFLANG-BROKEN` (high) |
| `hreflang="en_GB"` (underscore, not hyphen) | `/about/` | `S1-HREFLANG-INVALID` (medium) |
| `<script src="http://...">` on an https page | `/about/` | `S1-MIXED-CONTENT` (high) |
| Empty `<div id="root">` and one script, no text | `/app/` | `S1-JS-SHELL` (medium) |
| `<lastmod>last tuesday</lastmod>` | `sitemap.xml` | `S1-SITEMAP-LASTMOD-INVALID` (medium) |

The other five findings are noise the fixture accepts on purpose: `S3-THIN` on each of
the four pages (they are one sentence long) and one grouped `S2-BREADCRUMB-MISSING`.

### What it should NOT report

- `/fr/` lists only itself, which is a valid set: no `S1-HREFLANG-NO-SELF`.
- `/` and `/fr/` are different text: no duplicate or cannibalization findings.
- Every canonical is self-referential on a non-placeholder origin: no `S1-CANONICAL-*`.
- Redirect chains, security headers and `S1-JS-DEPENDENT` need an http crawl, so they
  never appear when auditing a folder.
