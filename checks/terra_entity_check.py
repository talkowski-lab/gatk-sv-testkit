#!/usr/bin/env python3
"""terra_entity_check.py — for every Terra launch config at a ref: which entity does it run against,
and does a shipped entity table actually carry every attribute it reads?

Why this exists
---------------
`docs/plan-launch-any-module.md` Stage A needs one answer before any module outside the batch chain can
be launched: the entity type a method config runs against. A wrong type is not a loud failure —
`terra/steps.py` already says a wrong type "resolves every `this.*` binding to nothing at runtime, after
the VMs booted". Three candidate sources were measured; two are dead:

  * the WDL: `git grep -l 'this\\.' -- 'wdl/*.wdl'` at gatk-sv `e1909d2f` returns **0 files**. `this.<x>`
    is Terra's entity-passing syntax; it lives in method configs and input templates, never in a WDL.
  * the member rule (`this.<collection>.<attr>` names the type): dead. `MergeBatchSites.json.tmpl` reads
    `${this.sample_set_set_id}` once and `${this.sample_sets.<attr>}` three times. The member rule
    derives `sample_sets`; the row is a `sample_set_set`. Deriving from the member collection is how a
    wrong type gets printed with confidence.
  * the **name key** (`${this.<etype>_id}`, one path component): holds, and this tool measures it per ref
    instead of quoting the plan.

So the rule is the name key, and it refuses rather than guessing.

What is looked at, and how it is read
------------------------------------
Corpus: every `*.json.tmpl` under `inputs/templates/terra_workspaces/**` at the ref. Not `wdl/*.wdl`,
and nothing is assumed about the layout inside that directory: the cohort flavor keeps its configs under
`workflow_configurations/` (write-back configs one level deeper), while the single-sample config sits
directly in its flavor directory. The flavor is read off the path and printed, never used to pick a rule.

The tree is read with `git archive` into a temp directory — the discipline of
`checks/wdl_inputs_check.py::render()` and `terra/batch_configs.py::_tree_from_ref()`: `git archive`
cannot touch a checkout's working tree, index or HEAD, and nothing here writes to a checkout. `--tree`
takes an already-materialized tree instead, which is how the offline selftest grades this file.

The templates are JSON with `${...}` and `{{ ... }}` inside strings. `terra/batch_configs.py` already
carries the one substitution that survives them (`JINJA_RE`: a `{{ ... }}` expression becomes `null`,
because rendering would need jinja2 *and* upstream's `inputs/values/**` bundle, which answers "what did
that values profile bind" instead of "what does this ref bind"). This file defines no second pattern: it
reads `JINJA_RE` out of that source with `ast` (`_shared_expander()`), so §10's "one expander per kind"
stays true by construction — one definition in the repo, and a missing one is a prerequisite refusal.

The four questions, and the refusal each one can print
------------------------------------------------------
1. **workflow** — the prefix of the binding keys (`"MergeBatchSites.cohort"` → `MergeBatchSites`), not
   the file stem. Those differ for real: `output_configurations/MergeBatchSitesOutputs.json.tmpl` binds
   `MergeBatchSites.*`. Both print (`wf=` and `filestem=`) so the mismatch is visible.
2. **name key(s)** — every read `${this.<etype>_id>` whose path is ONE component. A two-component read is
   a member read, never a name key: `CombineBatches.json.tmpl` reads `${this.sample_set_set_id}` and
   `${this.sample_sets.sample_set_id}`, and counting the second would turn a single-answer config into
   "many". Zero name keys, or two distinct `<etype>`s, refuse. `${this.id}` (nothing before `_id`)
   carries no type, so it is not a name key either, and the zero-name-key reason names it.
3. **member collections** — the `<collection>` of each `${this.<collection>.<attr>}`. The collection NAME
   is checked against the derived table (it must be a column of the root row); the member ATTRIBUTE is
   reported and counted but NOT checked, because checking it needs the member entity type, and
   plural-to-singular is a guess. That is the same refusal `terra/steps.py` makes.
4. **the shipped-table cross-check** — the derived `<etype>` must be the type of a TSV in the corpus whose
   first header column is `entity:<etype>_id` or `membership:<etype>_id`, and every single-component
   `${this.<attr>}` and every member collection must be a column of it (`<etype>_id` itself counts: it is
   the name column). Tables match by TYPE across the whole corpus and the covering table's path prints, so
   "which file answered, and is it this flavor's own" is readable from the output. One table must cover
   ALL the reads, because one table is what gets imported: two tables of the same type each lacking a
   different column is a refusal naming both gaps, not a pass by union. `${workspace.*}` bindings are out
   of this question — the section below reads the table they name, and still resolves none of them.

Refusal precedence is fixed, because the first failure makes the later ones unanswerable:
`cannot-parse` → `no-name-key` / `many-name-keys` → `no-shipped-table` → `columns-missing`.

The workspace attribute table, and the two honest words it prints
----------------------------------------------------------------
A `${workspace.X}` binding names a workspace attribute, not an entity column, so it belongs to none of the
four questions above — and it is 304 of the 597 top-level binding values at gatk-sv `e1909d2f`. The offline
oracle for it is upstream's own table: `inputs/templates/terra_workspaces/<flavor>/workspace.tsv.tmpl` at
the ref being launched, whose FIRST COLUMN is the set of attribute names that flavor declares. A binding to
a name with no row there is a plan-time error; the alternative is a submitted job that discovers the missing
input after the VMs booted. This layer reads that table and reports what it declares. It classifies no
binding, changes no exit code, and refuses nothing by itself — the value-provenance layer under it does.

  * **Which file.** The oracle is a file named exactly `workspace.tsv.tmpl` that is a DIRECT CHILD of the
    flavor directory. Any other `*.tsv.tmpl` is not the oracle: upstream ships `sample.tsv.tmpl`,
    `participant.tsv.tmpl` and `sample_set_membership_1kgp.tsv.tmpl` beside it, and `read_tables()` above
    already excludes them all from the ENTITY tables — that exclusion is untouched here. When the flavor
    has no such file the answer is `ABSENT`, and the TSVs that flavor DOES ship are printed next to it, so
    an operator can tell "this flavor ships no tables" from "its table is named almost right" from "its
    table sits one directory down". A near-miss name is named, never read.
  * **Which flavors.** The flavor list comes from where the CONFIGS live, not from where tables were found.
    A reader that enumerated tables could only ever report flavors that have one, and `ABSENT` — the answer
    that matters, because it is the one that says nothing in that flavor can be checked — would be
    unreachable. A check that can never fire is not a check.
  * **What a name is.** The first tab-separated field, with a leading `workspace:` stripped when present,
    then whitespace-stripped. The strip ignores row position on purpose: both real tables carry the prefix
    on line 1 and NOWHERE ELSE (75 and 97 rows carry none), so a reader that special-cased row 1 would work
    today and break the moment somebody reorders a table. A name must be `[A-Za-z0-9_.]+` or it is a
    `MALFORMED` finding printed with the row text; a name appearing twice in one table is a `DUPE` finding
    printed with its row numbers. Nothing is lowercased, silently deduplicated or repaired. A row carrying
    more than two tab-separated fields is reported as `MALFORMED` while still declaring its first field — a
    stray tab in the value half is not the same fact as "this flavor does not declare that attribute", and
    printing the row is what keeps the oddity from being absorbed either way.
  * **Two answers, never one.** A flavor whose table exists and declares nothing is `attrs=0 status=OK`;
    a flavor with no table is `status=ABSENT`. "Empty" and "absent" are different answers, and the fixtures
    keep them apart.
  * **`values=not-read`.** The second field of every row is an upstream values-profile expression
    (`{{ dockers.cloud_sdk_docker }}`). No row's second field was parsed, resolved or used in any decision:
    reading it answers "what did that values profile bind" instead of "what does this ref declare", which is
    the same reason the `{{ ... }}` in a launch config above is neutralised rather than rendered. A finding
    quotes its WHOLE row — the row's shape is what the finding is about — so a second field's bytes do appear
    in the human block, and that quoting is the only place they appear and the only thing they decide: nothing.
  * **`enforced=yes`.** A `${workspace.*}` name IS refused now — by the value layer below, which turns this
    census into exit 1. The two words still print on every run, including a quiet one, because a reader has
    to be able to tell what this layer decides from what it merely reports: the oracle decides `status`, and
    `status` is the only thing the refusal keys off.

Value provenance: which bundle each value came from, and what a launch may carry
-------------------------------------------------------------------------------
The oracle above answers *which row*; this answers *which values*, and it is the layer that must never end up
launching somebody else's sample. The plan's own history is the warning: revision 1's Stage B "resolved"
everything by rendering upstream's inputs, and 14 `*_docker` bindings including a personal dev image came
along for the ride. So every value in every config is classified — deep through arrays and objects, one class
per value, after the same `{{ … }}`-neutralising substitution (the shared `JINJA_RE`, never a second pattern)
— and the vocabulary is three-state: **`UNRESOLVED` refuses (exit 1)**, `NEEDS-BUNDLE` is disclosed and exits
0, `WORKSPACE-ATTR` / `ENTITY-FIELD` / `LITERAL` are launchable.

  * **`workspace-attr` / `workspace-missing` / `no-oracle`.** The value is ONE `${workspace.X}` read, and the
    answer comes from that flavor's own `workspace.tsv.tmpl`. Only a table whose `status=OK` grants
    resolution: `ABSENT`, `MALFORMED` and `DUPE` all refuse, each under its own word (`no-oracle-absent`,
    `no-oracle-malformed`, `no-oracle-dupe`), because a malformed table's *cleanly-read* names do not resolve
    either — a stray column means the file's shape is not what was assumed about column 1, and this tool
    resolves a workspace attribute only from a table it read cleanly. `attrs=8` is not cleanliness; `status`
    is. The consequence is printed where a human reads it, not only in the machine line — and so is the cure
    (ruling R21), conditioned on the state the sentence just printed: a row to ADD where `status=OK` says a
    table exists, rows to REPAIR under `MALFORMED`, rows to COLLAPSE under `DUPE`, and a file to CREATE under
    `ABSENT` (there is nothing to edit there, and the TSVs that flavor does ship are printed beside it as
    what EXISTS, never as what to edit). Every one of them names the file the diagnosis already named, and the
    alternative that needs no file at all (`stop binding`). `scripts/selftest.d/entity.sh`'s cure legs RUN
    each cure against a copy of the fixture corpus — filename needles out of the printed sentence, edit applied
    to what it named, refusal required to clear — and then apply the same row to the decoy and require the
    refusal to survive, because a cure that names the wrong file is worse than no cure.
  * **`entity`.** One `${this.…}` read. Stage A's layer already graded the entity half; no second name-key
    verdict is printed here.
  * **`literal`.** A value the config itself carries — a scalar, or a string with no read in it. The only
    class that supplies an actual value.
  * **`needs-bundle`.** A `{{ <root>.<attr> }}` the config cannot carry. It is NAMED by its root
    (`bundles=reference_resources`) and never resolved: nothing is rendered and no values profile is ever
    opened, so a root is a bundle NAME and not a file, and no claim is made that a profile answering it
    exists (at `e1909d2f` every one of the 30 such reads names `reference_resources`, and of the three roots
    upstream uses only `dockers` maps to a shipped file). Disclosed, counted, exit 0, never launched.
  * **`test-bundle`.** Provenance is a test values profile (a `test…` root, or the stem of a shipped
    `inputs/values/test_*.json`, listed by NAME out of the materialized tree for `--tree` and out of
    `git ls-tree` for `--repo/--ref` — the archive carries one subtree, so the ref is asked directly), or the
    value carries a test sample's coordinate (`gs://`, `fc-`, `NA12878`, `NA19240`). Never launchable, refused wherever it appears — including inside a string that would
    otherwise class `literal`. Measured: 0 instances in the configs or the workspace tables at that ref, so
    the committed fixtures are the positive control for this branch, and a guard no fixture reaches is a
    guard that never fires.
  * **`docker`.** Excluded BY RULE rather than by value, in two shapes: a binding whose LEAF KEY ends
    `_docker`, or a binding whose VALUE IS one whole `${workspace.*_docker}` read (ruling R18 — "the image must
    be stated" is a claim about the image, not about what the key happens to be called — as narrowed by
    ruling R20: the value-shaped arm reads the WHOLE value only, because `docker` is an exclusion that refuses
    nothing, so a value that merely MENTIONS an image inside a longer string must not stop that string being
    graded for the rest of its reads). A container that
    resolves from an ambient profile is the failure this whole plan exists to prevent, so no value for such a
    binding ever comes from this tool. `--image KEY=REF` or a profile pin is the only source, and
    `terra/batch_rerun_step.py::_stated()` is where that rule already lives. It costs
    `GATKSVPipelineSingleSample`'s 14 container inputs their value, and is worth it. The value-shaped arm has
    **0** instances at `e1909d2f` (measured), so it moves no real number; the shape exists in this repo's own
    fixtures, where a leaf named `docker` is not a `_docker` key.
  * **`mixed`.** Text around or between reads, the `${[this.a, this.b, …]}` list form included — the plan's
    own grammar trap, since a reader testing `startswith("this.")` loses all five of those reads. The reads
    inside are enumerated and counted at read level; there is no whole-value answer to grade, so a
    `${workspace.X}` inside one that the flavor's oracle cannot answer refuses under its own `mixed-` word
    rather than passing on the surrounding text.

`bindings=` is the SUM of the class fields — computed from `VALUE_CLASSES`, never counted beside them — so a
class added without its field skews a phase's identity assertion instead of quietly coexisting with it, and
`workspace-reads=` is likewise the sum of the three read-level fields beside it (at `e1909d2f`: 304 = 288
resolved + 16 missing + 0 unanswerable). "Empty" and "absent" stay different answers here too: a config that
binds no value at all is `no-values`, a document this layer cannot parse is `values-unreadable`, a corpus with
no config is exit 3, and "every value resolved" is `unresolved=0` with the other counters at 0 — never one `0`
standing for all four. Duplicate keys: this is the PARSED reader, so a key bound twice is classified on its
LAST value (`json.loads`' own rule, named on the entity line as `dupkeys=`) and what the dropped side read is
counted as `dropped-values=` / `dropped-workspace-reads=` rather than quietly missing — which is why the raw
text and this reader disagree at that ref (305 `${workspace.` occurrences against 304 parsed), and why saying
which side a reader is on is part of the number.

Required-input acceptance: does the config that names a workflow bind what that workflow requires?
--------------------------------------------------------------------------------------------------
Stage B's acceptance sentence (`docs/plan-launch-any-module.md`): every required input of every workflow a
launch config binds is either given a value by that config or named by a workspace attribute the flavor's own
table declares. This layer is the second half of that sentence, and it grades the VALUE CLASS of the binding
that carries each required input rather than mere presence — a presence test is the broken tool the plan's
revision 1 was, which "resolved" a launch by pulling in somebody else's test bundle.

  * **One predicate, one loader, imported not copied.** The required-input predicate and the WDL loader are
    `checks/wdl_inputs_check.py::required_inputs` / `::load_workflow`, IMPORTED (`REQUIRED_SOURCE`). The
    expander above is read out of a source with `ast` because importing that module would drag `firecloud`
    into an offline check; this one is a function over a parsed document, and its file imports nothing but
    `config`. A copied predicate is exactly what row C8 of `docs/gap-ledger.md` records: a `"optional" in
    value` test looked like a one-liner and silently dropped every struct-typed required input whose member
    happened to be named `optional`. A tree whose sibling checker carries no such function is exit 3, naming
    the file — never a silently half-built layer.
  * **`--wdl-dir` does not exist, deliberately.** The WDL tree is `<tree>/wdl` (`WDL_SUBTREE`), the same
    pairing rule upstream CI applies by basename. A flag would let one run point the required layer at a
    different tree than the configs it grades, and two readers of the layout is how two checkers start
    disagreeing about one ref.
  * **The classes are the value layer's, plus the two shapes no value class describes.** `container` — the
    config hands the key a whole JSON object or list (a struct passed over as one object), so there is no
    single bundle class for the key and the value layer classes what is INSIDE it; calling that `unbound`
    would be a false refusal. `unbound` — nothing in the config names the key, which is the one new refusal
    this layer exists to print. Every other class is inherited from the value layer's own verdict on the same
    bytes (`class_of`), so the two layers cannot disagree about one document; a required input carried by a
    class the value layer already refused is refused here too, under its own word
    (`required-unbound`, `required-workspace-missing`, `required-test-bundle`, `required-no-oracle`).
  * **What asking one workflow can answer** — `capture=`, one word, in precedence order, because the earlier
    word makes the later ones unaskable (the same single-answer rule the entity refusals and the oracle
    statuses state): `NO-MINIWDL` (no WDL anywhere is readable) → `NO-WDL-DIR` (this tree/ref ships no `wdl/`
    subtree, or it would not archive) → `NO-DOCUMENT` (this config is unreadable, so it names no workflow to
    ask) → `LOAD-FAILURE` (the file is there and miniwdl will not load it; same word as `wdl_inputs_check`) →
    `NO-WORKFLOW` (it loads and declares tasks only — nothing to bind; `wdl_inputs_check` folds this into
    `NO-WDL` and its pins keep it that way, this layer keeps the two apart because "no such file" and "the
    file you named binds nothing" are different operator actions) → `NO-WDL` (no `<wdl>/<workflow>.wdl`) →
    `OK`. `NO-MINIWDL`, `NO-WDL-DIR` and `NO-WDL` are corpus-wide facts and are stated ONCE in the run-level
    banner rather than 31 times, which is the same reason the oracle block caps a 98-row table: repeating the
    one big cause buries the causes that really are per-config.
  * **`state=` is a third thing besides pass and finding.** `NOT-CAPTURED` means the question was never asked
    of that workflow: neither a pass nor a refusal, and counted on `GSVTK-ENTITY-UNCHECKED`
    (`required-configs-uncaptured=`, cause in `required-uncaptured-because=`) so a green exit cannot be read
    as "every required input is bound". Precedence is fixed the other way for findings: a config with both an
    unbound required input and an unreadable second workflow is `UNRESOLVED`, never `NOT-CAPTURED` — a
    finding is never hidden by a disclosure (`mixed` is the honest word for the cause field when more than one
    capture word applied to the corpus).
  * **What it costs.** A SECOND subtree is unpacked on the `--repo/--ref` path: `wdl/`, 1.4 MB / 118 files at
    `e1909d2f`, one `git archive` of one pathspec, quiet. Its failure is the disclosed `NO-WDL-DIR` state and
    NEVER exit 3, because the entity and value answers do not depend on it. `miniwdl` is a dev dependency, so
    its absence is disclosed the same way — a `capture=NO-MINIWDL` on every config and a counted skip in the
    gate phase — while a missing sibling predicate stays exit 3.

A pass does NOT mean
--------------------
It means the derived type is a shipped table's type, the read names are its columns, every value this config
binds is classed by where it came from, and every required input of every workflow it captured is carried by
one of those classes. It does not mean the entity ROW exists (that is the read-only `terra.entity_sample()`
call the plan puts before any submission), it does not mean a disclosed bundle was ever stated by an operator
(`needs-bundle` exits 0 precisely because naming it is a human decision), it does not mean the attribute
VALUES in a flavor's workspace table were read (they never are — see `workspace-attribute-values-unread=`),
it does not mean a required input that `container` merely *names* will arrive (the image rule and the bundle
rule both withhold the value), and it does not mean Terra's own `rootEntityType` agrees — that string appears
in no file upstream and is still the plan's open, credential-using decision. And an exit 0 whose required
lines all say `capture=<word>` means only that the entity and value questions were answered: `0` beside a
non-zero `required-configs-uncaptured=` is a scope statement, not a clean audit.

Measured on the tree this file was written against (gatk-sv `e1909d2f`); run the command in
docs/static-checks.md and it prints all of this itself. 31 configs: 29 with exactly one name key, 2 with
none (both `output_configurations/*` write-back files), 0 with many. Derived types: `sample_set_set` 16,
`sample_set` 10, `sample` 3, none 2. Shipped-table check: 3 pass (`GatherSampleEvidence`,
`StripyWorkflow`, `GATKSVPipelineSingleSample`), 16 have no shipped table of the derived type, 10 have a
table that lacks a column they read, 0 fail to parse. The same corpus also carries one `${[this.a, …]}`
binding holding **five** reads (`PlotSVCountsPerSample.vcfs`), **1** key bound twice with different values
(`GATKSVPipelineSingleSample.mei_bed` — `json.loads` is last-wins, so `${workspace.reference_mei_bed}`
survives), 0 reads inside nested objects, 0 `this.` outside `${ }` and 30 `{{ ... }}` expressions across 9
configs. Every one of those shapes is reported per config (`nested=`, `barethis=`, `dupkeys=`,
`neutralised=`) rather than assumed away: the list binding is the plan's own warning — "a grammar that
treats a value as a path iff it begins `this.` mis-reads `${[this.a, this.b, …]}`" — and with that
grammar `PlotSVCountsPerSample` loses 5 of its 6 reads and passes the table check it honestly fails. The
two workspace tables on that tree are structurally clean: 76 and 98 rows, 76 and 98 distinct names, 0
duplicate names, 0 blank rows, 0 rows with more than two tab-separated fields, and the `workspace:` prefix
on line 1 of each file and nowhere else — so `MALFORMED` and `DUPE` are defensive states with no upstream
instance at this ref, which is why the committed fixtures, not the real tree, are what prove them
reachable. The value layer on that same tree classifies 622 values over the 31 configs: 187 `workspace-attr`,
16 `workspace-missing` (all `cohort_mode` — `cohort_ped_file` x12, `merge_batch_sites_vcf` x3,
`merge_batch_sites_vcf_index` x1, all three absent from the table ENTIRELY rather than blank), 174 `entity`
with 5 more reads inside the one `${[ … ]}`, 113 `literal`, 30 `needs-bundle` (every one `reference_resources`,
all `| tojson`), 101 `docker`, 1 `mixed`, 0 `test-bundle`; 304 `${workspace.*}` reads = 288 resolved + 16
missing + 0 unanswerable, plus 1 value dropped by the duplicated `mei_bed` key — exactly the difference between
this parsed reader's 304 and the raw text's 305. Those 16 refusals are findings about upstream's corpus, not
defects in this check: a refusal that never fires on real drift is not a product. And because `test-bundle`
has no instance at this ref, the committed fixture is the only thing that proves that guard reachable. The
required layer on the same tree asks 29 workflows (all 29 captured) for 427 required inputs and finds 152
`workspace-attr`, 109 `entity`, 91 `docker`, 35 `literal`, 6 `needs-bundle`, 1 `mixed`, 11
`workspace-missing` and 22 `unbound` — 18 configs `OK`, 13 `UNRESOLVED`, 0 `NOT-CAPTURED`, every config
`capture=OK`. Read the two halves together: 11 of the value layer's 16 refusals are promoted to a required
input here — 9 `ped_file`, `GenotypeBatch.vcf` and `RegenotypeCNVs.merge_batch_sites_vcf`, over 11 configs, each
one a key bound to an attribute its flavor never declares — and all 22 `unbound` belong to the two
`output_configurations/*` write-back files, which already refuse at the entity layer as `no-name-key` — so
this layer reports this ref's brokenness rather than manufacturing it, which is the leg that has to hold
before anyone reads its `configs-ok=18` as good news. `GATKSVPipelineSingleSample`, the module the plan is
about, prints 83 required inputs with `unbound=0` and `state=OK`, 10 of them images and 2 of them bundle
renders, both disclosed rather than resolved.

Usage
-----
    checks/terra_entity_check.py --repo ~/gatk-sv --ref main            # read a ref (git archive)
    checks/terra_entity_check.py --tree /tmp/gatk-sv-at-ref             # an already-materialized tree
    checks/terra_entity_check.py                                        # GSVTK_GATK_SV_CHECKOUT / _REF
    checks/terra_entity_check.py --tree T --corpus inputs/templates/terra_workspaces

What it needs
-------------
No network, no credentials, no Terra API, no write outside a temp dir — and, for everything except the
required-input layer, the standard library alone. The claim used to be unconditional and is not anymore:
`--help`, the entity layer, the oracle and the value layer still run on a bare interpreter with no venv, but
the required-input layer asks a WDL what its required inputs are, so it imports `miniwdl` — LAZILY, through
`checks/wdl_inputs_check.py`, which is why the probe happens once per run rather than at import time and why
nothing else in this file pays for it. Where `miniwdl` is absent the required layer answers `capture=NO-MINIWDL`
for every config, counts them, and moves no exit code: a dev dependency that is not installed is a disclosed
scope, not a finding, and `GSVTK-ENTITY-UNCHECKED` is where a green run says so.

Exit codes
----------
    0  every config in the corpus answered, and every value it binds is classifiable and launchable. With the
       required layer that is a SCOPE statement, not a clean audit: read `capture=` and
       `required-configs-uncaptured=` before reading this 0 as "every required input is bound"
    1  findings — at least one config is UNRESOLVED, by the entity derivation, by value provenance, or by a
       required input nothing binds (the refusal, with its reason, is the answer)
    2  usage error (bad flag combination)
    3  prerequisite missing, so the question could not be asked at all: no checkout, a ref that will not
       archive, no corpus at that ref, no config in it, or no `terra/batch_configs.py` to read the
       one expander from

Machine lines
-------------
One `GSVTK-ENTITY` line per config, key=value, so a caller can read verdicts while the human table beside
it stays readable; plus one `GSVTK-ENTITY-SUMMARY` line with the census counters, so a phase can assert
that the per-config lines add up, plus one `GSVTK-ENTITY-UNCHECKED` line counting what this tool deliberately
did NOT evaluate (member attributes, the workspace tables' attribute VALUES, the reads in documents this layer
could not parse, Terra's own `rootEntityType`). That line prints on a clean run too: no refusal found is not
the same fact as nothing left unexamined, and a reader must be able to tell the two apart without reading this
file. `GSVTK-VALUE` / `GSVTK-VALUE-SUMMARY` are the value layer's own pair, printed beside the entity lines;
`GSVTK-REQUIRED` / `GSVTK-REQUIRED-SUMMARY` are the required-input layer's pair. The prefixes
`GSVTK-ENTITY`, `GSVTK-ENTITY-SUMMARY`, `GSVTK-ENTITY-UNCHECKED`, `GSVTK-ORACLE`, `GSVTK-ORACLE-SUMMARY`,
`GSVTK-VALUE`, `GSVTK-VALUE-SUMMARY`, `GSVTK-REQUIRED` and `GSVTK-REQUIRED-SUMMARY` are byte-stable because
other phases and other lanes assert them.

    GSVTK-ENTITY config=<relpath> flavor=<f> wf=<workflow> filestem=<s> namematch=yes|no
      state=OK|UNRESOLVED namekeys=<n> namekey=<etype|-> collections=<a,b|-> reads_direct=<n>
      reads_member=<n> member_attrs=<n> tables_of_type=<n> table=<path|-> table_kind=entity|membership|-
      columns=<n> missing=<a,b|-> nested=<n> barethis=<n> dupkeys=<n> neutralised=<n> unbraced=<n>
      reason=<code|->

The workspace oracle prints its own two lines, under a prefix of its own, because it answers a different
question and enforces nothing yet. `ok`, `absent`, `malformed` and `dupes` are FLAVOR-status counters that
add up to `flavors` — a flavor whose table is both malformed and duplicated is counted once, under the
earlier word, and BOTH findings are printed in the human block — and `names` is the total declared
attribute names over the flavors that have a table. `attrs` and `names` count names that were READ out of a
row's first field; they are not a cleanliness signal — a `MALFORMED` or `DUPE` table's readable names are
counted in them too. Cleanliness is `status`, and nothing else on the line:

    GSVTK-ORACLE flavor=<f> file=<relpath|-> attrs=<n> status=OK|ABSENT|MALFORMED|DUPE
    GSVTK-ORACLE-SUMMARY flavors=<n> ok=<n> absent=<n> malformed=<n> dupes=<n> names=<n>
      values=not-read enforced=yes

The value layer prints one `GSVTK-VALUE` line beside each config's entity line, plus its own census. The class
fields ARE the class tokens, spelled once, and `bindings` is their sum on both lines: a class that exists
without being printed, or a field outside `VALUE_CLASSES`, skews the identity a phase asserts rather than
coexisting with it. `unresolved` counts VALUES in the three refusing classes; `state=UNRESOLVED` is wider, so
a `mixed` value whose read cannot be answered, a config with no value at all, an unreadable document and an
unattributable bundle read appear in `reasons=` and never inside that sum.

    GSVTK-VALUE config=<relpath> flavor=<f> wf=<workflow> bindings=<n> workspace-attr=<n>
      workspace-missing=<n> entity=<n> literal=<n> needs-bundle=<n> test-bundle=<n> docker=<n> mixed=<n>
      no-oracle=<n> nested-reads=<n> workspace-reads=<n> workspace-resolved-reads=<n>
      workspace-missing-reads=<n> no-oracle-reads=<n> bundles=<a,b|-> dropped-values=<n>
      dropped-workspace-reads=<n> unresolved=<n> state=OK|UNRESOLVED reasons=<a,b,c|->
    GSVTK-VALUE-SUMMARY configs=<n> bindings=<n> <the same class fields, summed> nested-reads=<n>
      workspace-reads=<n> workspace-resolved-reads=<n> workspace-missing-reads=<n> no-oracle-reads=<n>
      bundles=<a,b|-> dropped-values=<n> dropped-workspace-reads=<n> unresolved=<n> configs-unresolved=<n>
      configs-no-oracle=<n> configs-needs-bundle=<n> configs-no-values=<n> configs-unclassified=<n>

The required layer prints the third pair, beside the other two, and its `required=` is the sum of ITS class
fields (`REQUIRED_CLASSES` = the value layer's classes + `container` + `unbound`) on both lines, while
`configs=` is the sum of the state counters AND of the capture-word counters — two identities a reader can
check against the printed line, which is how `scripts/selftest.d/entity.sh` grades them. `workflows=` counts
the workflow NAMES behind the corpus (a config binding two workflows asks two questions), `workflows-captured=`
those whose document was read, and `files=` lists the WDLs a question was answered from, ref-relative — never
the `git archive` temp directory, because a provenance path a reader cannot open is not provenance.
`state=NOT-CAPTURED` always travels with a non-`OK` `capture=`, and never with `reason=required-*`: a finding
takes the line off `configs-unsatisfied`, off `configs-not-captured`, and off a reader's ability to mistake it
for coverage.

    GSVTK-REQUIRED config=<relpath> flavor=<f> wf=<a,b> workflows=<n> capture=<word> required=<n>
      workspace-attr=<n> workspace-missing=<n> entity=<n> literal=<n> needs-bundle=<n> test-bundle=<n>
      docker=<n> mixed=<n> no-oracle=<n> container=<n> unbound=<n> files=<a.wdl,b.wdl|->
      state=OK|UNRESOLVED|NOT-CAPTURED reason=<code|->
    GSVTK-REQUIRED-SUMMARY configs=<n> required=<n> <the same class fields, summed> workflows=<n>
      workflows-captured=<n> configs-ok=<n> configs-unsatisfied=<n> configs-not-captured=<n>
      capture-no-miniwdl=<n> capture-no-wdl-dir=<n> capture-no-document=<n> capture-load-failure=<n>
      capture-no-workflow=<n> capture-no-wdl=<n> capture-ok=<n>

`GSVTK-ENTITY-UNCHECKED` carries the two required fields that make a green run readable —
`required-configs-uncaptured=<n>` and `required-uncaptured-because=<word|mixed|->` — because the census line is
where a reader looks for "what did this run NOT answer", and a required question that was never asked belongs
there beside the member attributes and the unread table values. The per-word breakdown is on
`GSVTK-REQUIRED-SUMMARY`; the `because` field is the ONE word (or `mixed`) saying which, and `-` when nothing
went unasked.
"""
from __future__ import annotations

