# Capability tiers — the honesty contract

Every claim this skill makes carries one of three tiers. The tier is not decoration;
it decides whether a number may be written down at all.

## MEASURED

Observed directly by a script in this run. Safe to state as fact, with the evidence.

- Crawl facts: page count, titles, meta descriptions, canonicals, headings, link graph,
  image attributes, robots.txt and sitemap.xml contents.
- Derived graph facts: orphans, click depth, inbound link counts, broken internal links,
  sitemap↔crawl gaps.
- Text facts: word counts, exact and near-duplicate similarity, shared head terms.
- Schema facts: JSON-LD parse status, present/absent properties, whether a claim's
  terms appear in the page's visible text.
- Lighthouse lab metrics — but see the caveat below, which is mandatory.

## INFERRED

Reasoned from observation. Must be stated as an inference, with the reasoning visible.

- **Search intent** read off SERP composition. Say what the SERP looked like.
- **Cannibalization**, which is a judgement about two pages competing for one intent.
  The term overlap is MEASURED; the conclusion that they compete is INFERRED.
- **Thin content.** A low word count is MEASURED; "this page is too thin to rank" is
  INFERRED and depends on the query. A JS-rendered page will under-count — check the
  rendered DOM before asserting it.
- **Any extrapolation of local Lighthouse numbers to production.** Always INFERRED,
  never MEASURED: production has a CDN, real latency, and real devices that a localhost
  run does not.

## HANDOFF

Not observable from a codebase or a local browser. **No number may be written.** Emit a
concrete action naming exactly where the user should look.

| What | Where the user goes |
| --- | --- |
| Whether a URL is actually indexed | Search Console → Indexing → Pages |
| Field Core Web Vitals (real users, CrUX) | Search Console → Experience → Core Web Vitals |
| Queries, impressions, CTR, position | Search Console → Performance |
| Search volume, keyword difficulty | Ahrefs / Semrush / Keyword Planner |
| Backlinks, referring domains, anchors | Ahrefs / Search Console → Links |
| Rich-result eligibility | Google Rich Results Test |
| Manual actions, security issues | Search Console → Security & Manual Actions |

### The line, stated plainly

A local crawl proves a page is **crawlable**. It never proves the page is **indexed**.
Those are different claims and the report must not blur them.

Likewise: Lighthouse gives **lab** metrics. Core Web Vitals as Google uses them are
**field** data from real Chrome users. Reporting a Lighthouse LCP as "the site's LCP"
is wrong, and the report template tags it `MEASURED-lab` for exactly that reason.

## Why this is strict

An SEO report's only value is that its claims can be trusted. One invented search
volume makes every other number in the document suspect, including the ones that were
real. The cost of saying "I can't measure this, here's where to look" is a single line
of text. The cost of a fabricated number is the entire report's credibility.

This applies to generated content too, not just analysis: no invented testimonials,
review counts, client logos, author credentials, or statistics. If the user did not
supply it, it does not go on the page. See the do-no-harm rules in `SKILL.md`.
