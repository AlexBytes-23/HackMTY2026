"""
Módulo de Inferencia Bayesiana Tabular
Rama 3: Análisis Tabular / Probabilístico
Ubicación: src/detectors/bayes_tabular.py

Implementa una Red Bayesiana de inferencia probabilística sobre las observaciones
generadas por los detectores deterministas y estadísticos (tabulares.py).

Principio Epistemológico:
- Modela la incertidumbre calibrando con una tasa base real (Prior: 2%).
- Demuestra matemáticamente por qué un promedio ponderado lineal falla ante
  falsos positivos aislados, mientras que Bayes integra evidencia multiplicativa.
"""

from typing import List, Dict, Any, Tuple
import json
import sys
from pathlib import Path
import pandas as pd

# Permitir importaciones relativas o absolutas
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

try:
    from src.detectors.tabulares import (
        TabularObservation,
        run_all_tabular_detectors,
        invoices_df,
        payments_df,
        ledger_df,
        vendor_transactions_df
    )
except ImportError:
    from tabulares import (
        TabularObservation,
        run_all_tabular_detectors,
        invoices_df,
        payments_df,
        ledger_df,
        vendor_transactions_df
    )


# ==============================================================================
# 1. Parámetros y Supuestos Explícitos de la Red Bayesiana
# ==============================================================================
"""
SUPUESTOS FORENSES DE LIKELIHOOD:
P(E_i | Anomalía) = Sensibilidad / True Positive Rate (probabilidad de observar la señal si hay fraude/desvío).
P(E_i | Legítimo) = Falso Positivo / False Alarm Rate (probabilidad de que una operación legítima active la señal por ruido/error clerical).

- E1: Discrepancia 3-Way Match (LEDGER_UNDER_RECORDING / OVERPAYMENT)
  P(E1 | A) = 0.85 : 85% de esquemas de desvío/skimming causan alteración documental o desajuste de montos.
  P(E1 | ~A) = 0.03 : 3% de operaciones legítimas tienen descuadres temporales o errores humanos de captura en ERP.

- E2: Colapso de Entropía (ENTROPY_COLLAPSE_STRUCTURED_PATTERN)
  P(E2 | A) = 0.70 : 70% de esquemas de estructuración (smurfing) usan montos idénticos repetitivos.
  P(E2 | ~A) = 0.05 : 5% de proveedores legítimos facturan igualas fijas recurrentes (ej. suscripciones).

- E3: Factor Temporal Atípico (OFF_HOURS_EXECUTION / HIGH_VELOCITY_BURST)
  P(E3 | A) = 0.65 : 65% de dispersiones automatizadas no supervisadas corren de noche o en ráfagas.
  P(E3 | ~A) = 0.08 : 8% de pagos legítimos corren en tareas batch nocturnas de fin de mes o urgencias.

- E4: Outlier Multivariado (MULTIDIMENSIONAL_ISOLATION_ANOMALY)
  P(E4 | A) = 0.80 : 80% de operaciones anómalas complejas se sitúan en colas multidimensionales aisladas.
  P(E4 | ~A) = 0.10 : 10% de tasa natural de falsos positivos calibrada por la cota de contaminación (contamination=0.10).
"""

PRIOR_ANOMALY: float = 0.02  # Tasa base: 2% de operaciones en auditoría forense son anómalas
PRIOR_LEGIT: float = 1.0 - PRIOR_ANOMALY

LIKELIHOOD_MATRIX: Dict[str, Dict[str, float]] = {
    "E1_3way_match": {
        "P_E_given_Anomaly": 0.85,
        "P_E_given_Legit": 0.03,
        "description": "Discrepancia en 3-way match (LEDGER_UNDER_RECORDING / OVERPAYMENT)"
    },
    "E2_entropy_collapse": {
        "P_E_given_Anomaly": 0.70,
        "P_E_given_Legit": 0.05,
        "description": "Colapso de entropía de Shannon (Montos idénticos / Smurfing)"
    },
    "E3_temporal_factor": {
        "P_E_given_Anomaly": 0.65,
        "P_E_given_Legit": 0.08,
        "description": "Horario nocturno / fin de semana o ráfaga de alta velocidad"
    },
    "E4_multivariate_outlier": {
        "P_E_given_Anomaly": 0.80,
        "P_E_given_Legit": 0.10,
        "description": "Outlier multidimensional aislado por Isolation Forest"
    }
}

