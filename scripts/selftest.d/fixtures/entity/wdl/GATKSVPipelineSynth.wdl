version 1.1

# The WDL beside `beta/GATKSVPipelineSynth.json.tmpl`. Nothing else in this corpus ships one, which is the
# point: this config gets a required-input verdict (its `cutoffs` binding reads a workspace attribute beta's
# own table does not declare, and that value is a REQUIRED input), and the other 41 configs show what the
# tool prints when the workflow behind a config is not in the tree — `capture=NO-WDL`, counted on
# `GSVTK-ENTITY-UNCHECKED`, never a silent zero.
#
# Synthetic names only: no upstream data, no machine path, no coordinate.
workflow GATKSVPipelineSynth {
    input {
        # required: no default, not optional
        String sample_id
        # required, and the config binds it to `${workspace.ref_panel_cutoffs}` — an attribute beta's
        # `workspace.tsv.tmpl` does not declare
        File cutoffs
        # satisfied by the WDL itself, so demanding it in the config would manufacture a finding
        String batch = "synthetic"
        # optional, and therefore nobody's required input
        File? optional_extra
    }
    call synth_step { input: label = sample_id }
    output {}
}

task synth_step {
    input {
        String label
    }
    command <<< echo "~{label}" >>>
    output {}
}