import argparse
import ast
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
import config  # noqa: E402  # GATK_SV_CHECKOUT / GATK_SV_REF: one source of truth for both

DEFAULT_CORPUS = "inputs/templates/terra_workspaces"
# Where the one Jinja-neutralising pattern lives. Read out of that source, never copied in here.
EXPANDER_SOURCE = os.path.join(os.path.dirname(HERE), "terra", "batch_configs.py")
EXPANDER_NAME = "JINJA_RE"

# A Terra binding is `${ ... }`; one value may carry several, or carry them around other text.
BINDING_RE = re.compile(r"\$\{([^{}]*)\}")
# Inside one binding: `this.<path>`. A deeper path keeps its tail (it is still one member read).
THIS_TOKEN_RE = re.compile(r"this\.([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")
# `this.` outside `${ }`: Terra binds expressions inside `${ }`, so these are literals. Counted, not read.
BARE_THIS_RE = re.compile(r"this\.[A-Za-z_][A-Za-z0-9_.]*")
# First header column of a shipped entity table. `entity:` is a row table, `membership:` a collection
# table; both declare a type and both are importable as that type.
TABLE_HEADER_RE = re.compile(r"^(entity|membership):(.+)_id$")
CONFIG_SUFFIX = ".json.tmpl"
# The flavor a config belongs to when it sits in the corpus root rather than in a flavor directory. One
# spelling, used both where the flavor is read off a path and where the oracle's location is derived from it.
ROOT_FLAVOR = "(root)"

# The workspace attribute table. Upstream ships one per flavor directory and its FIRST column is the set of
# attribute names that flavor declares, which makes it the offline oracle for `${workspace.X}` bindings.
# The name is exact and the location is exact (a direct child of the flavor directory): the other
# `*.tsv.tmpl` files beside it are entity, membership and near-miss tables, and they get named, not read.
WORKSPACE_TABLE = "workspace.tsv.tmpl"
# Terra's own prefix on an attribute name. Optional, and stripped wherever it appears: the real tables carry
# it on row 1 only, so a reader that trusted that position breaks the first time a table is reordered.
WORKSPACE_PREFIX = "workspace:"
# What an attribute name may contain. Anything else cannot be the target of a `${workspace.<name>}` binding,
# so it is a finding about the row — printed with the row text, never repaired.
WORKSPACE_NAME_RE = re.compile(r"[A-Za-z0-9_.]+\Z")
# How many declared names the human block lists before it says how many more there are. The machine line
# carries the count either way; this only keeps a 98-row table off the operator's screen.
ORACLE_NAMES_SHOWN = 8

# R21: a refusal that ends at the diagnosis tells an operator what is wrong and leaves them to find the file.
# Every cure sentence below therefore names ONE file — the same variable the diagnosis printed, never a path
# rebuilt from a pattern, which would be a second reader of the layout and would disagree with
# `read_workspace_oracle()` the day that layout moved. This corpus ships two decoys that show what guessing
# the target produces (`workspace_other.tsv.tmpl`, and `workspace.tsv.tmpl` one directory down), so every
# sentence says which file to edit OR create and says what else exists is not the target.
CURE_ONLY_FILE = ("— that exact file, and no other TSV that flavor ships, because only this one is read —")
# The per-flavor statuses, in precedence order, which is also the order the summary counts them in. One word
# has to answer, so the first applicable one wins (`ABSENT` before a table that could not be parsed, then
# `MALFORMED` before `DUPE`); every finding is printed regardless, so a flavor that is both malformed and
# duplicated loses no detail — only the counted label is decided.
ORACLE_STATUSES = ("ABSENT", "MALFORMED", "DUPE", "OK")

# The value-provenance classes, in the order the machine lines print them. One class per value, and the
# FIELD NAME IS THE TOKEN: the lines spell each class exactly one way, in the same hyphens the vocabulary
# table in this docstring uses.
# `bindings=` on BOTH lines is the sum of these fields — computed from this tuple, never counted beside it
# — so a class added here without its field, or a field counting something outside it, skews the identity a
# phase asserts (`bindings == sum of the class fields`) instead of quietly coexisting with it (ruling R15).
# The fields are generated from this tuple by `_value_class_tail()`, which is what makes that structural.
VALUE_CLASSES = ("workspace-attr", "workspace-missing", "entity", "literal", "needs-bundle",
                 "test-bundle", "docker", "mixed", "no-oracle")
# The classes that refuse a launch. `needs-bundle` is NOT one of them: an unstated bundle is a plan-time
# fact an operator states, disclosed and counted, exit 0. Refusing it would put this tool in the business
# of naming bundles, which is the guessing this plan exists to stop.
VALUE_REFUSAL_CLASSES = ("workspace-missing", "no-oracle", "test-bundle")
# A binding key whose leaf ends this suffix is a container image, and it is excluded BY RULE rather than by
# value: a container that resolves from an ambient profile is the failure this whole plan exists to prevent,
# so no value for such a key ever comes from this tool. `--image KEY=REF` or an operator's profile pin is
# the only source, and `terra/batch_rerun_step.py::_stated()` is where that rule already lives.
# Ruling R18 extends the rule to the OTHER end of the binding: a value that names a `*_docker` WORKSPACE
# ATTRIBUTE is docker-shaped whoever the key is called, because the claim is about the image. Measured 0
# instances at `e1909d2f`, so no real number moves; the shape exists in this repo's own fixtures, where the
# leaf is `docker` and a suffix test on the key never sees it.
# Ruling R20 narrows that second shape to a value that IS the image (`whole_docker` in `classify_values`), not
# a value that mentions one: `DOCKER-MUST-BE-STATED` is an EXCLUSION, so the wide form quietly stopped grading
# every other read the same string carried — measured, in this repo's own fixtures, as the same missing
# attribute refusing in a value naming no image and launching in a value that also named one.
DOCKER_KEY_SUFFIX = "_docker"
DOCKER_CLASS = "docker"
# A test values profile is named by its FILE, and these are the coordinates a test sample's data sits at.
# Both refuse wherever they appear in a value — including inside a string that otherwise reads as a
# literal — so an ambient test profile cannot ride into a launch. B0 measured 0 instances of any of them in
# the configs or the workspace tables at `e1909d2f`, which is exactly why the committed fixtures, not the
# real tree, are the positive control for this branch.
TEST_COORDINATE_MARKERS = ("gs://", "fc-", "NA12878", "NA19240")
TEST_PROFILE_DIR = os.path.join("inputs", "values")
TEST_PROFILE_PREFIX = "test_"
# Ruling R16: only a `status=OK` oracle grants resolution, and the three states that refuse do it under
# three DISTINCT words, because one reason for three different states is a refusal that cannot tell an
# operator what to fix. The `mixed-` variants are the same three states reached from a value that is text
# around reads, where no whole-value answer exists to refuse in the first place.
NO_ORACLE_REASONS = {"ABSENT": "no-oracle-absent", "MALFORMED": "no-oracle-malformed",
                     "DUPE": "no-oracle-dupe"}
