# Changelog

## 1.4.0 - 2026-10-15

Three checks for deploys that break SEO:

- **`S1-HOST-MISMATCH`:** canonicals, `og:url`, sitemap `<loc>`s, the robots `Sitemap:`
  line and the base URL name different hosts. Critical when one is a hosting platform's
  preview host (`*.vercel.app`, `*.netlify.app`, `*.pages.dev`...) that is not the base.
- **`S1-SOFT-404`:** http mode requests one random path that cannot exist and reports a
  200 answer (SPA catch-all, redirect to home).
- **`S2-UNCRAWLABLE-LINK`:** `href="#"`, `javascript:`, an `<a>` with a click handler
  but no href, or an `onclick` that sets `location` on a non-link. Menu toggles, back-to-top
  links and bare `<a>` breadcrumb items are not reported.

**The example GitHub Action is now a ratchet.** It audits the PR's base branch as the
baseline and fails only on critical or high findings the PR adds, so a site with an
existing backlog can adopt it without every PR going red. It posts one PR comment,
updated on each push, listing new and closed findings with their pages and evidence.
`verify` now also writes `verify.md` next to `verify.json` for that comment. The example
pins `sitemend==1.4.0`, the first release with `verify.md`.

`X-Robots-Tag: noindex` on the production homepage was already the critical
`S1-NOT-INDEXABLE`; a test now pins that.

**Positioned as SEO regression tests for repo-based sites.** The README gains a category
line, a Common questions section and a large-site limitation. The PyPI description,
keywords and Testing/QA classifiers, and the plugin and marketplace descriptions, use
the same wording. A stale README claim is fixed: the demo comparison said sitemend did
not check placeholder domains or Open Graph tags, and it does now.

## 1.3.0 - 2026-10-14

**Renamed everywhere to `sitemend`.** 1.2.1 renamed only the PyPI package and command;
this finishes the job. Breaking for anyone on the old names:

- **Skill and plugin:** `/sitemend`, installed as `sitemend@sitemend` from
  `SurendharVr/sitemend`. Remove a `seo-repair` plugin or `~/.claude/skills/seo-repair`
  copy, then install the new one.
- **Script:** `scripts/seo_repair.py` is now `scripts/sitemend.py`.
- **Environment variables:** `SEO_REPAIR_*` are now `SITEMEND_*`
  (`SITEMEND_NO_SANDBOX`, `SITEMEND_RENDER_TEST`, `SITEMEND_LIGHTHOUSE_TEST`).
- **Default output folder:** `./sitemend-out`. An old `seo-repair-out/` is still skipped
  by the crawler and still git-ignored, so a leftover report is never audited or committed.
- Crawler User-Agent is `sitemend-crawler/1.0`; the dashboard and report say sitemend.
- The GitHub Action example installs `sitemend==1.3.0` from PyPI instead of cloning a tag.
- PyPI keywords add `ai-seo` and `ai-agent`.

## 1.2.1 - 2026-10-14

First release on PyPI, as `sitemend`.

- **The PyPI package and command are now `sitemend`.** `pip install sitemend`, then
  `sitemend audit <folder|URL>`; the wheel's import package is `sitemend`. Nothing was
  ever published as `seo-repair`, so no install breaks. The Claude Code skill, plugin,
  scripts and `SEO_REPAIR_*` variables keep their names.

Fixes from reviewing 1.2.0:

- **Chrome keeps its sandbox on.** `--render` added `--no-sandbox` on every non-Windows
  OS. It is now added only when running as root, or with `SEO_REPAIR_NO_SANDBOX=1`.
- **A slash-less hreflang href is not a missing page.** `href="/en"` for the page `/en/`
  was reported "does not exist" in an http crawl (a folder crawl already aliased it).
  Case and file paths stay strict.
- **Reciprocal hreflang alternates are not duplicates.** en-GB and en-US pages with the
  same text were reported as a critical `S3-DUPLICATE-EXACT` and blocked Gate B. Pages
  that do not name each other are still reported.
