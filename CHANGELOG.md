# Changelog

## Unreleased

- **A re-run no longer audits its own last report.** With `--out` inside the audited
  folder (the default for `audit .`), the second run crawled the first run's
  `dashboard.html` and the demo site went from 22 findings to 29. `seo-repair-out` is now
  excluded from the crawl and ignored by git.
- **GitHub Action example** in `examples/github-action/seo-repair.yml`: audits on every
  pull request and fails the check on a critical finding.
- 1 new test (59 total).

## 1.0.2 - 2026-10-14

The tool now runs without an agent, and two false positives found on the first real-site
run are fixed. Against a real 13-page catering site, findings went from 14 to 3, one of
them real.

- **`scripts/seo_repair.py`: the whole pipeline in one command.** `audit` chains crawl,
  schema check, audit, report and dashboard for a folder or a URL; `verify` diffs a new
  audit against an earlier `findings.json`; `--fail-on` turns open findings into a CI
  exit code. Needs no agent.
- **`S5-SCHEMA-REQUIRED-MISSING` no longer fires on `@id` references.** A nested
  `{"@id": ".../#business", "name": ...}` on a Menu or Service page was reported as a
  FoodEstablishment missing its address, though the full node is defined on another
  page. A property defined for that `@id` anywhere on the site now counts. A business
  with no address anywhere is still flagged.
- **`S3-CANNIBALIZATION` ignores template words.** Terms on at least 35% of pages
  (minimum 4) are dropped before comparing, so five menu pages sharing "catering menu
  set dish" no longer flag each other ten ways. Two pages chasing the same
  distinguishing terms are still reported. Ceiling: a family of 5+ near-identical
  pages on a 13-page site reads as a template.
- README leads with the outcome, says who it is for, and has a "Works with" section.
- 6 new tests (58 total). Demo-site output is unchanged: 22 findings.

## 1.0.1

Two http-mode defects, both found by scoring the tool against its own demo fixture
alongside Lighthouse rather than by reading the code.

- **The http crawl now seeds its frontier from `sitemap.xml`.** It only followed links
  before, so a page that was unlinked *and* listed in the sitemap — the population most
  likely to have an indexing problem — was never fetched. It was then reported as
  `S1-SITEMAP-ORPHAN`, whose message is "listed in sitemap.xml does not exist in the
  crawl", while actually serving a healthy 200. Anyone acting on that went looking for a
  dead URL and found a working page. Seeding from the sitemap is what Screaming Frog and
  Sitebulb do by default, and it makes the frontier match what a search engine discovers.
- **URLs returning 4xx/5xx are no longer recorded as pages.** They are kept in the crawl's
  `skipped` list with their status. Admitting them put them in the alias index, which
  would have silenced `S1-SITEMAP-ORPHAN` for genuinely dead sitemap entries and
  `S1-BROKEN-LINK` for links pointing at them.
- **Gate B has a third state, `NOT ASSESSED`.** It was a pure findings-presence test, so
  in a mode that cannot enumerate pages it turned absence of evidence into a pass —
  printing "no orphan or duplicate pages" on a site with two orphans, and unblocking
  schema work on pages that were merge candidates. fs mode still reports `CLEAR`, since
  walking the filesystem can genuinely prove absence; `BLOCKED` outranks both. Same
  principle as the report's per-stage `assessed` state. The dashboard paints the new state
  neutral — it previously mapped everything that was not `BLOCKED` to the green pill.
- 7 new tests (52 total), including a threaded-HTTP-server fixture pinning the seeding
  behaviour and the dead-sitemap-URL finding it must not silence.

## 1.0.0

First public release.

- Crawler with fs and http modes, handling both clean-URL directories
  (`about/index.html` → `/about/`) and flat files (`services.html`). Build output
  (`dist/`, `node_modules/`, …) is excluded — without that, a `dist/` copy makes every
  real page look like duplicate content.
- Rules engine across five stages, with three ordering gates enforced in code rather
  than only documented.
- Structured-data claim corroboration: checks whether markup asserts content the page
  never shows, including a stricter per-author check for review markup.
- Lighthouse integration for lab Core Web Vitals, tagged as lab and never as field data.
- Regression gate diffing a re-crawl against a baseline.
- Markdown report plus a self-contained HTML dashboard.
- 45 tests and an end-to-end run against a seeded demo site.

Calibration notes, since the thresholds are deliberate rather than arbitrary: titles are
only flagged past 65 characters (50–60 is a target, not a cliff); a `noindex` on a 404 or
a robots-disallowed page is reported as a deliberate exclusion rather than a defect;
findings sharing identical evidence across pages are grouped into one, because a heading
skip in a shared footer is one fix and not five; and cannibalization is suppressed on a
pair already reported as duplicates, since it is the same root cause.
