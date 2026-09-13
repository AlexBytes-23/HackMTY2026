"""A lead's subject must be a party, never a bank account.

``CLABE:`` is internal (invariant 7 of the project rules): it may never reach a
submission or a case file.  A lead whose subject was a CLABE therefore lost its
subject downstream and was dropped from ``leads_not_pursued`` altogether, which
is a scored section.
"""

from __future__ import annotations

from src.core.models import EvidenceRef, Observation
from src.investigation.lead_builder import build_leads, official_subject_entities


class FakeEstate:
    """Only the two account-ownership lookups ``build_leads`` performs."""

    def __init__(self, vendors=None, employees=None):
        self._vendors = vendors or {}
        self._employees = employees or {}

    def find_vendor_by_clabe(self, clabe):
        return self._vendors.get(clabe)

    def find_employee_by_clabe(self, clabe):
        return self._employees.get(clabe)


def _cycle_observation(entities):
    return Observation(
        observation_id="OBS-REL-BANK-1",
        detector_name="deterministic_relational",
        signal_type="directed_bank_transfer_cycle",
        entities=entities,
        statement="The supplied estate contains a directed cycle of transfers.",
        evidence=[EvidenceRef(source_table="bank_txns", record_id="TX-1")],
    )


# ==========================================================================
# RESOLUTION
# ==========================================================================

def test_a_clabe_is_replaced_by_its_registered_owner():
    estate = FakeEstate(
        vendors={"111111111111111111": {"rfc": "AAA010101AAA"}},
        employees={"222222222222222222": {"emp_id": "0007"}},
    )

    entities = official_subject_entities(
        [_cycle_observation(["CLABE:111111111111111111", "CLABE:222222222222222222"])],
        estate,
    )

    assert entities == ["RFC:AAA010101AAA", "EMP:0007"]


def test_an_already_prefixed_owner_id_is_not_prefixed_twice():
    estate = FakeEstate(employees={"222222222222222222": {"emp_id": "EMP:0007"}})

    entities = official_subject_entities(
        [_cycle_observation(["CLABE:222222222222222222"])], estate
    )

    assert entities == ["EMP:0007"]


def test_an_unowned_clabe_falls_back_to_the_next_official_entity():
    """The lead survives; the account number does not become its subject."""

    observation = _cycle_observation(
        ["CLABE:999999999999999999", "RFC:BBB020202BB2"]
    )

    leads = build_leads([observation], FakeEstate())

    assert len(leads) == 1
    assert leads[0].subject_entities == ["RFC:BBB020202BB2"]


def test_an_unowned_clabe_with_no_official_entity_yields_no_subject():
    """No owner is invented, and the account number is never emitted."""

    leads = build_leads([_cycle_observation(["CLABE:999999999999999999"])], FakeEstate())

    assert len(leads) == 1
    assert leads[0].subject_entities == []


def test_build_leads_without_an_estate_still_drops_the_raw_clabe():
    leads = build_leads(
        [_cycle_observation(["CLABE:111111111111111111", "RFC:BBB020202BB2"])]
    )

    assert leads[0].subject_entities == ["RFC:BBB020202BB2"]


# ==========================================================================
# NOTHING INTERNAL LEAKS INTO THE LEAD'S TEXT
# ==========================================================================

def test_no_clabe_reaches_subject_entities_or_reason_opened():
    estate = FakeEstate(vendors={"111111111111111111": {"rfc": "AAA010101AAA"}})

    lead = build_leads(
        [_cycle_observation(["CLABE:111111111111111111"])], estate
    )[0]

    assert not any(e.startswith("CLABE:") for e in lead.subject_entities)
    assert "CLABE" not in lead.reason_opened
    assert "111111111111111111" not in lead.reason_opened
    assert "RFC:AAA010101AAA" in lead.reason_opened


def test_reason_opened_does_not_name_an_account_when_no_owner_is_known():
    lead = build_leads([_cycle_observation(["CLABE:999999999999999999"])])[0]

    assert "999999999999999999" not in lead.reason_opened
    assert lead.reason_opened.strip()


# ==========================================================================
# EXISTING BEHAVIOUR THAT MUST NOT DRIFT
# ==========================================================================

def test_official_entities_keep_their_order_and_are_not_duplicated():
    first = Observation(
        observation_id="OBS-1",
        detector_name="d",
        signal_type="s",
        entities=["RFC:AAA010101AAA", "EMP:0001"],
        statement="one",
        evidence=[EvidenceRef(source_table="vendors", record_id="AAA010101AAA")],
    )
    second = Observation(
        observation_id="OBS-2",
        detector_name="d",
        signal_type="s",
        entities=["RFC:AAA010101AAA", "RFC:CCC030303CC3"],
        statement="two",
        evidence=[EvidenceRef(source_table="vendors", record_id="AAA010101AAA")],
    )

    assert official_subject_entities([first, second]) == [
        "RFC:AAA010101AAA",
        "EMP:0001",
        "RFC:CCC030303CC3",
    ]


def test_an_empty_prefix_payload_is_not_a_subject():
    observation = _cycle_observation(["RFC:", "EMP:", "RFC:DDD040404DD4"])

    assert official_subject_entities([observation]) == ["RFC:DDD040404DD4"]