WEIGHTED_ENSEMBLE_WEIGHTS: Dict[str, float] = {
    "E1_3way_match": 0.35,
    "E2_entropy_collapse": 0.25,
    "E3_temporal_factor": 0.20,
    "E4_multivariate_outlier": 0.20
}


# ==============================================================================
# 2. Motor de Inferencia Bayesiana
# ==============================================================================
class BayesianTabularEngine:
    def __init__(
        self,
        prior: float = PRIOR_ANOMALY,
        likelihoods: Dict[str, Dict[str, float]] = LIKELIHOOD_MATRIX,
        weights: Dict[str, float] = WEIGHTED_ENSEMBLE_WEIGHTS
    ):
        self.prior_anomaly = prior
        self.prior_legit = 1.0 - prior
        self.likelihoods = likelihoods
        self.weights = weights

    def compute_posterior(self, evidence: Dict[str, bool]) -> Dict[str, Any]:
        """
        Calcula la probabilidad a posteriori P(Anomalía | E) usando la regla de Bayes
        asumiendo independencia condicional dadas las hipótesis (Naive Bayes Classifier).
        
        P(A | E) = [P(A) * prod P(E_i | A)] / [P(A) * prod P(E_i | A) + P(~A) * prod P(E_i | ~A)]
        """
        p_evidence_given_anomaly = 1.0
        p_evidence_given_legit = 1.0

        for key, present in evidence.items():
            params = self.likelihoods[key]
            p_e_a = params["P_E_given_Anomaly"]
            p_e_l = params["P_E_given_Legit"]

            if present:
                p_evidence_given_anomaly *= p_e_a
                p_evidence_given_legit *= p_e_l
            else:
                p_evidence_given_anomaly *= (1.0 - p_e_a)
                p_evidence_given_legit *= (1.0 - p_e_l)

        # Numeradores no normalizados
        joint_anomaly = self.prior_anomaly * p_evidence_given_anomaly
        joint_legit = self.prior_legit * p_evidence_given_legit

        # Normalizador de evidencia marginal P(E)
        marginal_evidence = joint_anomaly + joint_legit
        posterior_anomaly = (joint_anomaly / marginal_evidence) if marginal_evidence > 0 else self.prior_anomaly

        # Bayes Factor / Likelihood Ratio: P(E | A) / P(E | ~A)
        likelihood_ratio = (p_evidence_given_anomaly / p_evidence_given_legit) if p_evidence_given_legit > 0 else float("inf")

        # Comparativa: Ensamble Lineal de Promedio Ponderado
        weighted_score = sum(self.weights[k] * (1.0 if evidence.get(k, False) else 0.0) for k in self.weights)

        return {
            "posterior_anomaly": round(posterior_anomaly, 4),
            "posterior_legit": round(1.0 - posterior_anomaly, 4),
            "prior_anomaly": self.prior_anomaly,
            "likelihood_ratio": round(likelihood_ratio, 2),
            "weighted_score": round(weighted_score, 4),
            "evidence_evaluated": evidence
        }

    def evaluate_benchmark_scenarios(self) -> pd.DataFrame:
        """
        Genera una tabla comparativa rigurosa entre Bayes y el Ensamble Ponderado,
        contrastando escenarios de falso positivo aislado vs. coincidencia multifactorial.
        """
        scenarios = [
            {
                "Escenario": "0. Ninguna evidencia (Línea Base)",
                "E1": False, "E2": False, "E3": False, "E4": False,
                "Contexto": "Transacción habitual en días hábiles sin descuadres"
            },
            {
                "Escenario": "1. Falso Positivo: Pago Nocturno Aislado",
                "E1": False, "E2": False, "E3": True,  "E4": False,
                "Contexto": "Gasto legítimo de guardia médica o batch nocturno"
            },
            {
                "Escenario": "2. Falso Positivo: Outlier ML Aislado",
                "E1": False, "E2": False, "E3": False, "E4": True,
                "Contexto": "Compra legítima de activo fijo por monto inusual"
            },
            {
                "Escenario": "3. Señal Moderada: Entropía Colapsada Sola",
                "E1": False, "E2": True,  "E3": False, "E4": False,
                "Contexto": "Facturación recurrente mensual de suscripción SaaS"
            },
            {
                "Escenario": "4. Coincidencia Dual: Descuadre + Horario Nocturno",
                "E1": True,  "E2": False, "E3": True,  "E4": False,
                "Contexto": "Sub-registro contable ejecutado en fin de semana"
            },
            {
                "Escenario": "5. Coincidencia Triple: Descuadre + Entropía + Nocturno",
                "E1": True,  "E2": True,  "E3": True,  "E4": False,
                "Contexto": "Smurfing sistemático con sub-registro en libros"
            },
            {
                "Escenario": "6. Alerta Máxima: 4 Evidencias Simultáneas",
                "E1": True,  "E2": True,  "E3": True,  "E4": True,
                "Contexto": "Patrón coordinado: libros alterados + smurfing + ráfaga nocturna + outlier"
            }
        ]

        rows = []
        for s in scenarios:
            ev = {
                "E1_3way_match": s["E1"],
                "E2_entropy_collapse": s["E2"],
                "E3_temporal_factor": s["E3"],
                "E4_multivariate_outlier": s["E4"]
            }
            res = self.compute_posterior(ev)
            post = res["posterior_anomaly"]
            w_score = res["weighted_score"]
            lr = res["likelihood_ratio"]

            # Decisión forense
            if post >= 0.80:
                veredicto = "CRÍTICO: Lead Prioritario para Investigator"
            elif post >= 0.20:
                veredicto = "REVISIÓN: Anomalía moderada (requiere cruce)"
            else:
                veredicto = "DESCARTADO: Probable Falso Positivo / Ruido"

            rows.append({
                "Escenario": s["Escenario"],
                "Vector (E1,E2,E3,E4)": f"({int(s['E1'])},{int(s['E2'])},{int(s['E3'])},{int(s['E4'])})",
                "Ensamble Ponderado": f"{w_score:.1%}",
                "Posterior Bayesiano": f"{post:.1%}",
                "Likelihood Ratio": f"{lr:,.1f}x",
                "Veredicto Epistemológico": veredicto
            })

        return pd.DataFrame(rows)


