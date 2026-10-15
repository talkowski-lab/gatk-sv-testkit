version 1.1

# One of TWO workflows the same config binds. Its unbound required input must still refuse while the
# OTHER workflow (`AcceptPairB`, shipped nowhere) stays uncaptured: a disclosure is never allowed to hide a
# finding, which is the precedence `grade_required()` states.
workflow AcceptPairA {
    input {
        String unbound_key
    }
    call accept_step { input: label = unbound_key }
    output {}
}

task accept_step {
    input {
        String label
    }
    command <<< echo "~{label}" >>>
    output {}
}