MIXED_NO_ORACLE_REASONS = {"ABSENT": "mixed-no-oracle-absent",
                           "MALFORMED": "mixed-no-oracle-malformed",
                           "DUPE": "mixed-no-oracle-dupe"}
# One `${workspace.<name>}` read, and the two whole-value forms. A name keeps its dots: `workspace.a.b`
# is not an attribute anyone declared, and printing it with its dots beats resolving it as `a`.
WORKSPACE_READ_RE = re.compile(r"workspace\.([A-Za-z0-9_.]+)")
WORKSPACE_WHOLE_RE = re.compile(r"\A\$\{workspace\.([A-Za-z0-9_.]+)\}\Z")
THIS_WHOLE_RE = re.compile(r"\A\$\{this\.([A-Za-z_][A-Za-z0-9_.]*)\}\Z")
# The root of a `{{ root.attr | tojson }}` expression: the bundle NAME. B0 measured that only the `dockers`
# root maps to a shipped values file at `e1909d2f`, so a root is a name and never a file, and nothing here
# claims a profile answering it exists.
BUNDLE_ROOT_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)")
# `{{ ... }}` is neutralised, never rendered — but neutralised must not mean invisible, or the 30 bundle
# reads at `e1909d2f` would classify as nothing. The value layer substitutes this MARKER with the same
# shared `JINJA_RE` (never a second pattern) and keeps each expression beside it. Two spellings of one
# marker: the JSON source has to escape it (`\u0001` is six characters; a raw 0x01 inside a string is not
# valid JSON and would fail the parse), and `json.loads` hands back the character it stands for.
JINJA_MARK_SRC = "\\u0001gsvtk-jinja-%d\\u0001"
JINJA_MARK_RE = re.compile(r"\x01gsvtk-jinja-(\d+)\x01")

# Refusal codes, in precedence order. Every one prints the paths it tried.
REASONS = ("cannot-parse", "no-name-key", "many-name-keys", "no-shipped-table", "columns-missing")
PLACEHOLDER = "\x00"

# --------------------------------------------------------------------------- required-input acceptance
# The tree's WDL directory, read as a SECOND subtree of the same ref. `--corpus` fixes where the configs
# live; this fixes where the workflows live, and it is not configurable on purpose: `wdl/` at the tree root
# is the layout upstream ships (118 files, flat, at `e1909d2f`), the layout `checks/wdl_inputs_check.py`
# assumes, and the layout `scripts/test/terra_validation.py` pairs configs against by basename. A second
# answer to "where are the WDLs" is a second reader of the layout, which is how two checkers start
# disagreeing about the same ref.
WDL_SUBTREE = "wdl"
# The one required-input predicate in this repo lives in the OTHER WDL checker. It is IMPORTED here, not
# read out of the source with `ast` the way the expander is: the expander is one regex (a value) whose
# module cannot be imported without dragging `firecloud` into an offline check, while this is a function
# over a parsed document, and the file that owns it imports nothing but `config`. A copied predicate is
# exactly what row C8 of docs/gap-ledger.md records: `"optional" in value` looked like a one-liner and
# silently dropped every struct-typed required input whose member happened to be named `optional`.
REQUIRED_SOURCE = os.path.join(HERE, "wdl_inputs_check.py")
# The same file, spelled the way a reader opens it: from the repo root. Printed, so a claim about where a
# rule lives is the path a reader can check rather than a temp-directory absolute.
REQUIRED_WHERE = os.path.relpath(REQUIRED_SOURCE, os.path.dirname(HERE))
# The required layer's class fields: the value layer's class of the value that CARRIES the required input,
# plus the two shapes no value class describes. `required=` on both lines is the sum of these fields —
# computed from this tuple, never counted beside it — which is the same identity discipline `VALUE_CLASSES`
# states (ruling R15): a class added here without its field skews the identity a phase asserts instead of
# quietly coexisting with it.
#   `container` — the config hands this key a whole JSON object or list (a struct passed over as one
#       object). The value layer classes what is INSIDE it, one class per scalar, so there is no single
#       bundle class for the key itself and inventing one would be a second vocabulary.
#   `unbound`   — nothing in the config names this key. The only new refusal this layer exists to print.
REQUIRED_CLASSES = VALUE_CLASSES + ("container", "unbound")
# A required input bound to a refusing value class is not satisfied either: "resolved from the render or
# named as a workspace attribute that exists" (docs/plan-launch-any-module.md's criterion) is not met by a
# value the value layer already refused. One word per class, so the refusal says WHICH state it inherited.
REQUIRED_REASONS = ("required-unbound", "required-workspace-missing", "required-test-bundle",
                    "required-no-oracle")
REQUIRED_REASON_OF = dict([("unbound", "required-unbound")] +
                           [(c, "required-" + c) for c in VALUE_REFUSAL_CLASSES])
# What asking one workflow for its required inputs can answer, in precedence order: the earlier word makes
# the later ones unaskable, and the FIRST applicable one is the word that prints (the same single-answer
# rule `ORACLE_STATUSES` states). `OK` last.
#   NO-MINIWDL    — `WDL` is not importable, so no WDL anywhere can be read. miniwdl is a dev dependency
#       (`requirements-dev.txt` says the gate SKIPS these instead of failing), so this is disclosed, never
#       a finding — but it is disclosed WITH A COUNT on every run, which is what keeps a skip from being a
#       pass (`checks/wdl_inputs_check.py`'s own rule).
#   NO-WDL-DIR    — this tree/ref ships no `wdl/` subtree, or it would not archive.
#   NO-DOCUMENT   — this config is a document no reader here can parse, so it names no workflow to ask.
#   LOAD-FAILURE  — the file is there and miniwdl will not load it. Same word as `wdl_inputs_check`.
#   NO-WORKFLOW   — the file is there, loads, and declares tasks only: no input section, so nothing to
#       bind. `wdl_inputs_check` folds this into `NO-WDL` and its own pins keep it that way; this layer
#       keeps the two apart because "no such file" and "the file you named binds nothing" are two answers
#       an operator acts on differently (9 of gatk-sv's 118 WDLs at `e1909d2f` are task-only libraries).
#   NO-WDL        — no `<wdl>/<workflow>.wdl` in the tree.
REQUIRED_CAPTURES = ("NO-MINIWDL", "NO-WDL-DIR", "NO-DOCUMENT", "LOAD-FAILURE", "NO-WORKFLOW",
                     "NO-WDL", "OK")
# A config's own answer. `NOT-CAPTURED` is neither a pass nor a finding: it is the honest word for a
# question that was never asked, and it is counted on `GSVTK-ENTITY-UNCHECKED` so a clean exit cannot be
# read as "every required input is bound".
REQUIRED_STATES = ("OK", "UNRESOLVED", "NOT-CAPTURED")
# The causes stated ONCE, in the run-level banner, instead of once per config. A corpus whose tree ships no
# `wdl/` at all is one fact, not 42; repeating it per config buried the two causes that ARE per-config
# (`LOAD-FAILURE`, and a document that named no workflow), which is the same reason the oracle block caps a
# 98-row table rather than dumping it.
REQUIRED_BANNER_CAUSES = ("NO-MINIWDL", "NO-WDL-DIR", "NO-WDL")
# How many uncaptured workflow names the banner lists before it says how many more there are — the same
# discipline as `ORACLE_NAMES_SHOWN`: the count is the fact, the list is the courtesy.
REQUIRED_UNCAPTURED_SHOWN = 8


def _shared_expander():
    """The one `{{ ... }}`-neutralising pattern, read out of terra/batch_configs.py by name.

    Importing that module would drag in `firecloud` (it is the tool that POSTs method configs), which an
    offline check must not need, and copying the pattern would put a second expander in the repo — the
    drift §10 of docs/module-profiles.md exists to close. So the pattern is parsed out of the source with
    `ast`: one definition in the tree, and a tree whose file no longer carries it gets a named refusal.
    Returns (compiled or None, "where it was found").
    """
    try:
        src = open(EXPANDER_SOURCE).read()
    except OSError:
        return None, "unreadable"
    try:
        tree = ast.parse(src, EXPANDER_SOURCE)
    except SyntaxError as e:
        return None, "%s does not parse (%s)" % (EXPANDER_SOURCE, e)
    for node in tree.body:
        if not (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == EXPANDER_NAME for t in node.targets)):
            continue
        call = node.value
        if (isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "compile"
                and call.args and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)):
            where = "%s:%s" % (os.path.relpath(EXPANDER_SOURCE, os.path.dirname(HERE)), node.lineno)
            # The FLAGS travel with the pattern. Reading only `args[0]` dropped `re.S`, so a `{{ ... }}` that
            # spans lines — a legal binding, and what a multi-line `| tojson` render looks like — survived
            # neutralisation HERE while the launcher neutralised it, and the document came back
            # `cannot-parse`: a checker that disagrees with the launcher in the REFUSING direction costs an
            # operator an hour chasing a defect that is in the checker. The selftest's `MultiLineBundle`
            # fixture is the control for these four lines, and an unevaluable flag is a named refusal
            # rather than a pattern quietly compiled without it.
            flags = 0
            for arg in list(call.args[1:]) + [kw.value for kw in call.keywords]:
                if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name) \
                        and arg.value.id == "re" and hasattr(re, arg.attr):
                    flags |= getattr(re, arg.attr)
                elif isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                    flags |= arg.value
                else:
                    return None, "%s:%s carries a flag this reader cannot evaluate" % (
                        os.path.relpath(EXPANDER_SOURCE, os.path.dirname(HERE)), node.lineno)
            return re.compile(call.args[0].value, flags), where
        return None, "%s:%s is not a re.compile(<string>) assignment" % (
            os.path.relpath(EXPANDER_SOURCE, os.path.dirname(HERE)), node.lineno)
    return None, "%s carries no %s assignment" % (EXPANDER_SOURCE, EXPANDER_NAME)


def _tree_from_ref(repo: str, ref: str, subpath: str, quiet: bool = False) -> tuple:
    """One subtree of <repo> at <ref>, unpacked into a temp dir: (dir, why_it_failed).

    Read-only by construction. `git archive` streams the tree: it cannot touch the checkout's index, HEAD or
    working tree, and the tarball lands in a directory this process removes on exit. Same discipline as
    `checks/wdl_inputs_check.py::render()`.

    `quiet` is for a subtree whose absence is an ANSWER rather than a missing prerequisite: the required-input
    layer asks about `wdl/`, and a ref that ships none (or a ref whose `wdl` will not archive) has to be
    reported as `NO-WDL-DIR` beside every config's real entity and value verdict, not answer for the whole
    run with exit 3. The reason still travels — in `why`, printed beside the count.
    """
    repo = os.path.expanduser(repo)
    tmp = tempfile.mkdtemp(prefix="gsvtk-entity-")
    atexit.register(shutil.rmtree, tmp, True)
    arc = subprocess.run(["git", "-C", repo, "archive", ref, subpath], capture_output=True)
    if arc.returncode != 0:
        why = (arc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        why = (why or ["?"])[0][:200]
        if not quiet:
            print("PREREQUISITE `git -C %s archive %s %s` failed: %s" % (repo, ref, subpath, why))
        return "", "`git -C %s archive %s %s` failed: %s" % (repo, ref, subpath, why)
    untar = subprocess.run(["tar", "-x", "-C", tmp], input=arc.stdout, capture_output=True)
    if untar.returncode != 0:
        why = "could not unpack `git archive %s %s` from %s" % (ref, subpath, repo)
        if not quiet:
            print("PREREQUISITE %s" % why)
        return "", why
    return tmp, ""


def unfinished_template(neutral: str) -> tuple:
    """(`unbraced` count, why this substituted document cannot be read — "" when it can).

    Both readers substitute with the shared `JINJA_RE` and then parse, and two shapes survive that
    substitution with a different MEANING behind the text: a `${` with no closing `}` leaves a binding this
    file cannot see at all, and a `{{` with no closing `}}` survives as RAW TEMPLATE TEXT that the value
    layer used to class `literal` — the one class this file says may ride into a launch. Neither is a JSON
    error, which is why they were counted (`unbraced=`) or silently classed instead of refused. One spelling
    here, because both readers have to give the same answer about the same bytes.

    `JINJA_RE` carries `re.S`, so an expression that merely SPANS LINES is matched and never reaches this
    test: the test is "no closing braces", not "not on one line". `MultiLineBundle.json.tmpl` is the control
    that keeps a later simplification from dropping `re.S` and turning every multi-line expression in a
    future ref into a false refusal.
    """
    unbraced = len(re.findall(r"\$\{", neutral)) - len(BINDING_RE.findall(neutral))
    unterm = neutral.count("{{")
    why = []
    if unbraced:
        why.append("%d `${` with no closing `}`: a binding sits behind it that no reader here can see"
                   % unbraced)
    if unterm:
        why.append("%d `{{` with no closing `}}`: the shared expander cannot match it, so it survives "
                   "substitution as raw template text, which is not a value a launch may carry" % unterm)
    return unbraced, "; ".join(why)


def read_config(path: str, jinja_re) -> dict:
    """One launch config read STRUCTURALLY: {ok, keys, neutralised, dupkeys, unbraced, error}.

    `keys` keeps the document's nesting, because a `${this.x}` inside a nested object is still a binding
    (0 of them in the corpus at `e1909d2f`, and this file walks for them rather than trusting that count
    at the next ref). `dupkeys` names a key bound more than once, and the value that survives is the LAST
    one — that is what `json.loads` does, so reporting it is the honest description of what a structural
    reader sees (the risk the plan names for `mei_bed`).
    """
    out = {"ok": False, "keys": {}, "neutralised": 0, "dupkeys": [], "unbraced": 0, "error": ""}
    try:
        text = open(path).read()
    except (OSError, UnicodeDecodeError) as e:
        # `UnicodeDecodeError` is a ValueError, not an OSError, so catching `OSError` alone let one
        # undecodable byte in one config escape as a traceback. A traceback prints zero `GSVTK-*` lines
        # and exits 1 for the wrong reason; the `cannot-parse` refusal carries the same fact and still
        # prints every other config's verdict. Same fix as `read_workspace_table`, same reason.
        out["error"] = str(e)
        return out
    out["neutralised"] = len(jinja_re.findall(text))
    neutral = jinja_re.sub("null", text)

    def keep_pairs(pairs):
        names = [k for k, _ in pairs]
        for k in names:
            if names.count(k) > 1 and k not in out["dupkeys"]:
                out["dupkeys"].append(k)
        return dict(pairs)                                # last wins, exactly json.loads' own rule

    try:
        obj = json.loads(neutral, object_pairs_hook=keep_pairs)
    except ValueError as e:
        out["error"] = str(e)
        return out
    if not isinstance(obj, dict):
        out["error"] = "top level is a %s, not an object of bindings" % type(obj).__name__
        return out
    out["unbraced"], why = unfinished_template(neutral)
    if why:
        # Counted AND refused, not counted instead of refused: an unfinished template is not a document
        # whose bindings can be reported, and the half that survives as raw text would otherwise be classed
        # as launch data. `parse_with_bundle_marks` gives the same answer about the same bytes.
        out["error"] = why
        return out
    out["ok"] = True
    out["keys"] = obj
    return out


def walk_strings(value, path: str = "", depth: int = 1) -> list:
    """[(key_path, string, depth)] for every string in a parsed value, nested objects/lists included.

    `depth` 1 means the string is a binding value of the config map itself; 2+ means it sits inside a
    nested object or list under that binding. The distinction is reported, not assumed: the corpus holds
    0 nested reads at gatk-sv `e1909d2f`, and nothing guarantees the next ref holds none.
    """
    out = []
    if isinstance(value, str):
        out.append((path, value, depth))
    elif isinstance(value, dict):
        for k, v in value.items():
            out.extend(walk_strings(v, "%s.%s" % (path, k), depth + 1))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.extend(walk_strings(v, "%s[%d]" % (path, i), depth + 1))
    return out


def binding_strings(keys: dict) -> list:
    """[(key_path, string, is_nested)] for every string binding in a config document.

    `is_nested` is True when the string sits inside an object or list BELOW a binding key rather than
    being the binding's value. Reported per config (`nested=`), never assumed: the corpus carries 0 of
    them at gatk-sv `e1909d2f`, and a reader who cannot see the difference cannot see it appear.
    """
    return [(p, t, d > 1) for (p, t, d) in walk_strings(keys, "", 0)]


def reads_in(text: str) -> tuple:
    """(paths, bare_this_count) for one config value.

    `paths` are the `this.<path>` bindings inside `${ ... }`, including the ones inside
    `${[this.a, this.b]}` that a starts-with grammar loses. `bare_this_count` is `this.` outside
    `${ }`: Terra binds expressions inside `${ }` only, so those are literal strings, and printing the
    count is what stops "that read was not seen" from turning into a silent pass.
    """
    reads, rest = [], text
    for inner in BINDING_RE.findall(text):
        reads.extend(THIS_TOKEN_RE.findall(inner))
        rest = rest.replace("${" + inner + "}", PLACEHOLDER)
    return reads, len(BARE_THIS_RE.findall(rest))


def split_read(path: str) -> tuple:
    """("direct", attr, "") | ("member", collection, attr_tail) for one `this.<path>`."""
    parts = path.split(".")
    if len(parts) == 1:
        return ("direct", parts[0], "")
    return ("member", parts[0], ".".join(parts[1:]))


def name_key_type(attr: str) -> str:
    """<etype> for a name-key-shaped read, or "" when the read carries no type.

    `sample_set_set_id` -> `sample_set_set`. `id` -> "": a name key with no type is not a name key, and
    saying so beats deriving an empty entity type that no table could match.
    """
    if attr.endswith("_id") and len(attr) > len("_id"):
        return attr[:-len("_id")]
    return ""


def read_tables(corpus_dir: str) -> tuple:
    """([table], other_tsv_count, unreadable[]) for the TSVs shipped in the corpus.

    A table is {etype, kind, path, columns}. A header carrying a `{{ ... }}` expression is UNREADABLE and
    is named, not guessed: inventing a column name out of a Jinja expression would let a column check pass
    on a name nobody wrote.
    """
    tables, other, unreadable = [], 0, []
    for dirpath, _dirs, files in os.walk(corpus_dir):
        for fn in sorted(files):
            if not (fn.endswith(".tsv") or fn.endswith(".tsv.tmpl")):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, corpus_dir).replace(os.sep, "/")
            try:
                head = open(full).readline().rstrip("\n").rstrip("\r")
            except (OSError, UnicodeDecodeError) as e:
                # A byte that will not decode is a finding about the table, not a traceback: one bad byte
                # in one header used to kill the run before a single `GSVTK-*` line printed (see
                # `read_workspace_table` for the same pair of exceptions and the same reason).
                unreadable.append("%s (%s)" % (rel, e))
                continue
            fields = head.split("\t")
            first = fields[0].strip()
            if "{{" in first:
                unreadable.append("%s (its type-declaring first column carries a {{ ... }} "
                                  "expression)" % rel)
                continue
            m = TABLE_HEADER_RE.match(first)
            if not m:
                other += 1                                  # workspace.tsv.tmpl and friends: not a table
                continue
            cols, unreadable_cols = [], 0
            for c in fields[1:]:
                c = c.strip()
                if not c:
                    continue
                if "{{" in c:
                    unreadable_cols += 1                    # a name nobody wrote down; never guessed
                    continue
                cols.append(c)
            tables.append({"etype": m.group(2), "kind": m.group(1), "path": rel,
                           "columns": cols, "columns_unreadable": unreadable_cols})
    return sorted(tables, key=lambda t: (t["etype"], t["path"])), other, unreadable


