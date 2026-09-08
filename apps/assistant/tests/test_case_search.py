import json
from pathlib import Path

import pytest
from app.sub_agent.case_search import (
    _select_ranked_cases,
    _temporal_alignment,
    find_similar_cases,
)

DEFAULT_STORE = Path(__file__).resolve().parents[1] / "output/cases/incidents.jsonl"
VERIFICATION = {
    "incident_at": "2026-08-31T14:00:00+09:00",
    "verified_at": "2026-09-01T09:00:00+09:00",
    "verified_by": "fab-incident-review-board",
    "evidence_refs": ["INCIDENT-001", "EVENT-LOG-001"],
}


def _write_cases(path: Path) -> None:
    records = [
        {
            "case_id": "INC-001",
            "case_type": "verified",
            "summary": "Dry_Etch 설비 down 이후 WIP와 Queue Time 증가",
            "cause": "PM 지연 뒤 장비 고장으로 병목 발생",
            "actions": ["대체 설비 qualification 확인", "영향 lot 추적"],
            "outcome": "장비 복구 후 queue 정상화",
            "source": "incident-system",
            "verification": VERIFICATION,
            "metadata": {"fab_id": "fab10", "toolgroup": "Dry_Etch"},
        },
        {
            "case_id": "SIM-002",
            "case_type": "simulated_reference",
            "summary": "Photo 공정 material shortage",
            "cause": "photoresist 재고 부족",
            "actions": ["투입 우선순위 검토"],
            "outcome": "입고 후 정상화",
            "source": "simulation-v1",
            "metadata": {"fab_id": "fab12", "toolgroup": "Photo"},
        },
    ]
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )


def test_case_search_ranks_incident_store_and_preserves_provenance(tmp_path: Path) -> None:
    store = tmp_path / "cases.jsonl"
    _write_cases(store)

    evidence = find_similar_cases("Dry_Etch Queue Time 증가와 down 유사 사례", store_path=store)

    assert evidence[0].source_type == "similar_case"
    assert evidence[0].metadata["case_id"] == "INC-001"
    assert evidence[0].metadata["case_type"] == "verified"
    assert evidence[0].metadata["verification"] == VERIFICATION
    assert evidence[0].metadata["score"] > 0


def test_case_search_does_not_treat_missing_store_as_zero_result(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError, match="Incident case store is not available"):
        find_similar_cases("Queue Time", store_path=tmp_path / "missing.jsonl")


def test_case_search_rejects_records_without_provenance(tmp_path: Path) -> None:
    store = tmp_path / "cases.jsonl"
    store.write_text('{"case_id":"INC-001"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="missing fields"):
        find_similar_cases("Queue Time", store_path=store)


def test_case_search_rejects_self_declared_verified_case_without_verification(
    tmp_path: Path,
) -> None:
    store = tmp_path / "cases.jsonl"
    record = {
        "case_id": "INC-UNVERIFIED",
        "case_type": "verified",
        "summary": "Dry_Etch down",
        "cause": "unknown",
        "actions": ["review"],
        "outcome": "pending",
        "source": "incident-system",
    }
    store.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(TypeError, match="requires verification metadata"):
        find_similar_cases("Dry_Etch down", store_path=store)


@pytest.mark.parametrize(
    "verification",
    [
        {**VERIFICATION, "verified_at": "not-a-date"},
        {**VERIFICATION, "verified_at": "2026-09-01"},
        {**VERIFICATION, "verified_by": ""},
        {**VERIFICATION, "evidence_refs": []},
        {**VERIFICATION, "incident_at": "2026-08-31"},
        {
            **VERIFICATION,
            "incident_at": "2026-09-02T09:00:00+09:00",
            "verified_at": "2026-09-01T09:00:00+09:00",
        },
    ],
)
def test_case_search_rejects_invalid_verification_contract(
    tmp_path: Path,
    verification: dict[str, object],
) -> None:
    store = tmp_path / "cases.jsonl"
    record = {
        "case_id": "INC-INVALID",
        "case_type": "verified",
        "summary": "Dry_Etch down",
        "cause": "unknown",
        "actions": ["review"],
        "outcome": "pending",
        "source": "incident-system",
        "verification": verification,
    }
    store.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ValueError, match="Verified incident"):
        find_similar_cases("Dry_Etch down", store_path=store)


