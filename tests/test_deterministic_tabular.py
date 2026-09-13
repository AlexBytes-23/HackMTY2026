import pandas as pd

from src.detectors.deterministic_tabular import (
    detect_efos_vendor_matches,
    detect_shared_vendor_clabe,
    detect_vendor_employee_shared_clabe,
    run_deterministic_tabular_detectors,
)


def test_vendor_employee_shared_clabe_is_factual():

    vendors = pd.DataFrame(
        [
            {
                "rfc": "VEN001",
                "bank_clabe": "111111111111111111",
            }
        ]
    )

    employees = pd.DataFrame(
        [
            {
                "emp_id": "EMP001",
                "bank_clabe": "111111111111111111",
            }
        ]
    )

    observations = detect_vendor_employee_shared_clabe(
        vendors,
        employees,
    )

    assert len(observations) == 1

    obs = observations[0]

    assert obs.signal_type == "vendor_employee_shared_clabe"
    assert obs.score is None

    assert {
        (ref.source_table, ref.record_id)
        for ref in obs.evidence
    } == {
        ("vendors", "VEN001"),
        ("employees", "EMP001"),
    }

    assert "kickback" in obs.limitations[0].lower()


def test_vendor_employee_shared_clabe_ignores_blank_accounts():

    vendors = pd.DataFrame(
        [
            {
                "rfc": "VEN001",
                "bank_clabe": "",
            }
        ]
    )

    employees = pd.DataFrame(
        [
            {
                "emp_id": "EMP001",
                "bank_clabe": "",
            }
        ]
    )

    observations = detect_vendor_employee_shared_clabe(
        vendors,
        employees,
    )

    assert observations == []


def test_shared_vendor_clabe_preserves_all_vendor_provenance():

    vendors = pd.DataFrame(
        [
            {
                "rfc": "VEN001",
                "bank_clabe": "222222222222222222",
            },
            {
                "rfc": "VEN002",
                "bank_clabe": "222222222222222222",
            },
            {
                "rfc": "VEN003",
                "bank_clabe": "333333333333333333",
            },
        ]
    )

    observations = detect_shared_vendor_clabe(vendors)

    assert len(observations) == 1

    obs = observations[0]

    assert obs.facts["vendor_count"] == 2
    assert obs.facts["vendor_rfcs"] == [
        "VEN001",
        "VEN002",
    ]

    assert {
        ref.record_id
        for ref in obs.evidence
    } == {
        "VEN001",
        "VEN002",
    }


def test_efos_match_reports_status_without_declaring_fraud():

    vendors = pd.DataFrame(
        [
            {
                "rfc": "VEN001",
            }
        ]
    )

    efos = pd.DataFrame(
        [
            {
                "rfc": "VEN001",
                "legal_name": "Vendor Uno",
                "status": "Presunto",
                "publication_date": "2026-01-15",
            }
        ]
    )

    observations = detect_efos_vendor_matches(
        vendors,
        efos,
    )

    assert len(observations) == 1

    obs = observations[0]

    assert obs.facts["efos_statuses"] == ["Presunto"]
    assert obs.score is None

    assert "does not establish" in obs.limitations[0].lower()


class FakeEstate:

    def __init__(self):
        self.tables = {
            "vendors": pd.DataFrame(
                [
                    {
                        "rfc": "VEN001",
                        "bank_clabe": "111111111111111111",
                    },
                    {
                        "rfc": "VEN002",
                        "bank_clabe": "111111111111111111",
                    },
                ]
            ),
            "employees": pd.DataFrame(
                [
                    {
                        "emp_id": "EMP001",
                        "bank_clabe": "111111111111111111",
                    }
                ]
            ),
            "efos_list": pd.DataFrame(
                [
                    {
                        "rfc": "VEN001",
                        "legal_name": "Vendor Uno",
                        "status": "Definitivo",
                        "publication_date": "2026-01-15",
                    }
                ]
            ),
        }

    def get_all(self, table_name):
        return self.tables[table_name].copy()

    def table_df(self, table):
        import pandas as pd
        return pd.DataFrame(self.get_all(table))


def test_runner_returns_shared_observation_contract():

    observations = run_deterministic_tabular_detectors(
        FakeEstate()
    )

    signal_types = {
        observation.signal_type
        for observation in observations
    }

    assert "vendor_employee_shared_clabe" in signal_types
    assert "shared_vendor_clabe" in signal_types
    assert "vendor_efos_record_match" in signal_types

    for observation in observations:
        assert observation.detector_name == "deterministic_tabular"
        assert observation.score is None
        assert observation.evidence

def test_shared_clabe_does_not_double_the_employee_prefix():
    """The official employees schema documents emp_id as 'EMP:0001'.

    An estate that stores the prefix must not produce 'EMP:EMP:0001': that
    entity matches nothing, so a correct finding would score as a missed
    scheme and an unattributed accusation at the same time.
    """

    vendors = pd.DataFrame(
        [{"rfc": "VEN001", "bank_clabe": "111111111111111111"}]
    )
    employees = pd.DataFrame(
        [{"emp_id": "EMP:0001", "bank_clabe": "111111111111111111"}]
    )

    obs = detect_vendor_employee_shared_clabe(vendors, employees)[0]

    assert "EMP:0001" in obs.entities
    assert not any(entity.startswith("EMP:EMP:") for entity in obs.entities)


def test_shared_clabe_still_adds_the_prefix_when_it_is_absent():
    vendors = pd.DataFrame(
        [{"rfc": "VEN001", "bank_clabe": "111111111111111111"}]
    )
    employees = pd.DataFrame(
        [{"emp_id": "0001", "bank_clabe": "111111111111111111"}]
    )

    obs = detect_vendor_employee_shared_clabe(vendors, employees)[0]

    assert "EMP:0001" in obs.entities