def grade(cfg: dict, tables: list) -> dict:
    """The verdict for one parsed config against the corpus's shipped tables."""
    v = dict(cfg)
    reads, bare, nested = [], 0, 0
    for _where, text, is_nested in binding_strings(cfg["keys"]):
        found, b = reads_in(text)
        reads.extend(found)
        bare += b
        if is_nested:
            nested += len(found)
    direct, member, attrs = [], [], []
    for path in reads:
        kind, first, tail = split_read(path)
        if kind == "direct":
            if first not in direct:
                direct.append(first)
        else:
            if first not in member:
                member.append(first)
            attrs.append("%s.%s" % (first, tail))
    key_types = []
    for attr in direct:
        et = name_key_type(attr)
        if et and et not in key_types:
            key_types.append(et)
    v.update(direct=direct, member=member, member_attrs=attrs, bare_this=bare, nested=nested,
             namekey_types=key_types, this_reads=len(reads), etype="", table=None, table_kind="",
             columns=0, missing=[], tables_of_type=0)

    if not cfg["ok"]:
        v.update(state="UNRESOLVED", reason="cannot-parse")
        return v
    if not key_types:
        v.update(state="UNRESOLVED", reason="no-name-key")
        return v
    if len(key_types) > 1:
        v.update(state="UNRESOLVED", reason="many-name-keys")
        return v
    v["etype"] = key_types[0]
    of_type = [t for t in tables if t["etype"] == key_types[0]]
    v["tables_of_type"] = len(of_type)
    if not of_type:
        v.update(state="UNRESOLVED", reason="no-shipped-table")
        return v
    # What the type has to carry: every single-component read, and every member collection (a collection
    # is a column of the root row before anything can scatter over it). The name column is implicit.
    wanted = sorted(set(direct) | set(member))
    cover, best = None, None
    for t in of_type:
        cols = set(t["columns"]) | {"%s_id" % key_types[0]}
        miss = [w for w in wanted if w not in cols]
        if not miss:
            cover = t
            break
        if best is None or len(miss) < len(best[1]):
            best = (t, miss)
    chosen = cover or (best[0] if best else None)
    v["table"] = chosen["path"] if chosen else None
    v["table_kind"] = chosen["kind"] if chosen else ""
    v["columns"] = len(chosen["columns"]) if chosen else 0
    v["missing"] = [] if cover else (best[1] if best else [])
    v.update(state="OK" if cover else "UNRESOLVED", reason="" if cover else "columns-missing")
    return v


def workspace_reads(cfg: dict) -> int:
    """How many bindings read `${workspace.*}` — quoted by the zero-name-key reason, never guessed."""
    n = 0
    for _p, text, _nested in binding_strings(cfg["keys"]):
        n += sum(1 for inner in BINDING_RE.findall(text) if "workspace." in inner)
    return n


def _row_shown(row: str) -> str:
    """One workspace-table row, printable on one line: tabs and CRs shown, so a finding quotes the bytes."""
    return row.replace("\t", "\\t").replace("\r", "\\r")


def read_workspace_table(full: str, rel: str) -> dict:
    """Parse one flavor's `workspace.tsv.tmpl`: the names it declares, and every structural finding.

    Only the FIRST tab-separated field of each row is parsed. The second is an upstream values-profile
    expression (`{{ dockers.cloud_sdk_docker }}`) and parsing it would answer "what did that values profile
    bind" instead of "what does this ref declare" — which is why the census can print `values=not-read`
    rather than merely claim it. A finding does quote its whole row, because a row's shape (a stray third
    field, an unparseable name) is what the finding is about; those quoted bytes are never parsed and decide
    nothing. There is no header row: every physical line is a data row, and a reader
    that skipped line 1 would lose an attribute (`cloud_sdk_docker` in both real tables).

    A finding never repairs anything: a bad name is not lowercased, trimmed of odd characters or dropped
    from the count of ROWS read, and a duplicate is not collapsed. `names` is in table order, deduplicated
    only in the sense that a name declared twice is one declared name — and that duplication is itself the
    `DUPE` finding, printed with the row numbers.
    """
    # `value_cells` COUNTS second fields; it never looks inside one. The scope line needs to say how many
    # attribute values exist and remain unread, which is a different fact from "there were no rows".
    o = {"found": True, "path": rel, "rows": 0, "names": [], "malformed": [], "dupes": [],
         "value_cells": 0}
    try:
        text = open(full).read()
    except (OSError, UnicodeDecodeError) as e:
        # The file is there and will not open, or opens and will not decode. `ABSENT` would be a lie (the
        # flavor does ship a table) and `OK` would be worse, so it is reported as a finding about the table
        # itself. `UnicodeDecodeError` is caught with `OSError` because it is a ValueError, not an OSError:
        # left alone it escapes as a traceback, and a traceback exits 1 on a path whose exit code this
        # layer has promised not to move.
        o["malformed"].append("the flavor's own workspace table would not open and read as text (%s), so "
                              "nothing it declares is known" % e)
        return o
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()                                       # the final newline's artifact, not a blank row
    seen = {}
    for i, row in enumerate(lines, 1):
        o["rows"] += 1
        fields = row.split("\t")
        if len(fields) > 1 and fields[1].strip():
            # Counted before the name is judged, because a row whose NAME is odd still carries a value that
            # nobody opened: the scope line asks how many attribute values remain unexamined, and skipping
            # the malformed rows would answer that with a smaller number than the file holds.
            o["value_cells"] += 1
        name = fields[0].strip()
        if name.startswith(WORKSPACE_PREFIX):
            name = name[len(WORKSPACE_PREFIX):].strip()
        if not WORKSPACE_NAME_RE.match(name):
            o["malformed"].append("row %d: `%s` is not an attribute name ([A-Za-z0-9_.]+) — row: `%s`"
                                  % (i, name, _row_shown(row)))
            continue
        if len(fields) > 2:
            # The row still names its attribute in its first field, so the name IS declared: refusing to
            # count it would turn a stray tab in the value half into "this flavor does not declare X",
            # which is a louder claim than the row supports. The row is a finding either way, and the
            # flavor's status says MALFORMED, so nothing downstream reads this table as clean.
            o["malformed"].append("row %d: %d tab-separated field(s), not the documented 2 — name `%s` is "
                                  "still declared; row: `%s`"
                                  % (i, len(fields), name, _row_shown(row)))
        seen.setdefault(name, []).append(i)
        if name not in o["names"]:
            o["names"].append(name)
    for name in o["names"]:
        rows_at = seen[name]
        if len(rows_at) > 1:
            o["dupes"].append("`%s` is declared %d times (rows %s) — one row per attribute is the shape "
                              "Terra imports, so which value that attribute ends up with is not in this file"
                              % (name, len(rows_at), ", ".join(str(r) for r in rows_at)))
    return o


def nearby_tsvs(flavor_dir: str, corpus_dir: str) -> list:
    """Every TSV a flavor ships, corpus-relative and sorted — printed when its oracle is missing.

    "Name the missing thing" (CONTRIBUTING.md). Upstream ships `sample.tsv.tmpl`, `participant.tsv.tmpl`
    and `sample_set_membership_1kgp.tsv.tmpl` in the same directories, so an `ABSENT` that stops at "no such
    file" reads like a tool that wants a file it will not describe. This list is what lets an operator see
    the difference between a flavor that ships no tables, one whose table is named almost right, and one
    whose table sits in the wrong directory.
    """
    if not os.path.isdir(flavor_dir):
        return []
    found = []
    for dirpath, _dirs, files in os.walk(flavor_dir):
        for fn in files:
            if fn.endswith(".tsv") or fn.endswith(".tsv.tmpl"):
                found.append(os.path.relpath(os.path.join(dirpath, fn), corpus_dir).replace(os.sep, "/"))
    return sorted(found)


def read_workspace_oracle(corpus_dir: str, flavors: list) -> list:
    """One answer per flavor: the attribute names that flavor's own workspace table declares.

    The flavor list comes from where the CONFIGS live, never from where tables were found: a reader that
    enumerated tables could only ever report flavors that have a table, and then `ABSENT` — the answer that
    says "nothing this flavor's bindings read can be checked offline" — would be unreachable, and a check
    that can never fire is not a check (CONTRIBUTING.md).

    Status precedence is ABSENT, then MALFORMED, then DUPE, then OK. One word has to answer, so a flavor
    whose table is both malformed and duplicated is counted once, under the earlier word — and both
    findings are printed in full, so the precedence chooses a label, not a fact.
    """
    out = []
    for flavor in sorted(set(flavors)):
        sub = "" if flavor == ROOT_FLAVOR else flavor
        expected = WORKSPACE_TABLE if flavor == ROOT_FLAVOR else "%s/%s" % (flavor, WORKSPACE_TABLE)
        flavor_dir = os.path.join(corpus_dir, sub)
        full = os.path.join(flavor_dir, WORKSPACE_TABLE)
        if os.path.isfile(full):
            o = read_workspace_table(full, expected)
        else:
            o = {"found": False, "path": None, "rows": 0, "names": [], "malformed": [], "dupes": [],
                 "value_cells": 0}
        o["flavor"] = flavor
        o["expected"] = expected
        o["attrs"] = len(o["names"])
        o["nearby"] = nearby_tsvs(flavor_dir, corpus_dir) if not o["found"] else []
        o["status"] = ("OK" if o["found"] and not o["malformed"] and not o["dupes"]
                       else "ABSENT" if not o["found"]
                       else "MALFORMED" if o["malformed"] else "DUPE")
        out.append(o)
    return out


def oracle_machine_line(o: dict) -> str:
    """The machine line for one flavor. `attrs=0 status=OK` (an empty table) is not `status=ABSENT`."""
    return "GSVTK-ORACLE flavor=%s file=%s attrs=%s status=%s" % (
        o["flavor"], o["path"] or "-", o["attrs"], o["status"])


def oracle_report(o: dict) -> list:
    """The human lines for one flavor: what was looked for, what answered, every finding quoted."""
    fmt = "  %-14s %-9s "                 # flavor, status, then a space before the sentence
    label = fmt % (o["flavor"][:14], o["status"])
    pad = " " * len(fmt % ("", ""))       # continuation lines line up under the sentence
    if o["status"] == "ABSENT":
        looked = ("no %s — that flavor has configs and ships no workspace attribute table, so if any of the "
                  "${workspace.*} names its bindings read, none of them can be checked against anything "
                  "(reads are not attributed to a flavor here: what is missing is the table, which is what "
                  "every such read would have to be found in" % o["expected"])
        out = [label + looked]
        if o["nearby"]:
            out.append("%sTSV(s) that flavor does ship: %s" % (pad, ", ".join(o["nearby"])))
        else:
            out.append("%sthat flavor ships no TSV of any kind" % pad)
        misfiled = [n for n in o["nearby"] if os.path.basename(n) == WORKSPACE_TABLE]
        if misfiled:
            out.append("%s%d of them carries the oracle's own name but is not the flavor's table: %s — the "
                       "table has to sit beside the flavor's configs (%s), because the flavor is read off "
                       "where its configs live, not off where a table happens to be"
                       % (pad, len(misfiled), ", ".join(misfiled), o["expected"]))
        return out
    listed = ", ".join(o["names"][:ORACLE_NAMES_SHOWN])
    if len(o["names"]) > ORACLE_NAMES_SHOWN:
        listed += " (+%d more, not listed)" % (len(o["names"]) - ORACLE_NAMES_SHOWN)
    out = ["%s%s declares %d attribute(s) from %d row(s): %s"
           % (label, o["path"], o["attrs"], o["rows"], listed or "-")]
    if not o["names"] and not o["malformed"]:
        out.append("%sthe table exists and declares nothing — a different answer from ABSENT, which says "
                   "there is no table to declare anything" % pad)
    out += ["%sMALFORMED %s" % (pad, f) for f in o["malformed"]]
    out += ["%sDUPE %s" % (pad, f) for f in o["dupes"]]
    return out


def _json_string_spans(text: str) -> list:
    """[(start, end)] for the JSON string literals in `text`, quotes included, escapes honoured.

    The value layer needs one fact the entity layer never asked for: whether a `{{ ... }}` sits INSIDE a
    string (a fragment of a value) or BARE where a value was expected — which is how upstream binds a whole
    bundle render (`"Foo.tracks": {{ reference_resources.clustering_tracks | tojson }}`). Substituting a
    marker string for a bare expression needs quotes the expression does not carry, and putting those quotes
    in the wrong place would produce a document that is not the one the entity layer read. So: one pass, no
    heuristic — a string starts at a `"` and ends at the next unescaped `"`.
    """
    spans, i, n = [], 0, len(text)
    while i < n:
        if text[i] != '"':
            i += 1
            continue
        j = i + 1
        while j < n:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == '"':
                break
            j += 1
        spans.append((i, min(j + 1, n)))
        i = spans[-1][1]
    return spans


def parse_with_bundle_marks(path: str, jinja_re) -> dict:
    """Re-read one config with every `{{ ... }}` replaced by a MARKER STRING instead of `null`.

    The same shared pattern the entity layer uses (`JINJA_RE`, never a second definition), a different
    substitution: `null` answers "will this document parse", and a marker answers "which key would have
    carried this render". `exprs` holds each expression in document order so a marker can be named back as
    `bundle=reference_resources`, and `claimed` holds every marker the document put in a key or a value
    INCLUDING the duplicate-key values `dict()` dropped — and walking the CONTAINERS inside those dropped
    values, so a dropped list's marker is CLAIMED and its reads counted rather than refused. That is what
    makes the paired guard in `classify_values` the intra-file tripwire it now says it is.

    `dropped` is the other half of the duplicate-key ruling. This reader is the PARSED one, so a key bound
    twice is classified on its LAST value (`json.loads`'s own rule, named on the entity line as `dupkeys=`)
    and what the dropped side read is counted here rather than quietly missing. That is why the raw text and
    this reader legitimately disagree at `e1909d2f` — 305 `${workspace.` occurrences against 304 parsed —
    and one of them had to say which side it was on.
    """
    out = {"ok": False, "obj": {}, "exprs": [], "claimed": set(), "dropped": {"values": 0, "ws_reads": 0},
           "error": ""}
    try:
        text = open(path).read()
    except (OSError, UnicodeDecodeError) as e:
        # Both layers read the same file, so both have to refuse the same undecodable byte: caught here
        # this becomes `values-unreadable` on the value line instead of a traceback with no machine lines
        # at all (see `read_workspace_table`).
        out["error"] = str(e)
        return out
    spans = _json_string_spans(text)
    exprs = []

    def swap(m):
        src = JINJA_MARK_SRC % len(exprs)
        exprs.append(m.group(0))
        return src if any(s < m.start() < e for s, e in spans) else '"%s"' % src

    claimed = set()
    dropped = out["dropped"]

    def keep(pairs):
        last = {}
        for k, val in pairs:
            for side in (k, val):
                # `_reads_of` walks containers, so a side that is a LIST or an OBJECT contributes the markers
                # of every string inside it. `isinstance(side, str)` alone saw none of them, and the marker
                # carried by a duplicated key's dropped LIST then reached the `unclaimed` guard below.
                claimed.update(str(m) for m in _reads_of(side)[0])
            if k in last:
                # The DROPPED side is the EARLIER value: `dict(pairs)` keeps the last, so `val` here is the
                # survivor and `last[k]` is what is being discarded. Reading `val` counted a read twice and
                # answered "0 read(s) dropped" for a key whose dropped value really carried some — invisible
                # at `e1909d2f`, where `mei_bed` is bound to two values that each read one attribute, and
                # immediate in this repo's own `DroppedList` fixture.
                dropped["values"] += 1
                _marks, ws, _ths = _reads_of(last[k])
                dropped["ws_reads"] += len(ws)
            last[k] = val
        return dict(pairs)                                # last wins, exactly json.loads' own rule

    substituted = jinja_re.sub(swap, text)
    try:
        obj = json.loads(substituted, object_pairs_hook=keep)
    except ValueError as e:
        out["error"] = str(e)
        return out
    if not isinstance(obj, dict):
        out["error"] = "top level is a %s, not an object of bindings" % type(obj).__name__
        return out
    _unbraced, why = unfinished_template(substituted)
    if why:
        # The entity layer refuses this document; so does this one, under its own word. A value layer that
        # called unfinished template text `literal` would be saying a launch may carry it, and "refused by
        # one reader, passed by the other" is the split this file exists to close.
        out["error"] = why
        return out
    out.update(ok=True, obj=obj, exprs=exprs, claimed=claimed)
    return out


def walk_values(value, path: str = "", depth: int = 0) -> list:
    """[(key_path, scalar, depth)] for every JSON scalar in a parsed config, containers entered.

    Deliberately broader than `walk_strings`, which stops at strings: `false`, `15` and `null` are values a
    launch carries too, and the class that says so (`literal`) has to count them or the identity
    `bindings == sum of the class fields` would be an identity about strings. `depth` 1 is a binding value of
    the config map itself; 2+ sits inside a nested object or list, which is the shape a shallow reader loses —
    reported per config as `nested-reads=`, never assumed away.
    """
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            out.extend(walk_values(v, "%s.%s" % (path, k), depth + 1))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.extend(walk_values(v, "%s[%d]" % (path, i), depth + 1))
    else:
        out.append((path, value, depth))
    return out


def _leaf_key(path: str) -> str:
    """The leaf key name of a walk path: `.Foo.manta_docker` -> `manta_docker`, `.Foo.args[2]` -> `args`.

    The `_docker` rule reads the leaf, because a binding key is `<workflow>.<attr>` and it is the attribute
    half that says `docker`. An element index is not a name, so it is stripped.
    """
    return re.sub(r"(?:\[\d+\])+\Z", "", path.split(".")[-1])