# ==============================================================================
# 3. Mapeador de Observaciones Tabulares a Inferencia Bayesiana
# ==============================================================================
def infer_bayesian_from_observations(
    observations: List[TabularObservation]
) -> List[TabularObservation]:
    """
    Toma la lista de TabularObservation de tabulares.py, extrae los perfiles de
    evidencia por entidad/transacción, aplica el motor bayesiano y produce
    nuevas observaciones TabularObservation con rule_or_model: 'bayesian_network_inference'.
    """
    engine = BayesianTabularEngine()
    bayesian_observations: List[TabularObservation] = []

    # Agrupar evidencias identificadas por entidad
    # Asociamos los hallazgos a las entidades observadas (proveedores, facturas, transacciones)
    evidence_by_target: Dict[str, Dict[str, Any]] = {}

    for obs in observations:
        mtype = obs.facts.get("mismatch_type", "")
        prov = obs.provenance

        # Extraer el ID principal de la observación (vendor, grupo o factura)
        target_id = (
            obs.facts.get("vendor_id")
            or obs.facts.get("group_by_value")
            or obs.facts.get("invoice_id")
            or "UNKNOWN_ENTITY"
        )

        if target_id not in evidence_by_target:
            evidence_by_target[target_id] = {
                "E1_3way_match": False,
                "E2_entropy_collapse": False,
                "E3_temporal_factor": False,
                "E4_multivariate_outlier": False,
                "provenance": set(),
                "original_mismatches": []
            }

        target_record = evidence_by_target[target_id]
        target_record["provenance"].update(prov)
        target_record["original_mismatches"].append(mtype)

        # Mapear a los 4 booleanos
        if mtype in ["LEDGER_UNDER_RECORDING", "OVERPAYMENT", "UNDERPAYMENT"]:
            target_record["E1_3way_match"] = True
        if mtype in ["ENTROPY_COLLAPSE_STRUCTURED_PATTERN"]:
            target_record["E2_entropy_collapse"] = True
        if mtype in ["OFF_HOURS_EXECUTION", "HIGH_VELOCITY_BURST"]:
            target_record["E3_temporal_factor"] = True
        if mtype in ["MULTIDIMENSIONAL_ISOLATION_ANOMALY"]:
            target_record["E4_multivariate_outlier"] = True

    # Realizar inferencia bayesiana para cada entidad consolidada
    for target_id, data in evidence_by_target.items():
        evidence_vector = {
            "E1_3way_match": data["E1_3way_match"],
            "E2_entropy_collapse": data["E2_entropy_collapse"],
            "E3_temporal_factor": data["E3_temporal_factor"],
            "E4_multivariate_outlier": data["E4_multivariate_outlier"]
        }

        # Ignorar entidades sin ninguna evidencia activa
        if not any(evidence_vector.values()):
            continue

        result = engine.compute_posterior(evidence_vector)
        posterior = result["posterior_anomaly"]
        weighted = result["weighted_score"]
        lr = result["likelihood_ratio"]

        # Determinar hipótesis forenses según la posterior bayesiana
        if posterior >= 0.70:
            hypotheses = [
                f"Probabilidad a posteriori elevada ({posterior:.1%}) por co-ocurrencia de señales forenses independientes.",
                "Consistencia estadística con patrón estructurado o manipulación contable no atribuible al azar.",
                "Prioridad ALTA para apertura de caso formal en CaseState y profundización por Investigator."
            ]
        elif posterior >= 0.15:
            hypotheses = [
                f"Probabilidad a posteriori intermedia ({posterior:.1%}): la señal no es concluyente por sí sola frente a la tasa base (2%).",
                "Requiere corroboración documental (ej. revisión de complementos de pago o notas de crédito por Case Critic).",
                "No escalar a acusación sin verificar evidencia externa o relación de grafo."
            ]
        else:
            hypotheses = [
                f"Probabilidad a posteriori baja ({posterior:.1%}) a pesar de alertas lineales (ensamble={weighted:.1%}).",
                "La ausencia de desajuste contable o entropía colapsada amortigua fuertemente el riesgo (tasa base = 2%).",
                "Clasificado como probable falso positivo o evento operativo rutinario desatendido."
            ]

        facts = {
            "target_entity": target_id,
            "evidence_vector": evidence_vector,
            "prior_base_rate": result["prior_anomaly"],
            "posterior_probability_anomaly": posterior,
            "posterior_probability_legit": result["posterior_legit"],
            "likelihood_ratio_bayes_factor": lr,
            "weighted_ensemble_comparison": weighted,
            "differential_bayes_vs_weighted": round(posterior - weighted, 4),
            "original_mismatches_detected": list(set(data["original_mismatches"])),
            "mismatch_type": "BAYESIAN_POSTERIOR_EVALUATION"
        }

        obs = TabularObservation(
            rule_or_model="bayesian_network_inference",
            facts=facts,
            anomaly_score_optional=posterior,
            hypotheses_optional=hypotheses,
            provenance=sorted(list(data["provenance"]))
        )
        bayesian_observations.append(obs)

    return bayesian_observations


