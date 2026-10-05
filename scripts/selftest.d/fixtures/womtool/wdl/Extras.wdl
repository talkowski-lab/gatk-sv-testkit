version 1.0

struct WidgetOpt {
    Int cpu
    Int? disk_gb
}

# Every key the two input JSONs beside this file name is LEGAL, and the point of the fixture is that
# four of them are the shapes a naive "is it a declared input?" test would call extra: a call-qualified
# task input (`Extras.MakeThing.cpu`, which the call site above deliberately does not bind), a DEFAULTED
# task input (`Extras.MakeThing.threads`), a struct member path written flat (`Extras.attrs.cpu`), and a
# key INSIDE a struct handed over as one object (`Extras.more_attrs.unknown_member`, which womtool
# itself accepts -- `Success!` -- because members of a value are nobody's key).
workflow Extras {
  input {
    File vcf
    WidgetOpt attrs
    WidgetOpt? more_attrs
    String? note
  }
  call MakeThing {
    input:
      vcf = vcf
  }
  output {
    File out = MakeThing.out
  }
}

task MakeThing {
  input {
    File vcf
    Int cpu
    Int threads = 1
  }
  command <<<
    echo "~{vcf} ~{cpu} ~{threads}" > out.txt
  >>>
  output { File out = "out.txt" }
  runtime { docker: "ubuntu:22.04" }
}
