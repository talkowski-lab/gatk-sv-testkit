# `fixtures/trs/`: Dockstore and git-tip fixtures for `checks/version_resolves.py`

The selftest legs read these files and never write them. No leg makes a network call.

`dockstore-trs-genotypebatch-2026-10-14.json`
: An excerpt of a real response to an unauthenticated
  `GET https://dockstore.org/api/ga4gh/trs/v2/tools?organization=broadinstitute&toolname=GenotypeBatch`.
  It keeps one entry and two version rows verbatim, and drops the long entry-level fields
  (`description`, `aliases`, `checker_url`, `has_checker`). TRS version rows carry no commit field,
  which is the case the legs use for the REFUSED outcome.

`dockstore-published-genotypebatch-excerpt.json`
: An excerpt of a real response from
  `https://dockstore.org/api/workflows/path/workflow/github.com%2Fbroadinstitute%2Fgatk-sv%2FGenotypeBatch/published?include=versions`,
  which answers without a token and carries `commitID` on every version row. Four rows are kept, and
  `versions_dropped` records how many were removed, so the file holds no list of other people's branch
  names. It drives the `CURRENT` and `STALE` outcomes. Its `_about` block explains two decoy fields:
  `workflow_path` is the descriptor file and `path` is the repository, so neither identifies the entry.

`workflowversions-genotypebatch-shape-holder.json`
: Hand-built, not captured. Two rows in Dockstore's `WorkflowVersion` shape (field names from
  Dockstore's `openapi.json`), each with a real gatk-sv `commitID`. The published excerpt above
  confirms the shape.

`tip-ls-remote-stale.txt`, `tip-ls-remote-current.txt`
: The output shape of `git ls-remote origin refs/heads/<branch>`: tab-separated, with two
  `refs/heads/` lines so a leg can show the tool picks the branch it was asked about. The stale file's
  tip is newer than the commit Dockstore serves for `dev_genotype_scale`; the current file uses that
  same commit, so one capture drives both outcomes.