# ==============================================================================
# 4. Ejecución Principal y Demostración Comparativa
# ==============================================================================
if __name__ == "__main__":
    print("=" * 85)
    print("FORENSIC AUDITOR - RAMA 3: RED BAYESIANA DE INFERENCIA TABULAR")
    print("=" * 85)

    # 1. Ejecutar el pipeline previo de tabulares.py
    base_observations = run_all_tabular_detectors(
        invoices_df=invoices_df,
        payments_df=payments_df,
        ledger_df=ledger_df,
        transactions_df=vendor_transactions_df
    )
    print(f"\n[+] Observaciones base generadas por tabulares.py: {len(base_observations)}")

    # 2. Imprimir Tabla Comparativa de Benchmarking (Bayes vs Ensamble Ponderado)
    engine = BayesianTabularEngine()
    benchmark_df = engine.evaluate_benchmark_scenarios()

    print("\n" + "=" * 85)
    print("BENCHMARK COMPARATIVO: RED BAYESIANA VS. ENSAMBLE DE PROMEDIO PONDERADO")
    print("Demostración de calibración frente a la Tasa Base (Prior = 2.0%)")
    print("=" * 85)
    print(benchmark_df.to_string(index=False))

    # 3. Aplicar Inferencia Bayesiana a las Observaciones de los Mocks
    bayesian_findings = infer_bayesian_from_observations(base_observations)

    print("\n" + "=" * 85)
    print("HALLAZGOS CALIBRADOS POR INFERENCIA BAYESIANA (Formato TabularObservation)")
    print("=" * 85)

    json_output = json.dumps([obs.model_dump() for obs in bayesian_findings], indent=2, ensure_ascii=False)
    print(json_output)
