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