- **The report says how many pages `--render` rendered**, and how many failed.
- **`pyproject.toml` uses the SPDX `license` form**, ahead of setuptools dropping the old
  one on 2027-02-18.
- **The render test now runs in CI** against Chrome on Ubuntu.
- **A second fixture, `examples/demo-site-intl/`**, plants one defect per 1.2.0 rule and is
  checked by a unit test.
- The 1.2.0 entry said "Fourteen new checks"; it is thirteen.
- 12 new tests (146 total, two opt-in).

## 1.2.0 - 2026-10-14

Closes the three biggest gaps found by comparing seo-repair with other SEO audit tools:
rule coverage, JavaScript rendering outside Claude Code, and distribution.

- **Thirteen new checks, all stage 1.** `S1-REDIRECT-CHAIN`, `S1-REDIRECT-LOOP`,
  `S1-MIXED-CONTENT`, `S1-SECURITY-HEADERS`, `S1-SITEMAP-LASTMOD-INVALID`,
  `S1-SITEMAP-LASTMOD-UNIFORM`, `S1-HREFLANG-INVALID`, `-BROKEN`, `-NO-SELF`, `-NO-RETURN`,
  `S1-JS-SHELL`, `S1-JS-DEPENDENT`, `S1-JS-HEAD-MISMATCH`. Redirects and headers need an
  http crawl and are silent in fs mode; security headers are only judged on an https
  origin, so a local dev server is never nagged. Alternates on another origin are not
  called broken: one crawl cannot see them. `rel=next/prev` is deliberately not checked
  (Google stopped using it). The demo site is unchanged: 24 findings.
- **`--render`: JavaScript rendering without Claude Code.** An http crawl can parse
  Chrome's post-JavaScript DOM (`chrome --headless --dump-dom`, no Playwright or
  Selenium) for up to `--render-limit` pages (default 50, ~5 s each). The raw HTML's word
  count and head are kept so the two can be compared. Verified on Windows against real
  Chrome; the opt-in test is `SEO_REPAIR_RENDER_TEST=1`.
- **A client-rendered shell is no longer reported as missing its H1.** On an unrendered
  page with an empty `#root`, the headings do not exist yet; `S1-JS-SHELL` says so instead.
- **`seo-repair` is installable.** `pyproject.toml` builds a wheel that ships the scripts
  and the dashboard template and installs a `seo-repair` command (`audit`, `verify`).
  CI builds it, installs it into a clean venv and audits the demo site through it.
  `.github/workflows/release.yml` publishes to PyPI on a `v*` tag via trusted publishing
  (one-time setup on pypi.org, see `RELEASING.md`). **Not yet published**; install with
  `pip install git+https://github.com/SurendharVr/seo-repair` until it is.
- **Python 3.9+.** The README said 3.8, which CI never tested and which is end-of-life.
- 38 new tests (134 total, two opt-in), including a threaded server that redirects
  `/a -> /b -> /c`, loops, and sends response headers, and a version-agreement test across
  `pyproject.toml`, `plugin.json` and `SKILL.md`.

## 1.1.0 - 2026-10-14

Two new checks, found by comparing against a plain prompt that noticed both and seo-repair
did not, and one dashboard bug found while regenerating the README screenshot.

- **`S1-PLACEHOLDER-DOMAIN` (high).** Canonicals, `og:url`, sitemap URLs or the robots
  `Sitemap:` line that use example.com/.org/.net, `localhost`, a reserved TLD (`.test`,
  `.invalid`, ...) or a template name like `yourdomain.com`. The base origin is inferred
  from those same URLs, so a site that was uniformly wrong looked consistent to every
  canonical check. Matching is on whole labels: `notexample.com` is not flagged.
- **`S3-OG-MISSING` (low).** An indexable page without `og:title`, `og:description` or
  `og:image`; the evidence names the missing ones. Noindex and 404-style pages are skipped.
- **The dashboard's BLOCKED pill had no styling since 1.0.1.** The class the script
  produced (`pill block`) did not match the stylesheet (`.pill.blocked`), so gates showed
  plain text instead of the red pill. A test now pins the class names. The README
  screenshot is regenerated.
