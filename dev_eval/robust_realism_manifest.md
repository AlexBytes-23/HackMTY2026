# Robust realism estate - scenario manifest

Seed `20260913`. Period 2024-01-01 to 2025-12-31. Company `RFC:IMX210415K72` (Industrias Meridiano de Mexico, S.A. de C.V.).

> **This file is the answer key in prose.** It lives in `dev_eval/` and must never be reachable from `src/`.

Company accounts: operating `072580900000001013`, treasury `012580900000001028`, petty cash `058580900000001037`.

Internal approval ladder modelled in the data (stated nowhere in the estate; discoverable only from the relationship between `purchase_orders.amount` and `purchase_orders.approver`): under 100000 MXN a Coordinador may sign, under 500000 a Gerente, above that a Director. About one order in twenty is signed one level high, so the ladder is a tendency and not a rule.

## Row counts

| table | rows |
|---|---:|
| `vendors` | 127 |
| `invoices` | 1497 |
| `ledger` | 6827 |
| `bank_txns` | 1232 |
| `purchase_orders` | 832 |
| `contracts` | 96 |
| `employees` | 46 |
| `efos_list` | 58 |

## Planted schemes

### S1_phantom_vendor_1 - `phantom_vendor`

- **Classification:** true positive
- **Difficulty:** easy
- **Entities:** `RFC:BER2407128JB`
- **peso_amount:** 2,795,600.00 MXN
- **Vendor CLABE:** `127580000000001651`
- **efos_list rows:** `BER2407128JB`
- **EFOS publication_date:** 2025-03-14
- **Invoice UUIDs:** cbfdabf0-3455-5d5c-904d-9e95d1cc5d58, f6ce7be9-7de7-588a-9ce0-af79d3153d92, 7ac4979d-cef4-518e-9865-68e225d2be68, 4e5c9473-057f-5c59-a1d4-5208516e118b, 702c949b-419f-5418-af51-0698d525cd67, 17b14842-dc67-534d-91e4-acc5553bb949, d8b57cb1-3dec-5439-911b-e809d06c5cea
- **Transaction ids:** BNK-000740, BNK-000773, BNK-000823, BNK-000873, BNK-000929, BNK-000996, BNK-001047
- **PO ids:** PO-2025-00502, PO-2025-00553, PO-2025-00616

**First read:** A consultancy registered eight months before its first invoice bills 2.4 million pesos in six months, is paid within a week every time, and its RFC appears in efos_list with status 'definitivo'.

**Legitimate alternative:** A genuinely new consultancy with fast payment terms. Being on the 69-B list is an act of the tax authority, not a fact about this company's dealings.

**Strongest supporting evidence:** Exact RFC match against efos_list; publication_date 2025-03-14 precedes every one of the seven invoices; no contract row; four of seven invoices have no purchase order and the other three were raised the same day as the invoice.

**Strongest counterevidence:** The vendor shares its address with three tenants that check out (D12), so the address proves nothing. Fast payment alone is a treasury practice, not a scheme.

**Expected next investigative action:** get_vendor_contracts, get_vendor_purchase_orders, check_efos, then compare efos_list.publication_date against every issue_date.

**What must NOT be concluded:** Do not claim the invoices are fake or that the vendor does not exist. The defensible statement is that the issuer is on the definitive 69-B list and continued to invoice after publication, with no contract and incomplete purchase-order coverage in the supplied estate.

**Expected final outcome:** `authorize_probable`

**Expected investigation path:**

```
EFOS match observed -> lead opened
    -> H1 phantom_vendor
        -> Challenger: could the listing post-date the trade? -> check publication_date
            -> Investigator: check_efos + get_vendor_invoices -> every invoice is later
                -> Challenger: is there a contract that explains it? -> get_vendor_contracts -> none found
                -> Method Critic: EFOS row and invoice rows are independent records -> clear
                -> Verifier: PHANTOM-EFOS-INVOICE-LINK + PESO-RECONCILIATION verified
                -> Gate: authorize_probable
```

### S2_kickback_1 - `kickback`

