# Worked example: what the table differ prints

A real capture from one head-to-head: the v1.1.1 baseline on the left, a branch
trainer on the right. Produced by `compare/compare_batch_tables.py` (stdlib only,
read-only, no network, no Terra). Your columns and values will differ; the verdict
vocabulary is the point -- MATCH / DELTA / MISSING / STRATEGY, where STRATEGY means
this row changed by design and must never be diffed as a bare number.

```
python compare/compare_batch_tables.py \
    --baseline-dir work/staging --new-dir work/runs/train_definitive --out-prefix /tmp/tbl
```

Sides consumed: baseline `work/staging/all_samples.{sr,pe}_metric_file.txt`,
`all_samples.depth.depth_sepcutoff.txt`, `all_samples.pesr.pesr_sepcutoff.txt`,
`GenotypeBatch.all_samples.metrics.tsv`, `all_samples.cutoffs`; new
`work/runs/train_definitive/all_samples.{sr,pe}_geno_params.tsv`,
`all_samples.rd_{depth,pesr}_geno_params.tsv`, `all_samples.sr_cutoff_diagnostics.txt`.
Batch metrics exist only on the baseline side -> that surface reports one `MISSING` row.

Reading: `sr_count` and the rare/common bin edges reproduce exactly; `median_hom`/`sd_het` are real
DELTAs for both evidence types; RD state-0 mean/sd agree to ~13 s.f. (MATCH) while the state-1/2 cutoff
rows differ (0.615152 vs 0.6151515..., 0.703763604621896 vs 0.7011975175605271); the four SR frequency
cutoffs are labelled STRATEGY (v1.1.1 fitted loose-single/strict-both on purpose); SR_sum_log_pval /
PE_log_pval vs SRQ / PEQ are never diffed numerically because the new metric is GATK QUAL =
-10*log10(p) -- the 10x-reconciled check prints in NOTES instead.

## stdout

