# seo-repair

[![tests](https://github.com/SurendharVr/seo-repair/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/SurendharVr/seo-repair/actions/workflows/tests.yml)
[![license](https://img.shields.io/github/license/SurendharVr/seo-repair)](LICENSE)

**Technical SEO audit and repair, as a [Claude Code](https://claude.com/claude-code)
skill.**

A site crawler and audit tool that finds indexing problems, orphan pages, duplicate
content, broken internal links, thin content, missing canonical tags, keyword
cannibalization and invalid schema markup — then proposes the fixes, applies the ones
you approve, and re-verifies with a second crawl.

What makes it different from a checklist: **the order is enforced.** SEO findings are
not independent, so this treats them as a repair system with a dependency order.

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
22 findings: 1 critical, 6 high, 7 medium, 8 low
  stage 1: 4
  stage 2: 6
  stage 3: 10
  stage 5: 2
  gate A (Index before content): CLEAR -- every crawled page is indexable
  gate B (Architecture before schema): BLOCKED -- 3 page(s) orphaned or pending consolidation: /, /old-home.html, /reviews.html
  gate C (Everything before authority): BLOCKED -- 7 critical/high finding(s) open in stages 1-5: S1-CANONICAL-CROSS, S1-SITEMAP-ORPHAN, S2-ORPHAN, S2-UNREACHABLE
```

Each run also produces a self-contained HTML dashboard:

![The seo-repair dashboard: severity totals, the seven-stage repair pipeline with gates drawn between the stages, and the three gate cards showing why each is clear or blocked](docs/dashboard.png)

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

---

## Install

**As a plugin:**

```
/plugin marketplace add SurendharVr/seo-repair
```

**Or clone straight into your skills directory:**

```bash
git clone https://github.com/SurendharVr/seo-repair ~/.claude/skills/seo-repair
pip install -r ~/.claude/skills/seo-repair/requirements.txt
```

Requires Python 3.8+ with `beautifulsoup4` and `lxml` (`requests` only for http-mode
crawling). Core Web Vitals additionally need Node and a local Chrome, via
`npx lighthouse`.

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

## Usage

Four verbs, driven through Claude Code:

| Verb | Does | Writes |
| --- | --- | --- |
| `seo-repair audit <path>` | Crawl → audit → report. Establishes the baseline. | report only |
| `seo-repair plan` | Ranks findings into stage-ordered batches, previews each diff | nothing |
| `seo-repair apply <batch>` | Applies **one approved batch**, re-checks that stage | source files |
| `seo-repair verify` | Re-crawl, diff vs. baseline, regression gate | report only |

Nothing is edited without approval. `plan` refuses to run without a baseline; `apply`
refuses without a named, approved batch.

## What it finds

| Stage | Checks |
| --- | --- |
| **Crawling & indexing** | `robots.txt` rules, XML sitemap coverage and contradictions, `noindex` directives, canonical tags (missing, cross-page, broken, off-origin), broken internal links, URL hygiene |
| **Site architecture** | Orphan pages, unreachable pages, click depth, internal link distribution, heading structure, breadcrumbs |
| **Content & on-page** | Duplicate and near-duplicate content (MinHash), keyword cannibalization, thin content, title tags, meta descriptions, image alt text, oversized images, unused assets |
| **Core Web Vitals** | Lighthouse LCP, CLS and TBT — reported as *lab* metrics, with the localhost caveat stated |
| **Schema markup** | JSON-LD validity, required and recommended properties, and whether the markup's claims are corroborated by visible page text |
| **Authority / off-page** | Advisory only. It cannot measure a backlink profile and does not pretend to — it produces a human action list, and refuses paid-link and PBN tactics outright |

Findings cite the offending string from your source, so every one is checkable.

## Limitations, stated plainly

- **Claude Code only** for the JS-rendering and mobile-overflow checks, which use the
  built-in browser tools. The Python scripts run standalone anywhere.
- **Core Web Vitals need local Chrome.** These are lab numbers from your machine, not
  field data. Real CWV comes from CrUX via Search Console, which is a handoff item.
- **`vitals.py` is verified on Windows only.** CI runs the test suite on Linux, macOS
  and Windows, but excludes Lighthouse because it needs a browser — so that one script's
  cross-platform behaviour is untested rather than proven. Reports welcome.
- **Stage 6 does no off-page work.** By design.
- **The unused-asset check recommends deletion**, so it is deliberately conservative: a
  filename appearing anywhere in any page's source counts as used. Verify before
  deleting anyway — files can be referenced from a CMS or hot-linked externally.

## Tests

```bash
python tests/test_skill.py
```

45 tests, no fixture files — each builds a throwaway site in a temp directory. They pin
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
