"""Offline tests for terra/batch_freeze.py: profile-driven freezing per pipeline flavor.

Nothing here reaches Terra or GCS. `terra` is replaced by an in-process fake before the tool is run
(the runner below installs it in sys.modules, then runs the script as __main__), and `gsutil` is a
fake on PATH backed by a JSON object store in the test's tmp dir.
"""
import json
import os
import stat
import subprocess
import sys

import pytest

from conftest import ROOT, clean_env

TOOL = ROOT / "terra" / "batch_freeze.py"
BUCKET = "fc-sandbox-bucket"
BASE = "gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X"

# The cohort chain's 17 frozen attributes, in the order the tool has always written them.
GENO_ATTRS = [
    "clustered_depth_vcf", "clustered_depth_vcf_index",
    "clustered_manta_vcf", "clustered_manta_vcf_index",
    "clustered_scramble_vcf", "clustered_scramble_vcf_index",
    "clustered_wham_vcf", "clustered_wham_vcf_index",
    "merged_PE", "merged_PE_index",
    "merged_SR", "merged_SR_index",
    "merged_bincov", "merged_bincov_index",
    "merged_BAF", "merged_BAF_index",
    "median_cov",
]

FAKE_GSUTIL = r'''#!{py}
"""Fake gsutil: `stat URL` and `[-q] cp SRC DST` against a JSON object store; logs every call."""
import fcntl, json, os, sys
store_path = os.environ["FAKE_GCS_STORE"]
lock = open(store_path + ".lock", "w")
fcntl.flock(lock, fcntl.LOCK_EX)          # copies run in parallel; serialise the store
with open(os.environ["FAKE_GCS_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
store = json.load(open(store_path))
args = [a for a in sys.argv[1:] if a != "-q"]
if args[0] == "stat":
    o = store.get(args[1])
    if o is None:
        sys.stderr.write("No URLs matched: %s\n" % args[1]); sys.exit(1)
    print(args[1] + ":")
    print("    Content-Length:         %d" % o["bytes"])
    print("    Hash (crc32c):          %s" % o["crc32c"])
    sys.exit(0)
if args[0] == "cp":
    src, dst = args[1], args[2]
    if src not in store:
        sys.stderr.write("No URLs matched: %s\n" % src); sys.exit(1)
    store[dst] = dict(store[src])
    json.dump(store, open(store_path, "w"))
    sys.exit(0)
sys.stderr.write("fake gsutil: unsupported %r\n" % args); sys.exit(2)
'''

RUNNER = r'''
"""Install a fake `terra` module, then run batch_freeze.py as __main__ with the remaining argv."""
import json, os, runpy, sys, types

LOG = os.environ["FAKE_TERRA_LOG"]


def _rec(**kw):
    with open(LOG, "a") as fh:
        fh.write(json.dumps(kw) + "\n")


t = types.ModuleType("terra")


class TerraError(RuntimeError):
    pass


t.TerraError = TerraError
t.BASELINE_NS = os.environ.get("GSVTK_BASELINE_NAMESPACE", "base-ns")
t.BASELINE_WS = os.environ.get("GSVTK_BASELINE_WORKSPACE", "base-ws")


def workspace(ns, name):
    _rec(op="workspace", ns=ns, ws=name)
    return {"workspace": {"bucketName": os.environ["FAKE_BUCKET"]}}


def assert_writable_target(ns, ws, what, allow=False):
    _rec(op="assert_writable_target", ns=ns, ws=ws, allow=allow)
    if not allow and ns == t.BASELINE_NS and ws == t.BASELINE_WS:
        raise TerraError("refusing to %s in the baseline workspace %s/%s" % (what, ns, ws))


def upload_entities_tsv(ns, name, tsv, confirm=False):
    if not confirm:
        raise TerraError("refusing to upload entities without confirm=True")
    with open(tsv) as fh:
        _rec(op="upload", ns=ns, ws=name, path=tsv, text=fh.read())
    return {"ok": True}


t.workspace = workspace
t.assert_writable_target = assert_writable_target
t.upload_entities_tsv = upload_entities_tsv
sys.modules["terra"] = t
script = sys.argv[1]
sys.argv = [script] + sys.argv[2:]
runpy.run_path(script, run_name="__main__")
'''