```text
discovered sr_params      baseline=work/staging/all_samples.sr_metric_file.txt new=work/runs/train_definitive/all_samples.sr_geno_params.tsv
discovered pe_params      baseline=work/staging/all_samples.pe_metric_file.txt new=work/runs/train_definitive/all_samples.pe_geno_params.tsv
discovered rd_depth       baseline=work/staging/all_samples.depth.depth_sepcutoff.txt new=work/runs/train_definitive/all_samples.rd_depth_geno_params.tsv
discovered rd_pesr        baseline=work/staging/all_samples.pesr.pesr_sepcutoff.txt new=work/runs/train_definitive/all_samples.rd_pesr_geno_params.tsv
discovered batch_metrics  baseline=work/staging/GenotypeBatch.all_samples.metrics.tsv new=MISSING
discovered rf_cutoffs     baseline=work/staging/all_samples.cutoffs            new=MISSING
discovered sr_diagnostics baseline=MISSING                                        new=work/runs/train_definitive/all_samples.sr_cutoff_diagnostics.txt

== sr_params: 15 row(s)
surface      param            baseline    new                       abs_diff       rel_pct  verdict
sr_params    sr_count         10          10                               0             0  MATCH
sr_params    median_hom       78          69                               9   11.53846154  DELTA
sr_params    sd_het           26.8276     21.949892999999996        4.877707   18.18167484  DELTA
sr_params    rare_min         0           0                                0             0  MATCH
sr_params    rare_max         2           2                                0             0  MATCH
sr_params    common_min       2           2                                0             0  MATCH
sr_params    common_max       156         156                              0             0  MATCH
sr_params    rare_pass        -           11131                            -             -  MISSING
sr_params    rare_fail        -           36328                            -             -  MISSING
sr_params    common_pass      -           151701                           -             -  MISSING
sr_params    common_fail      -           1097543                          -             -  MISSING
sr_params    rare_single      .1          0.2                            0.1            50  STRATEGY
sr_params    rare_both        .6          0.6000000000000001    1.110223025e-16  1.850371708e-14  STRATEGY
sr_params    common_single    .9          1                              0.1            10  STRATEGY
sr_params    common_both      .9          0.6000000000000001             0.3   33.33333333  STRATEGY

== pe_params: 3 row(s)
surface      param         baseline    new                       abs_diff       rel_pct  verdict
pe_params    pe_count      8           7                                1          12.5  DELTA
pe_params    median_hom    76          71                               5   6.578947368  DELTA
pe_params    sd_het        24.3888     21.949892999999996        2.438907   10.00011071  DELTA

== rd_depth: 15 row(s)
surface     param        baseline               new                         abs_diff       rel_pct  verdict
rd_depth    0.cutoffs    0.106875141793862      0.1077624262612434      0.0008872844674  0.8233709078  DELTA
rd_depth    0.mean       0.0454528152647421     0.045452815264742       9.714451465e-17  2.137260675e-13  MATCH
rd_depth    0.sd         0.00630194386003265    0.006301943860032662    1.214306433e-17  1.926875993e-13  MATCH
rd_depth    1.cutoffs    0.615152               0.615151515151515       4.84848485e-07  7.88176719e-05  DELTA
rd_depth    1.mean       0.502752538010007      0.502521357587388       0.0002311804226  0.04598294492  DELTA
rd_depth    1.sd         0.0406171056582705     0.03992560029950335     0.0006915053588    1.70249787  DELTA
rd_depth    2.cutoffs    1.38485                1.384848484848485       1.515151515e-06  0.0001094090707  DELTA
rd_depth    2.mean       1.00804145738871       1.0078545635382714      0.0001868938504  0.01854029406  DELTA
rd_depth    2.sd         0.0614836083585576     0.0616252430507473      0.0001416346922  0.2298322654  DELTA
rd_depth    3.cutoffs    1.77475101492298       1.7760892932431183      0.00133827832  0.0753497206  DELTA
rd_depth    3.mean       1.54074181741101       1.5403906799503697      0.0003511374606  0.0227901558  DELTA
rd_depth    3.sd         0.0769897035283469     0.07760372042308546     0.0006140168947   0.791220951  DELTA
rd_depth    4.cutoffs    2.25                   2.25                               0             0  MATCH
rd_depth    4.mean       2.07940143317286       2.077874824073739       0.001526609099  0.07341579527  DELTA
rd_depth    4.sd         0.100230869684706      0.09936282456283949     0.0008680451219  0.8660456849  DELTA

== rd_pesr: 15 row(s)
surface    param        baseline               new                         abs_diff       rel_pct  verdict
rd_pesr    0.cutoffs    0.106875141793862      0.1077624262612434      0.0008872844674  0.8233709078  DELTA
rd_pesr    0.mean       0.0454528152647421     0.045452815264742       9.714451465e-17  2.137260675e-13  MATCH
rd_pesr    0.sd         0.00630194386003265    0.006301943860032662    1.214306433e-17  1.926875993e-13  MATCH
rd_pesr    1.cutoffs    0.703763604621896      0.7011975175605271      0.002566087061  0.3646234395  DELTA
rd_pesr    1.mean       0.502752538010007      0.502521357587388       0.0002311804226  0.04598294492  DELTA
rd_pesr    1.sd         0.0406171056582705     0.03992560029950335     0.0006915053588    1.70249787  DELTA
rd_pesr    2.cutoffs    1.24456602563782       1.2435646254993695      0.001001400138  0.08046179293  DELTA
rd_pesr    2.mean       1.00804145738871       1.0078545635382714      0.0001868938504  0.01854029406  DELTA
rd_pesr    2.sd         0.0614836083585576     0.0616252430507473      0.0001416346922  0.2298322654  DELTA
rd_pesr    3.cutoffs    1.77475101492298       1.7760892932431183      0.00133827832  0.0753497206  DELTA
rd_pesr    3.mean       1.54074181741101       1.5403906799503697      0.0003511374606  0.0227901558  DELTA
rd_pesr    3.sd         0.0769897035283469     0.07760372042308546     0.0006140168947   0.791220951  DELTA
rd_pesr    4.cutoffs    2.25                   2.25                               0             0  MATCH
rd_pesr    4.mean       2.07940143317286       2.077874824073739       0.001526609099  0.07341579527  DELTA
rd_pesr    4.sd         0.100230869684706      0.09936282456283949     0.0008680451219  0.8660456849  DELTA

== batch_metrics: 1 row(s)
surface          param                         baseline    new           abs_diff       rel_pct  verdict
batch_metrics    batch_metrics: no new file    -           -                    -             -  MISSING

== rd_separation: 2 row(s)
surface          param                                                        baseline             new                      abs_diff       rel_pct  verdict
rd_separation    pesr_min_separation (v1.1.1 TrainRDGenotyping selection)     0.203639398099545    0.203639398099545               0             0  MATCH
rd_separation    depth_min_separation (v1.1.1 TrainRDGenotyping selection)    0.384848484848485    0.384848484848485               0             0  MATCH

== qc_scale: 2 row(s)
surface     param                                                baseline              new            abs_diff       rel_pct  verdict
qc_scale    PE_log_pval -> PEQ (GATK QUAL = -10*log10(p))        3.4743558552260145    34.7436               -             -  STRATEGY
qc_scale    SR_sum_log_pval -> SRQ (GATK QUAL = -10*log10(p))    4.664513885836166     46.6451               -             -  STRATEGY

== NOTES
  NOTE: SRQ/PEQ are GATK QUAL = -10*log10(p); v1.1.1 SR_sum_log_pval/PE_log_pval are -log10(p), i.e. a 10x scale change on top of the rename -- never diffed as raw numbers
  PE_log_pval*10 == PEQ? True (34.743559 vs 34.743600; tolerance 0.0001 relative = print precision)
  SR_sum_log_pval*10 == SRQ? True (46.645139 vs 46.645100; tolerance 0.0001 relative = print precision)

== SUMMARY  MATCH=13  DELTA=29  STRATEGY=6  MISSING=5
wrote 53 rows -> /tmp/tbl.tsv

```

