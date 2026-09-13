"""Casos adversariales para el pre-analisis determinista de los criticos.

Cada prueba construye la forma exacta de un error de razonamiento que los criticos
han dejado pasar, y verifica que Python lo deja por escrito ANTES de que el LLM
opine. El criterio es siempre el mismo: si un humano tendria que inferirlo a ojo
desde un volcado JSON, este modulo debe calcularlo.
"""

from __future__ import annotations

from src.agents.case_analysis import (
    analyze_absence_claims,
    analyze_cycle_continuity,
    analyze_evidence_independence,
    analyze_scores,
    analyze_shared_identifier_claims,
    build_case_briefing,
    collect_declared_alternatives,
)
from src.core.models import (
    ActionRecord,
    CaseEvidence,
    CaseState,
    EvidenceRef,
    Hypothesis,
    Observation,
)


def _case(**overrides) -> CaseState:
    base = dict(case_id="CASE-1", lead_id="LEAD-1")
    base.update(overrides)
    return CaseState(**base)


def _observation(observation_id: str, signal_type: str, **overrides) -> Observation:
    base = dict(
        observation_id=observation_id,
        detector_name="deterministic_relational",
        signal_type=signal_type,
        statement="señal detectada",
    )
    base.update(overrides)
    return Observation(**base)


# ==========================================================================
# 3 DETECTORES SOBRE UNA SOLA TRANSACCION
# ==========================================================================

def test_three_detectors_on_one_transaction_are_not_three_facts():
    """El caso mas comun de doble conteo: tres señales, un solo registro."""

    ref = EvidenceRef(source_table="bank_txns", record_id="BNK-00001")

    case = _case(
        observations=[
            _observation("OBS-1", "vendor_to_employee_bank_transfer", evidence=[ref]),
            _observation("OBS-2", "directed_bank_transfer_cycle", evidence=[ref]),
            Observation(
                observation_id="OBS-3",
                detector_name="deterministic_tabular",
                signal_type="shared_vendor_clabe",
                statement="señal detectada",
                evidence=[ref],
            ),
        ],
        evidence=[
            CaseEvidence(
                evidence_id=f"EV-{i}",
                statement="s",
                direction="neutral",
                produced_by=f"detector_{i}",
                source_refs=[ref],
            )
            for i in (1, 2, 3)
        ],
    )

    result = analyze_evidence_independence(case)

    # Tres items de evidencia, tres observaciones... y UN solo hecho.
    assert result["evidence_item_count"] == 3
    assert result["observation_count"] == 3
    assert result["distinct_underlying_records"] == 1

    shared = result["records_supporting_more_than_one_signal"]
    assert len(shared) == 1
    assert shared[0]["record_id"] == "BNK-00001"
    assert shared[0]["source_table"] == "bank_txns"
    assert shared[0]["detector_count"] >= 2
    assert shared[0]["evidence_ids"] == ["EV-1", "EV-2", "EV-3"]


def test_genuinely_independent_evidence_is_not_flagged_as_dependent():
    """Contrapeso: una hipotesis con evidencia realmente distinta debe sobrevivir.

    Si el analisis marcara esto como dependiente, bloquearia casos solidos.
    """

    case = _case(
        evidence=[
            CaseEvidence(
                evidence_id="EV-1",
                statement="factura",
                direction="for",
                produced_by="d1",
                source_refs=[EvidenceRef(source_table="invoices", record_id="INV-1")],
            ),
            CaseEvidence(
                evidence_id="EV-2",
                statement="listado",
                direction="for",
                produced_by="d2",
                source_refs=[
                    EvidenceRef(source_table="efos_list", record_id="RFC-A")
                ],
            ),
            CaseEvidence(
                evidence_id="EV-3",
                statement="transferencia",
                direction="for",
                produced_by="d3",
                source_refs=[
                    EvidenceRef(source_table="bank_txns", record_id="BNK-1")
                ],
            ),
        ]
    )

    result = analyze_evidence_independence(case)

    assert result["distinct_underlying_records"] == 3
    assert result["records_supporting_more_than_one_signal"] == []


# ==========================================================================
# AUSENCIA EN EL ESTATE
# ==========================================================================

def test_a_query_that_found_nothing_is_reported_as_estate_absence_only():
    """"No se encontro contrato" nunca puede leerse como "no existe contrato"."""

    case = _case(
        actions_taken=[
            ActionRecord(
                step=1,
                action_name="get_vendor_contracts",
                arguments={"vendor_rfc": "AAA010101AA1"},
                reason="buscar cobertura contractual",
                question_resolved="¿Existe contrato?",
                result_summary="0 contratos",
                success=True,
                produced_evidence_ids=[],
            )
        ]
    )

    result = analyze_absence_claims(case)
    empty = result["successful_queries_returning_no_record"]

    assert len(empty) == 1
    assert empty[0]["action_name"] == "get_vendor_contracts"

    conclusion = empty[0]["what_may_be_concluded"]
    assert "SUPPLIED ESTATE" in conclusion
    assert "does not establish that no such record exists" in conclusion
    # La formulacion prohibida no aparece.
    assert "does not exist" not in conclusion.replace(
        "does not establish that no such record exists in reality", ""
    )


