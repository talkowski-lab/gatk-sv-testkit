version 1.0

import "SharedTasks.wdl" as shared

# Binds TWO container inputs, from TWO different places, to TWO different verdicts: the sv_pipeline one
# has a WDL default whose tag is `<branch>-<sha>` (the shape docker/gatk-sv-build.sh mints), and the
# gatk one has no default and is bound by a rendered input JSON to a date-prefixed tag. One workflow,
# both halves of the distinction `--images` exists to make.
workflow Alpha {
  input {
    File in_file
    String sv_pipeline_docker = "us.gcr.io/acme-test/gatk-sv/sv-pipeline:my-branch-9a34dc"
    String gatk_docker
  }
  call shared.CountThings { input: in_file = in_file, sv_pipeline_docker = sv_pipeline_docker }
  output { Int n = CountThings.n }
}
