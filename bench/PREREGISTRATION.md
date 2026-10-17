# Pre-registration: replay benchmark

Written before any replay data exists. Every value below is fixed before the harness
(Task 5) runs on real repositories, and the decision rules in section 5 are applied to the
results **exactly as written**. Changing any value after data exists invalidates the run.

Fields marked `____` are for B to fill in. Where the plan proposed a value, it is shown
next to the field for reference only; the accepted value is the one that counts.

**Status:** signed by SurendharVr on 2026-10-17. The values in sections 2 to 4 were
suggested by Claude on 2026-10-17 and accepted as written. The replay must not run on
real data until this file is merged.

## 1. Frozen version

- Package: `sitemend==1.6.0`
- Tag: `v1.6.0`
- Commit: `badbb38e8c8235f35e7b8ffcdc12f9785263cb13`

No rule, threshold or severity changes until the report (Task 8) is published. Rule bugs
found during the run go into `bench/ledger.csv` and an issue, and are not fixed.

## 2. Repository list

Criteria, all required:

- Built with a supported stack: Next.js static export, Astro/Starlight, Docusaurus,
  VitePress, or Hugo.
- Builds with one command on a clean Ubuntu runner.
- At least 75 commits to the default branch in the 12 months before the cutoff date.
- Not used to tune rules. That excludes the 20 sites in `docs/benchmark-2026-10.md`,
  which names none of them, so B holds that list, plus the three sites the 1.5.2
  crawler fixes came from: biomejs.dev, pnpm.io and turborepo.dev.

Target size: 10 to 15 repositories.

| # | Repository (owner/name) | Stack | Build command | Site dir | Node version |
|---|---|---|---|---|---|
| 1 | electron/website | Docusaurus | `yarn install --immutable && yarn build` | `build` | 24 |
| 2 | ionic-team/ionic-docs | Docusaurus | `npm ci && npm run build:production` | `build` | 24 |
| 3 | trpc/trpc | Docusaurus | `pnpm install --frozen-lockfile && pnpm --dir www build` | `www/build` | 24 |
| 4 | vuejs/docs | VitePress | `pnpm install --frozen-lockfile && pnpm run build` | `.vitepress/dist` | 24 |
| 5 | unocss/unocss | VitePress | `pnpm install --frozen-lockfile && pnpm --dir docs build` | `docs/dist` | 24 |
| 6 | vitest-dev/vitest | VitePress | `pnpm install --frozen-lockfile && pnpm ci:docs` | `docs/.vitepress/dist` | 24 |
| 7 | cloudflare/cloudflare-docs | Astro/Starlight | `pnpm install --frozen-lockfile && pnpm run build` | `dist` | 24 |
| 8 | drizzle-team/drizzle-orm-docs | Astro | `pnpm install --frozen-lockfile && pnpm run build` | `dist` | 24 |
| 9 | webpro-nl/knip | Astro/Starlight | `pnpm install --frozen-lockfile && pnpm --dir packages/docs build` | `packages/docs/dist` | 24 |
| 10 | open-telemetry/opentelemetry.io | Hugo | `npm ci && npm run build:production` | `public` | 24 |
| 11 | nodejs/nodejs.org | Next.js static export | `pnpm install --frozen-lockfile && cd apps/site && pnpm run deploy` | `apps/site/build` | 24 |

How these were chosen, and what is not yet verified:

- Each repository's stack was confirmed from its config file (`docusaurus.config.*`,
  `.vitepress/config.*`, `astro.config.*`, `hugo.yaml`, `next.config.*`). nodejs.org
  builds a static export through its `deploy` script (`NEXT_PUBLIC_STATIC_EXPORT=true`,
  output in `build`).
- Commits on the default branch from 2025-10-01 to the cutoff, from the GitHub API on
  2026-10-17: electron/website 292, ionic-docs 432, trpc 258, vuejs/docs 90, unocss 278,
  vitest 1230, cloudflare-docs 5424 (branch `production`), drizzle-orm-docs 128, knip 1524,
  opentelemetry.io 3056, nodejs.org 657. All are at least 75.
- Build commands come from each repository's `package.json` scripts, lockfile and
  `netlify.toml`. They have **not been run**. The pilot (Task 5) is the check that each one
  builds with one command on a clean Ubuntu runner, and a repository that fails it is
  replaced before the full run. Node 24 matches each repository's `.nvmrc`, `engines` or
  Netlify setting. Package-manager versions come from `packageManager` through corepack.
