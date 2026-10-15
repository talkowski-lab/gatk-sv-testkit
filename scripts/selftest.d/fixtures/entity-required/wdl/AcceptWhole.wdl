version 1.1

struct WholeOptions {
    String? note
    Int? shards
}

# Every member of `WholeOptions` is optional, so the WDL asks for the STRUCT, not for a member — and an
# input file may hand the struct over as one object. The config does exactly that, which is the shape no
# value class describes: the scalars inside it are classed, the key that carries them is not, and calling
# that `unbound` would refuse a config that really does bind the input.
workflow AcceptWhole {
    input {
        WholeOptions opts
        String? label
    }
    call accept_step { input: label = select_first([label, "x"]) }
    output {}
}

task accept_step {
    input {
        String label
    }
    command <<< echo "~{label}" >>>
    output {}
}