def _reads_of(value):
    """(bundle marker indexes, workspace names, this paths) carried by one config value.

    Containers are WALKED, never skipped, because the duplicate-key scan asks this question about a DROPPED
    value and a dropped `["${workspace.X}", …]` is a read the document really carries: answering "none" for
    it is what turned a duplicated key into an unattributable bundle read. A scalar that is not a string
    carries none, and that is an answer rather than an error: `false` and `15` are values the config itself
    writes down, which is what makes `literal` the one class that supplies a real value.
    """
    marks, ws, ths = [], [], []
    for _path, text, _depth in walk_strings(value):
        marks.extend(int(m) for m in JINJA_MARK_RE.findall(text))
        for inner in BINDING_RE.findall(text):
            ws.extend(WORKSPACE_READ_RE.findall(inner))
            ths.extend(THIS_TOKEN_RE.findall(inner))
    return marks, ws, ths


def _bundle_root(expr: str) -> str:
    """The root key of one `{{ root.attr | tojson }}` expression — the bundle's NAME, never a file."""
    m = BUNDLE_ROOT_RE.search(expr)
    return m.group(1) if m else "?"


def _test_profile_stem(name: str) -> str:
    """The stem of a shipped test profile's FILE NAME, or "" when that name is not one.

    The one spelling of "is this the name of a shipped test profile", shared by the two ways this file lists
    them (`--tree` walks a directory, `--repo/--ref` asks the ref). The name is the whole claim: a profile's
    bytes are never opened, because opening one answers "what did that profile bind" instead of "what does
    this ref bind" — the same reason a config's `{{ ... }}` is neutralised rather than rendered, and the
    reason the oracle's second column is never opened.
    """
    if name.startswith(TEST_PROFILE_PREFIX) and name.endswith(".json"):
        return name[:-len(".json")]
    return ""


def test_profile_stems(tree: str) -> list:
    """The stems of the shipped `inputs/values/test_*.json` profiles in a MATERIALIZED tree: names off the
    directory listing, contents closed.

    This is the `--tree` branch. The ref branch (`test_profile_stems_at_ref`) is NOT the same call: the
    archive this tool builds holds one subtree, so there is no `inputs/values` directory in it to walk.
    """
    out = []
    for dirpath, _dirs, files in os.walk(os.path.join(tree, TEST_PROFILE_DIR)):
        for fn in files:
            stem = _test_profile_stem(fn)
            if stem:
                out.append(stem)
    return sorted(set(out))


def test_profile_stems_at_ref(repo: str, ref: str) -> list:
    """The same stems, listed off the REF with `git ls-tree` — one plumbing call, no unpack, nothing opened.

    Why this exists separately: `_tree_from_ref` archives ONE subtree (the corpus), so on the `--repo/--ref`
    path — the path that reads real data, and the one the real-ref block grades — the temp tree holds
    `inputs/templates/terra_workspaces` and NOTHING else. Walking it for `inputs/values/test_*.json` returns
    nothing at any ref, which left the shipped-stem half of `_test_bundle_reason` unreachable exactly where
    it matters, and let its sentence print `shipped test profile(s): none` about a ref that ships
    `test_single_sample_NA12878.json` and `…_NA19240.json`. The claim is about NAMES only, so asking git to
    list them answers it without unpacking a second subtree.

    A listing that fails returns the same empty list as a ref that ships no profiles, and that costs only
    the parenthetical list inside one refusal sentence: the `test`-root rule refuses either way, so a failed
    listing can never turn a refusal into a pass.
    """
    lst = subprocess.run(["git", "-C", os.path.expanduser(repo), "ls-tree", "--name-only", ref,
                          TEST_PROFILE_DIR + "/"], capture_output=True)
    if lst.returncode != 0:
        return []
    out = []
    for line in (lst.stdout or b"").decode("utf-8", "replace").splitlines():
        stem = _test_profile_stem(os.path.basename(line.strip()))
        if stem:
            out.append(stem)
    return sorted(set(out))


def _test_bundle_reason(value, roots: list, test_profiles: list) -> str:
    """Why this value belongs to a TEST bundle, or "" when nothing here says it does.

    Two ways in, both decided on names alone. A `{{ root.… }}` whose ROOT names a test profile: the stem of a
    shipped `inputs/values/test_*.json`, or a root that itself begins `test` — upstream's two test profiles
    are `test_single_sample_NA12878` by file and call themselves `test_NA12878` by their own `name` field, so
    both spellings have to refuse. And a string carrying a test sample's coordinate wherever it sits, so a
    test path inside an otherwise plain string is refused rather than classed LITERAL. The cost of the prefix
    rule is a hypothetical non-test root that happens to begin `test`, which is the direction this tool is
    allowed to be wrong in.
    """
    for root in roots:
        if root.lower().startswith(TEST_PROFILE_PREFIX):
            return ("its `{{ %s.* }}` read names a test values profile by root (shipped test profile(s): "
                    "%s; no profile was opened to decide this)" % (root, ", ".join(test_profiles) or "none"))
        # Subsumed while the rule above is a prefix test: every stem `test_profile_stems*` can return begins
        # `test_`, so the arm above answers first for every root this list could name. It stays because it is
        # the more specific sentence (it names the FILE), and it becomes the arm that fires the moment the
        # prefix rule is narrowed to the profiles this ref actually ships.
        if root in test_profiles:
            return "its `{{ %s.* }}` read names the shipped test profile inputs/values/%s.json" % (root, root)
    if isinstance(value, str):
        for marker in TEST_COORDINATE_MARKERS:
            if marker in value:
                return ("it carries the test-sample coordinate `%s`, the shape a test bundle's data comes in"
                        % marker)
    return ""


def classify_values(full: str, rel: str, flavor: str, wf: str, oracle: dict, jinja_re,
                    test_profiles: list) -> dict:
    """Every value of one config, each classed by the bundle it came from. Deep, one class per value.

    `oracle` is that flavor's record from `read_workspace_oracle()`, and only `status == "OK"` grants
    resolution (ruling R16). The count of names read out of a table is NOT a cleanliness signal (ruling R10),
    so a MALFORMED or DUPE table resolves nothing — not even the names it read cleanly — and says so where a
    human can check it.

    The file is re-read here rather than handed the entity layer's parse: that one substitutes `null`, and a
    `null` cannot be traced back to the key that would have carried a bundle render. Class precedence is
    fixed: `*_docker` first (excluded by rule, whatever the value says — and the rule now reads BOTH ends, so
    a value that IS a `*_docker` attribute read is docker-shaped under any key, ruling R18 as narrowed by
    ruling R20: the whole value only, because an exclusion must not swallow the grading of a longer string),
    then a test bundle
    (refused wherever it appears), then the whole-value forms, then `mixed` for anything still carrying a
    read, then `literal`. Refusing is the conservative direction, so a MIXED value whose `${workspace.*}` read
    the flavor's oracle cannot answer refuses too, under its own `mixed-` word rather than passing on its
    surrounding text.
    """
    classes = dict((c, 0) for c in VALUE_CLASSES)
    v = {"config": rel, "flavor": flavor, "wf": wf, "classes": classes, "bindings": 0, "nested_reads": 0,
         "ws_reads": 0, "ws_resolved": 0, "ws_missing": 0, "ws_no_oracle": 0, "bundles": [],
         "dropped_values": 0, "dropped_ws_reads": 0, "docker_keys": [], "docker_unenforced": [],
         "findings": [], "reasons": [], "unresolved": 0, "state": "OK", "unclassified": "",
         # The per-key side of the same census, for the layer below: `class_of[key path]` is the class this
         # loop just gave that value, and `containers` is the paths holding an object or a list instead of a
         # scalar. Built HERE, in the one pass that classes anything, so the required-input layer grades each
         # required key against THIS document's verdict on the bytes carrying it — never a second reader
         # that could disagree with the first (the split `values-unreadable` above exists to close).
         "class_of": {}, "containers": []}
    parsed = parse_with_bundle_marks(full, jinja_re)
    if not parsed["ok"]:
        # A document this layer cannot read is not a document with no values in it: `values-unreadable` and
        # `no-values` are different answers and must never print as one 0.
        v["unclassified"] = "values-unreadable"
        v["reasons"].append("values-unreadable")
        v["findings"].append({"key": "-", "cls": "-", "reason": "values-unreadable",
                              "detail": parsed["error"], "names": []})
        v["state"] = "UNRESOLVED"
        return v
    clean = bool(oracle) and oracle["status"] == "OK"
    declared = set(oracle["names"]) if clean else set()
    status = oracle["status"] if oracle else "ABSENT"
    obj, exprs = parsed["obj"], parsed["exprs"]
    seen = set()
    for path, value, depth in walk_values(obj):
        key = path[1:] if path.startswith(".") else path
        marks, ws, ths = _reads_of(value)
        seen.update(str(m) for m in marks)
        for name in ws:
            v["ws_reads"] += 1
            if not clean:
                v["ws_no_oracle"] += 1
            elif name in declared:
                v["ws_resolved"] += 1
            else:
                v["ws_missing"] += 1
        if depth > 1:
            v["nested_reads"] += len(marks) + len(ws) + len(ths)
        roots = [_bundle_root(exprs[i]) for i in marks if i < len(exprs)]
        for root in roots:
            if root not in v["bundles"]:
                v["bundles"].append(root)
        whole_ws = WORKSPACE_WHOLE_RE.match(value) if isinstance(value, str) else None
        whole_mark = JINJA_MARK_RE.fullmatch(value) if isinstance(value, str) else None
        whole_this = THIS_WHOLE_RE.match(value) if isinstance(value, str) else None
        test_bundle = _test_bundle_reason(value, roots, test_profiles)
        # R18: the docker rule reads both ends of the binding. The KEY LEAF first — a `*_docker` key is
        # excluded whatever its value says, including a value that would otherwise refuse as a test bundle,
        # which is the precedence this file shipped with. Then the VALUE: a `${workspace.<name>_docker}` is
        # an image whoever the key is called, and classing it `workspace-attr` would hand a launch a
        # container resolved from an ambient profile.
        # R20 narrows that VALUE end to the WHOLE value, which is what "the value IS the image" claims and
        # `whole_ws` (a fullmatch of one `${workspace.X}`) is the only reader of. Asking instead "does this
        # value mention a `*_docker` read anywhere" made an EXCLUSION swallow the grading of everything else
        # in the string: `docker` refuses nothing, so `--img ${workspace.sv_docker} --ref ${workspace.X}`
        # stopped being graded for `X` and launched, while the identical missing `X` in a value that named no
        # image refused. A command string that embeds an image reference is a command, and whether the launch
        # is launchable cannot depend on whether somebody concatenated.
        # R18: the docker rule reads BOTH ends of the binding. The VALUE end first: a
        # `${workspace.<name>_docker}` is an image whoever the key is called, and classing it
        # `workspace-attr` would hand a launch a container resolved from an ambient profile.
        # R20 narrows that VALUE end to the WHOLE value, which is what "the value IS the image" claims and
        # `whole_ws` (a fullmatch of one `${workspace.X}`) is the only reader of. Asking instead "does this
        # value mention a `*_docker` read anywhere" made an EXCLUSION swallow the grading of everything else
        # in the string: `docker` refuses nothing, so `--img ${workspace.sv_docker} --ref ${workspace.X}`
        # stopped being graded for `X` and launched, while the identical missing `X` in a value that named no
        # image refused. A command string that embeds an image reference is a command, and whether the launch
        # is launchable cannot depend on whether somebody concatenated.
        # The KEY-LEAF end now comes LAST, which is the same rule read from the other side: an EXCLUSION may
        # not outrank a REFUSAL. Tested first, `*_docker` on the leaf swallowed two refusals it had no
        # business swallowing — a `manta_docker` key bound to a `gs://` test path never met the TEST-BUNDLE
        # rule, and a `manta_docker` key bound to `${workspace.X}` never met the rule that says this flavor
        # does not declare `X`. Measured both ways in this repo's own fixtures (`beta/KeyLeafDocker`,
        # `beta/KeyLeafMissing`): the SAME value refused under a key called `manta_image` and passed under a
        # key called `manta_docker`, decided by nothing but the name of the key — the class of bug R20
        # closed one arm over. Measured 0 instances of the two swallowed shapes on the real tree at
        # `e1909d2f` (all 101 docker bindings there are a `_docker` leaf bound to a `_docker` workspace
        # read), so this moves no real number: it makes the answer for the shape not depend on the key.
        whole_docker = bool(whole_ws) and whole_ws.group(1).endswith(DOCKER_KEY_SUFFIX)
        leaf_docker = _leaf_key(path).endswith(DOCKER_KEY_SUFFIX)
        refused = False                       # did this VALUE meet a refusal, whatever class it landed in
        if whole_docker:
            cls = DOCKER_CLASS
        elif test_bundle:
            cls = "test-bundle"
            refused = True
            v["findings"].append({"key": key, "cls": cls, "reason": "test-bundle",
                                  "detail": test_bundle, "names": []})
        elif whole_mark:
            cls = "needs-bundle"
        elif whole_ws:
            name = whole_ws.group(1)
            if not clean:
                cls = "no-oracle"
                refused = True
                v["findings"].append({"key": key, "cls": cls, "reason": NO_ORACLE_REASONS[status],
                                      "detail": name, "names": [name]})
            elif name in declared:
                cls = "workspace-attr"
            else:
                cls = "workspace-missing"
                refused = True
                v["findings"].append({"key": key, "cls": cls, "reason": "workspace-missing",
                                      "detail": name, "names": [name]})
        elif whole_this:
            cls = "entity"
        elif marks or ws or ths:
            cls = "mixed"
            if ws and not clean:
                refused = True
                v["findings"].append({"key": key, "cls": cls, "reason": MIXED_NO_ORACLE_REASONS[status],
                                      "detail": "", "names": sorted(set(ws))})
            elif ws and any(n not in declared for n in ws):
                refused = True
                v["findings"].append({"key": key, "cls": cls, "reason": "mixed-workspace-missing",
                                      "detail": "", "names": sorted(n for n in ws if n not in declared)})
        else:
            cls = "literal"
        if leaf_docker and not refused:
            # The key-leaf exclusion, applied last: a `*_docker` key is withheld whatever its value says —
            # `--image KEY=REF` or a profile pin is the only source — but only for a value that refused
            # nothing, because an exclusion a key name can win is a pass the key name can grant.
            cls = DOCKER_CLASS
        if cls == DOCKER_CLASS:
            v["docker_keys"].append(key)
            if not leaf_docker:
                # Docker-shaped by its VALUE alone. The launcher's pin gate keys on the KEY (see
                # `docker_unenforced` in `value_report`), so this is an exclusion this tool applies alone.
                v["docker_unenforced"].append(key)
        classes[cls] += 1
        v["class_of"][key] = cls
        v["bindings"] += 1
    # Same path spelling `walk_values` uses, leading dot stripped; the document's own root (`path` "") is
    # not a key and is dropped with it.
    v["containers"] = [p[1:] if p.startswith(".") else p
                       for p, _kind in walk_containers(obj) if p]
    v["dropped_values"] = parsed["dropped"]["values"]
    v["dropped_ws_reads"] = parsed["dropped"]["ws_reads"]
    # This guard is a tripwire on THIS FILE's own two scans disagreeing, and it is unreachable by input.
    # Both scans are exhaustive over one document: `parse_with_bundle_marks`'s hook claims every marker it
    # finds in a KEY or a VALUE of every object pair — including the earlier value of a duplicated key,
    # which is the side `dict()` drops, and including the strings INSIDE that dropped value, because the
    # scan walks containers through `_reads_of` (that walking is the half ruling R19 got wrong) — while
    # `seen` above collects the markers of every scalar `walk_values` reaches, arrays and nested objects
    # included, and `json.loads` parses a nested object even inside a value it then discards. So the
    # outcome R19 stated was right and the reason it gave was false: before the scan walked, a duplicated
    # key whose dropped value was a LIST carrying a `{{ … }}` marker DID reach this arm, and the sentence it
    # built crashed with `TypeError: %d format: a real number is required, not str` — zero machine lines,
    # exactly the shape the undecodable-byte fix one section up had to close. A guard whose firing path
    # crashes guards nothing, so the format arguments are fixed, and what is left is the failure this arm
    # can actually see: a future substitution landing a marker outside both scans. No INPUT can produce one
    # — the selftest reaches the arm by INJECTING the disagreement, a parse whose `exprs` holds an
    # expression no key or value carries, because nothing else can — and no corpus in this repo prints this
    # reason (measured: the fixture corpus and `e1909d2f` both print it 0 times, which leg D1 asserts).
    unclaimed = [i for i in range(len(exprs)) if str(i) not in seen and str(i) not in parsed["claimed"]]
    if unclaimed:
        v["findings"].append({"key": "-", "cls": "-", "reason": "bundle-read-unattributed",
                              "detail": "%d marker(s) in a document whose every other marker is claimed"
                                        % len(unclaimed),
                              "names": [exprs[i].strip() for i in unclaimed]})
    if not v["bindings"]:
        v["reasons"].append("no-values")
    for f in v["findings"]:
        if f["reason"] not in v["reasons"]:
            v["reasons"].append(f["reason"])
    v["unresolved"] = sum(classes[c] for c in VALUE_REFUSAL_CLASSES)
    if v["unresolved"] or v["reasons"]:
        v["state"] = "UNRESOLVED"
    return v


def _value_class_tail(classes: dict) -> str:
    """The class fields, generated FROM `VALUE_CLASSES` so a class cannot exist without being printed."""
    return " ".join("%s=%s" % (cls, classes[cls]) for cls in VALUE_CLASSES)


def _value_read_tail(nested, ws, resolved, missing, no_oracle, bundles, dropped_values,
                     dropped_reads) -> str:
    """The read-level fields, shared by the per-config line and the census so the two cannot drift.

    `workspace-reads` is the sum of the three beside it, and it is the number a phase reconciles against the
    corpus (`304 = 288 resolved + 16 missing + 0 unanswerable` at `e1909d2f`). `bundles` names the roots
    disclosed by this config without resolving any of them, and the `dropped-` pair is what last-wins threw
    away on a duplicated key — the reason a raw reader and this one disagree by exactly one read there.
    """
    return ("nested-reads=%s workspace-reads=%s workspace-resolved-reads=%s workspace-missing-reads=%s "
            "no-oracle-reads=%s bundles=%s dropped-values=%s dropped-workspace-reads=%s"
            % (nested, ws, resolved, missing, no_oracle, ",".join(bundles) or "-", dropped_values,
               dropped_reads))


def value_machine_line(v: dict) -> str:
    """The value line for one config, printed beside its `GSVTK-ENTITY` line.

    `bindings=` is `sum(class fields)`, computed, so the identity cannot be quoted at a caller it does not
    hold. `unresolved=` counts the VALUES in the three refusing classes; `state=UNRESOLVED` is wider, because
    a MIXED value whose read the flavor's oracle cannot answer, a config that binds no value at all, a
    document this layer could not read and an unattributable bundle read all refuse without landing in a
    refusing CLASS — which is why their words are in `reasons=` and never folded into the sum.
    """
    return ("GSVTK-VALUE config=%s flavor=%s wf=%s bindings=%s %s %s unresolved=%s state=%s reasons=%s"
            % (v["config"], v["flavor"], v["wf"], v["bindings"], _value_class_tail(v["classes"]),
               _value_read_tail(v["nested_reads"], v["ws_reads"], v["ws_resolved"], v["ws_missing"],
                                v["ws_no_oracle"], v["bundles"], v["dropped_values"],
                                v["dropped_ws_reads"]), v["unresolved"], v["state"],
               ",".join(v["reasons"]) or "-"))


