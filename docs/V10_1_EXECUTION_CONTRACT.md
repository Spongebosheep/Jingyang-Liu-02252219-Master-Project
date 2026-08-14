# PurrStone v10.1 technical execution contract

## Why v10.1 exists

The preserved v9 evidence showed that the prompt-only Baseline completed only
one of sixteen attempts. Fifteen attempts stopped when the model selected an
extra follow-up and the next frozen participant input belonged to the next
topic. The v10 construct/Consent correction did not change that path mechanism.

v10.1 therefore makes four pre-formal corrections:

1. Baseline prompt v3 makes each locked section's `assessment_guidance` the
   binding Protocol-coverage threshold. `required_information` remains a set of
   researcher-defined dimensions and is conjunctive only when that guidance
   explicitly says so.
2. Cross-field inconsistent structured output is retained as a parse failure;
   it is never semantically repaired.
3. One executable schedule defines the exact 16 pairs and 32 condition attempts.
4. Blinded packaging excludes and reports incomplete/ineligible pairs instead
   of aborting because one pair is incomplete.

The Baseline runner's section/path check is deliberately unchanged. If a live
model selects a path that cannot consume the next common frozen participant
input, the attempt fails. Automatically overriding that action would invalidate
the prompt-only comparison.

## Exact schedule

```text
S1-R01 S1-R02 S1-R03
S2-R01 S2-R02 S2-R03
S3-R01 S3-R02 S3-R03
S4-R01
S5-R01
S6-R01 S6-R02 S6-R03
S7-R01
S8-R01
```

Each pair contains one `mvp` and one `prompt_only_baseline` attempt. This is 16
pairs and 32 attempts, not three repetitions of all eight scenarios.

## Development gate

`run_baseline_development_gate` executes those sixteen Baseline identities with
the live Responses API and `dry_run` database transactions. It records a plan
snapshot before the first call, preserves each raw runner/validator artefact,
continues through all sixteen paths, and passes only at 16/16 independently
validated paths. Every gate result is explicitly ineligible as formal evidence
and may not replace a formal attempt.

After both live gates pass without source changes, the final C2 freeze changes
exactly `evaluation/specs/freeze_manifest.v10_1.json` and
`SOURCE_MANIFEST.sha256`. C2 is therefore a freeze-metadata-only commit, not a
literal one-file manifest-only commit.

## Formal plan

Before creating a formal-plan directory or calling either runner, the command
requires `product_baseline.local_baseline_commit` to contain the finalised full
lowercase 40-character hexadecimal C1 commit hash. The pending sentinel or any
malformed value blocks formal execution.

`run_formal_evaluation_plan` writes the complete attempt plan before its first
call. It executes both conditions for `S1-R01`; a failed pilot stops the plan.
After a passed pilot, it attempts all remaining registered identities once,
retaining later failures without replacement.

## Pair selection for human coding

`quality_review.v2.json` requires an included pair to be:

- on the registered schedule;
- exactly one run from each condition;
- identity/fairness `PASS`;
- execution `completed` for both;
- independent validation `PASS` for both; and
- formally evidence-eligible for both.

Excluded and absent pairs are listed in `pair_selection_report.json`. Sixteen is
the recommended target, eight is the minimum formal coding threshold, and fewer
than eight blocks coder-material generation. No missing pair may be replaced by
a v9 record or a second attempt with the same identity.

## Claim boundary

Passing offline tests, live development gates, formal execution, export, or
automatic metrics does not perform A/B quality review, UCR proposition coding,
Meaning Preservation coding, or a superiority test. Those human judgements
remain separate and require two independent real coders plus retained consensus.
