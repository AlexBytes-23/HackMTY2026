# Source provenance — robust realism evaluation estate

What public data was consulted, exactly what it was used for, and what it was **not**
used for. Access date for everything below: **2026-09-13**.

Nothing in this directory may be imported from `src/`. The reference files under
`dev_eval/external_reference/` are generation aids for the estate builder and
evaluation harness only. The production auditor reads the estate database and
nothing else.

---

## The one rule that governed every choice

Public Mexican data was used to learn **formats, vocabularies, code sets and
distributions**. It was never used to attach an invented transaction to a real,
identifiable taxpayer, company or person.

Concretely:

- No RFC was copied from the SAT Artículo 69-B listing. Every RFC in
  `efos_list` is procedurally generated.
- No establishment record was copied from DENUE. Vendor names are built by
  composing invented syllables (`_SYL_A × _SYL_B` in the generator), which is why
  they read as *Berlanda*, *Quilanda*, *Ondarreta* rather than as anything a
  registry would return.
- No CompraNet contract, supplier or amount was copied. Only procurement
  phrasing was reused.
- Bank **institution codes** are real and verbatim, because a code is a public
  routing identifier, not an accusation. The account numbers attached to them
  are synthetic and the check digits are computed.

---

## A. SAT — Servicio de Administración Tributaria

### A.1 Artículo 69-B / EFOS listing

| | |
|---|---|
| **Used for** | The `status` vocabulary and the publication cadence of `efos_list`. |
| **How obtained** | Web search over public reporting of the SAT 69-B publications, including the DOF global definitive listings of 2025-10-03, 2026-03-13 and 2026-07-03 (oficio 500-05-00-00-00-2026-16059). |
| **Copied directly** | Nothing. |
| **Used as reference** | Status vocabulary (`presunto` → `definitivo`, with `desvirtuado` and `sentencia favorable` as further states); the fact that listings are published in DOF batches on a handful of dates per year; the fact that the fiscal effect attaches from the definitive publication. |
| **Transformed into synthetic values** | 58 `efos_list` rows with generated RFCs, generated legal names and publication dates drawn from twelve DOF-shaped batch dates spanning 2023–2025. |
| **Limitations** | The official estate schema constrains `efos_list.status` to `definitivo \| presunto`, so the two further real statuses are documented in `external_reference/sat_efos_structure_reference.json` but do not appear in the estate. The SAT listing CSV itself was not downloaded. |

`XAXX010101000`, the well-known generic RFC for operations with the general
public, appears once as a petty-cash counterparty (decoy D17). It is a
placeholder, not a taxpayer.

### A.2 CFDI 4.0 catalogues (Anexo 20)

| | |
|---|---|
| **Used for** | `invoices.uso_cfdi`, `invoices.forma_pago`, `invoices.metodo_pago`, and the VAT rates. |
| **Sources attempted** | `https://www.sat.gob.mx/aplicacion/75169/servicio-de-facturacion-cfdi-version-4.0-...` and the SAT-hosted Anexo 20 guide at `https://www.sat.gob.mx/cs/Satellite?...blobwhere=1461173536663`. |
| **Retrieval limitation — stated plainly** | `www.sat.gob.mx/consultas/13615/...` returned **HTTP 403** to automated fetch and `omawww.sat.gob.mx` **refused the connection** on 2026-09-13. The SAT-hosted Anexo 20 PDF downloaded (2.4 MB) but emits its text one glyph per show-operator, and extraction did not complete inside the time budget. |
| **What was actually confirmed** | `G01` *Adquisición de mercancías*, `G03` *Gastos en general*, `I01` *Construcciones* and `P01` *Por definir*, from SAT-hosted Anexo 20 documents surfaced by search. |
| **What was reproduced from the documented catalogue, not re-verified this run** | The remaining `c_UsoCFDI` entries used (`G02`, `I02`, `I03`, `I04`, `I06`, `I08`, `S01`, `CP01`), the `c_FormaPago` entries used (`01`, `02`, `03`, `04`, `28`, `99`) and `c_MetodoPago` (`PUE`, `PPD`). |
| **Status** | **Format realism only.** This is not asserted to be a current or complete snapshot of `catCFDI`. |

Consistency rules the generator enforces, taken from the Anexo 20 filling rules:

- `metodo_pago = PPD` ⇒ `forma_pago = 99`. This is **valid**, and 353 invoices in
  the estate use it deliberately, so that a detector treating `forma_pago 99` as
  suspicious produces a false positive (decoy D14).
- `metodo_pago = PUE` ⇒ a concrete `forma_pago`.

### A.3 VAT rates

Three rates coexist in the estate, so `total == subtotal * 1.16` is **not** a safe
universal check:

| Rate | Invoices | Why |
|---|---:|---|
| 16 % | 1,347 | General rate. |
| 8 % | 118 | Northern-border-region fiscal stimulus, applied to vendors domiciled in Tijuana, Mexicali, Ciudad Juárez, Reynosa and Nuevo Laredo. |
| 0 % | 32 | Zero-rated supplies (canteen foodstuffs). |

---

## B. Banking — bank institution codes and CLABE structure