def value_summary_line(records: list) -> str:
    """The census of the value lines, so a phase can assert the per-config lines add up.

    Every total is summed from the per-config records — `bindings` included, which is the point of computing
    it from the class fields rather than beside them (ruling R15). These are VALUE-level and READ-level
    counts, a different unit from the oracle's flavor-status buckets, which is why they live here and not
    there (ruling R11). The last five counters exist so that "no configs", "no values", "nothing I could
    read" and "every value resolved" stay four answers: an empty corpus prints `configs=0` at exit 3, a
    corpus of empty documents prints `configs-no-values`, an unreadable document prints
    `configs-unclassified`, and a clean one prints `unresolved=0` with both of those at 0.
    """
    tot = dict((c, 0) for c in VALUE_CLASSES)
    keys = ("bindings", "nested_reads", "ws_reads", "ws_resolved", "ws_missing", "ws_no_oracle",
            "dropped_values", "dropped_ws_reads", "unresolved")
    sums = dict((k, 0) for k in keys)
    bundles = []
    cfg = dict((k, 0) for k in ("unresolved", "no-oracle", "needs-bundle", "no-values", "unclassified"))
    for v in records:
        for cls in VALUE_CLASSES:
            tot[cls] += v["classes"][cls]
        for k in keys:
            sums[k] += v[k]
        bundles.extend(b for b in v["bundles"] if b not in bundles)
        if v["state"] != "OK":
            cfg["unresolved"] += 1
        if v["classes"]["no-oracle"]:
            cfg["no-oracle"] += 1
        if v["classes"]["needs-bundle"]:
            cfg["needs-bundle"] += 1
        # A document this layer could not read has 0 classified values, and counting it as `no-values` too
        # would be exactly the collapse this file exists to prevent: "I could not read it" and "it holds
        # nothing" are different answers, so the unreadable one is counted only as unclassified.
        if not v["bindings"] and not v["unclassified"]:
            cfg["no-values"] += 1
        if v["unclassified"]:
            cfg["unclassified"] += 1
    return ("GSVTK-VALUE-SUMMARY configs=%s bindings=%s %s %s unresolved=%s configs-unresolved=%s "
            "configs-no-oracle=%s configs-needs-bundle=%s configs-no-values=%s configs-unclassified=%s"
            % (len(records), sums["bindings"], _value_class_tail(tot),
               _value_read_tail(sums["nested_reads"], sums["ws_reads"], sums["ws_resolved"],
                                sums["ws_missing"], sums["ws_no_oracle"], bundles, sums["dropped_values"],
                                sums["dropped_ws_reads"]), sums["unresolved"], cfg["unresolved"],
               cfg["no-oracle"], cfg["needs-bundle"], cfg["no-values"], cfg["unclassified"]))


def _declared_names(oracle: dict) -> str:
    """The names an oracle declares, capped for a human line the way the oracle block caps them."""
    names = oracle["names"] if oracle else []
    listed = ", ".join("`%s`" % n for n in names[:ORACLE_NAMES_SHOWN])
    if len(names) > ORACLE_NAMES_SHOWN:
        listed += " (+%d more, not listed)" % (len(names) - ORACLE_NAMES_SHOWN)
    return listed or "none"


def value_reason_text(f: dict, oracle: dict) -> str:
    """One refused value, said as a finding: what was looked for, in which file, what was found there, and
    what to do about it, in the file this sentence just named.

    The house style is `no-shipped-table`'s: name the thing, name the pattern, name every place it looked.
    The three `NO-ORACLE` sentences are deliberately DIFFERENT strings (ruling R16), and each states the
    consequence in the sentence a human can check: a malformed table's cleanly-read names do not resolve,
    because a stray column means the file's shape is not what was assumed about column 1, and this tool
    resolves a workspace attribute only from a table it read cleanly.

    R21 adds the other half, CONDITIONED on the state printed above it, because "add a row to the workspace
    table" is false advice about an `ABSENT` file and doubly false when what the flavor ships beside it is an
    entity table. Each arm says which file to edit or create, in which directory, and with which FIRST field;
    each names `table`, the variable the diagnosis printed, so the cure cannot point somewhere the tool never
    read; and where other TSVs exist they are labelled what EXISTS, never what to edit. `CONTRIBUTING.md`
    forbids documenting a fix nobody performed, so every clause here is executed by a leg of
    `scripts/selftest.d/entity.sh` (the cure legs), which needles the filename out of this sentence and edits
    the file it names.
    """
    reason, key = f["reason"], f["key"]
    table = (oracle["path"] or oracle["expected"]) if oracle else "?"
    status = oracle["status"] if oracle else "ABSENT"
    attrs = oracle["attrs"] if oracle else 0
    names = ", ".join("`%s`" % n for n in f["names"]) or "-"
    mixed = reason.startswith("mixed-")
    shape = ("" if not mixed else
             "the value is text around reads, so it has no whole-value answer to grade, and the "
             "${workspace.*} read inside it is what was looked for: ")
    if reason in ("workspace-missing", "mixed-workspace-missing"):
        return ("%s%s reads %s and `%s` does not declare it: that file declares %d attribute name(s), "
                "status=%s, and none of them is this one. Looked for that first field (an optional "
                "`workspace:` prefix stripped) in that file and no other; what it does declare: %s. A name "
                "with no row cannot be launched — the alternative is a submission that discovers the missing "
                "input after the VMs booted. To clear it: add a row whose FIRST field is each name quoted "
                "above to `%s` %s or stop binding `%s`"
                % (shape, key, names, table, attrs, status, _declared_names(oracle),
                   table, CURE_ONLY_FILE, key))
    if mixed:
        head = "%s%s reads %s; `%s` is status=%s" % (shape, key, names, table, status)
    else:
        head = "%s reads `%s`; `%s` is status=%s" % (key, f["detail"], table, status)
    if status == "ABSENT":
        # `ABSENT` is the state in which there is nothing to edit, so the cure is a file to create and the
        # list of what the flavor ships moves from "here is what it has" to "here is what is NOT the target".
        where = os.path.dirname(table) or "the top of that corpus directory"
        return ("%s because no such file exists: that flavor has configs and ships no workspace attribute "
                "table, so there is nothing this name could be checked against and NOTHING read through it "
                "resolves. We resolve a workspace attribute only from a table we read cleanly, and here "
                "there is no table at all. To clear it there is nothing to edit and a file to CREATE: `%s`, "
                "in %s, the directory that flavor's configs live in; every other TSV that flavor ships is "
                "something else and none of them is read for a workspace attribute. Add a first row whose "
                "FIRST field is each name quoted above (a leading `workspace:` on that field is optional; it "
                "is stripped before the name is compared), or stop binding `%s` — and until that file exists, "
                "nothing read through it resolves. What that flavor ships today is what EXISTS, not what to "
                "edit: %s"
                % (head, table, where, key,
                   ", ".join(oracle["nearby"]) or "none — it ships no TSV of any kind"))
    findings = ((oracle["malformed"] if status == "MALFORMED" else oracle["dupes"]) or [])
    why = ("a stray column means the file's shape is not what was assumed about its FIRST column, so a name "
           "read cleanly out of it is not a name that can be trusted" if status == "MALFORMED" else
           "an attribute declared twice has no single value to carry, so which one a launch would use is not "
           "in this file")
    cure = ("repair the row(s) quoted above in `%s` %s until the file reads cleanly" % (table, CURE_ONLY_FILE)
            if status == "MALFORMED" else
            "collapse the row(s) quoted above in `%s` %s to one row per attribute" % (table, CURE_ONLY_FILE))
    return ("%s (%d finding(s); first: %s), so the %d name(s) it did read cleanly do NOT resolve either: "
            "%s. We resolve a workspace attribute only from a table we read cleanly — this one was read, and "
            "it did not read cleanly. To clear it: %s, or stop binding `%s`"
            % (head, len(findings), findings[0] if findings else "-", attrs, why, cure, key))


def value_findings_text(f: dict) -> str:
    """The refusals that are not about an oracle at all, said once outside the flavor's vocabulary."""
    if f["reason"] == "test-bundle":
        return ("%s belongs to a TEST bundle: %s. Never launchable, and never resolved into a launch "
                "argument by this tool: the sources a launch may carry are the config's own literal, a "
                "${workspace.*} row this flavor declares, or a bundle an operator states"
                % (f["key"], f["detail"]))
    if f["reason"] == "values-unreadable":
        return ("no value of this config was classified: the document will not parse even after the one "
                "`{{ ... }}` -> marker substitution (%s), so this is `values-unreadable`, not `no-values`, "
                "and never a pass" % f["detail"])
    if f["reason"] == "bundle-read-unattributed":
        # `%d` against `f["detail"]` (a STRING) is what made this refusal crash instead of print, which is
        # how a guard against guessing ended up destroying the report it was filed in. The count is the
        # number of expressions this finding names, and `names` is the file's one spelling of "the things
        # this finding is about" — so both format arguments are the type the verb claims.
        return ("%d `{{ ... }}` expression(s) in this file could not be tied to a binding value or key (%s), "
                "so this tool refuses to guess which key would have carried them"
                % (len(f["names"]), ", ".join(f["names"])))
    if f["reason"] == "no-values":
        return ("the document binds no value at all (0 scalar(s)), so there is nothing to resolve and no "
                "clean verdict to print: `no configs`, `no values`, `values-unreadable` and `every value "
                "resolved` are FOUR different answers, and one 0 never stands for two of them")
    return ""


def value_report(v: dict, oracle: dict) -> list:
    """The human lines for one config: its census, then everything a machine line cannot say.

    A config prints here only when it has something to say — a refusal, a disclosed bundle, a `*_docker` key,
    a read below binding level, or a value that last-wins dropped. The census itself is on the machine line
    for every config, silent ones included, so this block staying short is never a missing fact.
    """
    pad = " " * 8
    said = []
    for f in v["findings"]:
        text = value_findings_text(f) or value_reason_text(f, oracle)
        said.append("        UNRESOLVED-VALUE %s [%s] %s" % (f["key"], f["cls"], text))
    if not v["findings"] and "no-values" in v["reasons"]:
        said.append("        UNRESOLVED-VALUE - [no-values] %s" % value_findings_text({"reason": "no-values"}))
    if v["classes"]["needs-bundle"] or v["bundles"]:
        said.append("%sNEEDS-BUNDLE bundle=%s (%d value(s)): a `{{ ... }}` render is NAMED here and never "
                    "resolved — this tool renders nothing and never opens a values profile, so a root is a "
                    "bundle NAME and not a file, and no claim is made that a profile answering it exists. A "
                    "launch may not carry these values until an operator states the bundle (exit 0: this is "
                    "a disclosure, not a refusal)"
                    % (pad, ",".join(v["bundles"]), v["classes"]["needs-bundle"]))
    if v["docker_keys"]:
        said.append("%sDOCKER-MUST-BE-STATED %d key(s) (%s): a container image is never resolved from an "
                    "ambient profile here — `--image KEY=REF` or a profile pin is the only source "
                    "(terra/batch_rerun_step.py::_stated()); a binding is excluded when its leaf key ends "
                    "`_docker` OR when the value it binds IS a single `${workspace.*_docker}` read (ruling "
                    "R18: the rule is about the image, not about what the key is called; narrowed by R20 to "
                    "the whole value, so a command that only MENTIONS an image stays a command and is still "
                    "graded for every other read it carries); a refusal always beats this exclusion, so a "
                    "`*_docker` key bound to a test bundle or to an attribute this flavor does not declare "
                    "is reported as that refusal and not as a withheld image"
                    % (pad, len(v["docker_keys"]), ", ".join(v["docker_keys"])))
    if v["docker_unenforced"]:
        # R22's honest half: `terra/batch_rerun_step.py` asks for a stated pin by KEY (`_stated()`, and the
        # two callers that build a launch around it), so a key that is docker-shaped only by its VALUE is
        # withheld HERE and never asked for THERE. Measured 0 instances on the real tree at `e1909d2f`, so
        # nothing real moves today; what moves is the advice, which used to name a key the launcher will
        # ignore as if stating a pin under it would clear anything. The exclusion stands — an image resolved
        # from an ambient profile is still never launch data — and the sentence now says who enforces it.
        said.append("%sDOCKER-KEY-UNENFORCED %d of those key(s) (%s): docker-shaped by their VALUE, not by a "
                    "leaf ending `_docker`, and the launcher's pin gate keys on the NAME "
                    "(terra/batch_rerun_step.py::_stated()) — so this tool withholds the value, and nothing "
                    "upstream will ever ask for a pin under this key. State the image under a `_docker` key, "
                    "or the launcher runs without checking it"
                    % (pad, len(v["docker_unenforced"]), ", ".join(v["docker_unenforced"])))
    if v["nested_reads"]:
        said.append("%s%d read(s) sit below binding level, inside a nested object or list — the shape a "
                    "reader that looks only at binding values loses" % (pad, v["nested_reads"]))
    if v["dropped_values"]:
        said.append("%s%d value(s) dropped by a duplicated key. This is the PARSED reader, so `json.loads`' "
                    "last-wins chose the side and the dropped side's %d ${workspace.*} read(s) are counted "
                    "nowhere here; the key is named as `dupkeys=` on the entity line, and a raw-text reader "
                    "sees %d read(s) where this one sees %d"
                    % (pad, v["dropped_values"], v["dropped_ws_reads"],
                       v["ws_reads"] + v["dropped_ws_reads"], v["ws_reads"]))
    if not said:
        return []
    census = " ".join("%s=%s" % (c, v["classes"][c]) for c in VALUE_CLASSES if v["classes"][c])
    return ["  %s (%s): %d value(s), %s\n%s"
            % (v["config"], v["flavor"], v["bindings"], census or "no class", "\n".join(said))]


# ------------------------------------------------------------------------------------------- required inputs
#
# Stage B's acceptance leg, restated as something a machine can check: EVERY required input of EVERY
# workflow a launch config binds is either given a value by that config, or named by a workspace attribute
# the flavor's own table declares. Nothing before this layer could print that sentence, because the
# required-input set lives in `checks/wdl_inputs_check.py` (miniwdl's AST) and the oracle lives here; the
# placement ruled for `terra_entity_check.py` because the oracle, the value classes and the refusal
# vocabulary are all already here, and the WDL is one more subtree of the same ref (see
# `docs/plan-launch-any-module.md`, which carried this criterion as un-built).

def walk_containers(value, path: str = "") -> list:
    """[(key_path, kind)] for every OBJECT and LIST node in a parsed config, `walk_values`'s mirror image.

    `walk_values` returns one entry per SCALAR, which is what a one-class-per-value vocabulary needs. This
    returns the nodes it does not: the containers. The required layer needs them because an input file may
    hand a struct over as ONE object (`{"Wf.struct": {"cpu": 2}}`) while the WDL asks for the struct itself,
    so the key the config really binds has no scalar at it — and reporting that as `unbound` would be a
    false refusal about a launch config, the failure class this file keeps closing.
    """
    out = []
    if isinstance(value, dict):
        out.append((path, "object"))
        for k, v in value.items():
            out.extend(walk_containers(v, "%s.%s" % (path, k)))
    elif isinstance(value, list):
        out.append((path, "list"))
        for i, v in enumerate(value):
            out.extend(walk_containers(v, "%s[%d]" % (path, i)))
    return out


def _required_reader():
    """The one required-input predicate in this repo, imported rather than copied: (fn or None, where).

    Reusing it is the point, not a convenience. `required_inputs` owns three rules that a local copy would
    have to re-derive and eventually get wrong: a declaration with a `= ...` default is satisfied by the
    WDL, an `optional` declaration is satisfied by the WDL, and a struct-typed input arrives as its
    MEMBERS — the exact shape row C8 of docs/gap-ledger.md records a one-line predicate getting wrong.
    The sibling checker imports nothing but `config` at module level, so importing it costs an offline run
    nothing (unlike `terra/batch_configs.py`, whose expander is read with `ast` for that reason).
    """
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import wdl_inputs_check
    except Exception as exc:                       # noqa: BLE001 - a missing sibling is a state, not a traceback
        return None, "%s cannot be imported (%s: %s)" % (
            os.path.relpath(REQUIRED_SOURCE, os.path.dirname(HERE)), type(exc).__name__, exc)
    fn = getattr(wdl_inputs_check, "required_inputs", None)
    loader = getattr(wdl_inputs_check, "load_workflow", None)
    if fn is None or loader is None:
        return None, "%s carries no required_inputs()/load_workflow() to reuse" % REQUIRED_WHERE
    return (fn, loader), "%s (one predicate, one loader, imported not copied)" % REQUIRED_WHERE


def _wdl_module():
    """(True, "") when miniwdl is importable, else (False, why). Probed once per run, never a traceback.

    miniwdl is a DEV dependency: `requirements-dev.txt` says the gate SKIPS it instead of failing, so a
    machine without it must get an answer about everything this tool can answer without a WDL — and that
    machine must also be told, in a count, that the required-input question went unasked. Both halves are
    load-bearing: a skip that is not named is how "green" comes to mean "the check ran".
    """
    try:
        import WDL                                 # noqa: F401 - the probe IS the answer
    except ImportError as exc:
        return False, "miniwdl is not importable (%s)" % exc
    return True, ""


def capture_workflow(wf: str, ctx: dict, cache: dict) -> dict:
    """One workflow's required-input set, loaded at most once per run and cached under its name.

    The FILE RULE is the other checker's and upstream CI's: `<wdl_dir>/<workflow>.wdl`, matched on the
    basename (gatk-sv ships 118 flat files there at `e1909d2f`, and `scripts/test/terra_validation.py`
    pairs configs to WDLs the same way). `file` is printed TREE-relative — `wdl/<workflow>.wdl` — because
    that is the path a reader can open in their own checkout; on the `--repo/--ref` path the bytes were read
    out of a temp directory `git archive` filled, and naming that instead would print a path that exists
    for nobody.
    """
    if wf in cache:
        return cache[wf]
    shown = "%s/%s.wdl" % (WDL_SUBTREE, wf)
    cap = {"wf": wf, "status": "OK", "file": shown, "required": [], "note": ""}
    if not ctx["miniwdl_ok"]:
        cap.update(status="NO-MINIWDL", file="-", note=ctx["miniwdl_note"])
    elif not ctx["dir_ok"]:
        # The note has to stand alone: `NO-WDL-DIR` is a tree-level cause, so it is stated once in the banner
        # and a reader who never sees a per-config line still has to be able to act on the word. On the
        # `--repo/--ref` path `dir_note` is why the archive itself failed; on `--tree` there is no earlier
        # sentence, so the missing directory is named here.
        cap.update(status="NO-WDL-DIR", file="-", note=ctx["dir_note"] or (
            "no %s/ directory under the tree, so no workflow's required inputs can be read" % WDL_SUBTREE))
    else:
        doc, bad, note = ctx["loader"](ctx["wdl_dir"], wf)
        if doc is None:
            # `file=-`: the machine line's `files=` lists what this run ACTUALLY opened, so a name that
            # exists in nobody's tree never appears in it. `LOAD-FAILURE`'s note carries the path that does
            # exist, and `NO-WDL`'s carries the one that does not.
            cap.update(status=bad, file="-", note=note)
            if bad == "NO-WDL" and os.path.isfile(os.path.join(ctx["wdl_dir"], wf + ".wdl")):
                # The file exists and the shared loader still says NO-WDL, which is that loader's
                # tasks-only arm (`wdl_inputs_check` keeps the two shapes together because its own question
                # — pair a JSON to a WDL — cannot act on the difference). Here the difference is the answer,
                # so the word is re-split from the fact rather than from the wording of the note.
                cap["status"] = "NO-WORKFLOW"
        else:
            cap["required"] = ctx["reader"](doc)
    cache[wf] = cap
    return cap


