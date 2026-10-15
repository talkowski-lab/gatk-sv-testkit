version 1.1

# The refusal this layer exists to print: `sample_id` is declared with no default and is not optional, and
# `acc/AcceptUnbound.json.tmpl` binds no key for it. Nothing else about that config is wrong — its entity
# layer and its value layer both pass — which is what makes this refusal the ONLY finding on the corpus and
# the exit code its own, not somebody else's. Upstream's real instance of this shape is
# `IntegrateGDVcf.sample_id` (row C8 of docs/gap-ledger.md), which typechecked green and failed at input
# resolution mid-submission.
workflow AcceptUnbound {
    input {
        String sample_id
    }
    call accept_step { input: label = sample_id }
    output {}
}

task accept_step {
    input {
        String label
    }
    command <<< echo "~{label}" >>>
    output {}
}
