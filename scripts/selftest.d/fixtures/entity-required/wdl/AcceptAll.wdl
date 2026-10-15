version 1.1

# The PASS shape of the required-input layer: every input this workflow demands with no default and no
# `optional` is bound by `acc/AcceptAll.json.tmpl`, and the class of the value carrying each one is a
# launchable one. A defaulted declaration (`batch`) and an optional one (`extra`) are deliberately NOT
# required: demanding them in a launch config would manufacture a finding, which is the failure
# `checks/wdl_inputs_check.py::required_inputs` exists to avoid.
workflow AcceptAll {
    input {
        String sample_id
        String reference_fasta
        Array[File] clustering_track_bed_files
        String manta_docker
        String batch = "synthetic"
        File? extra
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
