version 1.0

struct Inner {
    String label
    Int n
    File? maybe
}

workflow CrossCheck {
  input {
    File vcf
    Int? extra_note
    String defaulted = "hi"
    Inner attrs
  }
  call MakeThing {
    input:
      vcf = vcf
  }
  output {
    File out = MakeThing.out
    Int used = attrs.n
  }
}

task MakeThing {
  input {
    File vcf
    Int cpu                        # never bound at the call site: CrossCheck.MakeThing.cpu is a legal
    Int threads = 1                # JSON key, and so is the defaulted one beside it (`womtool inputs`
  }                                # lists both), so neither may be called an extra
  command <<<
    echo "~{vcf} ~{cpu} ~{threads}" > out.txt
  >>>
  output { File out = "out.txt" }
  runtime { docker: "ubuntu:22.04" }
}