- **Classification:** true positive
- **Difficulty:** medium
- **Entities:** `RFC:ARD190326XMY`, `EMP:0003`
- **peso_amount:** 534,238.00 MXN
- **Vendor CLABE:** `072580000000001667`
- **Employee CLABE:** `133580000000000031` (EMP:0003, Director de Operaciones)
- **Invoice UUIDs:** 4dc19754-f0a5-5346-ab5d-135bbc7a8742, d8ba91a1-f25d-5f7c-898d-269c90db93de, 6127bd5e-11fa-5f1e-8466-765603f1c5b2, 1df637b5-759d-5686-880f-e7b1be579753, 59af9cb6-a9a6-50d5-ba79-6b53b1c2224b
- **Transaction ids:** BNK-000166, BNK-000365, BNK-000618, BNK-000793, BNK-001022
- **Company payment ids (context, not the illicit leg):** BNK-000155, BNK-000348, BNK-000606, BNK-000774, BNK-001005
- **PO ids:** PO-2024-00102, PO-2024-00218, PO-2025-00364, PO-2025-00470, PO-2025-00602

**First read:** Every purchase order for this maintenance contractor is approved by the same director, and 4 to 9 days after each payment the contractor transfers about 7% of it to that director's personal account.

**Legitimate alternative:** The director could hold a legitimate, disclosed commercial relationship with the contractor, or the transfers could be unrelated personal business between two parties who happen to bank with the same institution.

**Strongest supporting evidence:** Five payment/transfer pairs; the percentage is stable at 6.8-7.5%; the lag is always under ten days; the recipient CLABE belongs to the same employee who signed each purchase order.

**Strongest counterevidence:** The contractor has a real 2024 framework contract and performs work billed under it. The percentage being stable is consistent with an agreed commission, which would not be illegal if disclosed. The estate contains no conflict-of-interest register, so non-disclosure cannot be read from it.

**Expected next investigative action:** get_bank_transactions_for_clabe on both CLABEs, get_vendor_purchase_orders to confirm the approver on every order, then test whether any OTHER employee receives transfers from this vendor.

**What must NOT be concluded:** Do not state that a bribe was paid or that the work was not performed. State the documented facts: same approver on every order, and a proportional payment from the supplier to that approver's account within days of each settlement.

**Expected final outcome:** `authorize_probable (needs a kickback verifier, which does not exist yet)`

**Expected investigation path:**

```
vendor-to-employee transfer observed -> lead opened
    -> H1 kickback
        -> Challenger: same bank? different account? -> compare full 18-digit CLABE, not the 3-digit prefix
            -> Investigator: get_vendor_purchase_orders -> same approver on 5 of 5
                -> Challenger: is the contract real? -> get_vendor_contracts -> yes, work is real
                -> Method Critic: the payment leg and the transfer leg are different rows -> independent
                -> Conclusion: the conflict is documented; the intent is not
```

### S3_round_tripping_1 - `round_tripping`

- **Classification:** true positive
- **Difficulty:** medium
- **Entities:** `RFC:IMX210415K72`, `RFC:QUI2211089GU`, `RFC:KAB230530JJI`
- **peso_amount:** 6,830,000.00 MXN
- **Cycle accounts:** `072580900000001013` -> `021580000000001676` -> `012580000000001685` -> `072580900000001013`
- **Invoice UUIDs:** a517bb72-c93f-5f41-a9c4-2c3dc3fa19c2, 7c6f29fb-fcaf-58bb-b297-2b308f086450, be4f0246-658a-5309-905a-1f9625917c71
- **Transaction ids:** BNK-000786, BNK-000965, BNK-001136
- **Remaining cycle legs (context; adding them to the claim breaks per-table reconciliation):** BNK-000795, BNK-000803, BNK-000975, BNK-000984, BNK-001143, BNK-001150
- **Related sales invoices (context; also cited by S5):** dd5f702e-36e7-5de0-a511-2675768cc013, 33ecb453-da88-56af-9f14-bcfede5dc0c6, c7ae4fd9-ba8e-5eb0-843c-9644a34ac144

**First read:** Money leaves the operating account for a materials supplier, moves to a related distributor three days later at 98-99% of the amount, and comes back into the SAME operating account four days after that, settling one of our own sales invoices. Three times, in May, August and November 2025.

**Legitimate alternative:** A group treasury arrangement: the company buys from one affiliate and sells to another, and balances net out. Circular flows inside a corporate group are ordinary.

**Strongest supporting evidence:** Continuity of amount (each leg within 1.5% of the previous), continuity of time (3 to 4 days per leg), and the loop closes on the same CLABE it opened on. The sales invoice is settled by money that originated with us.