def test_a_failed_query_is_an_execution_outcome_not_evidence():
    case = _case(
        actions_taken=[
            ActionRecord(
                step=1,
                action_name="get_vendor",
                arguments={"vendor_rfc": "X"},
                reason="r",
                result_summary="fallo",
                success=False,
                errors=["sqlite error"],
            )
        ]
    )

    result = analyze_absence_claims(case)

    assert result["successful_queries_returning_no_record"] == []
    failed = result["queries_that_failed_to_execute"]
    assert len(failed) == 1
    assert "not evidence about the subject" in failed[0]["what_may_be_concluded"]


# ==========================================================================
# CICLOS: MONTOS DISTINTOS
# ==========================================================================

def test_cycle_with_wildly_different_amounts_is_quantified_not_judged():
    """A->B->C->A con montos dispares: el analisis da los numeros, no un veredicto."""

    case = _case(
        observations=[
            _observation(
                "OBS-CYCLE",
                "directed_bank_transfer_cycle",
                facts={
                    "account_cycle": ["A", "B", "C", "A"],
                    "transaction_ids": ["BNK-1", "BNK-2", "BNK-3"],
                    "transaction_amounts": [100_000.0, 8_000.0, 95_000.0],
                    "transaction_dates": ["2025-01-10", "2025-06-02", "2025-11-20"],
                },
            )
        ]
    )

    [entry] = analyze_cycle_continuity(case)

    assert entry["leg_count"] == 3
    assert entry["largest_to_smallest_leg_ratio"] == 12.5
    assert entry["date_span_days"] == 314
    assert "does not establish that the same funds" in entry["what_this_does_not_establish"]
    # No se inventa umbral alguno.
    assert "no threshold" in entry["what_this_does_not_establish"].lower()


def test_cycle_with_near_equal_amounts_close_in_time_is_also_only_quantified():
    """El caso compatible con same-money tampoco recibe veredicto automatico."""

    case = _case(
        observations=[
            _observation(
                "OBS-TIGHT",
                "directed_bank_transfer_cycle",
                facts={
                    "account_cycle": ["A", "B", "A"],
                    "transaction_amounts": [100_000.0, 98_500.0],
                    "transaction_dates": ["2025-03-01", "2025-03-04"],
                },
            )
        ]
    )

    [entry] = analyze_cycle_continuity(case)

    assert entry["largest_to_smallest_leg_ratio"] < 1.02
    assert entry["date_span_days"] == 3
    assert "does not establish that the same funds" in entry["what_this_does_not_establish"]


def test_cycle_with_unparseable_dates_does_not_crash():
    case = _case(
        observations=[
            _observation(
                "OBS-BAD",
                "directed_bank_transfer_cycle",
                facts={
                    "transaction_amounts": [1.0, 1.0],
                    "transaction_dates": ["not-a-date", None],
                    "date_span_days": 7,
                },
            )
        ]
    )

    [entry] = analyze_cycle_continuity(case)
    assert entry["date_span_days"] == 7


# ==========================================================================
# IDENTIFICADOR COMPARTIDO SIN TRANSFERENCIA
# ==========================================================================

def test_shared_clabe_without_any_transfer_is_reported_as_such():
    """Vendor y employee comparten CLABE pero no hay transferencia observada."""

    case = _case(
        observations=[
            Observation(
                observation_id="OBS-CLABE",
                detector_name="deterministic_tabular",
                signal_type="vendor_employee_shared_clabe",
                statement="mismo CLABE",
                entities=["RFC:AAA010101AA1", "EMP:0001"],
                facts={"rfc": "AAA010101AA1", "emp_id": "0001", "clabe": "0" * 18},
            )
        ]
    )

    [entry] = analyze_shared_identifier_claims(case)

    assert entry["a_transfer_between_these_parties_is_also_observed"] is False
    assert "does not establish common ownership" in entry["what_this_does_not_establish"]
    assert "persona fisica" in entry["what_this_does_not_establish"]
    assert entry["discriminating_question"]


def test_shared_clabe_with_an_observed_transfer_says_so():
    case = _case(
        observations=[
            Observation(
                observation_id="OBS-CLABE",
                detector_name="deterministic_tabular",
                signal_type="vendor_employee_shared_clabe",
                statement="mismo CLABE",
                facts={"rfc": "AAA010101AA1", "emp_id": "0001"},
            ),
            Observation(
                observation_id="OBS-XFER",
                detector_name="deterministic_relational",
                signal_type="vendor_to_employee_bank_transfer",
                statement="transferencia",
                facts={"rfc": "AAA010101AA1", "emp_id": "0001"},
            ),
        ]
    )

    [entry] = analyze_shared_identifier_claims(case)
    assert entry["a_transfer_between_these_parties_is_also_observed"] is True


