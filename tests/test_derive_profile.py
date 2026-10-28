"""scripts/derive_profile.py against a tiny synthetic gatk-sv repo built in tmp_path. Offline."""
import hashlib
import importlib.util
import json
import subprocess
import sys

import pytest

from conftest import ROOT

TOOL = "scripts/derive_profile.py"
CORPUS = "inputs/templates/terra_workspaces/cohort/workflow_configurations"

TEMPLATE = """{
  "Toy.sv_pipeline_docker": "${workspace.sv_pipeline_docker}",
  "Toy.batch": "${this.sample_set_id}",
  "Toy.vcf": "${this.clustered_vcf}",
  "Toy.manta_vcf": "${this.clustered_manta_vcf}",
  "Toy.wham_vcf": "${this.clustered_wham_vcf}",
  "Toy.ref": "${workspace.reference_dict}",
  "Toy.n": "6",
  "Toy.chr_x": {{ reference_resources.chr_x | tojson }}
}
"""
OUT_TEMPLATE = """{
  "Toy.merged": "${this.merged}"
}
"""
WDL = """version 1.0

import "Other.wdl" as other

workflow Toy {
  input {
    String batch
    File vcf   # the main one
    File? manta_vcf
    File? wham_vcf
    File ref
    Int n = 6
    String chr_x
    Array[Pair[String, File]]? extra
    String sv_pipeline_docker
    String? branch_only_thing
  }

  call other.Other {
    input:
      vcf = vcf,
      prefix = "${batch}.x"
  }

  output {
    File merged = Other.out
    File stats = select_first([
      Other.stats,
      Other.out
    ])
  }
}

task Unused {
  input { String q }
  command <<< echo ~{q} >>>
  output { File not_a_workflow_output = "x" }
}
"""
DOCKSTORE = """version: 1.2
workflows:
  - subclass: WDL
    name: ToyEntry
    primaryDescriptorPath: /wdl/Toy.wdl
"""


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def _write(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def gsv(tmp_path):
    """A gatk-sv-shaped repo: one input template, one output template, the WDL and .dockstore.yml."""
    repo = tmp_path / "gatk-sv"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _write(repo, CORPUS + "/Toy.json.tmpl", TEMPLATE)
    _write(repo, CORPUS + "/output_configurations/ToyOutputs.json.tmpl", OUT_TEMPLATE)
    _write(repo, "wdl/Toy.wdl", WDL)
    _write(repo, "wdl/Other.wdl", "version 1.0\nworkflow Other {\n  input { File vcf }\n}\n")
    _write(repo, ".github/.dockstore.yml", DOCKSTORE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "toy")
    return repo


def _profile(**over):
    step = {
        "step": "01-Toy", "wdl": "Toy", "workflow": "Toy", "dockstore": "ToyEntry",
        "rootEntityType": "sample_set", "entity_row": "--entity",
        "inputs": {
            "Toy.batch": "this.sample_set_id",
            "Toy.vcf": "this.clustered_vcf{frz}",
            "Toy.@_vcf": "this.clustered_@_vcf{frz}",
            "Toy.ref": "workspace.reference_dict",
            "Toy.n": "6",
            "Toy.chr_x": "workspace.chr_x",
            "Toy.sv_pipeline_docker": "workspace.sv_pipeline_docker",
        },
        "outputs": {"Toy.merged": "this.merged{new}", "Toy.stats": "this.stats{new}"},
        "export": ["merged"],
        "_why_export": "hand-written rationale",
        "images": {"Toy.sv_pipeline_docker": "--image"},
    }
    step.update(over)
    return {"schema_version": 1, "name": "toy", "callers": ["manta", "wham"],
            "_why_callers": "hand-written", "steps": [step]}


def _save(tmp_path, doc, name="toy.json"):
    p = tmp_path / name
    p.write_text(json.dumps(doc, indent=2))
    return p


def _load_module():
    spec = importlib.util.spec_from_file_location("derive_profile", ROOT / TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- derivation
def test_show_derives_and_labels_every_field(run_tool, gsv, tmp_path):
    r = run_tool(TOOL, "show", "--repo", gsv, "--ref", "HEAD", "--profile", _save(tmp_path, _profile()))
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "rootEntityType" in out and "DERIVED  sample_set" in out and "this.sample_set_id" in out
    assert "DERIVED  ToyEntry" in out                       # dockstore entry for /wdl/Toy.wdl
    assert "wdl/Toy.wdl declares `workflow Toy`" in out
    assert "inputs[Toy.vcf]" in out and "DERIVED  this.clustered_vcf" in out
    assert "DERIVED  workspace.reference_dict" in out
    # Jinja: reported with its expression, never given a value.
    line = [ln for ln in out.splitlines() if "inputs[Toy.chr_x]" in ln][0]
    assert "NOT DERIVED FROM UPSTREAM" in line and "{{ reference_resources.chr_x | tojson }}" in line
    # Outputs: value from the output template where there is one; otherwise only the key.
    assert [ln for ln in out.splitlines() if "outputs[Toy.merged]" in ln][0].split()[1] == "DERIVED"
    stats = [ln for ln in out.splitlines() if "outputs[Toy.stats]" in ln][0]
    assert "NOT DERIVED FROM UPSTREAM" in stats
    assert "not_a_workflow_output" not in out              # a task's output is not the workflow's
    for field in ("suffixes", "entity_row", "export", "images", "literals"):
        ln = [x for x in out.splitlines() if x.strip().startswith(field + " ")][0]
        assert "NOT DERIVED FROM UPSTREAM" in ln
    assert "callers  NOT DERIVED FROM UPSTREAM" in out
    assert "GSVTK-DERIVE-SUMMARY" in out


def test_show_without_profile_by_workflow(run_tool, gsv, tmp_path):
    r = run_tool(TOOL, "show", "--repo", gsv, "--workflow", "Toy",
                 "--profile", tmp_path / "absent.json")
    assert r.returncode == 0, r.stderr
    assert "step Toy  (workflow Toy)" in r.stdout


def test_wdl_scan_reads_only_the_workflow_sections():
    mod = _load_module()
    ins, outs, err = mod.wdl_workflow_io(WDL, "Toy")
    assert err == ""
    assert ins == ["batch", "vcf", "manta_vcf", "wham_vcf", "ref", "n", "chr_x", "extra",
                   "sv_pipeline_docker", "branch_only_thing"]
    assert outs == ["merged", "stats"]
    assert mod.wdl_workflow_io(WDL, "Nope")[2]


# --------------------------------------------------------------------------- check
def test_check_agrees(run_tool, gsv, tmp_path):
    r = run_tool(TOOL, "check", "--repo", gsv, "--profile", _save(tmp_path, _profile()))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "disagree=0" in r.stdout
    assert "not compared  01-Toy inputs[Toy.chr_x]" in r.stdout


def test_check_disagrees_with_both_values(run_tool, gsv, tmp_path):
    doc = _profile(rootEntityType="sample", dockstore="Toy")
    step = doc["steps"][0]
    step["inputs"]["Toy.vcf"] = "this.other_vcf{frz}"
    step["inputs"]["Toy.extra"] = "workspace.extra"            # declared, upstream leaves it default
    step["inputs"]["Toy.bogus"] = "workspace.bogus"            # not declared at all
    del step["inputs"]["Toy.ref"]                             # upstream binds it, profile does not
    step["outputs"]["Toy.gone"] = "this.gone{new}"
    r = run_tool(TOOL, "check", "--repo", gsv, "--profile", _save(tmp_path, doc))
    assert r.returncode == 1, r.stdout + r.stderr
    out = r.stdout
    assert "DISAGREE      01-Toy rootEntityType" in out
    assert "profile:  sample\n" in out and "upstream: sample_set\n" in out
    assert "DISAGREE      01-Toy dockstore" in out
    assert "profile:  this.other_vcf\n                upstream: this.clustered_vcf" in out
    assert "inputs[Toy.extra]  [profile-only: upstream's template leaves it to the WDL default]" in out
    assert "inputs[Toy.bogus]  [profile-only: the WDL at this ref does not declare it]" in out
    assert "inputs[Toy.ref]  [upstream-only]" in out
    assert "outputs[Toy.gone]  [profile-only: not a declared output at this ref]" in out
    assert "disagree=7" in out


def test_check_branch_only_is_expected_not_a_disagreement(run_tool, gsv, tmp_path):
    doc = _profile(branch_only_inputs=["Toy.branch_only_thing"])
    doc["steps"][0]["inputs"]["Toy.branch_only_thing"] = "this.thing{new}"
    r = run_tool(TOOL, "check", "--repo", gsv, "--profile", _save(tmp_path, doc))
    assert r.returncode == 0, r.stdout
    assert "expected      01-Toy inputs[Toy.branch_only_thing]" in r.stdout


def test_check_nothing_derived_is_refused(run_tool, gsv, tmp_path):
    doc = _profile(workflow="Nope", wdl="Nope")
    doc["steps"][0]["inputs"] = {"Nope.batch": "this.sample_set_id"}
    doc["steps"][0]["outputs"] = {}
    doc["steps"][0].pop("images")
    r = run_tool(TOOL, "check", "--repo", gsv, "--profile", _save(tmp_path, doc))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "nothing could be derived" in r.stderr


# --------------------------------------------------------------------------- draft + lock
def test_write_draft_and_lock(run_tool, gsv, tmp_path):
    sys.path.insert(0, str(ROOT / "kit"))
    import module_profile as mp
    dest = tmp_path / "out" / "toy.draft.json"
    r = run_tool(TOOL, "show", "--repo", gsv, "--profile", _save(tmp_path, _profile()),
                 "--write-draft", dest)
    assert r.returncode == 0, r.stderr
    draft = json.loads(dest.read_text())
    lock = json.loads((tmp_path / "out" / "toy.draft.json.lock.json").read_text())

    assert mp.validate(draft) == []
    s = draft["steps"][0]
    assert s["inputs"]["Toy.vcf"] == "this.clustered_vcf{frz}"        # suffix carried, base derived
    assert s["inputs"]["Toy.manta_vcf"] == "this.clustered_manta_vcf{frz}"
    assert s["inputs"]["Toy.chr_x"] == "workspace.chr_x"              # Jinja: carried, not derived
    assert s["outputs"] == {"Toy.merged": "this.merged{new}", "Toy.stats": "this.stats{new}"}
    assert s["dockstore"] == "ToyEntry" and s["rootEntityType"] == "sample_set"
    assert s["export"] == ["merged"] and s["_why_export"] == "hand-written rationale"
    assert s["images"] == {"Toy.sv_pipeline_docker": "--image"}
    assert draft["callers"] == ["manta", "wham"] and draft["_why_callers"] == "hand-written"

    assert lock["sha"] == _git(gsv, "rev-parse", "HEAD").strip() and lock["ref"] == "HEAD"
    paths = {f["path"]: f for f in lock["files"]}
    assert set(paths) == {CORPUS + "/Toy.json.tmpl", CORPUS + "/output_configurations/ToyOutputs.json.tmpl",
                          "wdl/Toy.wdl", ".github/.dockstore.yml"}
    for p, f in paths.items():
        assert f["sha256"] == hashlib.sha256((gsv / p).read_bytes()).hexdigest()
        assert f["blob"] == _git(gsv, "rev-parse", "HEAD:" + p).strip()
    assert any("Toy.chr_x" in x and "Jinja" in x for x in lock["not_derived_or_dropped"])


def test_checkout_state_is_untouched(run_tool, gsv, tmp_path):
    first = _git(gsv, "rev-parse", "HEAD").strip()
    _write(gsv, CORPUS + "/Toy.json.tmpl", TEMPLATE.replace("clustered_vcf}", "renamed_vcf}"))
    _git(gsv, "commit", "-qam", "second")
    head = _git(gsv, "rev-parse", "HEAD").strip()
    # A dirty tree and a staged change, which a checkout-based reader would clobber or trip on.
    _write(gsv, "wdl/Toy.wdl", WDL + "\n# dirty\n")
    _write(gsv, "staged.txt", "staged\n")
    _git(gsv, "add", "staged.txt")
    before = (_git(gsv, "status", "--porcelain"), (gsv / ".git" / "index").read_bytes(),
              _git(gsv, "symbolic-ref", "HEAD"))

    r = run_tool(TOOL, "check", "--repo", gsv, "--ref", first, "--profile", _save(tmp_path, _profile()))
    assert r.returncode == 0, r.stdout + r.stderr               # the first commit agrees
    r2 = run_tool(TOOL, "check", "--repo", gsv, "--ref", "HEAD", "--profile", _save(tmp_path, _profile()))
    assert r2.returncode == 1 and "this.renamed_vcf" in r2.stdout

    after = (_git(gsv, "status", "--porcelain"), (gsv / ".git" / "index").read_bytes(),
             _git(gsv, "symbolic-ref", "HEAD"))
    assert after == before
    assert _git(gsv, "rev-parse", "HEAD").strip() == head
    assert (gsv / "wdl/Toy.wdl").read_text().endswith("# dirty\n")


# --------------------------------------------------------------------------- refusals
def test_refusals(run_tool, gsv, tmp_path):
    prof = _save(tmp_path, _profile())
    r = run_tool(TOOL, "check", "--repo", gsv, "--ref", "no-such-ref", "--profile", prof)
    assert r.returncode == 2 and "no ref 'no-such-ref'" in r.stderr
    r = run_tool(TOOL, "check", "--repo", tmp_path, "--profile", prof)
    assert r.returncode == 4 and "not a git repository" in r.stderr
    r = run_tool(TOOL, "check", "--repo", gsv, "--profile", prof, "--write-draft", tmp_path / "x.json")
    assert r.returncode == 2 and not (tmp_path / "x.json").exists()
    r = run_tool(TOOL, "check", "--repo", gsv, "--profile", prof, "--workflow", "Toy")
    assert r.returncode == 2
    r = run_tool(TOOL, "check", "--repo", gsv, "--profile", tmp_path / "missing.json")
    assert r.returncode == 4 and "no usable profile" in r.stderr
    bad = _profile()
    bad["steps"][0]["typo_field"] = 1
    r = run_tool(TOOL, "check", "--repo", gsv, "--profile", _save(tmp_path, bad, "bad.json"))
    assert r.returncode == 2 and "does not validate" in r.stderr


# --------------------------------------------------------------------------- explained differences
def test_check_explained_differences_are_expected(run_tool, gsv, tmp_path):
    doc = _profile(template_overrides=["Toy.extra"], _why_template_overrides="bound on purpose",
                   branch_only_outputs=["Toy.newout"], _why_branch_only_outputs="the branch adds it",
                   upstream_only_outputs=["Toy.stats"], _why_upstream_only_outputs="the branch drops it")
    doc["callers"] = ["manta"]
    doc["unused_callers"] = ["wham"]
    doc["_why_unused_callers"] = "wham did not run"
    step = doc["steps"][0]
    step["inputs"]["Toy.extra"] = "workspace.extra"
    step["outputs"]["Toy.newout"] = "this.newout{new}"
    del step["outputs"]["Toy.stats"]
    r = run_tool(TOOL, "check", "--repo", gsv, "--profile", _save(tmp_path, doc))
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "disagree=0" in out and "expected=4" in out
    assert "inputs[Toy.wham_vcf]  (unused_callers: wham)" in out
    assert "inputs[Toy.extra]  (template_overrides:" in out
    assert "outputs[Toy.newout]  (branch_only_outputs:" in out
    assert "outputs[Toy.stats]  (upstream_only_outputs:" in out


def test_template_override_does_not_excuse_an_undeclared_input(run_tool, gsv, tmp_path):
    doc = _profile(template_overrides=["Toy.bogus"], _why_template_overrides="x")
    doc["steps"][0]["inputs"]["Toy.bogus"] = "workspace.bogus"
    r = run_tool(TOOL, "check", "--repo", gsv, "--profile", _save(tmp_path, doc))
    assert r.returncode == 1, r.stdout
    assert "inputs[Toy.bogus]  [profile-only: the WDL at this ref does not declare it]" in r.stdout


@pytest.mark.parametrize("edit,needle", [
    (lambda d: d["steps"][0].update(branch_only_outputs=["Toy.merged"]), "has no `_why_branch_only_outputs`"),
    (lambda d: d["steps"][0].update(branch_only_outputs=["Toy.absent"], _why_branch_only_outputs="x"),
     "which `outputs` does not list"),
    (lambda d: d["steps"][0].update(upstream_only_outputs=["Toy.merged"], _why_upstream_only_outputs="x"),
     "but `outputs` still lists it"),
    (lambda d: d["steps"][0].update(template_overrides=["Toy.absent"], _why_template_overrides="x"),
     "which `inputs` does not bind"),
    (lambda d: d["steps"][0].update(template_overrides=["Other.vcf"], _why_template_overrides="x"),
     "which is not a Toy.* key"),
    (lambda d: d.update(unused_callers=["manta"], _why_unused_callers="x"), "both name manta"),
    (lambda d: d.update(unused_callers=["melt"]), "has no `_why_unused_callers`"),
])
def test_explaining_fields_are_validated(edit, needle):
    sys.path.insert(0, str(ROOT / "kit"))
    import module_profile as mp
    doc = _profile()
    edit(doc)
    problems = mp.validate(doc)
    assert any(needle in p for p in problems), problems


def test_draft_keeps_the_explained_differences(run_tool, gsv, tmp_path):
    doc = _profile(template_overrides=["Toy.extra"], _why_template_overrides="bound on purpose",
                   branch_only_outputs=["Toy.newout"], _why_branch_only_outputs="the branch adds it",
                   upstream_only_outputs=["Toy.stats"], _why_upstream_only_outputs="the branch drops it")
    doc["callers"] = ["manta"]
    doc["unused_callers"] = ["wham"]
    doc["_why_unused_callers"] = "wham did not run"
    step = doc["steps"][0]
    step["inputs"]["Toy.extra"] = "workspace.extra"
    step["outputs"]["Toy.newout"] = "this.newout{new}"
    del step["outputs"]["Toy.stats"]
    dest = tmp_path / "draft.json"
    r = run_tool(TOOL, "show", "--repo", gsv, "--profile", _save(tmp_path, doc), "--write-draft", dest)
    assert r.returncode == 0, r.stderr
    draft = json.loads(dest.read_text())
    s = draft["steps"][0]
    assert draft["unused_callers"] == ["wham"]
    assert "Toy.wham_vcf" not in s["inputs"] and s["inputs"]["Toy.extra"] == "workspace.extra"
    assert s["template_overrides"] == ["Toy.extra"]
    assert "Toy.newout" in s["outputs"] and "Toy.stats" not in s["outputs"]
    assert s["branch_only_outputs"] == ["Toy.newout"] and s["upstream_only_outputs"] == ["Toy.stats"]