**Strongest counterevidence:** Each outbound leg has a CFDI and a purchase order. The middle leg has no invoice in the estate at all, and absence of that record is not evidence the payment was improper - we only ever hold our own CFDIs.

**Expected next investigative action:** Resolve the owner of every CLABE in the cycle, then check whether any leg is covered by an invoice, PO and contract that independently explain it. Compare against the benign cycle in D6.

**What must NOT be concluded:** Do not claim the goods were never delivered. A cycle in the transfer graph is a topological fact; same-money continuity is an inference that requires the amount and date continuity to be stated explicitly.

**Expected final outcome:** `need_more_work (no round_tripping verifier in scope)`

**Expected investigation path:**

```
bank cycle detected -> lead opened
    -> H1 round_tripping
        -> Challenger: D6 has the same topology and is innocent - what distinguishes them?
            -> Investigator: compare amount deltas and date gaps leg by leg
                -> Method Critic: the cycle detector and the GNN both read the same bank rows - NOT independent corroboration
                -> Conclusion: structure plus continuity, reported as probable at most
```

### S5_revenue_inflation_1 - `revenue_inflation`

- **Classification:** true positive
- **Difficulty:** hard
- **Entities:** `RFC:IMX210415K72`, `RFC:KAB230530JJI`, `RFC:MEL230914D06`
- **peso_amount:** 13,953,060.00 MXN
- **Invoice UUIDs:** b35612c0-5042-54d2-8537-44729bfa5b88, 7ef7997b-10e2-558c-9f71-dbb7bfa10533, c8e46e23-a88d-5958-b786-02c1cb1fd936, 66ca4f25-d3a2-507c-b5cc-d94d4ae200b8, 5f403d45-7245-5bf0-88e5-ea30c06cc4ee, 6d43c71e-0fed-5d55-8bde-f3d6c2fc23fe, f8936ad4-3f23-5f72-baec-d11adf3a8ca0, 2eb17fd7-5d57-563a-9aca-2e4aa0119058, ac83468e-95a3-5373-95c5-3388999a1fa2
- **Transaction ids:** (none)

**First read:** Nine sales invoices totalling about 14.3 million pesos are issued in the last seven days of June 2025 and the last nine days of December 2025 to two related parties. Revenue is booked for all nine. Five are later cancelled. None is ever collected.

**Legitimate alternative:** Ordinary year-end business. D8 shows that November and December are genuinely the company's heaviest months, and PPD terms legitimately delay settlement past the period end.

**Strongest supporting evidence:** Zero of nine settled, against 93% settlement for every other customer; both counterparties share an address with the phantom vendor's tower and with each other; the ledger carries 4100 revenue credits for all nine with no reversing entry for the five cancelled ones.

**Strongest counterevidence:** Invoices issued on 30 December under PPD terms would not be expected to settle inside the period. The cancellations may be ordinary commercial corrections. The estate contains no accounting policy that says a cancelled CFDI must be reversed.

**Expected next investigative action:** Compute the settlement rate per customer over the whole period, then check whether the June batch - which had six months to settle - settled. It did not.

**What must NOT be concluded:** Do not claim the sales never happened. State that revenue was recognised on invoices that were subsequently cancelled or never settled, and that no reversing entry appears in the supplied ledger.

**Expected final outcome:** `need_more_work (no revenue_inflation detector or verifier)`

**Expected investigation path:**

```
no detector fires today - this scheme is currently invisible
    -> Expected once a period-close detector exists: settlement-rate outlier -> lead
        -> Challenger: seasonality? -> compare with D8, which repeats across both years
            -> Investigator: check the June batch, which had six months to settle
```

### S4_threshold_splitting_1 - `threshold_splitting`

- **Classification:** true positive
- **Difficulty:** medium
- **Entities:** `RFC:OND2106030B1`, `EMP:0009`
- **peso_amount:** 586,830.00 MXN
- **Vendor CLABE:** `014580000000001706`
- **Invoice UUIDs:** d49e37f4-eb27-57de-8aa8-5dd83dd892ed, 22ac069a-0d37-5bcd-ae29-ae9be15b95ce, 0f1b4f24-0961-59f2-b990-f0f5399c5ca0, 20fb24ad-9677-54e1-b36b-b85577134bf5, 08bea9b6-b234-51e3-8579-50d39c04241b, fbdf5c7a-3dff-5732-8251-c7fc769104c5
- **Transaction ids:** BNK-000679, BNK-000669, BNK-000680, BNK-000687, BNK-000698, BNK-000699
- **PO ids:** PO-2025-00406, PO-2025-00409, PO-2025-00412, PO-2025-00419, PO-2025-00422, PO-2025-00425

