version 1.0

# Reaches nothing and binds nothing: the workflow that MUST still appear in an image list, printed as
# binding none. A per-workflow list that quietly leaves this one out is the failure class the whole
# mode exists to refuse, so it is a fixture and not an omission.
workflow Gamma {
  input {
    Int n
  }
  output { Int twice = n * 2 }
}
