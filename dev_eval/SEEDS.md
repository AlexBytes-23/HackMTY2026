# Tuning and reporting seeds

The judging rules require the two sets to be **disjoint and named**. Numbers
from a tuned seed cap Results regardless of the margin, so this file is the
record of which is which.

| Set | Seeds | Used for |
|---|---|---|
| **Tuning** | `101`, `102`, `103` | Development. Prompts and detectors were measured and adjusted against these. |
| **Held-out (reporting)** | `20260913` (primary), `201`, `202`, `203`, `204` | Never touched during development. Reported numbers come from here only. |

The primary held-out estate is checked in as
`dev_eval/robust_realism_estate.db` with its answer key. The rest are not
committed: they are ~28 MB of binaries that regenerate byte-for-byte in
seconds.

```bash
# tuning
for s in 101 102 103; do
  python dev_eval/generate_robust_realism_estate.py --out-dir dev_eval/tuning/seed_$s --seed $s
done

# held-out
for s in 201 202 203 204; do
  python dev_eval/generate_robust_realism_estate.py --out-dir dev_eval/heldout/seed_$s --seed $s
done
```

## Order of measurement

The held-out estates were measured **last**, after all tuning analysis was
complete, so nothing from the reporting set could influence a tuning decision.

## Current status, stated plainly

`results_table.csv` is filled from the clean throttled runs. The held-out row
is a **BEFORE-build** number: the AFTER run on the held-out estate died with
zero LLM calls when the Gemini free tier's 500 requests/day cap was reached, so
it was discarded rather than reported. Completing it needs roughly 300 calls
against a reset quota.