**First read:** Six purchase orders between 96,400 and 99,180 pesos, raised over eleven days, same requester, same approver, describing sections A through F of one job that totals 586,830 pesos.

**Legitimate alternative:** Genuinely phased delivery. Contractors do bill a large job in sections, and a coordinator approving six orders in his own band is not by itself irregular.

**Strongest supporting evidence:** All six sit under 100,000; every other order in the estate above 100,000 is signed by a gerente or a director, and above 500,000 always by a director; the six descriptions are sections of a single scope; no contract covers them.

**Strongest counterevidence:** The approval ladder is INFERRED from the data, not stated anywhere in the estate. Roughly one order in twenty in this estate is signed a level above the ladder, so the ladder is a tendency, not a rule. D7 shows twelve invoices just under the same number that are entirely legitimate.

**Expected next investigative action:** get_vendor_contracts (none found), then reconstruct the approver distribution by amount band across the whole purchase_orders table and state the inferred threshold as an inference with its support.

**What must NOT be concluded:** Do not cite a statutory procurement threshold. No law in the estate fixes 100,000 pesos. The rule at issue is an internal approval limit inferred from the company's own approval pattern, and it must be presented as inferred.

**Expected final outcome:** `authorize_probable (needs a threshold_splitting verifier)`

**Expected investigation path:**

```
invoice cluster detected -> lead opened
    -> H1 threshold_splitting
        -> Challenger: recurring services contract? -> get_vendor_contracts -> none
            -> Challenger: compare with D7, which IS covered by a contract
                -> Investigator: derive the approver-by-amount distribution
                -> Method Critic: the inferred threshold is a company tendency, label it as inferred
                -> Conclusion: reportable with the threshold stated as inferred
```

## Decoys - honest entities that trip a detector

### D1 - `RFC:NUV250804HWW`

- **Classification:** benign lookalike
- **Signal that fires:** `new_vendor_repeating_invoices_no_po`
- **Why innocent:** Contract for this vendor states a fixed monthly fee of 106,400.00 MXN and waives the purchase order; every invoice matches the fee exactly.
- **Invoice UUIDs:** 5381fdda-fa4a-5e24-b7b6-effcde458365, 94f1f72e-b30d-5403-965f-819e864246e1, b78aca11-464b-5b86-824b-5aefa471182d, bcba0824-597d-5c01-b2a5-f6e570ee7ffe, 839cb276-2acb-5fdc-a4ba-e477b312cad5
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** get_vendor_contracts
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `decline_hypothesis` (closed by investigator)

### D2 - `RFC:BER1903047A5`

- **Classification:** benign lookalike
- **Signal that fires:** `efos_legal_name_similarity`
- **Why innocent:** Legal name is one letter from the EFOS-listed Corporativo Berlanda, but the RFC is different and does not appear in efos_list. Contract, purchase orders and payments span 24 months.
- **Confusable with:** `RFC:BER2407128JB`
- **Invoice UUIDs:** aa96697e-6a58-5c81-ad3c-82f1ed03021e, 34eee283-d134-5b1d-9fae-7ead498b7500, f4f74aa4-5851-58e3-8980-c6e5d4078734, 95b75f8d-f0c8-5859-9110-82bffb667b76, d7414b93-c7dc-57b4-a492-735ee4680cd9, 0b1f22fc-ba3d-5215-ba75-83727db8caab, 5780a982-887b-53fa-98d4-c44f2ce21998, 0313be52-7874-5b4e-8161-9888836dec70 ... (+3 more)
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** check_efos on the exact RFC
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `no meaningful lead` (closed by validator)

### D3 - `RFC:ZAN200417TML`

- **Classification:** benign lookalike
- **Signal that fires:** `efos_list_membership`
- **Why innocent:** Present in efos_list but issues no invoice to the company and appears in no other table. An EFOS listing with no transaction is not an exposure.
- **Invoice UUIDs:** (none)
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** get_vendor_invoices - returns nothing
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `no meaningful lead` (closed by investigator)

