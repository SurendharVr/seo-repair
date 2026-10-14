# Fix recipes

One recipe per finding `recipe` key. Each says what to change, what to leave alone, and
what needs approval. **Nothing here may be applied without the user approving the batch.**

Risk legend: **safe** = mechanical, reversible · **care** = judgement needed ·
**gated** = requires explicit approval and a rollback path, every time.

---

## Stage 1 — Index

### `robots-create` / `robots-add-sitemap` — safe
Create `/robots.txt`. Minimum viable:
```
User-agent: *
Allow: /

Sitemap: https://<origin>/sitemap.xml
```
Only add `Disallow` for paths the user confirms should be private. Never disallow CSS
or JS — Google needs them to render the page.

### `sitemap-create` / `sitemap-add` / `sitemap-prune` — safe
List every indexable page, absolute URLs on the production origin, one `<loc>` each.
Rules: no `noindex` URL in the sitemap; no URL that 404s or redirects; `lastmod` only
when you know the real date — **do not invent one or set them all to today**.

### `indexability-review` — gated
Removing `noindex` can put a page into the index that was deliberately kept out. Always
ask why it was excluded before changing it. For `S1-INDEX-CONTRADICTION` (in sitemap
**and** blocked), present both options — remove from sitemap, or unblock — and let the
user pick. Do not guess the intent.

### `canonical-add` / `canonical-fix` — care
Self-referential absolute canonical in `<head>`:
```html
<link rel="canonical" href="https://<origin>/path/">
```
A cross-canonical (`S1-CANONICAL-CROSS`) tells Google to drop this page in favour of
another. That is sometimes correct — a deliberate duplicate — and sometimes an
accident. Ask which before "fixing" it.

### `domain-fix` — care
Replace the placeholder origin with the real production origin everywhere the finding's
evidence lists it: canonicals, `og:url`, every `<loc>` in `sitemap.xml`, and the
`Sitemap:` line in `robots.txt`. Do it in one batch, so the site never mixes two origins.
Ask for the real origin; never guess it.

### `broken-link-fix` — safe
Repoint to the correct URL, or remove the link. Check `detail.refs` for every source
page; a broken link in a shared header or footer appears once as a finding but is one
edit in one shared file.

### `redirect-fix` — care
Point every internal link, canonical and sitemap entry straight at the final URL, then
collapse the chain so the first hop redirects directly to the last (one 301, not two).
Never delete the old hops: they carry the signals earned by the old URLs. For a loop,
find the two rules that undo each other (often a trailing-slash rule against a
www/non-www rule) and fix the host config, not the pages.

### `mixed-content-fix` — safe
Change `http://` to `https://` on the evidence URLs. If the host does not serve https,
self-host the file instead; a scheme-relative `//` URL is not a fix.

### `security-headers-add` — care
At the host, not in the page: `Strict-Transport-Security: max-age=31536000` and
`X-Content-Type-Options: nosniff` (`_headers` on Netlify, `vercel.json`, `.htaccess`).
Start HSTS with a short `max-age` and without `includeSubDomains`: it is hard to undo.

### `lastmod-fix` — safe
Use W3C dates (`2026-05-01` or `2026-05-01T10:00:00Z`). Set `lastmod` only to the date
the page's content really changed. If you cannot know that, omit the field: a uniform or
invented date teaches crawlers to ignore it. **Do not set them all to today.**

### `hreflang-fix` — care
Every language version lists every version, itself included, using the same set on each
page, with absolute URLs:
```html
<link rel="alternate" hreflang="en" href="https://<origin>/en/">
<link rel="alternate" hreflang="fr" href="https://<origin>/fr/">
<link rel="alternate" hreflang="x-default" href="https://<origin>/en/">
```
Codes are `language[-Script][-REGION]` with a hyphen (`en-GB`, `zh-Hant-TW`). Fix a
missing return tag on the page that is missing it, not by deleting the other side.

### `js-render-fix` — care
Put the content, `<title>`, canonical and robots directive in the server's HTML
(server-side rendering or static generation) so every crawler sees the same page.
Never change `noindex` or the canonical in JavaScript. If a rewrite is out of scope,
say so in the report: a page that depends on JS is slower to index, not unindexable.

### `url-rename` / `url-normalize` — gated
**The highest-risk fix in this skill.** A renamed URL loses everything it had earned
unless a 301 redirect ships in the same batch. Required in the batch:
1. the new URL,
2. a 301 from old → new (`_redirects`, `netlify.toml`, `.htaccess`, or host config),
3. every internal link updated,
4. sitemap updated,
5. canonical updated.
If you cannot deliver all five, do not propose the rename. A slightly imperfect URL
that ranks beats a perfect URL that doesn't.

---

## Stage 2 — Architecture

### `internal-link-add` — safe
Add contextual links from relevant existing pages, using descriptive anchor text.
Prefer body copy over another nav entry — a link everyone ignores in a mega-menu passes
less signal than one inside relevant prose. For `S2-ORPHAN`, at least one link from a
topically related page. Never build a link block that exists only for crawlers.