# ==========================================================================
# SCORES
# ==========================================================================

def test_a_score_without_semantics_is_flagged_as_uninterpretable():
    case = _case(
        observations=[
            _observation("OBS-A", "gnn_relational_anomaly", score=0.87),
            _observation(
                "OBS-B",
                "gnn_relational_anomaly",
                score=0.42,
                score_semantics="relative to peers of the same node type in this estate",
            ),
        ]
    )

    results = analyze_scores(case)
    by_id = {r["observation_id"]: r for r in results}

    assert by_id["OBS-A"]["semantics_declared"] is False
    assert by_id["OBS-B"]["semantics_declared"] is True
    for entry in results:
        assert "not a calibrated probability of fraud" in entry["what_this_does_not_establish"]


def test_observations_without_a_score_are_not_listed():
    case = _case(observations=[_observation("OBS-A", "vendor_efos_record_match")])
    assert analyze_scores(case) == []


# ==========================================================================
# ALTERNATIVAS DECLARADAS POR LOS DETECTORES
# ==========================================================================

def test_detector_declared_alternatives_are_surfaced_for_the_challenger():
    """Facturas repetidas explicadas por un contrato mensual.

    El detector ya escribio la alternativa legitima; el Challenger debe recibirla
    al frente y no enterrada en el volcado del CaseState.
    """

    case = _case(
        observations=[
            _observation(
                "OBS-CLUSTER",
                "short_window_similar_invoice_cluster",
                statement="varias facturas similares en ventana corta",
                legitimate_alternatives=[
                    "Un contrato marco con cuota mensual fija produce el mismo patron."
                ],
                limitations=["El patron no establece intencion de fraccionar."],
                recommended_checks=["Revisar contratos vigentes del proveedor."],
            )
        ]
    )

    [entry] = collect_declared_alternatives(case)

    assert entry["observation_id"] == "OBS-CLUSTER"
    assert "contrato marco" in entry[
        "legitimate_alternatives_declared_by_the_detector"
    ][0]
    assert entry["limitations_declared_by_the_detector"]
    assert entry["checks_the_detector_recommended"]


def test_observations_declaring_nothing_are_omitted():
    case = _case(observations=[_observation("OBS-BARE", "vendor_efos_record_match")])
    assert collect_declared_alternatives(case) == []


# ==========================================================================
# DETERMINISMO Y FORMA DEL BRIEFING
# ==========================================================================

def test_briefing_carries_every_section_and_no_verdict():
    case = _case(
        hypotheses=[Hypothesis(hypothesis_id="H-1", statement="h")],
        observations=[_observation("OBS-1", "vendor_efos_record_match")],
    )

    briefing = build_case_briefing(case)

    assert set(briefing) == {
        "_what_this_is",
        "evidence_independence",
        "absence_claims",
        "cycle_continuity",
        "shared_identifier_claims",
        "scores",
    }
    # El pre-analisis nunca emite veredicto ni habla de probabilidad de fraude.
    rendered = repr(briefing).lower()
    for forbidden in ("fraud probability", "authorize", "guilty", "proven"):
        assert forbidden not in rendered


def test_briefing_is_deterministic_across_calls():
    ref_a = EvidenceRef(source_table="invoices", record_id="INV-2")
    ref_b = EvidenceRef(source_table="invoices", record_id="INV-1")

    case = _case(
        observations=[
            _observation("OBS-B", "directed_bank_transfer_cycle", evidence=[ref_a]),
            _observation("OBS-A", "directed_bank_transfer_cycle", evidence=[ref_b]),
        ],
        evidence=[
            CaseEvidence(
                evidence_id="EV-2",
                statement="s",
                direction="neutral",
                produced_by="d",
                source_refs=[ref_a, ref_b],
            ),
            CaseEvidence(
                evidence_id="EV-1",
                statement="s",
                direction="neutral",
                produced_by="d",
                source_refs=[ref_a],
            ),
        ],
    )

    assert repr(build_case_briefing(case)) == repr(build_case_briefing(case))

    shared = analyze_evidence_independence(case)[
        "records_supporting_more_than_one_signal"
    ]
    # Orden explicito por (tabla, record_id).
    assert [s["record_id"] for s in shared] == sorted(s["record_id"] for s in shared)


def test_empty_case_produces_a_usable_briefing():
    briefing = build_case_briefing(_case())

    assert briefing["evidence_independence"]["distinct_underlying_records"] == 0
    assert briefing["cycle_continuity"] == []
    assert briefing["scores"] == []
