# `fixtures/trs/` — what the `version_resolves.py` legs grade, and where each byte came from

Nothing here was produced by a leg: legs read these files and never write them, and no leg fetches. The offline gate
grades captures and shapes, exactly as every other fixture in this directory does.

`dockstore-trs-genotypebatch-2026-10-14.json`
: A **real response**, captured 2026-10-14 by one unauthenticated read-only
  `GET https://dockstore.org/api/ga4gh/trs/v2/tools?organization=broadinstitute&toolname=GenotypeBatch` (HTTP 200).
  The live response held **3 entries and 341 version rows**; this excerpt keeps **1 entry and 2 version rows**, and
  drops the entry-level fields `description`, `aliases`, `checker_url`, `has_checker` (36 KB of upstream prose). The
  two rows kept are verbatim, and that is the point: they carry **no commit field of any spelling**, which is what the
  legs grade the REFUSED arm against. A full listing is a list of other people's branch names and stays out of the
  repo; an excerpt of two names, one of which is the field case, is the cheapest thing that proves the shape is real.

`workflowversions-genotypebatch-shape-holder.json`
: A **shape-holder, not a capture** — labelled in its own name because a fixture that lies about its provenance is
  worse than no fixture. Dockstore's `WorkflowVersion` schema carries `commitID` (read off `dockstore.org`'s own
  `openapi.json` 2026-10-14, where `WorkflowVersion`/`Tag`/`Version` have it and `ToolVersion` does **not**); the
  routes for it were measured 2026-10-14 as closed anonymously (`/api/workflows/<workflowPath>/versions` -> 404, `/api/
  workflows/path/workflow/<repository>?include=versions` -> 401) and this file was therefore built by hand from the
  schema. **Corrected 2026-10-15:** both of those numbers are right, but the route that was never tried —
  `/published?include=versions` — answers **200 unauthenticated with `commitID` on every row**, so a real capture of
  this shape DOES exist, and it sits beside this file as `dockstore-published-genotypebatch-excerpt.json`. What that
  leaves of this file's claim: the field names and spellings were always verified against `openapi.json`, and the
  envelope this file inferred is now **confirmed** by the real excerpt rather than merely plausible. Both `commitID`
  values are **real gatk-sv commits** measured on 2026-10-14: `62d7f80d0b32eca55ca5eaeae770cf56eccbfeca`
  is the commit Dockstore was serving for `mw_genotype_scale` (its committer timestamp equals that row's
  `meta_version` in the capture above, to the second), and `743dd9d41edeee98bdd97cde0638a89a4432cfe7` is
  `refs/heads/main`.

`dockstore-published-genotypebatch-excerpt.json`
: A **real capture**, excerpted — the only committed file here that a network call produced. Fetched 2026-10-15 from
  `.../path/workflow/github.com%2Fbroadinstitute%2Fgatk-sv%2FGenotypeBatch/published?include=versions` with no token:
  HTTP 200, 28 version rows, `commitID` on all 28. Four rows are kept (the three the field case needs plus one
  unrelated real row) and `versions_dropped: 24` records the rest in data, because a full listing is a list of other
  people's branch names. It is what makes `CURRENT`/`STALE` reachable from evidence rather than from a hand-built file:
  `mw_genotype_scale` -> `62d7f80d0b32…` against a tip of `af68ae48…` is issue #3's case, graded from Dockstore's own
  bytes. Its decoys are load-bearing and named in its own `_about`: `workflow_path` is the descriptor FILE
  (`/Dockstore.cwl`) and `path` is the repo, both of which contain a `/` and would satisfy a generic entry-path test —
  reading the entry from either files every version under a `.cwl` name and then answers `ABSENT` for the entry asked
  about.

`tip-ls-remote-stale.txt`, `tip-ls-remote-current.txt`
: The tip seam — the exact output shape of `git ls-remote origin refs/heads/<branch>`, tab-separated, two `refs/heads/`
  lines so a leg can prove the tool picks the asked-for branch rather than the first one. `af68ae488725dddf7d43b66547c9bfa0324db1e1`
  is `refs/heads/mw_genotype_scale` as measured 2026-10-14, 12 commits past the commit above; `tip-ls-remote-current.txt`
  replaces it with that older commit so the same capture also drives the `CURRENT` arm. Both files are read as data:
  the legs never run `git ls-remote`, and `checks/version_resolves.py` never fetches at all.
