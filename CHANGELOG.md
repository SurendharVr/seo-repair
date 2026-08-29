# Changelog

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
