# Audit report — robust realism evaluation estate

Estate built at seed `20260913`. Pipeline measured at repo commit `e10bb17`
(branch `feature/integration-runtime-output`), 2026-09-13.

**No production code was changed to produce this report.** Everything below is
what the current `src/` does when pointed at an estate it has never seen.

---

## A. Public data used

| Source | Used for | Copied? |
|---|---|---|
| **SAT — Artículo 69-B / EFOS** | `efos_list` status vocabulary (`presunto` → `definitivo`), DOF batch-publication cadence, the fact that legal effect attaches from the definitive publication | No RFC or record copied. 58 synthetic rows. |
| **SAT — CFDI 4.0 / Anexo 20** | `uso_cfdi`, `forma_pago`, `metodo_pago`, the PPD ⇒ `forma_pago 99` filling rule, VAT rates (16 % / 8 % border / 0 % exempt) | `G01`, `G03`, `I01`, `P01` confirmed from SAT-hosted Anexo 20 docs. The rest reproduced from the published catalogue. **`sat.gob.mx` returned HTTP 403 and `omawww.sat.gob.mx` refused connection on 2026-09-13** — recorded, not papered over. |
| **gob.mx — *Catálogo de bancos*** | The first three digits of every CLABE | **91 institution codes extracted verbatim** from the PDF and checked into `external_reference/bank_codes_reference.json`. Short names normalised by hand (the PDF emits one glyph per operator). Plaza codes *not* verified against a primary source — flagged as format realism. |
| **INEGI — DENUE 11/2024 + SCIAN 2023** | Vendor category mix, sector names, real municipality/state names | Only the aggregate 44 % / 42 % / 11 % establishment split. No establishment record retrieved. |
| **CompraNet** | Contract shapes (`contrato marco`, `abierto`, `por proyecto`), milestone phrasing, 20 procurement descriptions | Nothing. Bulk download not retrieved; vocabulary only. |

Full detail, access dates and every limitation: [`source_provenance.md`](source_provenance.md).

**Ethical line held.** No invented transaction is attached to any real taxpayer,
company or person. All RFCs, CLABEs, company names and employee names are
procedurally generated. Every CLABE carries a **computed valid check digit**
(weights 3-7-1); all 127 vendor, 46 employee and 2,464 transaction endpoints pass
a CLABE validator.

---

## B. Estate summary

| Table | Rows |
|---|---:|
| `vendors` | 127 |
| `invoices` | 1,497 |
| `ledger` | 6,827 |
| `bank_txns` | 1,232 |
| `purchase_orders` | 832 |
| `contracts` | 96 |
| `employees` | 46 |
| `efos_list` | 58 |

Period 2024-01-01 → 2025-12-31. Company `RFC:IMX210415K72`. Total *vigente*
purchase exposure **136,006,964.73 MXN**.

- **Honest activity dominates:** 30 invoices out of 1,497 (**2.0 %**) belong to a
  planted scheme. A showcase estate would be mostly fraud; this one is not.
- **True suspicious scenarios:** 5 (all five official scheme types), two of them
  entangled through `RFC:KAB230530JJI`, which appears in both the round trip and
  the revenue inflation.
- **Benign lookalikes:** 18 hand-engineered decoys (D1–D18).
- **Ambiguous / incompleteness traps:** D13 (honest vendor with no `contracts`
  row at all — *absence in the estate ≠ absence in reality*), D3 (EFOS listing
  with zero transactions), D10 (cancelled invoice that must not count as
  exposure).
- **Unnamed honest population:** everything else. The scoring rule stated in the
  manifest is that **any accusation against an entity absent from the schemes
  section is a false accusation**, named decoy or not.

Three things were built in deliberately to defeat shortcuts:

1. **Three VAT rates coexist**, so `total == subtotal * 1.16` is not a safe check.
2. **353 invoices are `PPD` + `forma_pago 99`** — the coding the SAT filling rule
   *requires*. A detector that treats `99` as suspicious false-positives on 24 %
   of the book.
3. **Human-readable ids are renumbered chronologically after generation.** Planted
   rows are created last; had ids followed generation order, every scheme
   transaction would sit at the end of the sequence and a detector could cheat on
   ordinality alone. There is a test for this.