def grade_required(values: dict, prefixes: list, ctx: dict, cache: dict) -> dict:
    """One config's required-input answer: what its workflow(s) demand, against what this config gives.

    The classes come from the VALUE layer, which already classified every scalar in this document: a
    required input is graded by the class of the value that carries it, so the two layers cannot disagree
    about the same bytes and the acceptance vocabulary stays the one table printed above. Two shapes have
    no value class (`container`, `unbound`); see `REQUIRED_CLASSES`.

    Precedence is fixed, in the direction that refuses rather than the direction that soothes: an
    unsatisfied required input makes the config `UNRESOLVED` even when another of its workflows could not
    be captured at all, because a finding must never be hidden by a disclosure — and a config with nothing
    to report and an uncaptured workflow is `NOT-CAPTURED`, never `OK`, because the question was not asked.
    """
    r = {"config": values["config"], "flavor": values["flavor"], "wf": values["wf"],
         "classes": dict((c, 0) for c in REQUIRED_CLASSES), "required": 0, "captures": [],
         "capture": "OK", "state": "OK", "reason": "-", "unsatisfied": [], "files": [], "notes": []}
    if values["unclassified"] == "values-unreadable":
        # The same bytes the other two readers refused; this one loses its question with them, because the
        # workflow names come out of the document's own binding keys.
        r["capture"] = "NO-DOCUMENT"
        r["state"] = "NOT-CAPTURED"
        r["notes"].append("this config is a document neither reader here can parse, so it names no workflow "
                          "whose required inputs could be asked for")
        return r
    if not prefixes:
        r["capture"] = "NO-DOCUMENT"
        r["state"] = "NOT-CAPTURED"
        r["notes"].append("the document binds no key at all, so it names no workflow whose required inputs "
                          "could be asked for")
        return r
    seen = set()
    for wf in prefixes:
        cap = capture_workflow(wf, ctx, cache)
        r["captures"].append(cap)
        if cap["file"] != "-" and cap["file"] not in r["files"]:
            r["files"].append(cap["file"])
        if cap["status"] != "OK":
            continue
        for key, type_text, line, _parent in cap["required"]:
            if key in seen:                        # two files declaring one namespace: ask a key once
                continue
            seen.add(key)
            if key in values["class_of"]:
                cls = values["class_of"][key]
            elif key in values["containers"]:
                cls = "container"
            else:
                cls = "unbound"
            r["classes"][cls] += 1
            r["required"] += 1
            if cls in REQUIRED_REASON_OF:
                r["unsatisfied"].append({"key": key, "cls": cls,
                                         "reason": REQUIRED_REASON_OF[cls], "type": type_text,
                                         "line": line, "file": cap["file"], "config": values["config"]})
    for word in REQUIRED_CAPTURES:
        if word != "OK" and any(c["status"] == word for c in r["captures"]):
            r["capture"] = word
            break
    if r["unsatisfied"]:
        r["state"] = "UNRESOLVED"
        for word in REQUIRED_REASONS:
            if any(f["reason"] == word for f in r["unsatisfied"]):
                r["reason"] = word
                break
    elif r["capture"] != "OK":
        r["state"] = "NOT-CAPTURED"
    return r


def _required_class_tail(classes: dict) -> str:
    """The class fields, generated from `REQUIRED_CLASSES` so a class cannot exist unprinted (ruling R15)."""
    return " ".join("%s=%s" % (c, classes[c]) for c in REQUIRED_CLASSES)


def required_machine_line(r: dict) -> str:
    """The required line for one config, printed beside its entity and value lines.

    `required=` is the sum of the class fields, computed from them. `capture=` is what asking the workflow
    cost, and it is NOT a verdict: `capture=NO-WDL` sits beside `state=NOT-CAPTURED`, never beside `OK`.
    """
    return ("GSVTK-REQUIRED config=%s flavor=%s wf=%s workflows=%s capture=%s required=%s %s "
            "files=%s state=%s reason=%s"
            % (r["config"], r["flavor"], r["wf"], len(r["captures"]), r["capture"], r["required"],
               _required_class_tail(r["classes"]), ",".join(r["files"]) or "-", r["state"], r["reason"]))


def required_summary_line(records: list) -> str:
    """The census of the required lines, with three identities a phase can assert against it.

    `required` is the sum of the summed class fields; `configs` is the sum of the three `configs-*` state
    counters; `configs` is also the sum of the per-capture-word counters. Every one is computed FROM the
    per-config records, so none of the three can be quoted at a caller it does not hold.
    """
    tot = dict((c, 0) for c in REQUIRED_CLASSES)
    states = dict((s, 0) for s in REQUIRED_STATES)
    captures = dict((w, 0) for w in REQUIRED_CAPTURES)
    workflows, captured = set(), set()
    for r in records:
        for cls in REQUIRED_CLASSES:
            tot[cls] += r["classes"][cls]
        states[r["state"]] += 1
        captures[r["capture"]] += 1
        for cap in r["captures"]:
            workflows.add(cap["wf"])
            if cap["status"] == "OK":
                captured.add(cap["wf"])
    return ("GSVTK-REQUIRED-SUMMARY configs=%s required=%s %s workflows=%s workflows-captured=%s "
            "configs-ok=%s configs-unsatisfied=%s configs-not-captured=%s "
            "capture-no-miniwdl=%s capture-no-wdl-dir=%s capture-no-document=%s "
            "capture-load-failure=%s capture-no-workflow=%s capture-no-wdl=%s capture-ok=%s"
            % (len(records), sum(tot.values()), _required_class_tail(tot), len(workflows), len(captured),
               states["OK"], states["UNRESOLVED"], states["NOT-CAPTURED"],
               captures["NO-MINIWDL"], captures["NO-WDL-DIR"], captures["NO-DOCUMENT"],
               captures["LOAD-FAILURE"], captures["NO-WORKFLOW"], captures["NO-WDL"], captures["OK"]))


def required_uncaptured_cause(records: list) -> str:
    """The ONE word saying why required inputs went unasked, or `mixed`, or `-` when none did.

    A scope line is read alone, so it gets a cause and not just a count. When the configs disagree about
    the cause the aggregate says `mixed` rather than picking the most flattering one; the per-word counts
    on `GSVTK-REQUIRED-SUMMARY` carry the breakdown.
    """
    words = {r["capture"] for r in records if r["capture"] != "OK"}
    if not words:
        return "-"
    return words.pop() if len(words) == 1 else "mixed"


def uncaptured_workflows(records: list) -> tuple:
    """(distinct workflow names this run could not read, counted) — the names capped, the count not.

    Which workflow is missing is the thing an operator acts on, and a per-config scroll buries it. Both
    numbers come back because the sentence needs both: the list is capped so a 42-config corpus without a
    `wdl/` directory cannot print 42 lines, and the count is what proves the cap did not lose a name.
    """
    names = sorted({c["wf"] for r in records for c in r["captures"] if c["status"] != "OK"})
    shown = names[:REQUIRED_UNCAPTURED_SHOWN]
    tail = " (+%d more, not listed)" % (len(names) - len(shown)) if len(names) > len(shown) else ""
    return (", ".join(shown) + tail if shown else "none", len(names))


def required_reason_text(f: dict) -> str:
    """One unsatisfied required input, said with its cure, in the house shape: the thing, where it was
    looked for, what was found there, and the file the fix belongs in.

    `required-unbound` is the new fact this layer exists to print, and the record behind it is the plan's
    own warning: a required input with no binding does not fail typecheck, it fails at input resolution
    after the VMs booted (row C8 of docs/gap-ledger.md, upstream's `IntegrateGDVcf.sample_id`). The other
    three words NAME the value finding above rather than restating it: that refusal already printed, with
    its own cure and its own file, and this line's job is to say the same value is now carrying a required
    input, which is a stronger fact than a stray binding being wrong.
    """
    where = "%s:%s" % (f["file"], f["line"]) if f["line"] else f["file"]
    if f["reason"] == "required-unbound":
        decl = f["key"].rsplit(".", 1)[-1]
        return ("%s (%s) is declared with no default and not optional at `%s`, and `%s` binds no key for "
                "it — not a `${this.…}` entity read, not a `${workspace.…}` attribute, not a literal. "
                "Cromwell discovers this at input-resolution time, after the VMs booted. To clear it: bind "
                "\"%s\" in `%s` (a literal, an entity read, a workspace attribute this flavor declares, or "
                "a `{{ … }}` bundle render an operator states), or make `%s` optional / give it a default "
                "in `%s` — a defaulted or optional declaration is satisfied by the WDL itself"
                % (f["key"], f["type"], where, f["config"], f["key"], f["config"], decl, f["file"]))
    return ("%s (%s) is a REQUIRED input — declared at `%s` — and the value that carries it is refused "
            "above as [%s] %s. The cure is the one that finding prints; until it is cleared this key has "
            "no value to launch with"
            % (f["key"], f["type"], where, f["cls"], f["config"]))


def required_report(r: dict) -> list:
    """The human lines for one config's required inputs: only what a machine line cannot say.

    A config prints here when it refused, when a required input is an image or a bundle read (both are
    disclosures an operator has to act on, not findings), or when its workflow could not be captured.
    Every config still gets its machine line either way, so a short block is never a missing fact.
    """
    pad = " " * 8
    said = []
    for f in r["unsatisfied"]:
        said.append("        UNRESOLVED-REQUIRED %s [%s] %s" % (f["key"], f["cls"], required_reason_text(f)))
    for cap in r["captures"]:
        if cap["status"] not in ("OK",) + REQUIRED_BANNER_CAUSES and cap["note"]:
            # Only the per-config causes print here. `NO-MINIWDL` / `NO-WDL-DIR` / `NO-WDL` are stated once
            # by the run's banner (with the workflow names and the count), and the machine line carries the
            # word for every config either way, so dropping the repeat loses no fact and keeps the two
            # causes an operator can actually act on readable.
            said.append("%sREQUIRED-NOT-CAPTURED %s [%s] %s — the required-input question was never asked "
                        "of this workflow, so nothing about it passed or failed"
                        % (pad, cap["wf"], cap["status"], cap["note"]))
    for note in r["notes"]:
        said.append("%sREQUIRED-NOT-CAPTURED %s" % (pad, note))
    if r["classes"]["docker"]:
        said.append("%s%d required input(s) are container images: excluded by rule from anything this tool "
                    "resolves, so they arrive only under `--image KEY=REF` or a profile pin "
                    "(terra/batch_rerun_step.py::_stated()). They are counted as bound here because the "
                    "config DOES name them; a launch that never states the image still cannot run"
                    % (pad, r["classes"]["docker"]))
    if r["classes"]["needs-bundle"]:
        said.append("%s%d required input(s) carry a `{{ … }}` render: NAMED here and resolved by nobody, "
                    "because no values profile is opened anywhere in this tool. The plan's revision 1 "
                    "resolved everything by rendering upstream's inputs and came out somebody else's "
                    "sample, which is why an unstated bundle is disclosed (exit 0) and never quietly "
                    "satisfied"
                    % (pad, r["classes"]["needs-bundle"]))
    if not said:
        return []
    census = " ".join("%s=%s" % (c, r["classes"][c]) for c in REQUIRED_CLASSES if r["classes"][c])
    return ["  %s (%s): %d required input(s), %s\n%s"
            % (r["config"], r["flavor"], r["required"], census or "none",
               "\n".join(said))]


def table_columns(t: dict) -> str:
    """A table's columns as a reader wants them, with the unreadable count said out loud."""
    cols = ", ".join(t["columns"]) or "-"
    if t.get("columns_unreadable"):
        cols += " (+%d column name(s) unreadable: their header carries a {{ ... }} expression)"\
                % t["columns_unreadable"]
    return cols


def reason_text(v: dict, corpus: str, tables: list) -> str:
    """The sentence: what was looked for, and where. A bare code is not a reason."""
    r = v["reason"]
    if r == "cannot-parse":
        # Not "JSON `json.loads` rejects": an unfinished `${` or `{{` parses fine and is still unreadable,
        # so this has to be true of both causes. The cause itself is `v["error"]`'s to say.
        return ("its bindings cannot be read at all: after the one `{{ ... }}` -> null substitution "
                "(%d substituted), %s" % (v["neutralised"], v["error"]))
    if r == "no-name-key":
        typed = " it also reads `this.id`, which carries no <etype> before `_id`;" if "id" in v["direct"] else ""
        if not v["this_reads"]:
            return ("no ${this.<etype>_id} read:%s this config reads nothing through `this.` at all "
                    "(%d binding(s), %d of them through ${workspace.*}); a config that binds no entity "
                    "attribute cannot say what row it runs against"
                    % (typed, len(v["keys"]), workspace_reads(v)))
        return ("no ${this.<etype>_id} read:%s the %d single-component read(s) it does make are %s"
                % (typed, len(v["direct"]), ", ".join("`this.%s`" % a for a in v["direct"])))
    if r == "many-name-keys":
        return ("%d different entity types appear as ${this.<etype>_id}: %s. Which row a submission runs "
                "against is one answer, so this one refuses rather than picking"
                % (len(v["namekey_types"]), ", ".join("`%s`" % t for t in v["namekey_types"])))
    if r == "no-shipped-table":
        seen = sorted("%s (%s:%s_id, columns: %s)" % (t["path"], t["kind"], t["etype"], table_columns(t))
                      for t in tables)
        return ("no shipped entity table of type `%s`: looked in every TSV under %s for a first header "
                "column `entity:%s_id` or `membership:%s_id`. The tables this ref ships are: %s"
                % (v["etype"], corpus, v["etype"], v["etype"], "; ".join(seen) or "none at all"))
    if r == "columns-missing":
        of_type = [t for t in tables if t["etype"] == v["etype"]]
        return ("no shipped table of type `%s` carries %d column(s) this config reads: %s. "
                "The %d table(s) of that type, and what each carries: %s. One table has to carry them "
                "all, because one table is what gets imported — two tables covering the set between them "
                "is reported here, not passed on their union"
                % (v["etype"], len(v["missing"]), ", ".join("`%s`" % m for m in v["missing"]),
                   len(of_type), "; ".join("%s (columns: %s)" % (t["path"], table_columns(t))
                                           for t in of_type)))
    return ""


def note_text(v: dict) -> list:
    """What a pass or a refusal does NOT cover for this config, one line per fact that applies."""
    notes = []
    if v["dupkeys"]:
        notes.append("%d key(s) bound more than once (%s); `json.loads` keeps the LAST value, so which "
                     "one a submission would use is not in this file"
                     % (len(v["dupkeys"]), ", ".join("`%s`" % k for k in v["dupkeys"])))
    if v["nested"]:
        notes.append("%d read(s) sit inside a nested object/list, not at binding level" % v["nested"])
    if v["bare_this"]:
        notes.append("%d value(s) carry `this.` outside `${ ... }`, which Terra reads as a LITERAL and "
                     "this file therefore does not count as an entity read" % v["bare_this"])
    if v["unbraced"]:
        notes.append("%d `${` with no closing `}`, so a binding may have gone unread" % v["unbraced"])
    if v["member_attrs"]:
        notes.append("%d member attribute read(s) NOT checked (%s); the member entity type is not "
                     "derived, and plural-to-singular is a guess that binds nothing when wrong"
                     % (len(v["member_attrs"]), ", ".join(sorted(set(v["member_attrs"])))))
    if v["state"] == "OK":
        notes.append("row existence is NOT checked here: that is `terra.entity_sample()` before any "
                     "submission, and Terra's own rootEntityType is a credential-using read")
    return notes