class Rig:
    """A tmp tree with a fake gsutil on PATH, a fake object store, a work dir and an empty config."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        g = self.bin / "gsutil"
        g.write_text(FAKE_GSUTIL.replace("{py}", sys.executable))
        g.chmod(g.stat().st_mode | stat.S_IEXEC)
        self.runner = tmp / "runner.py"
        self.runner.write_text(RUNNER)
        self.store = tmp / "gcs.json"
        self.store.write_text("{}")
        self.gcs_log = tmp / "gsutil.log"
        self.gcs_log.write_text("")
        self.terra_log = tmp / "terra.log"
        self.terra_log.write_text("")
        self.work = tmp / "work"
        (self.work / "recon").mkdir(parents=True)
        self.cfg = tmp / "empty.env"
        self.cfg.write_text("")

    def put(self, url, crc="AAAAAA==", size=100):
        s = json.loads(self.store.read_text())
        s[url] = {"crc32c": crc, "bytes": size}
        self.store.write_text(json.dumps(s))

    def objects(self):
        return json.loads(self.store.read_text())

    def env(self, **extra):
        e = clean_env(GSVTK_CONFIG=str(self.cfg), GSVTK_WORK=str(self.work),
                      GSVTK_TERRA_NAMESPACE="sand-ns", GSVTK_TERRA_WORKSPACE="sand-ws",
                      GSVTK_BASELINE_NAMESPACE="base-ns", GSVTK_BASELINE_WORKSPACE="base-ws",
                      FAKE_GCS_STORE=str(self.store), FAKE_GCS_LOG=str(self.gcs_log),
                      FAKE_TERRA_LOG=str(self.terra_log), FAKE_BUCKET=BUCKET)
        e["PATH"] = str(self.bin) + os.pathsep + e.get("PATH", "")
        e.update(extra)
        return e

    def run(self, *args, **env):
        return subprocess.run([sys.executable, str(self.runner), str(TOOL), *map(str, args)],
                              capture_output=True, text=True, timeout=120, env=self.env(**env),
                              cwd=str(ROOT))

    def terra_calls(self, op=None):
        rows = [json.loads(line) for line in self.terra_log.read_text().splitlines() if line]
        return [r for r in rows if op is None or r["op"] == op]

    def gsutil_calls(self):
        return [json.loads(line) for line in self.gcs_log.read_text().splitlines() if line]

    def geno_dump(self):
        """The cohort-chain entity dump (`{row: attrs}`), with one shared basename to exercise renaming."""
        attrs = {}
        for a in GENO_ATTRS:
            leaf = "all_samples.%s.gz" % a
            if a == "merged_SR_index":
                leaf = "all_samples.merged_PE_index.gz"           # same basename, different object
            attrs[a] = "%s/%s/%s" % (BASE, a, leaf)
            self.put(attrs[a], crc="crc-%s==" % a, size=1000 + len(a))
        (self.work / "recon" / "sample_set_entities.json").write_text(json.dumps({"all_samples": attrs}))
        return attrs


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def capture_genotyping(rig):
    """stdout of the genotyping modes that print a plan, with tmp paths normalised."""
    rig.geno_dump()
    out = {}
    for mode in (["plan"], ["copy"], ["attrs", "--allow-unverified"], ["copy", "--write"], ["verify"]):
        r = rig.run(*mode)
        out[" ".join(mode)] = (r.returncode, r.stdout.replace(str(rig.tmp), "<TMP>"))
    tsv = (rig.work / "staging" / "frozen_baseline_attrs.tsv").read_text()
    out["tsv"] = tsv
    man = json.loads((rig.work / "manifests" / "baseline_frozen_inputs.json").read_text())
    out["manifest_keys"] = sorted(man)
    return out


# Captured from terra/batch_freeze.py BEFORE it read the module profile (same rig, same dump).
GENO_BEFORE = json.loads(r'''{
 "plan": [
  0,
  "   note: 1 basename(s) are shared by different source objects (all_samples.merged_PE_index.gz); frozen under attribute-qualified names instead of sharing one object\nclustered_depth_vcf        gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/clustered_depth_vcf/all_samples.clustered_depth_vcf.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_depth_vcf.gz\nclustered_depth_vcf_index  gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/clustered_depth_vcf_index/all_samples.clustered_depth_vcf_index.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_depth_vcf_index.gz\nclustered_manta_vcf        gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/clustered_manta_vcf/all_samples.clustered_manta_vcf.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_manta_vcf.gz\nclustered_manta_vcf_index  gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/clustered_manta_vcf_index/all_samples.clustered_manta_vcf_index.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_manta_vcf_index.gz\nclustered_scramble_vcf     gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/clustered_scramble_vcf/all_samples.clustered_scramble_vcf.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_scramble_vcf.gz\nclustered_scramble_vcf_index gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/clustered_scramble_vcf_index/all_samples.clustered_scramble_vcf_index.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_scramble_vcf_index.gz\nclustered_wham_vcf         gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/clustered_wham_vcf/all_samples.clustered_wham_vcf.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_wham_vcf.gz\nclustered_wham_vcf_index   gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/clustered_wham_vcf_index/all_samples.clustered_wham_vcf_index.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_wham_vcf_index.gz\nmerged_PE                  gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/merged_PE/all_samples.merged_PE.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_PE.gz\nmerged_PE_index            gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/merged_PE_index/all_samples.merged_PE_index.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/merged_PE_index__all_samples.merged_PE_index.gz\nmerged_SR                  gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/merged_SR/all_samples.merged_SR.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_SR.gz\nmerged_SR_index            gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/merged_SR_index/all_samples.merged_PE_index.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/merged_SR_index__all_samples.merged_PE_index.gz\nmerged_bincov              gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/merged_bincov/all_samples.merged_bincov.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_bincov.gz\nmerged_bincov_index        gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/merged_bincov_index/all_samples.merged_bincov_index.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_bincov_index.gz\nmerged_BAF                 gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/merged_BAF/all_samples.merged_BAF.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_BAF.gz\nmerged_BAF_index           gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/merged_BAF_index/all_samples.merged_BAF_index.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_BAF_index.gz\nmedian_cov                 gs://fc-baseline/submissions/ab12/ClusterBatch/wf1/call-X/median_cov/all_samples.median_cov.gz\n                           -> gs://fc-sandbox-bucket/frozen-baseline/all_samples.median_cov.gz\n\n17 objects\n"
 ],
 "copy": [
  0,
  "   note: 1 basename(s) are shared by different source objects (all_samples.merged_PE_index.gz); frozen under attribute-qualified names instead of sharing one object\n17 object(s), 0.00 GiB -> gs://fc-sandbox-bucket/frozen-baseline\ndry run: nothing was copied. Re-run with --write to copy into the bucket above.\n"
 ],
 "attrs --allow-unverified": [
  0,
  "   note: 1 basename(s) are shared by different source objects (all_samples.merged_PE_index.gz); frozen under attribute-qualified names instead of sharing one object\n   !! --allow-unverified: publishing frozen paths with no recorded crc32c verify\nentity:sample_set_id\tclustered_depth_vcf_frz\tclustered_depth_vcf_index_frz\tclustered_manta_vcf_frz\tclustered_manta_vcf_index_frz\tclustered_scramble_vcf_frz\tclustered_scramble_vcf_index_frz\tclustered_wham_vcf_frz\tclustered_wham_vcf_index_frz\tmerged_PE_frz\tmerged_PE_index_frz\tmerged_SR_frz\tmerged_SR_index_frz\tmerged_bincov_frz\tmerged_bincov_index_frz\tmerged_BAF_frz\tmerged_BAF_index_frz\tmedian_cov_frz\nall_samples\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_depth_vcf.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_depth_vcf_index.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_manta_vcf.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_manta_vcf_index.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_scramble_vcf.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_scramble_vcf_index.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_wham_vcf.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_wham_vcf_index.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_PE.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/merged_PE_index__all_samples.merged_PE_index.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_SR.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/merged_SR_index__all_samples.merged_PE_index.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_bincov.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_bincov_index.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_BAF.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_BAF_index.gz\t\n    gs://fc-sandbox-bucket/frozen-baseline/all_samples.median_cov.gz\n\ndry run: re-run with --write to upload\n"
 ],
 "copy --write": [
  0,
  "   note: 1 basename(s) are shared by different source objects (all_samples.merged_PE_index.gz); frozen under attribute-qualified names instead of sharing one object\n17 object(s), 0.00 GiB -> gs://fc-sandbox-bucket/frozen-baseline\ncopying clustered_depth_vcf\ncopying clustered_depth_vcf_index\ncopying clustered_manta_vcf\ncopying clustered_manta_vcf_index\ncopying clustered_scramble_vcf\ncopying clustered_scramble_vcf_index\ncopying clustered_wham_vcf\ncopying clustered_wham_vcf_index\ncopying merged_PE\ncopying merged_PE_index\ncopying merged_SR\ncopying merged_SR_index\ncopying merged_bincov\ncopying merged_bincov_index\ncopying merged_BAF\ncopying merged_BAF_index\ncopying median_cov\ncopied 17/17\n"
 ],
 "verify": [
  0,
  "   note: 1 basename(s) are shared by different source objects (all_samples.merged_PE_index.gz); frozen under attribute-qualified names instead of sharing one object\nOK   clustered_depth_vcf        crc32c=crc-clustered_depth_vcf== bytes=1019\nOK   clustered_depth_vcf_index  crc32c=crc-clustered_depth_vcf_index== bytes=1025\nOK   clustered_manta_vcf        crc32c=crc-clustered_manta_vcf== bytes=1019\nOK   clustered_manta_vcf_index  crc32c=crc-clustered_manta_vcf_index== bytes=1025\nOK   clustered_scramble_vcf     crc32c=crc-clustered_scramble_vcf== bytes=1022\nOK   clustered_scramble_vcf_index crc32c=crc-clustered_scramble_vcf_index== bytes=1028\nOK   clustered_wham_vcf         crc32c=crc-clustered_wham_vcf== bytes=1018\nOK   clustered_wham_vcf_index   crc32c=crc-clustered_wham_vcf_index== bytes=1024\nOK   merged_PE                  crc32c=crc-merged_PE== bytes=1009\nOK   merged_PE_index            crc32c=crc-merged_PE_index== bytes=1015\nOK   merged_SR                  crc32c=crc-merged_SR== bytes=1009\nOK   merged_SR_index            crc32c=crc-merged_SR_index== bytes=1015\nOK   merged_bincov              crc32c=crc-merged_bincov== bytes=1013\nOK   merged_bincov_index        crc32c=crc-merged_bincov_index== bytes=1019\nOK   merged_BAF                 crc32c=crc-merged_BAF== bytes=1010\nOK   merged_BAF_index           crc32c=crc-merged_BAF_index== bytes=1016\nOK   median_cov                 crc32c=crc-median_cov== bytes=1010\n\nmanifest -> <TMP>/work/manifests/baseline_frozen_inputs.json  total 0.00 GiB  verified=True\n"
 ],
 "tsv": "entity:sample_set_id\tclustered_depth_vcf_frz\tclustered_depth_vcf_index_frz\tclustered_manta_vcf_frz\tclustered_manta_vcf_index_frz\tclustered_scramble_vcf_frz\tclustered_scramble_vcf_index_frz\tclustered_wham_vcf_frz\tclustered_wham_vcf_index_frz\tmerged_PE_frz\tmerged_PE_index_frz\tmerged_SR_frz\tmerged_SR_index_frz\tmerged_bincov_frz\tmerged_bincov_index_frz\tmerged_BAF_frz\tmerged_BAF_index_frz\tmedian_cov_frz\nall_samples\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_depth_vcf.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_depth_vcf_index.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_manta_vcf.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_manta_vcf_index.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_scramble_vcf.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_scramble_vcf_index.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_wham_vcf.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.clustered_wham_vcf_index.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_PE.gz\tgs://fc-sandbox-bucket/frozen-baseline/merged_PE_index__all_samples.merged_PE_index.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_SR.gz\tgs://fc-sandbox-bucket/frozen-baseline/merged_SR_index__all_samples.merged_PE_index.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_bincov.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_bincov_index.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_BAF.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.merged_BAF_index.gz\tgs://fc-sandbox-bucket/frozen-baseline/all_samples.median_cov.gz\n",
 "manifest_keys": [
  "dest_prefix",
  "mismatches",
  "objects",
  "total_bytes",
  "verified"
 ]
}''')


# ------------------------------------------------------------------ genotyping: unchanged

def test_genotyping_output_unchanged(rig):
    got = capture_genotyping(rig)
    for key in ("plan", "copy", "attrs --allow-unverified", "copy --write", "verify", "tsv"):
        want = GENO_BEFORE[key]
        assert (list(got[key]) if isinstance(got[key], tuple) else got[key]) == want, key
    # The manifest gains the source run and entity; every key it had is still there.
    assert set(GENO_BEFORE["manifest_keys"]) <= set(got["manifest_keys"])


def test_genotyping_profile_carries_the_old_attribute_list():
    sys.path.insert(0, str(ROOT / "kit"))
    import module_profile
    spec = module_profile.load("genotyping").freeze
    assert spec["attrs"] == GENO_ATTRS
    assert (spec["entity_type"], spec["row_source"], spec["row_mode"], spec["tag"]) == \
        ("sample_set", "BATCH", "merge", "")


# ------------------------------------------------------------------ single sample

CRAM = "gs://fc-baseline/crams/HG00096.final.cram"
STRIPY = "gs://fc-baseline/stripy/HG00096.stripy.json"


def sample_dump(rig, participant=None, stripy=True):
    """recon.py's own dump shape, with one sample row (and a sample_set row that must be ignored)."""
    attrs = {"bam_or_cram_file": CRAM, "coverage_counts": "gs://fc-baseline/x.counts.tsv.gz"}
    if stripy:
        attrs["case_stripy_file"] = STRIPY
    if participant:
        attrs["participant"] = {"entityType": "participant", "entityName": participant}
    dump = {"sample": {"total": 2, "sample": [
                {"name": "HG00097", "attributes": {"bam_or_cram_file": "gs://fc-baseline/other.cram"}},
                {"name": "HG00096", "attributes": attrs}]},
            "sample_set": {"total": 1, "sample": [{"name": "all_samples", "attributes": {}}]}}
    (rig.work / "recon" / "baseline_entities.json").write_text(json.dumps(dump))
    rig.put(CRAM, crc="cram==", size=30 * 2**30)
    rig.put(CRAM + ".crai", crc="crai==", size=2_000_000)
    rig.put(STRIPY, crc="stripy==", size=5000)