### D4 - `RFC:TRVC84021936Z`

- **Classification:** benign lookalike
- **Signal that fires:** `vendor_employee_shared_clabe`
- **Why innocent:** The vendor IS the employee: a persona fisica con actividad empresarial registered as a training supplier, with a contract, purchase orders and invoices. One person, one bank account, two roles.
- **Employee:** `EMP:0031`
- **Invoice UUIDs:** 86e71ef5-773b-51ce-8d63-549ca3bbc2d8, f71d3a39-ada8-50b3-81cb-876050383330, 468d2fad-bad1-587c-8ee3-b8ea338bd14d, c6de3810-e5a7-57b9-9142-e9f2f2409177, 53f660e2-1bdc-5ed7-8d2f-d78a90c6b781, 68837589-c4e2-5d64-9991-249a405e7551, ff4ee1c8-1638-5715-a08e-6b6457469b7f
- **Fires on a production detector today:** yes
- **Expected next investigative action:** get_employee + get_vendor + get_vendor_contracts
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `decline_hypothesis` (closed by challenger)

### D5 - `RFC:VIA171002P3W`

- **Classification:** benign lookalike
- **Signal that fires:** `vendor_to_employee_transfer`
- **Why innocent:** Single 8,432.50 MXN transfer described as reimbursement of documented expenses, two weeks after the employee's trip and unrelated in amount to any purchase order. The vendor is a travel agency.
- **Employee:** `EMP:0022`
- **Invoice UUIDs:** 93e82806-1642-50f5-a778-74528d3cf493, cbf5743a-0486-5856-a879-4fd2a806510d
- **Transaction ids:** BNK-000880
- **Fires on a production detector today:** yes
- **Expected next investigative action:** get_bank_transactions_for_clabe, read the reference text
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `decline_hypothesis` (closed by challenger)

### D6 - `RFC:ORB160721LW3`

- **Classification:** benign lookalike
- **Signal that fires:** `directed_bank_transfer_cycle`
- **Why innocent:** The three legs are 5 and 10 months apart, differ by a factor of sixteen in amount, and each carries its own business purpose. Same topology as a round trip, none of the same money.
- **Cycle accounts:** `072580900000001013`, `044580000000001747`, `044580000000001750`
- **Invoice UUIDs:** (none)
- **Transaction ids:** BNK-000083, BNK-000337, BNK-000615
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** compare leg amounts and date gaps
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `decline_hypothesis` (closed by challenger)

### D7 - `RFC:SAL1801300VW`

- **Classification:** benign lookalike
- **Signal that fires:** `recurring_amount_just_below_approval_threshold`
- **Why innocent:** Twelve identical 89,500.00 MXN invoices one month apart, matching a contract that fixes exactly that monthly retainer for twelve months. Regular, not clustered.
- **Invoice UUIDs:** 1501d10c-efd7-50bd-8fd5-6ad30f024f87, 41e75f3f-c101-54ff-9a07-29c2f9f30ecb, 93be1fde-6a1f-5c2f-85e3-d5eb18fb6728, 83a0a91a-d8f4-5174-85ee-4ee9ae7690ef, 658a30bf-47d8-53f1-b66c-349046e54504, 5806584e-6e21-548c-ad87-002007c0bb79, 58f6c983-8641-5c00-9575-0c56b7eb5351, e52f7a18-9dee-53ed-a0ac-2a3495178ad5 ... (+4 more)
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** get_vendor_contracts
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `decline_hypothesis` (closed by investigator)

### D8 - `RFC:GRI130612H28`

- **Classification:** benign lookalike
- **Signal that fires:** `period_end_invoice_concentration`
- **Why innocent:** The November-December spike repeats identically in 2024 and 2025 and matches a packaging contract that states seasonal peaks. The base rate makes this normal.
- **Invoice UUIDs:** 347c1fd3-184e-5e6c-aa72-4e1fc7c4fca8, 48505675-5660-5c3c-ac02-b6290f295d3c, 6951f180-93d6-55c4-948a-d258bc1c2969, 4cbc85df-4598-587c-a441-4bcfb4f298bd, ad4f2716-2cc8-5a71-8bb9-c0f0630d639e, 28ee5399-4c07-55e6-ba0a-84f1a5b60e15, 88f93bc9-5f4a-5d44-a5b8-473190eadbfe, 363237c5-33cf-5700-a117-b1bb7f84a271 ... (+34 more)
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** get_vendor_invoices across both years (base rate)
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `decline_hypothesis` (closed by investigator)