- Considered and dropped: kubernetes/website (needs `make`, Hugo extended and git
  submodules, not one command), react-hook-form/documentation (Next.js without static
  export), shuding/nextra (24 commits), and three repos where no supported site config
  was found (oxc-project.github.io, typescript-eslint, pinia).
- trpc, unocss, vitest and knip are code monorepos with a docs site, so many of their
  commits never touch the site. That is realistic for a per-PR gate, but it lowers the
  per-commit frequency compared with docs-only repositories.
- cloudflare-docs is several thousand pages. It is the most likely to make the full run
  expensive; the pilot's compute projection decides whether it stays.

Excluded tuning sites checked against this list by: Claude, 2026-10-17, against the 20
site folders of the October 2026 benchmark run (kept outside the repo) plus the three
above. None of the 11 sites (electronjs.org, ionicframework.com, trpc.io, vuejs.org,
unocss.dev, vitest.dev, developers.cloudflare.com, orm.drizzle.team, knip.dev,
opentelemetry.io, nodejs.org) is on it. Confirmed by B: SurendharVr

## 3. Commit range

The last N first-parent commits on each repository's default branch before the cutoff.

- N (commits per repository): 50, so 11 x 50 = 550 comparisons from about 561 builds.
  At the frequency threshold below, 550 commits should contain about 6 qualifying ones.
- Cutoff date (UTC): 2026-10-01T00:00:00Z, before this plan existed, so no repository
  could have changed in response to it.

A commit whose build fails is recorded as `build_failed`, and both comparisons that touch
it are skipped. Skipped comparisons count toward neither frequency nor precision.

## 4. Pass/fail thresholds

| Measure | Unit | Proposed in the plan | Accepted value |
|---|---|---|---|
| Blocking precision | per blocking finding: `true_regression` / all blocking findings with a verdict | at least 95% (at most 1 false block in 20) | at least 95%, with at least 20 blocking findings with a verdict; below 20, N is extended for the same repositories 50 commits at a time (same cutoff, older commits) until 20 are reached or N is 200, and if 20 are never reached precision counts as not met |
| Frequency | per commit: commits introducing at least one verified critical or high regression / commits compared | at least 1 per 100 commits | at least 1 per 100 commits compared (6 of 550 if every build succeeds) |
| Seeded recall (Task 7) | per mutation, critical and high mutation types | at least 90% | at least 90%, pooled across the critical and high mutation types on 5 repositories; each type is also reported on its own (with 5 runs per type, one type cannot support a 90% line alone) |

Findings with the verdict `unclear` are counted as: `false_positive`. Precision is the
product, so a finding nobody can confirm does not count in its favour.

## 5. Decision rules

Applied to the full run's results as written, with the outcome at the top of the report.

- All three thresholds met: continue the release-gate positioning (Phase 4).
- Precision fails: stop all outreach and fix rules under the stability policy in
  `RELEASING.md`. Every confirmed false positive becomes a permanent test.
- Precision passes but frequency fails: re-test as a pre-launch and redesign audit, not a
  per-PR gate.
- Recall fails: do not market it as a gate until the missed mutation types are fixed.

## 6. Known limitations

- Replay builds each commit locally. It cannot reproduce environment variables, hosting
  configuration, or runtime behaviour.
- So preview-host leaks and soft 404s will be **under-counted**. A low replay count for
  those two types is not evidence that they are rare. The prospective run (Task 9) is the
  evidence for them.
- Seeded mutations (Task 7) change the built output, not the source, so they do not test
  whether a framework would produce that output.

## Sign-off