SS = ["--module", "single_sample", "--entity", "HG00096"]
DEST = "gs://%s/frozen-baseline/single_sample/HG00096" % BUCKET
SAMPLE_TSV = ("entity:sample_id\tbam_or_cram_file\tcase_stripy_file\n"
              "HG00096\t%s/HG00096.final.cram\t%s/HG00096.stripy.json\n" % (DEST, DEST))


def test_single_sample_plan(rig):
    sample_dump(rig)
    r = rig.run("plan", *SS)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "bam_or_cram_file           %s\n" % CRAM in out
    assert "-> %s/HG00096.final.cram\n" % DEST in out
    assert "-> %s/HG00096.final.cram.crai\n" % DEST in out          # the derived index travels along
    assert "-> %s/HG00096.stripy.json\n" % DEST in out
    assert "\n3 objects\n" in out
    assert "entity:sample_id\tbam_or_cram_file\tcase_stripy_file" in out   # plan shows the TSV
    assert "baseline_frozen_inputs.single_sample.json" in out
    assert not [c for c in rig.gsutil_calls() if "cp" in c]
    assert not rig.terra_calls("upload")


def test_single_sample_plan_skips_missing_optional(rig):
    sample_dump(rig, stripy=False)
    r = rig.run("plan", *SS)
    assert r.returncode == 0, r.stderr
    assert "not frozen: case_stripy_file" in r.stdout
    assert "\n2 objects\n" in r.stdout