### `heading-fix` — safe
Exactly one `<h1>` per page, matching the page's actual subject. No skipped levels.
Never change a heading's *text* to fit a keyword under this recipe — that is a stage-3
content decision and belongs in a content batch.

### `breadcrumb-add` — safe
Visible breadcrumb trail plus matching `BreadcrumbList` JSON-LD. The markup must mirror
what is actually rendered; breadcrumb markup without a visible breadcrumb is exactly
the kind of unsupported structured data stage 5 flags.

---

## Stage 3 — Content & on-page

### `title-write` — care
50–60 characters is the *target*, not a cliff; the real constraint is pixel width, and
the skill only flags past 65. Primary keyword first where it reads naturally, brand
last. Every title unique. Never keyword-stuff to hit a length.

### `description-write` — care
Roughly 140–160 characters. Not a ranking factor — it is ad copy for the SERP, so write
for the click. Unique per page. Must describe what is actually on the page.

### `content-expand` — care
`S3-THIN` is a signal, not a verdict. Before acting:
1. Check the rendered DOM — a JS-driven page under-counts (see `pipeline.md`).
2. Ask whether the page *should* be long. A contact page at 150 words is fine.
Expand only with substance the user supplies or approves. **Never pad with invented
facts, statistics, or testimonials.**

### `consolidate-pages` — gated
For `S3-DUPLICATE-EXACT` / `S3-NEAR-DUPLICATE`. Never delete unilaterally. Present:
- which page should win (more inlinks, better title, already in sitemap),
- what content moves across,
- the 301 from loser → winner,
- every internal link to update.
Then wait. A merged page cannot be un-merged once the content is gone.

### `keyword-map` — care
For `S3-CANNIBALIZATION`. Two pages competing for one intent: either differentiate them
onto genuinely different intents, or merge them (→ `consolidate-pages`). Decide with
SERP evidence (`serp-research.md`) or Search Console data, never by guessing which
"feels" more important.

### `alt-write` — safe
Describe the image's content and function. Decorative images take `alt=""` — an empty
alt is correct and is not the same as a missing one. Never stuff keywords.

### `img-dimensions` — safe
Add explicit `width`/`height` so the browser reserves space. Directly reduces CLS.

### `asset-prune` — gated
For `S3-ASSET-UNUSED`. Media files nothing references. The check is deliberately
conservative — a filename appearing anywhere in any page's source counts as used — so
what it reports is genuinely unreferenced. Still **verify before deleting**: files can
be referenced from a CMS, an email template, or an external site hot-linking them.
Remove them from the deploy package too, not just the source tree; an unused asset that
still ships is still dead weight.

### `img-optimize` — safe
Convert to WebP/AVIF, resize to the largest rendered size, add `loading="lazy"` to
below-fold images only. **Never lazy-load the LCP image** — it makes LCP worse.
Applies to `<picture>`/`<source srcset>` candidates too: those are what the browser
actually downloads, so a heavy `.webp` behind a `<picture>` costs exactly as much as a
heavy `<img src>`.

### `og-add` — safe
```html
<meta property="og:title" content="<page title>">
<meta property="og:description" content="<one-sentence summary>">
<meta property="og:image" content="https://<origin>/path/to/image.jpg">
```
Use an image that really represents the page (about 1200x630), reachable by absolute URL.
Reuse the page's own title and description; write nothing the page does not say.

### `lang-add` / `viewport-add` — safe
```html
<html lang="en">
<meta name="viewport" content="width=device-width, initial-scale=1">
```
A missing viewport is critical: without it the page is not mobile-friendly at all.

---

## Stage 4 — Performance

### `cwv-lcp` — care
Find the LCP element in the Lighthouse report first, then act on *that* element:
preload the hero image, `fetchpriority="high"`, remove render-blocking CSS/JS above the
fold, self-host fonts with `font-display: swap`.

### `cwv-cls` — safe
Dimensions on images and embeds; reserve space for anything injected late; avoid
inserting content above existing content; `size-adjust` on font fallbacks.

### `cwv-tbt` — care
Split or defer long tasks, `defer`/`async` on non-critical scripts, drop unused
dependencies. On a static site, a high TBT usually means a large synchronous inline
script — measure before rewriting.

---

## Stage 5 — Schema

### `schema-add` — care
Pick the type that matches what the page *is*: `LocalBusiness`/`FoodEstablishment` for
a business, `Product` for a product, `Article` for an article, `FAQPage` only when the
Q&A is genuinely visible on the page. One primary type per page.

### `schema-fix` — safe
Add missing required properties, fix JSON syntax. Only assert values that already
exist on the page or that the user supplies.

### `schema-claim-audit` — gated
For `S5-SCHEMA-UNSUPPORTED-CLAIM`. The markup asserts something the page does not show.
Two legitimate fixes, and **one that is never acceptable**:
- Make the claim visible on the page (render the reviews, show the address), or
- Remove the claim from the markup.
- **Never** keep invisible markup because "it works". Review and rating markup that
  does not correspond to visible on-page reviews violates Google's structured data
  policy and risks a manual action.
For `S5-SCHEMA-CLAIM-OFFPAGE` (low): a shared `LocalBusiness` block repeating the
address site-wide is normal. Confirm it is intended, then move on.
