version 1.0

struct Inner {
    String label
    File? maybe
}

struct Outer {
    String label
    Inner inner
    Int n
}

struct HasOptionalMember {
    String optional
    Int count
}

workflow ShapeProbe {
  input {
    String plain_req
    String? plain_opt
    String defaulted = "hi"
    File file_req
    Array[File] array_req
    Array[String]? array_opt
    Map[String, String] map_req
    Inner struct_req
    Inner? struct_opt
    Inner struct_defaulted = object { label: "a" }
    HasOptionalMember named_optional_member_req
    Array[Inner] array_of_struct_req
  }
  output {}
}