---

## C. Official scheme coverage

| Scheme | id | peso_amount | Difficulty | Entities |
|---|---|---:|---|---|
| `phantom_vendor` | S1 | 2,795,600.00 | easy | `RFC:BER2407128JB` |
| `kickback` | S2 | 534,238.00 | medium | `RFC:ARD190326XMY`, `EMP:0003` |
| `round_tripping` | S3 | 6,830,000.00 | medium | company, `RFC:QUI2211089GU`, `RFC:KAB230530JJI` |
| `threshold_splitting` | S4 | 586,830.00 | medium | `RFC:OND2106030B1`, `EMP:0009` |
| `revenue_inflation` | S5 | 13,953,060.00 | hard | company, `RFC:KAB230530JJI`, `RFC:MEL230914D06` |

`peso_amount` for each scheme reconciles per table to its own
`supporting_invoices` / `supporting_txns` within 2 %, the same rule the official
validator applies. S3 deliberately reports **only the three outbound legs**: the
full nine-leg cycle sums to 20.2 M and a finding citing all nine could never
reconcile per table. That constraint is in the answer key on purpose.

**No legal threshold was invented.** The official materials supply none, so S4
rests on an *internal approval ladder modelled in the data* (Coordinador
< 100,000 < Gerente < 500,000 < Director) that is written nowhere in the estate
and is discoverable only from the `amount` ↔ `approver` relationship. About one
order in twenty is signed one level high, so an auditor that infers a hard
threshold from this estate has over-fitted — there is a test asserting the ladder
is leaky.

---

## D. What our system detected

Six deterministic detectors, 0.67 s, 24 observations → 19 leads → 19 case states,
0 errors.

| Scheme | Detected? | By what |
|---|---|---|
| S1 `phantom_vendor` | **yes** | `detect_efos_vendor_matches` |
| S2 `kickback` | **yes** | `detect_vendor_to_employee_transfers` (5 of 5 legs) |
| S3 `round_tripping` | **yes** | `detect_directed_bank_transfer_cycles` — the exact 3-CLABE cycle |
| S4 `threshold_splitting` | **yes** | `detect_short_window_similar_invoice_clusters` (2 maximal windows) |
| S5 `revenue_inflation` | **no** | — |

Four of five schemes raise a lead. Then the deterministic production path
(`claim_builder` → `OfficialVerifier` → `EvidenceGate` → `FindingBuilder` →
`SubmissionBuilder`, with only the two LLM reviews synthesised, exactly as
`tests/test_vertical_slice_smoke.py` does) authorises S1:

```
claim_builder        2,795,600.00   errors=[]
PHANTOM-EFOS-INVOICE-LINK  verified
PESO-RECONCILIATION        verified
gate                 authorize_probable
finding              phantom_vendor  RFC:BER2407128JB  MXN 2,795,600.00  8 exhibits
```

---

## E. What it missed

**S5 `revenue_inflation` is completely invisible.** No detector reads the
sales side at all — no settlement-rate check, no period-close check, no
revenue-without-collection check. 13,953,060 MXN, the largest scheme in the
estate, produces zero observations. It is also the one scheme whose signal is a
*rate* (0 of 9 settled against a 93 % house average) rather than a row-level
pattern, so it needs an aggregate detector, not another row matcher.

Second-order misses, all of which raise a lead but cannot reach a finding because
no substantive verifier exists for the scheme type:

- S2 kickback: 5 observations, no `kickback` verifier.
- S3 round tripping: cycle found, no continuity test of amount or date, so
  nothing distinguishes it from the benign cycle.
- S4 threshold splitting: clusters found, no approver-ladder inference.

---

## F. False-positive risks

**This is the serious finding.** The estate produced a real false accusation.

### F.1 CONFIRMED FALSE ACCUSATION — D16, the pre-publication EFOS decoy

Driving the *same* production path that authorises S1, but with `RFC:GAL160126E3F`:

```
claim_builder        2,506,760.00   errors=[]
PHANTOM-EFOS-INVOICE-LINK  verified
PESO-RECONCILIATION        verified
gate                 authorize_probable
finding              phantom_vendor  RFC:GAL160126E3F  MXN 2,506,760.00  6 exhibits
```