### D9 - `RFC:CIT2002265JC`

- **Classification:** benign lookalike
- **Signal that fires:** `duplicate_payment_candidate`
- **Why innocent:** Same vendor, same date, same 78,300.00 MXN amount, but two distinct purchase orders, two distinct sites and two distinct cost centres. Two services, not one paid twice.
- **Invoice UUIDs:** ca6b53bc-bc18-502a-8182-904a2ca583fd, bd634722-5495-5e72-a842-446a00fdcf86
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** get_vendor_purchase_orders
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `decline_hypothesis` (closed by investigator)

### D10 - `RFC:HAL140819YW8`

- **Classification:** benign lookalike
- **Signal that fires:** `invoice_without_settlement`
- **Why innocent:** The 2,528,800.00 MXN invoice carries status 'cancelado' and was reissued two weeks later; the reissued invoice was paid once. Counting the cancelled one doubles the exposure.
- **Invoice UUIDs:** 81a04d49-8b21-5382-a025-8a0c7f5a0ad3, f69cc17e-7ce7-5a37-96b9-c70e53419164
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** read invoices.status before counting exposure
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `decline_hypothesis` (closed by validator)

### D11 - `RFC:IND1104084H9`

- **Classification:** benign lookalike
- **Signal that fires:** `outlier_transaction_amount`
- **Why innocent:** 4,872,000.00 MXN single payment backed by a contract signed four months earlier, a director-approved purchase order and a capitalised ledger entry. Large is not odd.
- **Invoice UUIDs:** 425f23b8-27f6-5d19-b354-0f398fb53c65
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** get_vendor_contracts + get_vendor_purchase_orders
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `no meaningful lead` (closed by investigator)

### D12 - `RFC:LAN1401257I6`

- **Classification:** benign lookalike
- **Signal that fires:** `shared_vendor_address`
- **Why innocent:** Five vendors share the same address because it is a leased office tower. Three of the five, including this one, have contracts, purchase orders and a multi-year payment history. Address is not control.
- **Co-tenants at the same address:** `RFC:LAN1401257I6`, `RFC:ERM171114YA1`, `RFC:DOV1208236B6`
- **Invoice UUIDs:** (none)
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** get_vendor for each co-tenant
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `no meaningful lead` (closed by challenger)

### D13 - `RFC:PAL160915XMG`

- **Classification:** benign lookalike
- **Signal that fires:** `no_contract_on_file`
- **Why innocent:** Nine invoices, each with its own approved purchase order and payment, but no row in contracts. The contracts table is incomplete for spot purchases. Absence of a record in the estate is not absence of a contract.
- **Invoice UUIDs:** 619a0723-0a80-59de-866f-b35b1e4ad7cd, b4aa616f-88b5-549a-bbd4-d6a0f91d86ab, 837164c1-d55c-5c2e-ba33-ee73f9b28a8d, d445c2d8-2222-597f-83ae-851b0e3245c6, ba4e8b07-57d1-50aa-a816-1c48062f1a1b, c1862bde-9c2f-5f82-bc8f-f5071b537b30, 4124de84-218e-50f5-82e7-c7be5778fe4c, e4d042de-d7ea-577f-a0af-75a5b8db6226 ... (+1 more)
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** get_vendor_contracts - returns nothing; say so precisely
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `inconclusive` (closed by challenger)

### D14 - `RFC:XEN19120372S`

- **Classification:** benign lookalike
- **Signal that fires:** `unusual_cfdi_coding`
- **Why innocent:** forma_pago 99 with metodo_pago PPD is the coding the SAT filling guide requires for deferred payment, and the contract sets 60-day terms. The partial settlements are the instalments, not short payments.
- **Invoice UUIDs:** ecbb31b8-0cae-5f76-a43f-b2cd89e1a167, 46dfe1cb-5380-5365-9844-d9affc3c3952, 07b89752-35bf-542c-9447-4a287a4af173, bcd48ff5-5683-5eb1-9847-35df98694b20, be68f3b3-3b1f-53f1-bb95-895ebf4ca197, f09eaf2d-ea2f-51e4-afe2-bb0508754275, f4944219-1dae-56fa-9797-f67acf998514
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** read metodo_pago before judging forma_pago
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `no meaningful lead` (closed by investigator)

