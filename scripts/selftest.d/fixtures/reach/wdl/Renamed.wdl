version 1.0

import "SharedTasks.wdl" as shared

# The workflow's DECLARED name differs from its file basename — the case docs/module-profiles.md rule 1
# exists for (12 of the 109 workflow-bearing WDLs at main are like this). Two lookups are therefore
# under test at once: this workflow's sv_pipeline_docker is bound by a JSON key that spells the
# WORKFLOW name, and its sv_base_mini_docker only by one that spells the FILE STEM.
workflow ClusterDepthByProfile {
  input {
    File in_file
    String sv_pipeline_docker
    String sv_base_mini_docker
  }
  call shared.CountThings { input: in_file = in_file, sv_pipeline_docker = sv_pipeline_docker }
  output { Int n = CountThings.n }
}