That vendor is honest. It is on the 69-B list as **`presunto`**, published
**2025-12-12**; its five invoices are dated **2024-02-14 through 2024-10-15**,
all more than a year *earlier*, each with an approved purchase order under a 2023
contract. The pipeline accused it of 2.5 M MXN of phantom invoicing.

Root cause, verified: `PHANTOM-EFOS-INVOICE-LINK` computes a bare set
intersection between `efos_list.rfc` and `invoices.issuer_rfc`. It reads
**neither `efos_list.status` nor `efos_list.publication_date`**. Every EFOS match
is therefore treated identically, whether the listing is a definitive resolution
that predates the invoices or a presumption published a year after them.

### F.2 Decoy pressure measured today

Of the 18 decoys, four fire on a current detector:

| Decoy | Signal | Outcome today |
|---|---|---|
| **D16** pre-publication EFOS | `efos_vendor_match` | **false accusation authorised** |
| D4 vendor = employee (persona física) | `vendor_employee_shared_clabe` | lead, no finding (no verifier) |
| D5 documented travel reimbursement | `vendor_to_employee_transfer` | lead, no finding |
| D18 two group companies, one treasury CLABE | `shared_vendor_clabe` | lead, no finding |

D4, D5 and D18 are safe only *by accident* — they stop at the gate because no
`kickback` verifier exists yet. When one lands, all three become candidate false
accusations unless it checks the clearing evidence.

D2 (the EFOS name twin, `RFC:BER1903047A5`, one letter from the phantom's legal
name) is **genuinely safe**: matching is on exact RFC, never `legal_name`.
Confirmed by test.

### F.3 Unnamed false-positive pressure

`detect_short_window_similar_invoice_clusters` fires on **12 windows, 10 of which
are ordinary honest vendors** that appear nowhere in the manifest. At ~83 %
noise, this detector alone would drive most of the LLM budget and most of the
false-accusation risk if anything downstream treated a cluster as a finding.

---

## G. Challenger performance

**Not measured.** Running the Challenger requires a live Gemini call or a
recorded session; neither exists for this estate and producing one is the peer
session's in-flight work. Reported as unmeasured rather than estimated.

What the estate is built to test, once it can be run:

- Does the Challenger surface the contract that clears D1, D7 and D13?
- Does it reach for `efos_list.publication_date` on D16 — the one move that
  prevents the confirmed false accusation?
- Does it distinguish S3 from D6, which have **identical topology** (both are
  3-CLABE directed cycles through the company operating account) and differ only
  in amount continuity (≤1.5 % per leg vs a 16× spread) and date continuity
  (3–4 days per leg vs 5–10 months)?
- Does it answer the judges' stated question — *"what if the employee just
  happens to bank at the same institution?"* — by comparing full 18-digit CLABEs
  rather than the 3-digit prefix? D4's vendor and employee share an **entire
  CLABE**, legitimately.

## H. Method Critic performance

**Not measured**, same reason. `evidence_dependency_map.json` was built to test
it and contains:

- **`same_money_groups`** — for every planted invoice, the ledger entries and the
  bank transaction that settle it. One economic event in three tables. A finding
  citing all three is one fact rendered three ways, never three independent
  corroborations.
- **`records_feeding_three_or_more_analytics`** — the rows consumed by three or
  more of {tabular detectors, relational detectors, bank graph, GNN, verifier,
  reconciliation}.

One dependency is already visible without the LLM: the five S2 kickback legs
produce **five near-identical observations** that collapse into one lead. Five
signals, one underlying pattern. And the bank-cycle detector and the GNN read the
*same* `bank_txns` rows, so agreement between them is not independent
corroboration — the map records this explicitly.

---

## I. Verifier / Gate performance

| Case | Verifier | Gate | Correct? |
|---|---|---|---|
| S1 phantom vendor | both critical checks verified | `authorize_probable` | **yes** |
| D16 pre-publication decoy | both critical checks verified | `authorize_probable` | **no — false accusation** |
| S2, S3, S4 | substantive check `unresolved` (no verifier for the scheme) | blocked | correct-by-accident: right outcome, wrong reason |
| S5 | never reaches the verifier | — | missed upstream |