def test_sample_row_tsv(rig):
    sample_dump(rig)
    r = rig.run("attrs", *SS, "--allow-unverified")
    assert r.returncode == 0, r.stderr
    assert (rig.work / "staging" / "frozen_baseline_attrs.single_sample.tsv").read_text() == SAMPLE_TSV
    assert not (rig.work / "staging" / "frozen_baseline_participant.single_sample.tsv").exists()
    assert "dry run: re-run with --write to upload" in r.stdout
    assert not rig.terra_calls("upload")


def test_sample_row_with_participant_writes_participant_first(rig):
    sample_dump(rig, participant="P96")
    r = rig.run("attrs", *SS, "--allow-unverified", "--write")
    assert r.returncode == 0, r.stderr
    ups = rig.terra_calls("upload")
    assert [u["text"] for u in ups] == [
        "entity:participant_id\nP96\n",
        "entity:sample_id\tparticipant\tbam_or_cram_file\tcase_stripy_file\n"
        "HG00096\tP96\t%s/HG00096.final.cram\t%s/HG00096.stripy.json\n" % (DEST, DEST)]
    assert all((u["ns"], u["ws"]) == ("sand-ns", "sand-ws") for u in ups)
    assert "undo:" in r.stdout


def test_entity_row_source_refusals(rig):
    sample_dump(rig)
    r = rig.run("plan", "--module", "single_sample")
    assert r.returncode == 2 and "--entity ROW" in r.stderr
    rig.geno_dump()
    r = rig.run("plan", "--entity", "all_samples")
    assert r.returncode == 2 and "GSVTK_BATCH" in r.stderr
    r = rig.run("plan", *SS[:2], "--entity", "NOPE")
    assert r.returncode == 1 and "no baseline sample row 'NOPE'" in r.stderr
    r = rig.run("plan", "--bogus")
    assert r.returncode == 2 and "unknown argument" in r.stderr