| | |
|---|---|
| **Source** | Gobierno de México, *Catálogo de bancos* — `https://www.gob.mx/cms/uploads/attachment/file/151413/catalogo_bancos.pdf` |
| **How obtained** | PDF downloaded and its content streams inflated with `zlib`; text-showing operators concatenated. **91 institution codes** were extracted automatically; the raw extraction is preserved verbatim in `external_reference/bank_codes_reference.json` under `codes_extracted_automatically`. |
| **Copied directly** | The three-digit institution codes. |
| **Normalised by hand** | The short names. The PDF emits one glyph per operator, so the automatic join truncates trailing characters (`BANAME`, `SANTANDE`). A curated 20-code commercial-bank subset with corrected names is used by the generator; the generator asserts at build time that every curated code is present in the extracted document. |
| **Not verified this run** | The **plaza codes** (digits 4–6). The five values used — 180 Ciudad de México, 580 Monterrey, 320 Guadalajara, 190 Chihuahua, 640 Querétaro — are the widely published ones but were not confirmed against a primary Banxico source. Treated as format realism. |

Every CLABE in the estate is 18 digits with a **computed valid control digit**
(weights 3,7,1 cycled over the first 17 digits, each product taken mod 10, control
= `(10 − sum mod 10) mod 10`). All 127 vendor CLABEs, all 46 employee CLABEs and
both endpoints of all 1,232 bank transactions pass a CLABE validator.

---

## C. INEGI — DENUE and SCIAN

| | |
|---|---|
| **Sources** | DENUE `https://www.inegi.org.mx/app/mapa/denue/`; methodology `https://www.inegi.org.mx/contenidos/temas/directorio/doc/metodologia.pdf`; SCIAN 2023 `https://www.inegi.org.mx/scian/`. |
| **Aggregate fact used** | DENUE 11/2024: 6,058,548 establishments — **44 % Comercio, 42 % Servicios, 11 % Industrias** (INEGI press release `denue2024_11.pdf`). |
| **Copied directly** | Nothing. No establishment record was retrieved. |
| **Used as reference** | SCIAN sector names and codes for the twenty vendor categories; real municipality and state names for addresses; the commerce/services/industry ordering. |
| **Limitation — stated plainly** | The DENUE mix describes the whole Mexican economy, not the supplier base of one manufacturer. The generator therefore tilts toward services and industrial supply, which is what a manufacturer actually buys. The DENUE figure anchors the ordering of the mix, not its exact proportions. |

Addresses combine **real street, municipality and state names** with synthetic
street numbers. Geography is not an identifiable business.

---

## D. CompraNet — public procurement

| | |
|---|---|
| **Source** | `https://compranet.hacienda.gob.mx/` |
| **Copied directly** | Nothing. The bulk open-data download was **not retrieved** in this run. |
| **Used as reference** | Contract-shape vocabulary (`contrato marco`, `contrato abierto`, `por proyecto`, `servicio recurrente`, `adquisición`), milestone phrasing (`Entregable 1 — Diagnóstico`, `Anticipo 30%`, `Estimación de avance de obra`) and twenty procurement description strings. |
| **Limitation** | The vocabulary reflects standard Mexican procurement phrasing rather than a verified extract. No CompraNet participant appears in the estate. |

---

## E. What is *not* grounded in an external source

Stated so a judge is never misled about what is real:

- **Company names, RFCs, CLABEs, employee names, invoice UUIDs, amounts and
  dates** — all procedurally generated from seed `20260913`.
- **The internal approval ladder** (Coordinador < 100,000 MXN < Gerente <
  500,000 MXN < Director) is a *company policy modelled in the data*, not a
  statute. It is written nowhere in the estate: it exists only as the empirical
  relationship between `purchase_orders.amount` and `purchase_orders.approver`,
  and roughly one order in twenty is signed one level high so that the ladder is
  a tendency rather than a rule. **No legal procurement threshold is invented
  anywhere**, because the official challenge materials supply none.
- **Employee names** are drawn from pools of common Mexican given names and
  surnames. They carry no address, no identifier and no other attribute, and the
  identifier used in every finding is `emp_id` (`EMP:0003`), not the name. A
  common name will inevitably coincide with real people; nothing in the estate
  attaches to any of them.
- **Synthetic RFC collision risk.** RFCs are generated in the real format (3 or 4
  letters + `YYMMDD` + 3-character homoclave) from invented name stems. Format
  validity means a generated RFC could in principle coincide with a real one.
  Nothing external is attached to any of them, no registry record was consulted
  to build them, and any coincidence is exactly that. This is a residual
  limitation, disclosed rather than hidden.
- **Email domains** are built from the invented company slug (`contacto@quilanda.mx`).
  They are not registered, resolved, or contacted.

---

## F. Reproducibility of this document

If the network is unavailable, the generator still runs: it reads only the JSON
files in `dev_eval/external_reference/`, which are checked in. Re-running the
generator without network access produces a byte-identical estate. What cannot be
done offline is **refreshing** these references — and that refresh has not been
attempted since 2026-09-13. Anyone regenerating with newer SAT catalogues should
update the reference JSONs and this file together.
