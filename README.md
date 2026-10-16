# sitemend

[![tests](https://github.com/SurendharVr/sitemend/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/SurendharVr/sitemend/actions/workflows/tests.yml)
[![license](https://img.shields.io/github/license/SurendharVr/sitemend)](LICENSE)

**Catch the deploys that break your SEO, before Google does.**

SEO regression tests for Next.js, Astro and docs sites.

Your site lives in a repo, ships every week, and more of its code is written by AI
tools. Any one of those changes can drop a canonical tag, leak a `*.vercel.app` preview
URL into the sitemap, orphan a page, or hide its content behind JavaScript. Usually you
find out weeks later, in Search Console.

sitemend crawls your build on every pull request, fails the check when a change breaks
crawling or indexing, and cites the exact page and string that caused it. In
[Claude Code](https://claude.com/claude-code) it goes one step further: each finding
becomes a fix you approve as a diff, followed by a re-crawl that proves the fix landed.

```bash
pip install sitemend
sitemend audit ./my-site --fail-on high
```

Free and MIT-licensed. No account and no API key; on a folder it makes no network
calls, so your site never leaves your machine.

**See it work:** a [live demo PR](https://github.com/SurendharVr/sitemend-demo/pull/2)
that leaks a preview URL and drops a canonical, failed by the Action with its comment.
**Proof:** a [hand-verified benchmark on 20 open-source sites](docs/benchmark-2026-10.md),
including the false positives it found in sitemend itself.

**Built for** product and docs teams whose site lives in a repo (Next.js, Astro, Hugo,
Docusaurus, plain HTML) on Vercel, Netlify or similar, and web agencies that want proof
a site is crawlable before handover. **Not for** sites that live only in a CMS such as
WordPress, Shopify or Webflow, where there is no code to fix, and not for keyword or
backlink research; use claude-seo or Ahrefs for that.

Formerly `seo-repair`; the old GitHub URL redirects here.

## What breaks, and what sitemend reports

| What went wrong | Finding |
| --- | --- |
| The canonical or sitemap points at `localhost`, `example.com` or another page | `S1-PLACEHOLDER-DOMAIN`, `S1-CANONICAL-CROSS`, `S1-CANONICAL-OFFSITE` |
| A preview deploy's host (`*.vercel.app`, `*.netlify.app`) leaked into the production canonicals, `og:url` or sitemap | `S1-HOST-MISMATCH` (critical) |
| Missing URLs answer 200 instead of 404 (an SPA catch-all, a redirect to home) | `S1-SOFT-404` (http mode) |
| Navigation became `javascript:` or an `onclick` with no link a crawler can follow, or text links became `href="#"` | `S2-UNCRAWLABLE-LINK` |
| A page is listed in the sitemap but blocked by `noindex` or `robots.txt` | `S1-INDEX-CONTRADICTION` (critical) |
| Content only appears after JavaScript runs, so crawlers that don't render it see an empty page | `S1-JS-SHELL`; with `--render`, `S1-JS-DEPENDENT` and `S1-JS-HEAD-MISMATCH` |
| A redesign orphaned pages or broke internal links | `S2-ORPHAN`, `S1-BROKEN-LINK` |
| A template produces duplicate or near-duplicate pages | `S3-DUPLICATE-EXACT`, `S3-NEAR-DUPLICATE` |
| Structured data claims ratings or reviews the page never shows | `S5-SCHEMA-UNSUPPORTED-CLAIM` |

The full list is under [What it finds](#what-it-finds).

## Fixes in the order that matters

SEO findings are not independent, so sitemend treats them as a repair system with a
dependency order, **enforced in code**.

```
Crawl → Index → Architecture → Content → On-Page → Performance → Schema → Authority → Monitor
```

Work done out of order is wasted. Optimising a title on a page Google cannot index
changes nothing. Writing schema for a page about to be merged away is thrown out with
the page. Building links to either is the most expensive mistake of the three. Three
**gates** make that ordering unskippable rather than advisory.

---

## What a run looks like

Auditing the bundled demo site — the gate lines are the point, and they say *why*, not just *blocked*:

```console
$ python audit.py --crawl crawl.json --schema schema.json --out findings.json
24 findings: 1 critical, 7 high, 7 medium, 9 low
  stage 1: 5
  stage 2: 6
  stage 3: 11
  stage 5: 2
  gate A (Index before content): CLEAR -- every crawled page is indexable
  gate B (Architecture before schema): BLOCKED -- 3 page(s) orphaned or pending consolidation: /, /old-home.html, /reviews.html
  gate C (Everything before authority): BLOCKED -- 8 critical/high finding(s) open in stages 1-5: S1-CANONICAL-CROSS, S1-PLACEHOLDER-DOMAIN, S1-SITEMAP-ORPHAN, S2-ORPHAN
```

Each run also produces a self-contained HTML dashboard:

![The sitemend dashboard: severity totals, the seven-stage repair pipeline with gates drawn between the stages, and the three gate cards showing why each is clear or blocked](docs/dashboard.png)

Note stage 04. Lighthouse was not run here, so Performance reads **not measured** rather
than *clear* — a stage that produced no findings because nobody looked at it is not a
stage that passed, and the dashboard will not imply otherwise.

---

## Two things that make it different

**It refuses to invent numbers.** Search volume, keyword difficulty, backlink counts and
field Core Web Vitals cannot be observed from a codebase. They are never estimated —
they become a handoff list naming the exact Search Console or Ahrefs report to open.
Every claim in a report is tagged `MEASURED` (observed directly), `INFERRED` (reasoned
from observation, stated as such), or `HANDOFF` (not observable here). A local crawl
proves a page is *crawlable*; it never proves the page is *indexed*, and the report will
not blur those.

The same standard applies to what it writes: no invented author bios, testimonials,
ratings or trust claims. A tool that refuses to guess at keyword volume has no business
guessing at a person's credentials.

**It checks whether your structured data is honest.** Most validators check that schema
parses and has its required properties. This one also checks whether the markup asserts
things the page never shows — a rating nobody can see, an address that appears nowhere,
or review markup naming people the page does not mention. That last one is a Google
rich-results policy violation and a manual-action risk, and a property-checker will
never catch it.

## Versus asking for an SEO audit

The alternative to a tool like this is pasting a page into a chat and asking for an SEO
audit. That gives you advice. This gives you a crawl, with the differences that follow
from that:

| | A checklist prompt | sitemend |
| --- | --- | --- |
| **Coverage** | The pages you paste or open, one at a time | Every page, from a folder or a live URL (200 by default), plus `robots.txt` and the sitemaps it declares (or `/sitemap.xml`, `/sitemap-index.xml`) |
| **What it sees** | Text or markup you supply | Real status codes and redirects, canonicals, internal links, orphans, click depth |
| **Repeatable** | Wording and findings can differ run to run | Same site, same crawl, same findings. `verify` diffs a new run against a baseline |
| **Evidence** | Advice in prose | Each finding names the page and cites the offending string |
| **Order of work** | One flat list | Dependency order, with three gates enforced in code: no content work on pages Google cannot index, no schema on orphaned pages, no link building while critical findings are open |
| **Numbers it cannot measure** | Nothing in a prompt stops a plausible figure | Never stated. Volume, difficulty, backlinks and field Core Web Vitals become a handoff list naming the report to open |
| **Structured data** | Required properties, if it checks at all | Required properties, and whether the markup claims things the page never shows, such as review authors |
| **Applying fixes** | Suggestions | One approved batch at a time, then a re-crawl to confirm it worked. Nothing is edited without approval |
| **In CI** | No | The example GitHub Action fails a PR only on findings it introduces, and comments the new and closed ones on the PR |
| **Tested** | Not testable | 189 tests, many pinned to false positives found on real runs |

**How big is the gap?** We measured it on the demo site (5 pages, 14 planted defects).
Three plain "audit this site" runs by an AI agent with read-only file access found 14, 12
and 13 of the defects, with one false alarm between them. sitemend found all 14 with no
false alarms, gave identical findings on two runs, and took about 3 seconds without using
a language model. On a site this small a good prompt does well, and it noticed two
things sitemend did not check at the time, a placeholder domain in the sitemap and
missing Open Graph tags; both are rules now (`S1-PLACEHOLDER-DOMAIN`, `S3-OG-MISSING`).
The advantage is repeatability, scale and CI, not a better score on five
pages. The demo was built around what sitemend detects, and this was one model and three
runs, so read it as an example, not a benchmark.

A prompt is the better tool for work that needs the live search results: keyword
research, reading what the top-ranking pages cover, and writing or rewriting content.
sitemend does not do that, and hands it off by design. Use them in that order: fix
what stops a page being crawled and indexed first, then decide what it should say.

---

## Works with

- **In CI:** a GitHub Action that fails the pull-request check when a change breaks
  crawling or indexing. See below.
- **On the command line:** `sitemend audit <folder|URL>` runs the whole audit on plain
  Python 3.9+. No agent needed.
- **In Claude Code:** the full workflow (audit, plan, apply, verify), where each fix
  arrives as a diff you approve.
- **Tested on:** Linux, macOS and Windows. The Lighthouse step is verified on Windows only.

## Install

**As a command-line tool:**

```bash
pip install sitemend
sitemend audit ./my-site
```

(For unreleased changes on `main`: `pip install git+https://github.com/SurendharVr/sitemend`.)

**As a Claude Code plugin, to apply fixes:**

```
/plugin marketplace add SurendharVr/sitemend
```

**Or clone straight into your skills directory:**

```bash
git clone https://github.com/SurendharVr/sitemend ~/.claude/skills/sitemend
pip install -r ~/.claude/skills/sitemend/requirements.txt
```

Requires Python 3.9+ with `beautifulsoup4`, `lxml` and `requests` (`pip` installs them;
from a clone, `requirements.txt` lists them). Core Web Vitals and `--render` additionally
need a local Chrome, Chromium or Edge; Core Web Vitals also need Node, via
`npx lighthouse`.

## Run it in CI or from the command line

One command audits a site folder or a live URL and writes a report and dashboard. It
edits nothing in the site.

```bash
python scripts/sitemend.py audit ./my-site              # or https://example.com
python scripts/sitemend.py audit ./my-site --fail-on high   # exit 1 if a high/critical finding is open (CI gate)
python scripts/sitemend.py verify ./my-site --baseline sitemend-out/findings.json
```

To guard a site on every pull request, add the GitHub Action. It checks out the PR and
its base branch, builds both, audits both, and fails the check only when the PR adds a
measured critical or high finding the base did not have. Findings already on the base
branch never fail a PR, so you can turn it on for a site with a backlog today. One PR
comment, updated on every push, lists the new and closed findings with each one's page
and quoted evidence; the full report and dashboard are attached to the run.

```yaml
- uses: actions/setup-node@v4
  with:
    node-version: 20
- uses: SurendharVr/sitemend@v1.5.0
  with:
    build-command: npm ci && npm run build
    site-dir: out
```

The complete workflow is [`examples/github-action/sitemend.yml`](examples/github-action/sitemend.yml).
For a stricter gate that also fails on findings the base already had, add
`fail-on: high`.

Only measured findings fail the check. Judgement calls, such as two pages that seem to
target the same terms (`S3-CANNIBALIZATION`) or near-duplicate text, are listed in the
comment as advisory, because adding an ordinary sibling page must not fail a PR.

### Build recipes

| Framework | `build-command` | `site-dir` | Notes |
| --- | --- | --- | --- |
| Next.js (static export) | `npm ci && npm run build` | `out` | Needs `output: 'export'` in `next.config.js` |
| Astro | `npm ci && npm run build` | `dist` | |
| Docusaurus | `npm ci && npm run build` | `build` | |
| SvelteKit (`adapter-static`) | `npm ci && npm run build` | `build` | |
| VitePress | `npm ci && npm run docs:build` | `docs/.vitepress/dist` | The script name `vitepress init` creates |
| Eleventy | `npm ci && npx @11ty/eleventy` | `_site` | |
| Hugo | `hugo --minify` | `public` | Add `peaceiris/actions-hugo` before the action |
| Plain HTML | leave empty | the folder that holds it | |
| Server-rendered (Next.js without export, Nuxt, Remix) | | | Use the preview workflow below |

### Server-rendered sites: check the preview deploy

[`examples/github-action/preview.yml`](examples/github-action/preview.yml) runs when Vercel
or Netlify reports a successful preview deploy. It crawls the preview, crawls production,
and fails only on what the preview broke; the comment goes on the PR the commit belongs
to. Vercel previews sit behind Deployment Protection by default: create a "Protection
Bypass for Automation" secret in the Vercel project, store it as a GitHub secret, and the
workflow sends it as the `x-vercel-protection-bypass` header. Headers come from the
`SITEMEND_HTTP_HEADERS` environment variable, never the command line, so a secret stays out
of process listings and reports.

On another CI system, `sitemend check` does the same job:
`sitemend check ./head/out --base ./base/out` for two builds, or
`sitemend check https://preview.example.com --base https://www.example.com` for two URLs.

To see what the comment looks like, open the
[demo PR](https://github.com/SurendharVr/sitemend-demo/pull/2) in
[sitemend-demo](https://github.com/SurendharVr/sitemend-demo), a small static site
running this exact workflow.

Output lands in `./sitemend-out`: `findings.json`, `SEO-REPORT-<date>.md` and a
self-contained `dashboard.html`. Applying fixes stays a Claude Code job, because each
edit needs your approval.

## Try it on the demo site

The repo ships a small site with defects planted on purpose — an orphan duplicate, a
thin page, an oversized image behind `<picture>`, review markup for someone the page
never names, and more:

```bash
cd scripts
python crawl.py        --root ../examples/demo-site --out /tmp/crawl.json
python schema_check.py --crawl /tmp/crawl.json      --out /tmp/schema.json
python audit.py        --crawl /tmp/crawl.json --schema /tmp/schema.json --out /tmp/findings.json
```

[`examples/demo-site/EXPECTED.md`](examples/demo-site/EXPECTED.md) lists exactly what a
correct run reports — **and what it must stay silent about**, which is the harder half.

## Fixing in Claude Code

Four verbs, driven through Claude Code:

| Verb | Does | Writes |
| --- | --- | --- |
| `sitemend audit <path>` | Crawl → audit → report. Establishes the baseline. | report only |
| `sitemend plan` | Ranks findings into stage-ordered batches, previews each diff | nothing |
| `sitemend apply <batch>` | Applies **one approved batch**, re-checks that stage | source files |
| `sitemend verify` | Re-crawl, diff vs. baseline, regression gate | report only |

Nothing is edited without approval. `plan` refuses to run without a baseline; `apply`
refuses without a named, approved batch.

## What it finds

| Stage | Checks |
| --- | --- |
| **Crawling & indexing** | `robots.txt` rules, XML sitemap coverage and contradictions, `noindex` directives, canonical tags (missing, cross-page, broken, off-origin), placeholder domains (example.com, localhost) in canonicals and the sitemap, mismatched hosts across canonicals, `og:url`, the sitemap and robots.txt (a preview host leaking into production), soft 404s (http mode), broken internal links, URL hygiene, redirect chains (HTTP and meta refresh) and loops, mixed content, `hreflang` (invalid codes, broken targets, missing self and return tags), sitemap `<lastmod>` validity, response security headers, and client-rendered shells (`--render` compares raw HTML to the rendered DOM) |
| **Site architecture** | Orphan pages, unreachable pages, click depth, internal link distribution, links a crawler cannot follow (`href="#"`, `javascript:`, `onclick` navigation), heading structure, breadcrumbs |
| **Content & on-page** | Duplicate and near-duplicate content (MinHash), keyword cannibalization, thin content, title tags, meta descriptions, Open Graph tags, image alt text, oversized images, unused assets |
| **Core Web Vitals** | Lighthouse LCP, CLS and TBT — reported as *lab* metrics, with the localhost caveat stated |
| **Schema markup** | JSON-LD validity, required and recommended properties, and whether the markup's claims are corroborated by visible page text |
| **Authority / off-page** | Advisory only. It cannot measure a backlink profile and does not pretend to — it produces a human action list, and refuses paid-link and PBN tactics outright |

Findings cite the offending string from your source, so every one is checkable.

## Common questions

**We already run Lighthouse CI.** Keep it. Lighthouse tests one page at a time and its
SEO category is a short list of per-page checks. sitemend crawls the whole site for
indexability: sitemap against pages, orphans, canonicals, duplicates, host consistency.
It runs Lighthouse itself for Core Web Vitals.

**We use Ahrefs or Screaming Frog.** Those report on the live site after a deploy, for
whoever does SEO. sitemend blocks the merge, for whoever wrote the change. They work
well together.

**How is this different from claude-seo?** claude-seo audits live URLs for strategy:
keywords, AI search, backlinks, a score. sitemend tests your repo in CI for
regressions, with evidence and no scores. Use claude-seo to decide what to do, and
sitemend so deploys don't undo it.

**Our site already has hundreds of SEO issues. Won't this fail every PR?** Start with
`--fail-on critical`, or save a baseline and gate on `sitemend verify --baseline`,
which fails only on critical or high findings that are new since the baseline.

**Does it need Claude?** No. The CLI and the GitHub Action run anywhere Python 3.9+
does. Claude Code adds the fix-as-a-diff step.

**Our Next.js site is server-rendered.** Use [`preview.yml`](examples/github-action/preview.yml):
it waits for the preview deploy, crawls it and production, and compares the two,
including headers and the soft-404 probe. A static export with the build recipe gets the
fullest coverage, including proof that no page is orphaned.

**Is my code sent anywhere?** No. On a folder it makes no network calls. On a URL it
only requests your own site.

## Limitations, stated plainly

- **JS rendering is for live URLs.** `sitemend audit https://... --render` parses
  Chrome's post-JavaScript DOM with a local Chrome, Chromium or Edge, for up to 50 pages
  (`--render-limit`, about 5 seconds each, one browser launch per page). A folder has no
  running site to render, so fs mode only gets the static-shell hint `S1-JS-SHELL`. The
  mobile-overflow check is still Claude Code only (it uses the built-in browser tools).
  The render path is verified against real Chrome on Windows, and in CI on Ubuntu; the
  opt-in test is `SITEMEND_RENDER_TEST=1`. Chrome runs with its sandbox on; as root, or in a container
  where it will not start, set SITEMEND_NO_SANDBOX=1. Each page gets 30 seconds; on a slow
  machine raise it with SITEMEND_RENDER_TIMEOUT (seconds).
- **Redirect chains, response headers and rendering need an http crawl**, because a
  folder on disk has none of them. Pagination (`rel=next/prev`) is not checked: Google
  stopped using it.
- **Core Web Vitals need local Chrome.** These are lab numbers from your machine, not
  field data. Real CWV comes from CrUX via Search Console, which is a handoff item.
- **`vitals.py` is verified on Windows only.** CI runs the test suite on Linux, macOS
  and Windows, but excludes Lighthouse because it needs a browser — so that one script's
  cross-platform behaviour is untested rather than proven. Reports welcome. One opt-in
  test (`SITEMEND_LIGHTHOUSE_TEST=1`) runs real Lighthouse against a local server.
- **Stage 6 does no off-page work.** By design.
- **An http crawl stops at `--max-pages`.** fs mode has no page cap. The duplicate and
  cannibalization checks compare only pairs that could match, so once a 10,000-page
  folder is crawled (7 to 10 minutes) the audit takes about 10 seconds.
- **The unused-asset check recommends deletion**, so it is deliberately conservative: a
  filename appearing anywhere in any page's source counts as used. Verify before
  deleting anyway — files can be referenced from a CMS or hot-linked externally.
- **`SITEMEND_HTTP_HEADERS` is not sent by `--render`.** Chrome makes its own requests,
  so a protected preview can be crawled but not rendered. The Action never renders.

## Tests

```bash
python tests/test_skill.py
```

189 tests (two are opt-in: real Lighthouse and real Chrome rendering), no fixture files — each builds a throwaway site in a temp directory. They pin
down calibration decisions made after real false positives: build-directory exclusion,
deliberate `noindex`, finding grouping, title thresholds, heading order, schema claim
corroboration, gate boundaries. A failure means *"was this behaviour meant to change"*,
not *"make it pass"*.

## Contributing

Bug reports and pull requests welcome, particularly on non-Windows platforms — the
Lighthouse integration is the least-tested part of this project.

If you change a rule, three things move together: the counts and defect table in
[`examples/demo-site/EXPECTED.md`](examples/demo-site/EXPECTED.md), and the `required`
finding ids in the CI workflow. [`RELEASING.md`](RELEASING.md) has the full checklist.

## License

MIT. See [LICENSE](LICENSE).