# ------------------------------------------------------------------ write gating

def test_copy_needs_write(rig):
    sample_dump(rig)
    r = rig.run("copy", *SS)
    assert r.returncode == 0, r.stderr
    assert "dry run: nothing was copied" in r.stdout
    assert not [c for c in rig.gsutil_calls() if "cp" in c]
    r = rig.run("copy", *SS, "--write")
    assert r.returncode == 0, r.stderr
    objs = rig.objects()
    for leaf in ("HG00096.final.cram", "HG00096.final.cram.crai", "HG00096.stripy.json"):
        assert "%s/%s" % (DEST, leaf) in objs
    assert "undo: gsutil -m rm -r %s/" % DEST in r.stdout


def test_attrs_write_requires_passed_verify(rig):
    sample_dump(rig)
    r = rig.run("attrs", *SS, "--write")
    assert r.returncode == 1 and "no frozen-input manifest" in r.stderr
    assert not rig.terra_calls("upload")


def test_full_single_sample_freeze_records_manifest(rig, tmp_path):
    sample_dump(rig)
    assert rig.run("copy", *SS, "--write").returncode == 0
    r = rig.run("verify", *SS)
    assert r.returncode == 2 and "names the run it came from" in r.stderr    # provenance required
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps({
        "id": "wf-1111", "workflowName": "GATKSVPipelineSingleSample",
        "workflowRoot": "gs://fc-baseline/submissions/0123abcd-0000-1111-2222-333344445555/"
                        "GATKSVPipelineSingleSample/wf-1111/",
        "calls": {"GATKSVPipelineSingleSample.GatherSampleEvidence": [{
            "subWorkflowMetadata": {"calls": {"GatherSampleEvidence.Manta": [{
                "dockerImageUsed": "us.gcr.io/x/manta@sha256:abc"}]}}}]}}))
    r = rig.run("verify", *SS, "--metadata", meta, "--method-version", "v1.1.1")
    assert r.returncode == 0, r.stderr
    man = json.loads((rig.work / "manifests" / "baseline_frozen_inputs.single_sample.json").read_text())
    assert man["verified"] is True and man["module"] == "single_sample"
    assert man["entity"] == {"type": "sample", "row": "HG00096", "attr_suffix": "",
                             "row_mode": "create"}
    run = man["source_run"]
    assert run["submission_id"] == "0123abcd-0000-1111-2222-333344445555"
    assert (run["workflow_id"], run["method"], run["method_version"]) == \
        ("wf-1111", "GATKSVPipelineSingleSample", "v1.1.1")
    assert run["images"] == {"GATKSVPipelineSingleSample.GatherSampleEvidence/GatherSampleEvidence.Manta":
                             "us.gcr.io/x/manta@sha256:abc"}
    assert run["unknown"] == []
    assert "profile_image_pins" in run
    pinned = {o["src"]: (o["bytes"], o["crc32c_src"], o["dest"]) for o in man["objects"]}
    assert pinned[CRAM] == (30 * 2**30, "cram==", DEST + "/HG00096.final.cram")
    assert pinned[CRAM + ".crai"][1] == "crai=="
    # Verified: attrs --write now uploads the sample row, into the sandbox only.
    r = rig.run("attrs", *SS, "--write")
    assert r.returncode == 0, r.stderr
    assert [u["text"] for u in rig.terra_calls("upload")] == [SAMPLE_TSV]
    # A verdict for HG00096 is not a verdict for another row.
    r = rig.run("attrs", "--module", "single_sample", "--entity", "HG00097", "--write")
    assert r.returncode == 1 and "Re-run `verify` for this row" in r.stderr
    assert len(rig.terra_calls("upload")) == 1


