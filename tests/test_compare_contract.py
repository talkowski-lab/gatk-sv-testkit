"""Comparator exit-code contract: 0 same, 1 differences, 2 nothing compared."""
import pytest


def write(d, name, text):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


def test_batch_tables_nothing_compared_exits_2(tmp_path, run_tool):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    r = run_tool("compare/compare_batch_tables.py", "--baseline-dir", tmp_path / "a",
                 "--new-dir", tmp_path / "b")
    assert r.returncode == 2, r.stdout + r.stderr


@pytest.mark.parametrize("new_value, want", [("5", 0), ("6", 1)])
def test_batch_tables_delta_exits_1(tmp_path, run_tool, new_value, want):
    write(tmp_path / "a", "GenotypeBatch.b.metrics.tsv", "count\t5\n")
    write(tmp_path / "b", "GenotypeBatch.b.metrics.tsv", f"count\t{new_value}\n")
    r = run_tool("compare/compare_batch_tables.py", "--baseline-dir", tmp_path / "a",
                 "--new-dir", tmp_path / "b")
    assert r.returncode == want, r.stdout + r.stderr


VCF = """##fileformat=VCFv4.2
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1
"""


def test_concordance_empty_side_is_nothing_compared(tmp_path, run_tool):
    pytest.importorskip("pysam")
    a = tmp_path / "a.vcf"
    a.write_text(VCF)
    b = tmp_path / "b.vcf"
    b.write_text(VCF + "chr1\t1\tv1\tN\t<DEL>\t.\tPASS\t.\tGT\t0/1\n")
    r = run_tool("compare/pair_level_concordance.py", a, b)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "Traceback" not in r.stderr
