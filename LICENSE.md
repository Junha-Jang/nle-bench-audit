# Licence

Copyright 2026 Someple Inc.

In short: covered material may be used, shared and adapted for non-commercial
purposes, and sharing requires attribution. Anyone, including for-profit
organisations, may also use it to reproduce or check the results of the paper
named below. A use that needs permission beyond these grants requires a
separate written agreement with Someple Inc. The material listed under "Not
covered" is outside these terms. The text below is what applies.

Every file in this repository, program source included, is licensed under the
Creative Commons Attribution-NonCommercial 4.0 International licence
(CC BY-NC 4.0), except the material listed under "Not covered". The legal code
is in `LICENSE`. On 14 November 2026, Junha Jang, answering for Someple Inc.,
chose this licence.

This includes the pinned benchmark modules, the scorer files and the case
scenario files. Their source commits and hashes, recorded under `provenance/`,
say where they come from; they do not change the terms offered here.

## Attribution

When you share this material, keep the notices that CC BY-NC 4.0 requires and
attribute it to Junha Jang (Someple Inc.). To cite it, see `CITATION.cff`.

## Additional verification permission

In addition to what CC BY-NC 4.0 permits, and within the Copyright and Similar
Rights that it covers, Someple Inc. permits anyone, including for-profit
organisations and people acting on their behalf, to copy, inspect, run and
locally modify the files of this repository, and to extract and analyse its
data, records and documents, solely to reproduce or check the results reported
in the paper "Red-Teaming the Grader: Mutation-Validated Constraint Checking for
Tool-Agent Benchmarks" (NeurIPS 2026 Workshop: Who Verifies the Agents?). This
includes publishing the findings of such a check, with the excerpts or patches
reasonably needed to support them. For these activities only, the licence's
NonCommercial limitation does not apply; all its other terms, including
attribution, apply as they do under the licence. The material listed under "Not
covered" stays outside this permission. The permission covers no other use,
such as use in a product or service, model training, or evaluation unrelated to
checking that paper. It does not narrow what the licence or the law otherwise
allows.

## Other uses

For other uses, including commercial use, contact Someple Inc. at
junha.jang@someple.com; any further permission requires a separate written
agreement.

## Not covered

- **The two stored model responses**,
  `cases/NLB-v3-EX-CO-004/states/agent_response.txt` and
  `cases/NLB-v3-EX-CO-009/states/agent_response.txt`. They are text a model
  produced. Whether anyone holds rights in them is not established, so nothing
  is granted for them here. They are kept because the two worked cases judge
  the runs they belong to.
- **The legal code** in `LICENSE`, which is supplied under its own terms.

## Scope

This is not a licence of the benchmark. The corpora are not in this
repository, and the pinned modules and scorer files are the parts of the
benchmark's source that the audit's commands load, nothing more.