- The demo site now reports 24 findings (1 critical, 7 high, 7 medium, 9 low): the two new
  checks fire on it by design (`demo.example.com`, no Open Graph tags). `EXPECTED.md`, the
  README output and the CI required-id list are updated. 11 new tests (96 total).

## 1.0.5 - 2026-10-14

The last open finding from the 1.0.3 code review: `vitals.py` on Windows.

- **`vitals.py` no longer mis-parses page paths on Windows.** Lighthouse was started
  through `cmd.exe` with an unquoted URL, so `/search?q=a&lang=en` was measured as
  `/search?q=a` and the rest of the command line (including `--output-path`) ran as a
  second command. Arguments that `cmd.exe` would re-parse are now quoted; a double quote,
  or a `%` that could start a `%VAR%` expansion, is refused with a clear error. Reproduced
  and fixed against real Lighthouse and Chrome.
- **A Lighthouse timeout now kills the whole process tree.** Only the shell was killed,
  so node and Chrome kept running. The wait after the kill is bounded, so a survivor
  cannot hang the audit.
- 6 new tests (85 total): five unit tests, and one opt-in test that runs real Lighthouse
  (`SEO_REPAIR_LIGHTHOUSE_TEST=1`; CI has no Chrome, so it is run by hand).

## 1.0.4 - 2026-10-14

Fixes from a code review of 1.0.3. Each has a regression test that failed before the fix.

- **A site that blocks itself no longer reads as clear.** With `Disallow: /` in robots.txt
  and an empty sitemap, the homepage was filed as a deliberate exclusion and Gate A said
  every crawled page was indexable. A robots-blocked homepage is now `S1-NOT-INDEXABLE` at
  critical and Gate A reads BLOCKED.
- **The heading-skip evidence quotes the right heading.** `S2-HEADING-SKIP` showed the
  first H4 after the first H2, which was fine, instead of the heading that skipped.
- **A lookalike domain no longer passes as the site's own canonical.** A canonical on
  `example.community` was accepted for `example.com` because the check was a string
  prefix. It now compares scheme and host.
- **Review authors are matched as whole words, in any script.** "Ann" was found inside
  "channel", short names were skipped, and non-Latin names were never checked. Names under
  two characters are ignored. Crawls gain a `visible_text` field; an older `crawl.json`
  without it skips the check.
- **A clean schema run shows Stage 5 as assessed.** It was reported as not measured when
  the check ran and found nothing (`findings.json` meta now has `schema_checked`). The
  report's re-run block now prints `seo_repair.py` commands instead of ones that passed a
  URL to `crawl.py --root`.
- **A failed regression gate is reported as a gate result.** `verify` printed
  "verify.py failed:" and exited as if the script had crashed when the gate failed. The output
  folder now writes its own `.gitignore`, so `audit .` cannot leave a crawl to commit.
- **An http redirect is no longer a second page.** `/about` redirecting to `/about/` was
  stored twice, which raised `S3-DUPLICATE-EXACT` at critical. The target is stored once
  and the requested path is recorded as an alias.
- 20 new tests (79 total).
- Not changed: `vitals.py` still runs Lighthouse through `shell=True` on Windows, so a page
  path containing `&` is mis-parsed (needs Windows + Chrome to verify).

## 1.0.3 - 2026-10-14

Fixes a bug in 1.0.2's CLI and adds a CI example.

- **A re-run no longer audits its own last report.** With `--out` inside the audited
  folder (the default for `audit .`), the second run crawled the first run's
  `dashboard.html` and the demo site went from 22 findings to 29. `seo-repair-out` is now
  excluded from the crawl and ignored by git.
- **GitHub Action example** in `examples/github-action/seo-repair.yml`: audits on every
  pull request and fails the check on a critical finding. Run end to end on GitHub:
  checkout, install, audit (failed on the demo site's critical finding, as intended)
  and the report uploaded as an artifact even though the audit step failed.
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