def machine_line(d: dict) -> str:
    """The one machine-readable line per config. Lists join with `,`; an empty list prints `-`."""
    def or_dash(xs):
        return ",".join(xs) if xs else "-"
    return ("GSVTK-ENTITY config=%s flavor=%s wf=%s filestem=%s namematch=%s state=%s namekeys=%s "
            "namekey=%s collections=%s reads_direct=%s reads_member=%s member_attrs=%s "
            "tables_of_type=%s table=%s table_kind=%s columns=%s missing=%s nested=%s barethis=%s "
            "dupkeys=%s neutralised=%s unbraced=%s reason=%s"
            % (d["config"], d["flavor"], d["wf"] or "-", d["filestem"],
               "yes" if d["wf"] == d["filestem"] else "no", d["state"], len(d["namekey_types"]),
               or_dash(d["namekey_types"]), or_dash(d["member"]), len(d["direct"]), len(d["member"]),
               len(d["member_attrs"]), d["tables_of_type"], d["table"] or "-", d["table_kind"] or "-",
               d["columns"], or_dash(d["missing"]), d["nested"], d["bare_this"],
               or_dash(d["dupkeys"]) if d["dupkeys"] else "0", d["neutralised"], d["unbraced"],
               d["reason"] or "-"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="terra_entity_check.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="", help="gatk-sv clone to read (default: GSVTK_GATK_SV_CHECKOUT)")
    ap.add_argument("--ref", default="", help="ref to read (default: GSVTK_GATK_SV_REF, else HEAD)")
    ap.add_argument("--tree", default="", help="already-materialized gatk-sv tree root (skips git archive)")
    ap.add_argument("--corpus", default=DEFAULT_CORPUS,
                    help="corpus subpath inside the tree (default: %s)" % DEFAULT_CORPUS)
    args = ap.parse_args(argv)

    jinja_re, where = _shared_expander()
    if jinja_re is None:
        print("PREREQUISITE no single `{{ ... }}` expander to reuse (%s) — this file refuses to define a "
              "second one, which is §10's rule" % where)
        return 3
    reader, required_where = _required_reader()
    if reader is None:
        # The same rule as the expander one line above, and for the same reason: this file defines no second
        # required-input predicate, so when the one it reuses is gone the question cannot be asked at all.
        # That is exit 3, not a disclosed skip — miniwdl (an OPTIONAL dev dependency) is the disclosed one,
        # below; a sibling checker that moved is a broken checkout, not a fact about a corpus.
        print("PREREQUISITE no required-input predicate to reuse (%s) — the required-input half of this run "
              "would otherwise be a second definition, which is the mistake row C8 of docs/gap-ledger.md "
              "records" % required_where)
        return 3
    required_inputs, load_workflow = reader
    if args.tree and args.ref:
        print("usage error: --tree already fixes the tree; drop --ref, or use --repo with --ref")
        return 2
    if args.tree:
        if not os.path.isdir(args.tree):
            print("usage error: --tree %s is not a directory" % args.tree)
            return 2
        tree, source = args.tree, "tree %s; shipped test profiles from the directory listing" % args.tree
        profiles = test_profile_stems(tree)
        # The WDL subtree of a materialized tree is simply the directory beside the corpus. Its absence is
        # an answer (`NO-WDL-DIR`), stated with the path that was asked for.
        wdl_dir = os.path.join(tree, WDL_SUBTREE)
        wdl_note = ("" if os.path.isdir(wdl_dir)
                    else "no %s/ directory under %s, so no workflow's required inputs can be read" %
                         (WDL_SUBTREE, tree))
    else:
        repo = args.repo or config.get("GATK_SV_CHECKOUT")
        # GSVTK_GATK_SV_REF is an environment variable, not a profile key (kit/gsvtk-config's `get` refuses
        # an unknown key rather than returning "", so asking it for a key that does not exist is a crash).
        ref = args.ref or os.environ.get("GSVTK_GATK_SV_REF") or "HEAD"
        if not repo or not os.path.isdir(os.path.expanduser(repo)):
            print("PREREQUISITE no gatk-sv checkout to read (set GSVTK_GATK_SV_CHECKOUT, pass --repo, or "
                  "pass --tree for an already-materialized tree)")
            return 3
        repo = os.path.expanduser(repo)
        if not os.path.isdir(os.path.join(repo, ".git")):
            print("PREREQUISITE %s is not a git repository, so there is no ref to archive" % repo)
            return 3
        tree, why = _tree_from_ref(repo, ref, args.corpus)
        if not tree:
            return 3
        source = "git archive %s from %s; shipped test profiles from git ls-tree" % (ref, repo)
        # The corpus archive holds ONE subtree, so `inputs/values/` is not in the temp tree this run reads
        # configs from. The shipped test profiles are therefore listed off the ref itself (names only,
        # contents never opened) rather than walked out of a directory that does not exist there. The
        # required-input layer adds a SECOND archive, of `wdl/` only — still not `inputs/values/`, which is
        # why that lister is still needed and still the only one that answers.
        profiles = test_profile_stems_at_ref(repo, ref)
        wdl_tree, wdl_why = _tree_from_ref(repo, ref, WDL_SUBTREE, quiet=True)
        wdl_dir = os.path.join(wdl_tree, WDL_SUBTREE) if wdl_tree else ""
        wdl_note = ("" if wdl_dir else wdl_why)

    corpus_dir = os.path.join(tree, args.corpus)
    if not os.path.isdir(corpus_dir):
        # Not "use the tree as the corpus" — that guess would let a typo'd --corpus grade the whole tree
        # and print a confident answer about a corpus nobody asked about. `--corpus .` is the way to say it.
        print("PREREQUISITE no %s under %s (%s) — the corpus this tool asks about is not there, and "
              "'no configs' is a stated answer, never a clean one" % (args.corpus, tree, source))
        return 3

    configs = []
    for dirpath, _dirs, files in os.walk(corpus_dir):
        configs.extend(os.path.join(dirpath, fn) for fn in files if fn.endswith(CONFIG_SUFFIX))
    configs.sort()
    tables, other_tsv, unreadable = read_tables(corpus_dir)
    miniwdl_ok, miniwdl_note = _wdl_module()
    ctx = {"wdl_dir": wdl_dir, "dir_ok": bool(wdl_dir) and os.path.isdir(wdl_dir), "dir_note": wdl_note,
           "miniwdl_ok": miniwdl_ok, "miniwdl_note": miniwdl_note,
           "reader": required_inputs, "loader": load_workflow}
    cap_cache = {}

    print("terra entity derivation — corpus %s (%s)" % (args.corpus, source))
    print("expander: %s from %s (one definition; `{{ ... }}` -> null, never rendered)"
          % (EXPANDER_NAME, where))
    # The provenance line is read on its own and has to be true on its own: printing a WDL path beside a
    # clean exit would otherwise read as "these WDLs were opened" on a run where the directory is not there
    # at all. The miniwdl note stays first, which is the same precedence `capture_workflow` uses.
    if not miniwdl_ok:
        wdl_state = miniwdl_note
    elif not ctx["dir_ok"]:
        wdl_state = "no %s/ directory here" % WDL_SUBTREE
    else:
        wdl_state = "miniwdl present"
    print("required inputs: %s; WDL tree %s (%s)" % (required_where, wdl_dir or "-", wdl_state))
    by_type = {}
    for t in tables:
        by_type.setdefault(t["etype"], []).append(t)
    print("shipped tables: %d entity/membership TSV(s), type(s) %s; %d other TSV(s) excluded (first "
          "column is neither `entity:<x>_id` nor `membership:<x>_id`)"
          % (len(tables), ", ".join("%s x%d" % (k, len(v)) for k, v in sorted(by_type.items())) or "-",
             other_tsv))
    for t in tables:
        print("  %-46s %s:%s_id  columns: %s" % (t["path"], t["kind"], t["etype"], table_columns(t)))
    for bad in unreadable:
        print("  UNREADABLE table: %s" % bad)
    if not configs:
        print("PREREQUISITE 0 configs: no %s file under %s, so there is nothing to derive an entity from"
              % (CONFIG_SUFFIX, corpus_dir))
        print("GSVTK-ENTITY-SUMMARY configs=0 ok=0 no-name-key=0 many-name-keys=0 no-shipped-table=0 "
              "columns-missing=0 cannot-parse=0 namekeys-one=0 namekeys-zero=0 namekeys-many=0")
        # The oracle census prints here too, at zero: the flavor list comes from the configs, so no configs
        # really is `flavors=0` — and a machine line that appears only on some paths is one every caller has
        # to special-case, which is how a missing line comes to mean two different things.
        print("GSVTK-ORACLE-SUMMARY flavors=0 ok=0 absent=0 malformed=0 dupes=0 names=0 "
              "values=not-read enforced=yes")
        # The value census prints at zero here too. A machine line that appears only on some paths is one
        # every caller has to special-case, and `configs=0` is this corpus's real answer, not a placeholder.
        print(value_summary_line([]))
        # And so does the required-input census, for the same reason: a caller that has to special-case a
        # missing line is a caller that reads its absence as a pass.
        print(required_summary_line([]))
        return 3

    rows = []
    prefixes_of = {}
    for full in configs:
        rel = os.path.relpath(full, corpus_dir).replace(os.sep, "/")
        cfg = read_config(full, jinja_re)
        prefixes = sorted({k.split(".")[0] for k in cfg["keys"]}) if cfg["ok"] else []
        prefixes_of[rel] = [p for p in prefixes if p]
        v = grade(cfg, tables)
        v.update(config=rel, filestem=os.path.basename(full)[:-len(CONFIG_SUFFIX)],
                 flavor=rel.split("/")[0] if "/" in rel else ROOT_FLAVOR,
                 wf=prefixes[0] if len(prefixes) == 1 else ",".join(prefixes) or "?",
                 workspace_reads=workspace_reads(cfg) if cfg["ok"] else 0)
        rows.append(v)

    # The oracle is read per FLAVOR, and the flavor list comes from the configs that were just graded —
    # never from a walk that found tables, which could not produce an ABSENT answer at all.
    oracles = read_workspace_oracle(corpus_dir, [r["flavor"] for r in rows])
    # Every value of every config is then classed against ITS flavor's oracle, and only against that one:
    # the bundle a value came from is a question about the config, and the only offline answer to a
    # `${workspace.X}` name is the table sitting beside the configs that read it.
    oracle_of = dict((o["flavor"], o) for o in oracles)
    # `profiles` was listed by whichever branch materialized the tree: the directory listing for `--tree`,
    # `git ls-tree` for `--repo/--ref`. Names only in both cases, and `source` says which of the two ran — the
    # two paths print byte-identical value censuses on a ref that ships no test marker, so a swapped branch
    # selection would otherwise be invisible in the output.
    for r in rows:
        r["values"] = classify_values(os.path.join(corpus_dir, r["config"].replace("/", os.sep)),
                                      r["config"], r["flavor"], r["wf"], oracle_of.get(r["flavor"]),
                                      jinja_re, profiles)
    # And the acceptance leg, against those same classes: every required input of every workflow this
    # config's own binding keys name, graded by the class of the value that carries it. One cache for the
    # whole run, keyed by workflow, because a WDL is loaded with its whole import closure (measured: the
    # biggest workflow in gatk-sv's tree costs ~7 s to load, and the corpus has ~31 of them).
    for r in rows:
        r["required"] = grade_required(r["values"], prefixes_of.get(r["config"], []), ctx, cap_cache)

    width = max([len(r["config"]) for r in rows] + [6])
    line = "%%-14s %%-%ds %%-22s %%-16s %%-12s %%-36s %%s" % width
    print("\n" + line % ("FLAVOR", "CONFIG", "WORKFLOW", "NAME KEY(S)", "COLLECTIONS", "TABLE", "STATE"))
    for r in rows:
        tbl = r["table"] or "-"
        if len(tbl) > 36:
            tbl = "..." + tbl[-33:]
        print(line % (r["flavor"][:14], r["config"], (r["wf"] or "?")[:22],
                      ",".join(r["namekey_types"]) or "-", ",".join(r["member"]) or "-", tbl,
                      r["state"] + ((" (%s)" % r["reason"]) if r["reason"] else "")))

    print("\n== refusals, naming what was looked for and where ==")
    findings = [r for r in rows if r["state"] != "OK"]
    if not findings:
        print("  (none — every config in the corpus answered)")
    for r in findings:
        print("UNRESOLVED %s\n    %s" % (r["config"], reason_text(r, args.corpus, tables)))
        for n in note_text(r):
            print("    note: %s" % n)
    for r in rows:
        notes = note_text(r) if r["state"] == "OK" else []
        if notes:
            print("NOTE %s\n    %s" % (r["config"], "\n    ".join(notes)))

    counts = dict((k, 0) for k in ("ok",) + REASONS)
    for r in rows:
        counts["ok" if r["state"] == "OK" else r["reason"]] += 1
    one = sum(1 for r in rows if len(r["namekey_types"]) == 1)
    zero = sum(1 for r in rows if len(r["namekey_types"]) == 0)
    many = sum(1 for r in rows if len(r["namekey_types"]) > 1)
    print("\n== census ==")
    print("configs %d | name keys: one %d, zero %d, many %d | shipped-table check: pass %d, no table %d, "
          "missing column %d, unparsable %d"
          % (len(rows), one, zero, many, counts["ok"], counts["no-shipped-table"],
             counts["columns-missing"], counts["cannot-parse"]))
    print("member collections across the corpus: %s"
          % (", ".join(sorted({c for r in rows for c in r["member"]})) or "none"))
    vals = [r["values"] for r in rows]
    print("value provenance: %d value(s) classified, %d refused by class, %d disclosed as needing a bundle, "
          "%d config(s) refusing, %d with no value at all, %d the layer could not read"
          % (sum(v["bindings"] for v in vals), sum(v["unresolved"] for v in vals),
             sum(v["classes"]["needs-bundle"] for v in vals),
             sum(1 for v in vals if v["state"] != "OK"), sum(1 for v in vals if not v["bindings"]),
             sum(1 for v in vals if v["unclassified"])))
    reqs = [r["required"] for r in rows]
    print("required-input acceptance: %d required input(s) across %d captured workflow(s) asked of %d "
          "config(s), %d not bound by the config that names them, %d already refused by the value layer, "
          "%d config(s) whose workflow(s) this run could not read"
          % (sum(x["required"] for x in reqs),
             len({c["wf"] for x in reqs for c in x["captures"] if c["status"] == "OK"}),
             sum(1 for x in reqs if x["state"] != "NOT-CAPTURED"),
             sum(x["classes"]["unbound"] for x in reqs),
             sum(x["classes"][c] for x in reqs for c in VALUE_REFUSAL_CLASSES),
             sum(1 for x in reqs if x["capture"] != "OK")))
    print("\n== workspace oracle ==")
    for o in oracles:
        for line_text in oracle_report(o):
            print(line_text)
    print("  values=not-read: no row's second field was parsed, resolved or used in any decision — it is an "
          "upstream values-profile expression, and reading it would answer \"what did that profile bind\", not "
          "\"what does this ref declare\". A finding quotes its whole row, because the shape of the row IS the "
          "finding; those quoted bytes decide nothing here.")
    print("  enforced=yes: `${workspace.*}` names ARE enforced now, by the value layer below: a name with no "
          "row in its flavor's own workspace table, or in a flavor whose table did not read cleanly "
          "(ABSENT, MALFORMED, DUPE), is UNRESOLVED and moves the exit code to 1. The oracle reader above "
          "still grades nothing by itself — what it decides is `status`, and `status` is what the refusal "
          "keys off, so a table that was read but did not read cleanly resolves nothing either.")

    # The value layer's own human block. The machine lines carry a census for EVERY config; this one prints
    # only the configs with something to say, and it says the refusal in the same style as the entity
    # refusals above: the name, the file it was looked for in, and what was found there.
    print("\n== value provenance ==")
    said_any = False
    for r in rows:
        for line_text in value_report(r["values"], oracle_of.get(r["flavor"])):
            print(line_text)
            said_any = True
    if not said_any:
        print("  (no config had anything to say: no refused value, no `{{ ... }}` bundle read to disclose, "
              "no `*_docker` key, no read below binding level, nothing dropped by a duplicate)")
    value_findings = [r for r in rows if r["values"]["state"] != "OK"]
    # The acceptance leg's own human block, in the same shape as the two above it: the name, the file the
    # question was asked of, what came back, and the file the cure belongs in.
    print("\n== required inputs ==")
    reqs = [r["required"] for r in rows]
    partial = [x for x in reqs if x["capture"] != "OK"]
    if partial:
        # Stamped once, at the top, because this is the shape a whole run can quietly stop answering and a
        # per-config scroll is how that fact gets skimmed past. `docs/static-checks.md` carries the same
        # sentence for the operator who reads the exit code instead of the output.
        shown, count = uncaptured_workflows(reqs)
        print("  REQUIRED-NOT-CAPTURED %d of %d config(s), cause %s (%d workflow(s) not read: %s): at least "
              "one workflow behind these could not be read, so the required-input census is incomplete for "
              "them and says NOT-CAPTURED where it could ask nothing. An exit code of 0 below means the "
              "entity and value questions were answered for every config; it does NOT mean every required "
              "input is bound."
              % (len(partial), len(rows), required_uncaptured_cause(reqs), count, shown))
    said_any = False
    for r in rows:
        for line_text in required_report(r["required"]):
            print(line_text)
            said_any = True
    if not said_any:
        print("  (nothing to say: every required input of every captured workflow is bound by the config "
              "that names it, with no unsatisfied key, no uncaptured workflow, no image and no bundle read "
              "to disclose)")
    required_findings = [r for r in rows if r["required"]["state"] == "UNRESOLVED"]
    for r in rows:
        print(machine_line(r))
        print(value_machine_line(r["values"]))
        print(required_machine_line(r["required"]))
    print("GSVTK-ENTITY-SUMMARY configs=%d ok=%d no-name-key=%d many-name-keys=%d no-shipped-table=%d "
          "columns-missing=%d cannot-parse=%d namekeys-one=%d namekeys-zero=%d namekeys-many=%d"
          % (len(rows), counts["ok"], counts["no-name-key"], counts["many-name-keys"],
             counts["no-shipped-table"], counts["columns-missing"], counts["cannot-parse"],
             one, zero, many))
    # The scope statement, printed every run whether or not anything refused. Everything above answers "did the
    # entity a config names hold the columns it reads"; it does NOT answer whether the values are right, and a
    # reader who only looks for UNRESOLVED lines will not notice the difference. So the things this tool
    # deliberately does not evaluate get a census of their own, with counts, on every run — including a clean
    # one. An absent line here would mean the corpus holds none of these reads, which is a fact about the
    # corpus and never a pass.
    member_cfg = sum(1 for r in rows if r["member_attrs"])
    member_reads = sum(len(r["member_attrs"]) for r in rows)
    # R17: `workspace-binding-reads` keeps its meaning — reads this tool did NOT look at — and so it goes to
    # 0 honestly, because the value layer above classified every read of every document it could read. What
    # stays here is the number for the documents it could NOT read: those reads really were not looked at,
    # and zeroing them because the layer above exists would be the same lie as a blank line.
    unread = [r for r in rows if r["values"]["unclassified"]]
    ws_reads = sum(r.get("workspace_reads", 0) for r in unread)
    ws_cfg = sum(1 for r in unread if r.get("workspace_reads"))
    attr_values = sum(o["value_cells"] for o in oracles)
    reqs = [r["required"] for r in rows]
    print("GSVTK-ENTITY-UNCHECKED configs=%d member-attr-reads=%d configs-with-member-attrs=%d "
          "workspace-binding-reads=%d configs-with-workspace-reads=%d "
          "workspace-attribute-values-unread=%d configs-with-unclassified-values=%d "
          "required-configs-uncaptured=%d required-uncaptured-because=%s "
          "root-entity-type=never-read"
          % (len(rows), member_reads, member_cfg, ws_reads, ws_cfg, attr_values, len(unread),
             sum(1 for x in reqs if x["capture"] != "OK"), required_uncaptured_cause(reqs)))
    print("  (the counts above are SCOPE, not verdicts, and none of them is 0 because the tool wished it: "
          "member attributes are counted and listed but never checked against a table, because "
          "plural-to-singular is a guess; the workspace ATTRIBUTE VALUES — the second column of every "
          "flavor table — are counted and never opened, because reading one answers \"what did that values "
          "profile bind\" instead of \"what does this ref declare\"; ${workspace.*} reads are now classified "
          "and enforced, so the reads still unexamined are only the ones inside documents this layer could "
          "not parse, and a document that will not parse can only have its unreadability reported, never its "
          "reads counted — which is what configs-with-unclassified-values= says; required-configs-uncaptured "
          "counts the configs whose workflow was never opened, so the required-input census does not cover "
          "them and their machine lines say capture=<word> rather than OK — the cause is that field, broken "
          "down per word on GSVTK-REQUIRED-SUMMARY, and a run with miniwdl absent reports EVERY config here "
          "because no WDL could be read at all; and Terra's own "
          "rootEntityType is never asked, because that is a credentialed read this tool must not make.)")
    for o in oracles:
        print(oracle_machine_line(o))
    buckets = dict((s, 0) for s in ORACLE_STATUSES)
    for o in oracles:
        buckets[o["status"]] += 1
    print("GSVTK-ORACLE-SUMMARY flavors=%d ok=%d absent=%d malformed=%d dupes=%d names=%d "
          "values=not-read enforced=yes"
          % (len(oracles), buckets["OK"], buckets["ABSENT"], buckets["MALFORMED"], buckets["DUPE"],
             sum(o["attrs"] for o in oracles)))
    print(value_summary_line([r["values"] for r in rows]))
    print(required_summary_line([r["required"] for r in rows]))
    # Every layer can refuse, and none is allowed to hide the others: an entity finding, a value finding and
    # a required-input finding are three different questions about the same config, and a run that exited on
    # one while the others went unscored would be a run that printed a number nobody asked for.
    return 1 if (findings or value_findings or required_findings) else 0


if __name__ == "__main__":
    sys.exit(main())