def test_baseline_workspace_is_refused(rig):
    sample_dump(rig)
    r = rig.run("attrs", *SS, "--allow-unverified", "--write",
                GSVTK_TERRA_NAMESPACE="base-ns", GSVTK_TERRA_WORKSPACE="base-ws")
    assert r.returncode != 0 and "baseline workspace" in r.stderr
    assert not rig.terra_calls("upload")


# ------------------------------------------------------------------ read-only

@pytest.mark.parametrize("mode", ["copy", "attrs"])
def test_read_only_refuses_writes(rig, mode):
    sample_dump(rig)
    extra = ["--allow-unverified"] if mode == "attrs" else []
    r = rig.run(mode, *SS, *extra, "--write", GSVTK_READ_ONLY="1")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "REFUSED" in r.stderr and "GSVTK_READ_ONLY" in r.stderr
    assert not [c for c in rig.gsutil_calls() if "cp" in c]
    assert not rig.terra_calls("upload")
    assert not rig.terra_calls("assert_writable_target")


def test_read_only_still_plans(rig):
    sample_dump(rig)
    r = rig.run("plan", *SS, GSVTK_READ_ONLY="1")
    assert r.returncode == 0 and "3 objects" in r.stdout


# ------------------------------------------------------------------ crc32c / size mismatch

