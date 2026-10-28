version 1.1

# A file that ships tasks and no workflow: nothing to bind, so the required question has no subject. The
# 9 task-only libraries in gatk-sv's `wdl/` at `e1909d2f` are this shape; a launch config naming one would
# get `NO-WDL` with this note rather than `required=0` read as "it needs nothing".
task accept_step {
    input {
        String label
    }
    command <<< echo "~{label}" >>>
    output {}
}
