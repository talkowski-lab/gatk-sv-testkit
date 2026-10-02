version 1.0

# Declares a container input that NOTHING in the tree binds: no WDL default, no rendered input JSON, no
# profile key. The honest answer is UNRESOLVED with that reason, printed and counted — not a row that
# goes missing, and not the plausible-looking image string sitting in dockers.json under the same name.
workflow Delta {
  input {
    File in_file
    String sv_utils_docker
  }
  output { File out = in_file }
}
