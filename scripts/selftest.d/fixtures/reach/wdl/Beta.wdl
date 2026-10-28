version 1.0

import "SharedTasks.wdl" as shared

# Reaches the same task as Alpha but binds the OTHER half of the distinction: its sv_pipeline_docker
# has no default and the rendered input JSON binds it to the date-prefixed tag dockers.json pins, so a
# run of this workflow tests the published image, not the branch.
workflow Beta {
  input {
    File in_file
    String sv_pipeline_docker
  }
  call shared.CountThings { input: in_file = in_file, sv_pipeline_docker = sv_pipeline_docker }
  output { Int n = CountThings.n }
}
