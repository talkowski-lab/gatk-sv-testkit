version 1.0

# The Terra flavour: its rendered workflow_configuration binds `${workspace.manta_docker}`, which is a
# Rawls placeholder, not an image. The value is known and the image is not, so the answer has to be
# UNRESOLVED-with-reason rather than the dockers.json string the test flavour happens to pin.
workflow Epsilon {
  input {
    File in_vcf
    String manta_docker
  }
  output { File out = in_vcf }
}