- Filled in and approved by (B): SurendharVr
- Date: 2026-10-17
- Merged before any replay run on real data (commit SHA of the merge):
  `5899d56a42f71ffdf8fad901913f2e5e2c8b6b50` (#48)

## Addendum 1: tool failures (2026-10-17, before the full run)

The Ubuntu pilot (GitHub Actions run 39007242130; data in `bench/replay/pilot/`) built all
21 replayed commits of electron/website, but sitemend 1.6.0 crashed auditing every one of
them (issue #51: `resolve_href` raises "Invalid IPv6 URL" on a URL with `[` in the host,
found in the site's CSS/JS bundles). vuejs/docs built and audited all 21 commits.

Decided by B before any full-run data exists:

- sitemend 1.6.0 stays frozen. The crash is not fixed until the Task 8 report is published.
- electron/website stays in the sample.
- A commit whose audit fails is recorded as `audit_failed`, and both comparisons that touch
  it as `skipped_audit_failed`. Like failed builds, they count toward neither frequency
  nor precision.
- The report counts them as **tool failures**: per repository, the number of commits
  sitemend could not audit and why, stated next to the results as a reliability finding,
  not left out.

Decided by (B): SurendharVr. Date: 2026-10-17.

## Addendum 2: build-command fixes (2026-10-17, before the full run)

The first seeded run (GitHub Actions run 39019105380) found that three of the pre-registered
build commands fail on the Ubuntu runner at the cutoff commit: ionic-team/ionic-docs (a
generated sidebar category was empty because the command skipped the project's markdown
generation), trpc/trpc (Node ran out of heap) and unocss/unocss (the docs import a workspace
package that has to be built first). Section 2 said a repository that fails its build is
replaced. Instead, decided by B before any full-run data exists:

- The repository list, N, the cutoff and the thresholds do not change.
- Only `build_command` (and `site_dir` if needed) may change, to follow the project's own
  build at the cutoff commit: its `package.json` scripts, `netlify.toml` and task runner.
- A build may not need a secret. Content a project fetches live at build time is replaced
  by its committed data or by a fixed stub, the same for every commit, so a difference
  between base and head comes from the commit.
- Each fix is verified by building the cutoff commit on the Ubuntu runner before the full
  run, and is recorded below with its source. Nothing changes once full-run data exists;
  a repository that still cannot be built is replaced as section 2 says.

| Repository | Was | Now | Source |
|---|---|---|---|
| ionic-team/ionic-docs | `npm ci && npm run build:production` | `npm ci && node scripts/cli.mjs && echo [] > src/components/page/reference/ReleaseNotes/release-notes.json && npm run build:preview` | `prebuild` runs `generate-markdown`. Of its three scripts, `cli.mjs` uses committed data; `native.mjs` fetches plugin READMEs at `@latest` and `release-notes.mjs` needs a `GITHUB_TOKEN` and live releases, so they are not run, and the gitignored `release-notes.json` is an empty list for every commit. `build:preview` is what `npm run build` runs without `VERCEL_ENV` (English only). |
| trpc/trpc | `pnpm install --frozen-lockfile && pnpm --dir www build` | `export NODE_OPTIONS=--max-old-space-size=8192 && pnpm install --frozen-lockfile && pnpm build-www` | The root `build-www` script (`turbo --filter www build`) builds the workspace packages the site depends on; the default heap ran out at about 4 GB. |
| unocss/unocss | `pnpm install --frozen-lockfile && pnpm --dir docs build` | `export NODE_OPTIONS=--max_old_space_size=4096 && pnpm install --frozen-lockfile && pnpm run build && pnpm --dir docs build` | `netlify.toml` runs `pnpm run deploy` (`nr build && npm -C docs run build && …`) with that `NODE_OPTIONS`; the playground and interactive builds are not part of the docs site. |

Verified on Ubuntu (GitHub Actions run 39020679625, branch `fix/build-commands`):
unocss/unocss built and was audited with the new command. ionic-team/ionic-docs still
failed: its next empty sidebar category ("Plugins") comes from `native.mjs`, which fetches
plugin READMEs at `@latest`, live content this addendum rules out. trpc/trpc built its
packages but failed type-checking a docs sample, although trpc's own Vercel deployment of
the same commit succeeded. Both are replaced, as section 2 says; see Addendum 3.

Decided by (B): SurendharVr. Date: 2026-10-17.

## Addendum 3: re-freeze on 1.7.0 and two replacements (2026-10-17, before the full run)

**Why.** Two problems found before any full-run data, both on 1.6.0:

- **#51:** sitemend 1.6.0 crashed auditing electron/website (Addendum 1), and then three
  of the replacement candidates below: typescript-eslint, react-navigation and
  pulsar-site. Tool failures would have been a large part of the result.
- **Rating tokens:** the seeded recall run found that `S5-SCHEMA-UNSUPPORTED-CLAIM`
  corroborated a rating of "4.7" from the digits "4" and "7", which every site contains.

B lifted the 1.6.0 freeze to fix both (#57), released as 1.7.0.

**1. Frozen version (supersedes section 1).**

- Package: `sitemend==1.7.0`
- Tag: `v1.7.0`
- Commit: `a64e05de358deece65a8065a65c5c65a34450490`

`FROZEN` in `bench/replay/replay.py` and both benchmark workflows pin it. No rule,
threshold or severity changes until the Task 8 report is published; a bug found from here
on is logged and gets an issue.

**2. Data from 1.6.0 is superseded.** The pilot (`bench/replay/pilot/`) and both seeded runs
used 1.6.0. They stay in the repository as the record of how these problems were found, but
the measurement uses only data produced with 1.7.0. The pilot and the seeded test are re-run
on 1.7.0 before the full run.

**3. The rating mutation is reported apart.** The rating rule was fixed after the seeded
test showed this mutation being missed, so its result on 1.7.0 is not independent evidence.
`rating-unsupported` is still run and reported, but it is left out of the pooled
critical-and-high recall that section 4's threshold applies to.

**4. Replacements for ionic-team/ionic-docs and trpc/trpc** (Addendum 2).

The pick rule was fixed before any candidate was built:

1. **Same stack** as the repositories replaced: both were Docusaurus.
2. **Section 2's criteria:** at least 75 default-branch commits in the 12 months before
   the cutoff, and not a tuning site or a repository already in the list.
3. **Addendum 2's rules:** the project's own build, run as-is, with no secret and no live
   content (no stubs).
4. **Order:** most commits in the window first. The first two that build on the Ubuntu
   runner at the cutoff commit are taken.

The candidate pool is nine Docusaurus repositories Claude identified on 2026-10-17. Builds
ran in GitHub Actions run 39021656493 (branch `tmp/replacement-candidates`).

| # | Candidate | Commits in window | Outcome |
|---|---|---|---|
| 1 | datahub-project/datahub | 3458 | Excluded before building: its setup script (`vercel-setup.sh`) runs only on Amazon Linux (`yum`) |
| 2 | facebook/flow | 2244 | Build failed: the site needs the `flow` binary (`spawn flow ENOENT`) |
| 3 | temporalio/documentation | 1247 | Excluded before building: `prebuild` clones a live repository |
| 4 | **facebook/relay** | 1014 | **Built. Taken** |
| 5 | **typescript-eslint/typescript-eslint** | 681 | **Built. Taken** (its 1.6.0 audit crashed on #51) |
| 6 | webdriverio/webdriverio | 550 | Excluded before building: its docs generator downloads live content |
| 7 | apache/pulsar-site | 417 | Built; not needed (1.6.0 audit crashed on #51) |
| 8 | react-navigation/react-navigation.github.io | 390 | Built; not needed (1.6.0 audit crashed on #51) |
| 9 | ionic-team/capacitor-docs | 82 | Built; not needed |

In `bench/replay/repos.json`, relay takes ionic-docs' place (3rd) and typescript-eslint
takes trpc's (4th), so the seeded test's first five are electron/website, vuejs/docs,
facebook/relay, typescript-eslint and unocss/unocss.

| Repository | Build command | Site dir |
|---|---|---|
| facebook/relay | `yarn --cwd website install --frozen-lockfile && yarn --cwd website build` | `website/build` |
| typescript-eslint/typescript-eslint | `pnpm install --frozen-lockfile && pnpm exec nx build website` (its `netlify.toml`) | `packages/website/build` |

**5. Unchanged:** N (50), the cutoff (2026-10-01), the thresholds, the decision rules,
Addendum 1 (tool failures) and Addendum 2's rules, with unocss/unocss's fixed command.

Approved by (B): SurendharVr. Date: 2026-10-17.

## Addendum 5: a reproducible sample (2026-10-17, before any data from it)

**Why.** The repositories in section 2 and Addendum 3 were chosen by Claude from memory:
well-known open-source docs sites, checked against the criteria but not drawn by any rule.
That makes the sample unrepeatable and tilted toward mature, heavily maintained projects,
and seven of its eleven build commands were never built before the full run (two then
failed on every commit). The full run (GitHub Actions run 39032774435) was cancelled before
it finished, and none of its data is used. This addendum replaces section 2's repository
list, Addendum 3's replacements and the N of section 3 with a procedure anyone can rerun.

**Unchanged:** the frozen version (sitemend 1.7.0, Addendum 3), the cutoff
(2026-10-01T00:00:00Z), the thresholds and decision rules (sections 4 and 5), tool
failures (Addendum 1), no secrets and no live content at build time (Addendum 2), and the
rating mutation reported apart (Addendum 3).

**1. Size.** 3 repositories for each of the 5 supported stacks, 15 in all, and the last
**N = 40** first-parent commits of each before the cutoff: 600 comparisons from about 615
builds. At the frequency threshold that is about 6 qualifying commits. The seeded recall
test uses all 15.

**2. Frame.** For each stack, one GitHub code search, run once and saved with its date
to `bench/sample/frame-<stack>.json` (every result, before any filtering), after this
addendum is signed and merged:

| Stack | Query |
|---|---|
| Docusaurus | `filename:docusaurus.config` |
| Astro/Starlight | `filename:astro.config` |
| VitePress | `filename:config path:.vitepress` |
| Hugo | `filename:hugo.toml` and `filename:hugo.yaml` (combined) |
| Next.js static export | `output export filename:next.config` |

Results are reduced to unique repositories, keeping each repository's shallowest
matching file (ties: alphabetical) as its site config. GitHub returns at most 1,000
results per query; that cap is a limitation of the frame, stated in the report.

**3. Draw.** Each stack's repositories are sorted by `owner/name`, then shuffled with
Python's `random.Random(20261017).shuffle`, one fresh generator per stack in the order
of the table above. The shuffled order is saved to `bench/sample/order-<stack>.json`.

**4. Eligibility,** checked in draw order. Every repository looked at is recorded in
`bench/sample/draw.csv` with its outcome and reason. A repository is eligible when all hold:

- public, not a fork, not archived;
- at least 75 commits on the default branch in the 12 months before the cutoff;
- its site config is present at the last commit before the cutoff (for Next.js, that file
  sets `output: 'export'`; for Hugo, the site folder has a `package.json` with a `build`
  script, since the runner has no Hugo binary);
- not excluded: the source repositories of the 23 tuning sites (the 20 in
  `docs/benchmark-2026-10.md` and the three in the 1.5.2 CHANGELOG), any repository whose
  GitHub homepage is on one of those hosts, and every repository this plan has used so far,
  since the 1.7.0 fixes were found on some of them (listed below).

**5. Build command, by rule.** Site folder `D` = the folder holding the site config.

- **Package manager:** the root `packageManager` field. Otherwise the root lockfile:
  `pnpm-lock.yaml` → pnpm, pinned by `lockfileVersion` (9.x → pnpm 9, 6.x → pnpm 8,
  5.x → pnpm 7); `yarn.lock` → yarn (Berry if `.yarnrc.yml` exists, else classic 1);
  `package-lock.json` → npm. No lockfile → ineligible.
- **Install,** at the repository root, frozen: `pnpm install --frozen-lockfile`,
  `yarn install --immutable` or `--frozen-lockfile`, or `npm ci`.
- **Build:** `D`'s own `build` script. If that build fails, one fallback: the root `build`
  script, then `D`'s `build` script.
- **Output:** the stack's default under `D`: `build/` (Docusaurus), `dist/` (Astro),
  `.vitepress/dist/` (VitePress), `public/` (Hugo), `out/` (Next.js).
- **Node:** `.nvmrc` or `.node-version` major, else the `engines.node` minimum major,
  else 24.

**6. Build check.** In draw order, each eligible repository is built once at its cutoff
commit on the Ubuntu runner with the command from rule 5. The first 3 per stack that build
are the sample. Candidates can be checked in parallel, but they are taken strictly in draw
order. A repository whose build fails, or whose output folder is missing, is recorded and
skipped. No command is edited by hand. The resulting `bench/replay/repos.json` and
`draw.csv` are committed before the full run.

**Excluded, beyond the tuning sites** (used in this plan before this addendum):
electron/website, vuejs/docs, ionic-team/ionic-docs, trpc/trpc, unocss/unocss,
vitest-dev/vitest, cloudflare/cloudflare-docs, drizzle-team/drizzle-orm-docs,
webpro-nl/knip, open-telemetry/opentelemetry.io, nodejs/nodejs.org, facebook/relay,
typescript-eslint/typescript-eslint, facebook/flow, apache/pulsar-site,
react-navigation/react-navigation.github.io, ionic-team/capacitor-docs,
datahub-project/datahub, temporalio/documentation, webdriverio/webdriverio.

**Source repositories of the tuning sites:** withastro/astro.build, biomejs/website,
withastro/docs, facebook/docusaurus, gohugoio/hugoDocs, jestjs/jest, vercel/next.js,
microsoft/playwright, pnpm/pnpm.io, prettier/prettier, reactjs/react.dev,
facebook/react-native-website, reduxjs/redux, withastro/starlight, sveltejs/svelte.dev,
tailwindlabs/tailwindcss.com, tauri-apps/tauri-docs, vercel/turborepo, shadcn-ui/ui,
vitejs/vite, plus any repository whose GitHub homepage host is one of the 23 hosts.

**Limitations to state in the report:** the frame is what GitHub code search returns
(capped, ranked by GitHub), so it is a reproducible sample of public repositories that
search can find, not of all sites; it is mostly docs and project sites, because those are
what is public; and the build rule is mechanical, so a project whose real deploy needs
more than its own `build` script is skipped rather than fixed.

Approved by (B): ____ Date: ____