def test_case_search_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    store = tmp_path / "cases.jsonl"
    record = {
        "case_id": "SIM-DUPLICATE",
        "case_type": "simulated_reference",
        "summary": "Dry_Etch down",
        "cause": "unknown",
        "actions": ["review"],
        "outcome": "pending",
        "source": "simulation-v1",
    }
    store.write_text(
        "\n".join([json.dumps(record), json.dumps({**record, "cause": "different"})]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate case_id: SIM-DUPLICATE"):
        find_similar_cases("Dry_Etch down", store_path=store)


def test_case_search_rejects_synthetic_source_with_verified_label(tmp_path: Path) -> None:
    store = tmp_path / "cases.jsonl"
    record = {
        "case_id": "SYN-MISLABELED",
        "case_type": "verified",
        "summary": "Dry_Etch down",
        "cause": "unknown",
        "actions": ["review"],
        "outcome": "pending",
        "source": "synthetic-evaluation-corpus-v1",
        "verification": VERIFICATION,
    }
    store.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ValueError, match="must not be synthetic or simulated"):
        find_similar_cases("Dry_Etch down", store_path=store)


def test_case_search_reserves_relevant_verified_case_among_simulated_ties(
    tmp_path: Path,
) -> None:
    store = tmp_path / "cases.jsonl"
    common = {
        "summary": "Dry_Etch Queue Time과 WIP 증가 및 station down",
        "cause": "station down",
        "actions": ["check station"],
        "outcome": "queue recovered",
        "source": "incident-system",
        "metadata": {"issue_type": "queue_time", "process_group": "Dry_Etch"},
    }
    records = [
        {
            **common,
            "case_id": f"SIM-TIE-{index}",
            "case_type": "simulated_reference",
        }
        for index in range(4)
    ]
    records.append(
        {
            **common,
            "case_id": "INC-TIE-VERIFIED",
            "case_type": "verified",
            "verification": VERIFICATION,
        }
    )
    store.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    evidence = find_similar_cases(
        "Dry_Etch Queue Time WIP station down 유사 사례",
        top_k=3,
        store_path=store,
    )

    assert len(evidence) == 3
    assert "INC-TIE-VERIFIED" in {item.metadata["case_id"] for item in evidence}


def test_case_search_does_not_reserve_low_relevance_verified_case() -> None:
    ranked = [
        (1.0, {"case_id": "SIM-1", "case_type": "simulated_reference"}),
        (0.9, {"case_id": "SIM-2", "case_type": "simulated_reference"}),
        (0.6, {"case_id": "INC-LOW", "case_type": "verified"}),
    ]

    selected = _select_ranked_cases(ranked, top_k=2)

    assert [record["case_id"] for _, record in selected] == ["SIM-1", "SIM-2"]


def test_case_search_filters_explicit_fab_mismatch(tmp_path: Path) -> None:
    store = tmp_path / "cases.jsonl"
    common = {
        "case_type": "simulated_reference",
        "summary": "Dry_Etch Queue Time 증가",
        "cause": "station down",
        "actions": ["check station"],
        "outcome": "queue recovered",
        "source": "simulation-v1",
    }
    records = [
        {**common, "case_id": "SIM-FAB11", "metadata": {"fab_id": "fab11"}},
        {**common, "case_id": "SIM-FAB10", "metadata": {"fab_id": "fab10"}},
    ]
    store.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    evidence = find_similar_cases("fab10 Dry_Etch Queue Time 증가 사례", store_path=store)

    assert [item.metadata["case_id"] for item in evidence] == ["SIM-FAB10"]


def test_case_search_prefers_newer_verified_case_when_relevance_ties(
    tmp_path: Path,
) -> None:
    store = tmp_path / "cases.jsonl"
    common = {
        "case_type": "verified",
        "summary": "DE_BE station down 사례",
        "cause": "breakdown",
        "actions": ["check event log"],
        "outcome": "station recovered",
        "source": "incident-system",
        "metadata": {"fab_id": "fab10", "equipment_type": "DE_BE"},
    }
    records = [
        {
            **common,
            "case_id": "INC-OLD",
            "verification": {
                **VERIFICATION,
                "incident_at": "2024-12-31T14:00:00+09:00",
                "verified_at": "2025-01-01T09:00:00+09:00",
            },
        },
        {
            **common,
            "case_id": "INC-NEW",
            "verification": {**VERIFICATION, "verified_at": "2026-09-01T09:00:00+09:00"},
        },
    ]
    store.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    evidence = find_similar_cases(
        "fab10 DE_BE_11 down 유사 사례",
        top_k=1,
        store_path=store,
    )

    assert evidence[0].metadata["case_id"] == "INC-NEW"


def test_case_search_prefers_time_aligned_verified_incident(tmp_path: Path) -> None:
    store = tmp_path / "cases.jsonl"
    common = {
        "case_type": "verified",
        "summary": "DE_BE station down 사례",
        "cause": "breakdown",
        "actions": ["check event log"],
        "outcome": "station recovered",
        "source": "incident-system",
        "metadata": {"fab_id": "fab10", "equipment_type": "DE_BE"},
    }
    records = [
        {
            **common,
            "case_id": "INC-HISTORICAL",
            "verification": {
                **VERIFICATION,
                "incident_at": "2025-01-10T14:00:00+09:00",
                "verified_at": "2025-01-11T09:00:00+09:00",
            },
        },
        {
            **common,
            "case_id": "INC-ALIGNED",
            "verification": {
                **VERIFICATION,
                "incident_at": "2026-08-31T14:00:00+09:00",
            },
        },
    ]
    store.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    evidence = find_similar_cases(
        "fab10 DE_BE_11 down 2026-08-30부터 2026-09-02까지 유사 사례",
        top_k=2,
        store_path=store,
    )

    assert [item.metadata["case_id"] for item in evidence] == [
        "INC-ALIGNED",
        "INC-HISTORICAL",
    ]
    assert evidence[0].metadata["temporal_alignment"] == "aligned"
    assert (
        evidence[1].metadata["temporal_alignment"]
        == "historical_outside_requested_period"
    )


def test_case_search_does_not_treat_gap_between_two_ranges_as_aligned() -> None:
    record = {
        "verification": {
            **VERIFICATION,
            "incident_at": "2026-02-01T14:00:00+09:00",
        }
    }

    alignment = _temporal_alignment(
        "2026-01-01~2026-01-07과 2026-03-01~2026-03-07 비교",
        record,
    )

    assert alignment == "historical_outside_requested_period"


@pytest.mark.parametrize(
    ("query", "expected_case_id"),
    [
        ("왜 fab10 Dry_Etch Queue Time이 늘었어?", "SIM-QT-001"),
        ("fab10 Dry_Etch 병목 원인 후보를 찾아줘", "SIM-BN-002"),
        ("fab10 Product_3 ontime이 떨어진 이유", "SIM-OTD-003"),
        ("fab10 DE_BE_11 down 비율이 높아진 원인", "SIM-DOWN-004"),
    ],
)
def test_default_simulated_case_corpus_ranks_scenario_cases(
    query: str,
    expected_case_id: str,
) -> None:
    evidence = find_similar_cases(query, store_path=DEFAULT_STORE)

    assert evidence[0].metadata["case_id"] == expected_case_id
    assert evidence[0].metadata["case_type"] == "simulated_reference"
    assert evidence[0].metadata["source"] == "synthetic-evaluation-corpus-v1"


@pytest.mark.parametrize(
    "query",
    [
        "fab10 PH_ST_2 down 비율 상승 유사 사례",
        "fab10 Photo Queue Time 증가 유사 사례",
    ],
)
def test_case_search_rejects_explicit_target_metadata_mismatch(query: str) -> None:
    evidence = find_similar_cases(query, store_path=DEFAULT_STORE)

    assert evidence == []
