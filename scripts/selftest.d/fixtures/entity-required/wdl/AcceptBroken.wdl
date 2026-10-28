version 1.1

# Not a valid document: the input section is never closed, so miniwdl refuses to load it and the required
# question cannot be asked. `LOAD-FAILURE` is the word `checks/wdl_inputs_check.py` uses for this too, and
# the note beside it is miniwdl's own first line — reported, never swallowed into "no required inputs".
workflow AcceptBroken {
    input {
        String sample_id