@pytest.mark.parametrize("field,value", [("crc32c", "corrupt=="), ("bytes", 7)])
def test_mismatch_refuses_verify_and_publish(rig, field, value):
    sample_dump(rig)
    assert rig.run("copy", *SS, "--write").returncode == 0
    s = rig.objects()
    s[DEST + "/HG00096.final.cram.crai"][field] = value
    rig.store.write_text(json.dumps(s))
    r = rig.run("verify", *SS, "--submission", "sub-1")
    assert r.returncode == 1 and "mismatch on 1 objects" in r.stderr
    assert "DIFF bam_or_cram_file.crai" in r.stdout
    man = json.loads((rig.work / "manifests" / "baseline_frozen_inputs.single_sample.json").read_text())
    assert man["verified"] is False and man["mismatches"][0]["attr"] == "bam_or_cram_file.crai"
    r = rig.run("attrs", *SS, "--write")
    assert r.returncode == 1 and "records no PASSED crc32c verify" in r.stderr
    assert not rig.terra_calls("upload")


def test_genotyping_crc_mismatch_refused(rig):
    rig.geno_dump()
    assert rig.run("copy", "--write").returncode == 0
    s = rig.objects()
    s["gs://%s/frozen-baseline/all_samples.median_cov.gz" % BUCKET]["crc32c"] = "bad=="
    rig.store.write_text(json.dumps(s))
    r = rig.run("verify")
    assert r.returncode == 1 and "DIFF median_cov" in r.stdout
    r = rig.run("attrs", "--write")
    assert r.returncode == 1 and not rig.terra_calls("upload")


# ------------------------------------------------------------------ --objects (sv-shell)

def svshell_objects(rig, tmp_path, source_run=True):
    vcf = "gs://fc-runs/svshell/run-7/HG00096.final.vcf.gz"
    objs = {"final_vcf": vcf, "final_vcf_index": vcf + ".tbi", "input_cram": CRAM}
    for i, u in enumerate([vcf, vcf + ".tbi", CRAM, CRAM + ".crai"]):
        rig.put(u, crc="c%d==" % i, size=100 + i)
    doc = {"name": "sv_shell", "objects": objs}
    if source_run:
        doc["source_run"] = {"run_id": "run-7", "method": "single_sample_pipeline.sh",
                             "images": {"sv-shell": "us.gcr.io/x/sv-shell@sha256:feed"}}
    f = tmp_path / "svshell.json"
    f.write_text(json.dumps(doc))
    return f