### D15 - `RFC:MEL2106282AL`

- **Classification:** benign lookalike
- **Signal that fires:** `shared_contact_email_domain`
- **Why innocent:** Five small suppliers bill from the same mail domain because they use the same outsourced accounting practice. They have different RFCs, different addresses, different CLABEs and different categories.
- **Vendors sharing the mail domain:** `RFC:MEL2106282AL`, `RFC:URB180526CBT`, `RFC:JOR21081870K`, `RFC:RAS140215RPI`, `RFC:TOR141113UQR`
- **Invoice UUIDs:** (none)
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** get_vendor for each of the five
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `no meaningful lead` (closed by challenger)

### D16 - `RFC:GAL160126E3F`

- **Classification:** benign lookalike
- **Signal that fires:** `efos_vendor_match`
- **Why innocent:** On the EFOS list as 'presunto', published 2025-12-12. Every invoice from this vendor was issued between February and October 2024, more than a year earlier, each with a purchase order under a 2023 contract. A presumption published later is not proof about earlier trade.
- **Invoice UUIDs:** c6180152-0cf3-52d3-9d12-2fa0224dad6a, 1f3f8e9f-399c-59b9-827f-a34ddb36fa75, e659169e-ef0a-5828-954b-372c18ea7603, 783db6a4-2fa5-5271-94ad-1278ff6e1508, 68ea2830-a21c-5209-ad06-cd7bd20e0bf1
- **Fires on a production detector today:** yes
- **Expected next investigative action:** compare efos_list.publication_date with every issue_date
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `decline_hypothesis` (closed by challenger)

### D17 - `RFC:XAXX010101000`

- **Classification:** benign lookalike
- **Signal that fires:** `generic_or_malformed_rfc`
- **Why innocent:** XAXX010101000 is the SAT generic RFC for sales to the general public. It is a placeholder, not a supplier, and the amounts are petty-cash sized.
- **Invoice UUIDs:** 538e251b-98f9-537c-953d-c249c26f4184, eb74d77e-9df6-5972-aaad-d1b1d96e1bc9, dea2682a-c503-5992-afbc-3ab845f030d7, f240cff2-e5b7-5abd-b695-85ce9c0e3027, ff472d2a-8530-57c8-b626-6aa17b4e6496, 2f2d443a-7d5b-5b6c-825b-17ea6d8cb163, 12381199-3435-5af5-a036-119233cbe5d1, f4fe9ef7-741b-5964-a3d4-601e69859e2b ... (+6 more)
- **Fires on a production detector today:** no - aimed at a detector on the roadmap
- **Expected next investigative action:** recognise the SAT generic RFC
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `no meaningful lead` (closed by investigator)

### D18 - `RFC:VAL150421WB8`

- **Classification:** benign lookalike
- **Signal that fires:** `shared_vendor_clabe`
- **Why innocent:** Two different RFCs collect into one CLABE because both are companies of the same group and their contracts state that collection is centralised in a corporate treasury account. Both have their own contracts, purchase orders and deliveries.
- **Vendors sharing the mail domain:** `RFC:VAL150421WB8`, `RFC:VAL150423DXA`
- **Invoice UUIDs:** (none)
- **Fires on a production detector today:** yes
- **Expected next investigative action:** get_vendor_contracts for both RFCs and read clause seven
- **What must NOT be concluded:** that the signal alone is the finding.
- **Expected final outcome:** `decline_hypothesis` (closed by challenger)

## Scoring note

Accusing any entity in the decoy section is a false accusation and is weighted at least as heavily as a missed scheme. An empty findings list scores better than a findings list that includes D2, D4, D6, D7 or D16.

**The decoy list is not the whole innocent population.** Every entity in this estate that does not appear in the *Planted schemes* section above is honest. The decoy section names only the ones deliberately engineered as bait; the ordinary supplier base contains many more entities that a loose detector will flag. The scoring rule is therefore: an accusation against any entity absent from the schemes section is a false accusation, whether or not it is named here. At commit e10bb17 the short-window invoice-cluster detector fires on ten ordinary vendors that appear nowhere in this manifest - each of those is a lead that must be closed, not a finding.