## head -40 /tmp/tbl*.tsv

```text
surface	param	baseline	new	abs_diff	rel_pct	verdict
sr_params	sr_count	10	10	0	0	MATCH
sr_params	median_hom	78	69	9	11.53846154	DELTA
sr_params	sd_het	26.8276	21.949892999999996	4.877707	18.18167484	DELTA
sr_params	rare_min	0	0	0	0	MATCH
sr_params	rare_max	2	2	0	0	MATCH
sr_params	common_min	2	2	0	0	MATCH
sr_params	common_max	156	156	0	0	MATCH
sr_params	rare_pass	-	11131	-	-	MISSING
sr_params	rare_fail	-	36328	-	-	MISSING
sr_params	common_pass	-	151701	-	-	MISSING
sr_params	common_fail	-	1097543	-	-	MISSING
sr_params	rare_single	.1	0.2	0.1	50	STRATEGY
sr_params	rare_both	.6	0.6000000000000001	1.110223025e-16	1.850371708e-14	STRATEGY
sr_params	common_single	.9	1	0.1	10	STRATEGY
sr_params	common_both	.9	0.6000000000000001	0.3	33.33333333	STRATEGY
pe_params	pe_count	8	7	1	12.5	DELTA
pe_params	median_hom	76	71	5	6.578947368	DELTA
pe_params	sd_het	24.3888	21.949892999999996	2.438907	10.00011071	DELTA
rd_depth	0.cutoffs	0.106875141793862	0.1077624262612434	0.0008872844674	0.8233709078	DELTA
rd_depth	0.mean	0.0454528152647421	0.045452815264742	9.714451465e-17	2.137260675e-13	MATCH
rd_depth	0.sd	0.00630194386003265	0.006301943860032662	1.214306433e-17	1.926875993e-13	MATCH
rd_depth	1.cutoffs	0.615152	0.615151515151515	4.84848485e-07	7.88176719e-05	DELTA
rd_depth	1.mean	0.502752538010007	0.502521357587388	0.0002311804226	0.04598294492	DELTA
rd_depth	1.sd	0.0406171056582705	0.03992560029950335	0.0006915053588	1.70249787	DELTA
rd_depth	2.cutoffs	1.38485	1.384848484848485	1.515151515e-06	0.0001094090707	DELTA
rd_depth	2.mean	1.00804145738871	1.0078545635382714	0.0001868938504	0.01854029406	DELTA
rd_depth	2.sd	0.0614836083585576	0.0616252430507473	0.0001416346922	0.2298322654	DELTA
rd_depth	3.cutoffs	1.77475101492298	1.7760892932431183	0.00133827832	0.0753497206	DELTA
rd_depth	3.mean	1.54074181741101	1.5403906799503697	0.0003511374606	0.0227901558	DELTA
rd_depth	3.sd	0.0769897035283469	0.07760372042308546	0.0006140168947	0.791220951	DELTA
rd_depth	4.cutoffs	2.25	2.25	0	0	MATCH
rd_depth	4.mean	2.07940143317286	2.077874824073739	0.001526609099	0.07341579527	DELTA
rd_depth	4.sd	0.100230869684706	0.09936282456283949	0.0008680451219	0.8660456849	DELTA
rd_pesr	0.cutoffs	0.106875141793862	0.1077624262612434	0.0008872844674	0.8233709078	DELTA
rd_pesr	0.mean	0.0454528152647421	0.045452815264742	9.714451465e-17	2.137260675e-13	MATCH
rd_pesr	0.sd	0.00630194386003265	0.006301943860032662	1.214306433e-17	1.926875993e-13	MATCH
rd_pesr	1.cutoffs	0.703763604621896	0.7011975175605271	0.002566087061	0.3646234395	DELTA
rd_pesr	1.mean	0.502752538010007	0.502521357587388	0.0002311804226	0.04598294492	DELTA
rd_pesr	1.sd	0.0406171056582705	0.03992560029950335	0.0006915053588	1.70249787	DELTA
```