def test_objects_freeze_for_sv_shell(rig, tmp_path):
    f = svshell_objects(rig, tmp_path)
    dest = "gs://%s/frozen-baseline/sv_shell" % BUCKET
    r = rig.run("plan", "--objects", f)
    assert r.returncode == 0, r.stderr
    assert "-> %s/HG00096.final.vcf.gz\n" % dest in r.stdout
    assert "-> %s/HG00096.final.cram.crai\n" % dest in r.stdout
    assert "\n4 objects\n" in r.stdout and "entity:" not in r.stdout
    assert rig.run("copy", "--objects", f, "--write").returncode == 0
    r = rig.run("verify", "--objects", f)
    assert r.returncode == 0, r.stderr
    man = json.loads((rig.work / "manifests" / "baseline_frozen_inputs.sv_shell.json").read_text())
    assert man["verified"] and man["entity"] is None and len(man["objects"]) == 4
    assert man["source_run"]["run_id"] == "run-7"
    assert man["source_run"]["images"] == {"sv-shell": "us.gcr.io/x/sv-shell@sha256:feed"}
    r = rig.run("attrs", "--objects", f, "--write")
    assert r.returncode == 2 and "no entity row" in r.stderr
    assert not rig.terra_calls("upload")


def test_objects_refusals(rig, tmp_path):
    f = svshell_objects(rig, tmp_path, source_run=False)
    assert rig.run("copy", "--objects", f, "--write").returncode == 0
    r = rig.run("verify", "--objects", f)
    assert r.returncode == 2 and "source_run" in r.stderr
    r = rig.run("plan", "--objects", f, "--module", "single_sample")
    assert r.returncode == 2 and "exclusive" in r.stderr
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"name": "x", "objects": {}}))
    r = rig.run("plan", "--objects", empty)
    assert r.returncode == 2 and "empty freeze" in r.stderr


# ------------------------------------------------------------------ the profile field

def test_profile_without_freeze_exits_4(rig, tmp_path):
    d = tmp_path / "profiles"
    d.mkdir()
    doc = json.loads((ROOT / "profiles" / "genotyping.json").read_text())
    for k in [k for k in doc if k == "freeze" or k.startswith("_why_freeze")]:
        doc.pop(k)
    (d / "genotyping.json").write_text(json.dumps(doc))
    rig.geno_dump()
    r = rig.run("plan", GSVTK_MODULE_DIR=str(d))
    assert r.returncode == 4 and "no `freeze` block" in r.stderr


def test_freeze_block_validation():
    sys.path.insert(0, str(ROOT / "kit"))
    import module_profile
    doc = json.loads((ROOT / "profiles" / "single_sample.json").read_text())
    assert module_profile.validate(doc) == []

    def problems(**change):
        d = json.loads(json.dumps(doc))
        for k, v in change.items():
            if v is None:
                d["freeze"].pop(k)
            else:
                d["freeze"][k] = v
        return "\n".join(module_profile.validate(d))

    assert "unknown field 'atrs'" in problems(atrs=["x"])
    assert "missing 'row_mode'" in problems(row_mode=None)
    assert "which no step runs on" in problems(entity_type="participant")
    assert "no sample step reads `this.coverage_counts`" in problems(attrs=["coverage_counts"])
    assert "bare row attributes" in problems(attrs=["this.bam_or_cram_file"])
    assert "attr_suffix" in problems(attr_suffix="{new}")
    assert "row_mode" in problems(row_mode="replace")
    assert "tag" in problems(tag="a/b")
    assert "both attrs and optional_attrs" in problems(optional_attrs=["bam_or_cram_file"])
    assert "not a key this repo's config layer knows" in problems(entity_row="SAMPLE")
    d = json.loads(json.dumps(doc))
    d["freeze"]["_why_nothing"] = "orphan"
    assert "does not set" in "\n".join(module_profile.validate(d))
