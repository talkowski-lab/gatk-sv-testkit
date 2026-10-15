version 1.0

# A task library with NO workflow in it, the shape `reach --images` has to survive: a reverse answer
# from one of its tasks reaches FILES (file -> file -> contains -> task), so the reaching workflows come
# out of the importers, not out of this file. Written for this selftest; not a copy of anything.
task CountThings {
  input {
    File in_file
    String sv_pipeline_docker
  }
  command <<<
    set -euo pipefail
    count_things.sh --in ~{in_file}
  >>>
  output { Int n = read_int(stdout()) }
  runtime { docker: sv_pipeline_docker cpu: 1 memory: "1G" preemptible: 0 }
}
