# Known concerns about the audited benchmark, and what this audit shows about each

`issues.json` holds the full rows: original concern, existing evidence, state
at the audited pins, handling in this repository, remaining limit and
the claim it touches.

**What this page is not.** It is not a certificate that no defect remains. It
adjudicates no flagged scenario. Nothing was re-run or re-rated to fill it:
every row cites a record that already existed. The audited versions are work in
development, not a benchmark release.

| | Concern | Status | In one line |
|---|---|---|---|
| I1 | label validity: feasibility labels were never re-rated blind | **open** | needs outside raters. The audit takes every scenario's arm as given, and its own dispositions (527 over both splits, 139 held here) were generated in a coding-agent workflow, without human validation |
| I2 | constraint coverage and preservation: success certifies the authored checks, not the instruction | **partial** | over both splits 527 candidate sites, 274 recorded as blocked, 240 surviving; held here, for the dev split, 139, 70 and 67. A candidate is not a confirmed defect, and the earlier flagged scenarios were not adjudicated one by one |
| I3 | missing-slot clarification: any question earns credit | **open** | unchanged at the development pin (`models.py`, line 1496 of the pinned copy). Outside the audit |
| I4 | refusal evidence: detector audited in aggregate only | **not checked** | 21,637 records over both splits (5,484 dev-split) are carried by the stored detector verdict; no refusal label was re-judged |
| I5 | post-hoc rewrite before submission | **not checked** | not examined. The audit measures a later change (1,372 feasible verdicts over both splits, 417 dev-split) and that comparison is counterfactual where inputs changed |
| I6 | added value of tool use over a tool-free baseline | **open** | needs new agent runs. None was made |
| I7 | effect of enforcing the direction of an attribute change | **addressed** | 195 stored verdicts change over both splits, in 32 of 55 rows; reported per row as recorded. Re-executed values, not reproduced independently |
| I8 | review-period problems not all reflected in data and grader | **partial** | some repairs exist between the pins; no record maps each concern to a change, and I1, I3, I4, I6 are not repaired |
| I9 | test-split material is not to be distributed with the audit material | **addressed** | no file here names a test-split scenario; files that named scenarios hold their dev-split part |
| I10 | pinned broken examples must not be silently fixed | **addressed** | modules are kept as byte-identical git blobs of each commit; scenario files too, except one placeholder value in six of them (`cases/SCENARIO_IDENTITIES.json`) |

Two counts share the number 173 and are different things: the scenarios an
earlier coverage audit flagged, and the surviving instruction-freedom keys of
this audit (over both splits) that were left unhardened on purpose. Neither is derived from the
other.

A full repair of the benchmark, a human study and a formal release are separate
work and are not part of this repository.