The gate fails closed and did its job everywhere a verifier existed. The failure
is not in the gate's logic; it is that the one substantive verifier it has asks
too weak a question.

---

## J. Official validator result

**PASS, against both estate forms, with estate checking enabled.**

```
python official_materials/student-materials/forensic-auditor/validate_format.py \
    --submission runs/robust_realism/submission.json \
    --estate dev_eval/robust_realism_estate.db
  findings: 2   leads_not_pursued: 0   estate check: yes (sqlite)
  PASS  submission conforms to the required format

  ... --estate-zip dev_eval/robust_realism_estate_csv.zip
  findings: 2   leads_not_pursued: 0   estate check: yes (zip)
  PASS  submission conforms to the required format
```

Note what this does and does not mean: the submission is **format-perfect and
50 % wrong**. One of those two findings is a false accusation. The validator
cannot see that, which is exactly why the answer key exists.

Repo state: `351 passed, 3 skipped` (the three skips are the GNN modules, which
need `torch`). The judges' isolation grep is clean:
`grep -r 'ground_truth' src/ --include='*.py'` returns nothing.

---

## K. Minimal fixes

Only fixes justified by a failure actually observed above. Ordered by value.
**All of these are in `src/`, which this session does not own** — they are
reported, not applied.

1. **Make `PHANTOM-EFOS-INVOICE-LINK` read `status` and `publication_date`.**
   The single highest-value change in the repo: it is the difference between a
   correct finding and a 2.5 M MXN false accusation, and D16 is a ready-made
   regression test. An EFOS match should support a `phantom_vendor` finding only
   where invoices were issued *after* a `definitivo` publication. A `presunto`
   listing, or invoices that predate the publication, is a lead — never an
   authorisation. *(Fixes F.1, I.)*

2. **Strip the double `EMP:` prefix.** Both `detect_vendor_employee_shared_clabe`
   and `detect_vendor_to_employee_transfers` emit `EMP:EMP:0003` — they prepend
   `EMP:` to an `emp_id` that already carries it, per the official
   `employees.csv` example. Any finding built from these observations would ship
   an entity id the judges cannot match. Three employees affected.
   *(Observed in the detector output.)*

3. **Resolve `CLABE:` lead subjects to an official entity.** `build_leads` groups
   on `entities[0]`; for a bank cycle and a shared-CLABE observation that is a
   `CLABE:` string, so 3 of 19 leads have a subject that can never appear in a
   submission. `run_audit._official_entity` already knows how to resolve a CLABE
   to its owner — the same resolution needs to happen at lead-building time, or
   the round-tripping lead arrives at the Investigator with no entity.
   *(Observed: 3 CLABE-subject leads.)*

4. **Add an aggregate sales-side detector for `revenue_inflation`.** Settlement
   rate per counterparty over the period, plus revenue booked on invoices later
   cancelled with no reversing entry. It is the only scheme with zero coverage,
   and at 13.9 M MXN the largest in the estate. D8 (a real year-end spike
   repeating across both 2024 and 2025) is the decoy that stops this becoming a
   seasonality detector. *(Fixes E.)*

5. **Gate the invoice-cluster detector behind cheap deterministic coverage
   checks** — `contract_coverage` and `po_coverage` — before any LLM call. Ten of
   twelve current cluster hits are honest vendors; D7 (twelve identical 89,500
   MXN invoices against a contract that fixes exactly that retainer) and D1 (a
   contract that explicitly waives the PO) are each closed by one lookup. Each
   closure should write a `leads_not_pursued` entry naming the contract.
   *(Fixes F.3, and fills the empty `leads_not_pursued` the judges will ask about.)*

6. **Add amount- and date-continuity tests to the cycle detector** before any
   `round_tripping` verifier is written. Topology alone cannot separate S3 from
   D6 — both are 3-CLABE directed cycles through the same company account — and
   a verifier built on topology would accuse D6. *(Fixes F.2's latent risk.)*

### Deliberately not proposed

- Nothing about `legal_name` matching: exact-RFC matching already defeats D2.
- No statutory procurement threshold: none exists in the official materials, and
  inventing one would break rule 6.
- No change to the Evidence Gate: it failed closed correctly every time.
