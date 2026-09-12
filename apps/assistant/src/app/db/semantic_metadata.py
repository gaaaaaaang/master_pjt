"""Versioned, code-grounded FAB meanings; unknown business semantics stay explicit."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

VERSION = "fab-semantics-v1"
MODEL_SOURCE = "apps/assistant/src/app/sub_agent/text2sql.py:SCHEMA_CATALOG"
SIM_SOURCE = "apps/assistant/scripts/insert_fab_live_process_snapshot.py"
RAW_SIM_SOURCE = "apps/assistant/scripts/simulate_fab_raw_events.py"
REPORT_SOURCE = "apps/assistant/scripts/load_autosched_postgres_reports.py"

TABLES = {
    "toolgroups": ("공정 영역별 설비군과 설비 대수, 배치·디스패칭 정책의 모델 입력", ["설비군", "장비 그룹", "toolgroup", "설비 대수"], "모델 입력의 설비군 행"),
    "pm": ("예방정비 이벤트 및 정비 시간 분포의 모델 입력", ["예방정비", "정기점검", "maintenance", "pm"], "정비 이벤트 정의 행"),
    "breakdown": ("고장 이벤트, 고장 간격과 수리 시간 분포의 모델 입력", ["고장", "수리", "mttr", "mttf", "breakdown"], "고장 이벤트 정의 행"),
    "setups": ("설비 setup 상태 간 전환 시간의 모델 입력", ["셋업", "준비 시간", "setup"], "setup 전환 정의 행"),
    "transport": ("출발 위치와 도착 위치 사이 운반 시간 분포의 모델 입력", ["운반", "이송", "transport"], "위치 간 운반 정의 행"),
    "autosched_perf": ("AutoSched 시뮬레이션 전체 성능 보고서: 평균 WIP, 완료 LOT, cycle time", ["전체 WIP", "공장 전체", "성능 보고서"], "원본 보고서의 기간/상대기간 구분별 성능 행"),
    "autosched_stngrp": ("AutoSched 설비군별 평균 WIP, 가동률, 완료 LOT 보고서", ["설비군 WIP", "toolgroup WIP", "설비군 가동률"], "원본 보고서의 기간/상대기간/설비군 행"),
    "autosched_stn": ("AutoSched 개별 설비의 평균 WIP, 가동률, 상태 보고서", ["설비 가동률", "장비 상태", "equipment utilization"], "원본 보고서의 기간/상대기간/설비 행"),
    "autosched_stnfam": ("AutoSched 설비 family별 성능 보고서", ["설비 패밀리", "station family"], "원본 보고서의 기간/상대기간/설비 family 행"),
    "autosched_part": ("AutoSched 제품별 WIP, 투입 및 완료 LOT 보고서", ["제품 WIP", "제품별 실적", "product WIP"], "원본 보고서의 기간/상대기간/제품 행"),
    "autosched_order": ("AutoSched 주문별 WIP와 납기 성과 보고서", ["주문", "납기", "order"], "원본 보고서의 기간/상대기간/주문 행"),
    "autosched_lot": ("AutoSched LOT별 시작/완료/납기 시각과 현재 공정 보고서", ["lot 이력", "lot 완료", "lot 상태"], "원본 보고서의 LOT 행"),
    "autosched_semi": ("AutoSched 주차·제품별 LOT 및 wafer 입출고, 폐기 보고서", ["주차", "wafer", "스크랩", "반제품"], "원본 보고서의 주차/제품 구분 행"),
    "live_process_snapshots": ("관측 구간별 공정 영역의 합성 시뮬레이션 WIP·대기·생산량·수율·가동률", ["시뮬레이션", "스냅샷", "simulation", "snapshot", "재공", "대기시간", "수율", "온도", "습도"], "interval_end + fab_id + area당 한 행; toolgroup은 생성된 영역 대표값"),
    "live_process_events": ("관측 구간별 합성 설비/LOT 상세 이벤트와 대기·작업·재작업 상태", ["시뮬레이션 이벤트", "상세 이벤트", "equipment event", "lot event", "재작업", "알람"], "interval_end + fab_id + event_id당 한 행; event_kind로 설비/LOT 구분"),
    "fab_process_raw_events": ("20분 주기 상태 머신이 생성한 FAB raw 공정 이벤트 원장", ["raw event", "원장", "현장 이벤트", "장비 신호", "작업자 보고", "공정 시작", "공정 종료"], "event_id당 한 행; event_time/event_end_time이 실제 합성 발생 시각"),
    "fab_simulation_state": ("FAB별 상태 머신의 현재 health, WIP, active incident, recovery 상태", ["상태 머신", "simulation state", "회복 진행", "active incident"], "fab_id당 한 행"),
    "fab_incidents": ("FAB별 장비 고장, 수율 저하, 병목, PM 지연 같은 사건 생명주기", ["incident", "사건", "장비 고장", "병목", "수율 저하", "PM 지연"], "incident_id당 한 행"),
}

# Values are definitions, not interchangeable metrics. Percentages/averages must
# not be blindly added or represented as weighted overall measurements.
COLUMNS = {
    "wip_lots": ("구간 공정 영역의 합성 재공 LOT 수", "lot", "snapshot_count"),
    "queue_lots": ("구간 공정 영역의 합성 대기 LOT 수", "lot", "snapshot_count"),
    "lot_starts": ("구간 공정 영역의 합성 투입 LOT 수", "lot", "flow_count"),
    "lot_completions": ("구간 공정 영역의 합성 완료 LOT 수", "lot", "flow_count"),
    "avg_queue_minutes": ("공정 영역의 합성 평균 대기시간", "minute", "mean"),
    "queue_minutes": ("합성 상세 이벤트의 대기시간", "minute", "duration"),
    "avg_cycle_hours": ("공정 영역의 합성 평균 cycle time", "hour", "mean"),
    "yield_percent": ("공정 영역의 합성 수율 백분율; 가중 합산의 분모는 제공하지 않음", "percent", "ratio"),
    "utilization_percent": ("공정 영역의 합성 가동률 백분율", "percent", "ratio"),
    "temperature_c": ("공정 영역의 합성 온도", "celsius", "measurement"),
    "humidity_percent": ("공정 영역의 합성 상대습도", "percent", "measurement"),
    "bottleneck_score": ("생성기가 대기/WIP, 다운타임, 가동률로 산출한 합성 점수 (0~1)", "score", "score"),
    "number_of_tools": ("모델에 정의된 설비군 소속 설비 대수", "tool", "model_count"),
    "wiplotavg": ("보고 기간의 평균 재공 LOT; 현재 재공 수와 구분", "lot", "period_mean"),
    "wiplotcur": ("보고서의 현재 재공 LOT; 실제 실시간 공장 관측과 구분", "lot", "snapshot_count"),
    "lotcomps": ("보고서의 완료 LOT 수; 기간 범위별 의미를 유지", "lot", "flow_count"),
    "util_percent": ("보고서의 가동률 백분율", "percent", "ratio"),
    "report_time": ("보고서 생성/기준 시각; period와 relative의 집계 기간과 동일하지 않음", None, "time"),
    "interval_start": ("합성 관측 구간의 시작 시각; 구간 길이는 종료-시작으로 확인", None, "time"),
    "interval_end": ("합성 관측 구간의 종료 시각; 15분/2시간 등 구간 길이를 고정 가정하지 않음", None, "time"),
    "inserted_at": ("DB 적재 시각; 공정 발생 시각이 아님", None, "ingestion_time"),
    "event_time": ("raw 공정 이벤트가 발생한 합성 현장 시각", None, "event_time"),
    "event_end_time": ("raw 공정 이벤트가 종료된 합성 현장 시각; active 이벤트는 null 가능", None, "event_time"),
    "created_at": ("DB 또는 시뮬레이터가 행을 생성한 시각; 공정 발생 시각이 아님", None, "ingestion_time"),
    "process_minutes": ("raw 이벤트의 합성 처리 시간", "minute", "duration"),
    "hold_minutes": ("raw 이벤트의 합성 hold 시간", "minute", "duration"),
    "down_minutes": ("raw 이벤트의 합성 설비 down 시간", "minute", "duration"),
    "pm_minutes": ("raw 이벤트의 합성 PM 소요/초과 시간", "minute", "duration"),
    "yield_loss_ppm": ("raw 이벤트에 배부된 합성 수율 손실 ppm", "ppm", "flow_count"),
    "defect_count": ("raw 이벤트에 배부된 합성 defect count", "defect", "flow_count"),
    "wip_delta": ("raw 이벤트가 구간 WIP에 준 합성 증감", "lot", "flow_count"),
    "start_date": ("모델의 계획 투입 날짜", None, "planned_time"),
    "due_date": ("모델의 계획 납기 날짜", None, "planned_time"),
}

SIMULATION_COLUMNS = {
    "wip_lots", "queue_lots", "lot_starts", "lot_completions", "avg_queue_minutes",
    "queue_minutes", "avg_cycle_hours", "yield_percent", "utilization_percent",
    "temperature_c", "humidity_percent", "bottleneck_score", "interval_start",
    "interval_end", "inserted_at",
}
RAW_SIMULATION_COLUMNS = {
    "event_time", "event_end_time", "created_at", "queue_minutes", "process_minutes",
    "hold_minutes", "down_minutes", "pm_minutes", "yield_loss_ppm", "defect_count",
    "wip_delta",
}
REPORT_COLUMNS = {"wiplotavg", "wiplotcur", "lotcomps", "util_percent", "report_time"}


def definition(logical: str, columns: list[dict[str, Any]]) -> dict[str, Any]:
    """Return meanings only for columns present in this actual FAB variant."""
    names = {column["name"] for column in columns}
    info = TABLES.get(logical)
    if logical.startswith("route_product_"):
        info = ("제품별 공정 순서, 설비군, 처리시간과 재작업 정책의 모델 입력",
                ["공정 경로", "공정 순서", "route", "routing", logical.removeprefix("route_")],
                "모델의 제품 경로별 공정 step 행")
    elif logical.startswith("lotrelease"):
        info = ("LOT 투입 시점·제품·경로·납기를 정의한 시뮬레이션 계획 입력",
                ["투입 계획", "release plan", "납기 계획", "lotrelease"], "모델의 투입 계획 정의 행")
    elif logical.startswith("setup_matrix"):
        info = ("제품/공정 setup 조합별 전환 행렬의 모델 입력; 동적 컬럼 의미는 원본 확인 필요",
                ["setup matrix", "전환 행렬"], "원본 setup matrix 행; 동적 컬럼 의미 미검토")
    known = info is not None
    info = info or (logical.replace("_", " "), [], "unknown")
    raw_simulation = logical in {"fab_process_raw_events", "fab_simulation_state", "fab_incidents"}
    simulation = logical.startswith("live_process_") or raw_simulation
    report = logical.startswith("autosched_")
    source = RAW_SIM_SOURCE if raw_simulation else SIM_SOURCE if simulation else REPORT_SOURCE if report else MODEL_SOURCE
    metrics = []
    descriptions = {}
    applicable = set()
    if known:
        if simulation:
            applicable = RAW_SIMULATION_COLUMNS if raw_simulation else SIMULATION_COLUMNS
        elif report:
            applicable = REPORT_COLUMNS
        elif logical == "toolgroups":
            applicable = {"number_of_tools"}
        elif logical.startswith("lotrelease"):
            applicable = {"start_date", "due_date"}
    for name in sorted(names & applicable):
        description, unit, kind = COLUMNS[name]
        descriptions[name] = {"description": description, "unit": unit, "semantic_role": kind}
        if unit:
            rule = "명시한 단위/그룹별 값. 평균·비율은 합산하지 않고 전체 가중값에 필요한 분모를 확인한다."
            if kind == "snapshot_count":
                rule = "같은 시점의 겹치지 않는 영역은 SUM 가능. 서로 다른 시점은 SUM하지 않으며 시간 평균과 구분한다."
            elif kind == "flow_count":
                rule = "겹치지 않는 기간/그룹만 SUM한다. 누적값과 부분 기간을 중복 합산하지 않는다."
            metrics.append({"id": name, "column": name, "definition": description,
                            "unit": unit, "kind": kind, "aggregation_rule": rule})
    notes = ["업무 담당자가 검토한 SSOT가 아니다. 코드/적재 구조에서 확인한 의미와 미확인 의미를 구분한다."]
    relationships = []
    if logical in {"pm", "breakdown"} or logical.startswith("route_product_"):
        left_key = "type_name" if logical in {"pm", "breakdown"} else "toolgroup"
        if left_key in names:
            relationships.append({"target_logical_table": "toolgroups",
                                  "keys": [{"source": left_key, "target": "area" if logical == "breakdown" else "toolgroup"}],
                                  "cardinality": "many_to_many" if logical == "breakdown" else "many_to_one_if_target_unique",
                                  "source": MODEL_SOURCE, "review_status": "code_grounded",
                                  "aggregation_rule": (
                                      "breakdown.down_event_valid_for_type=area의 type_name은 영역이다. area 조인은 설정 행을 설비군 수만큼 복제한다. 설정 건수는 COUNT(DISTINCT breakdown.source_row_id)를 쓰고, 설정을 단순 영역별 집계할 때는 type_name 자체로 그룹화한다."
                                      if logical == "breakdown" else "toolgroup 키 유일성을 확인한다. 조인 후 설비 대수를 반복 합산하지 않는다.")})
    if logical == "live_process_events" and {"interval_end", "fab_id", "area"} <= names:
        relationships.append({"target_logical_table": "live_process_snapshots",
                              "keys": [{"source": key, "target": key} for key in ("interval_end", "fab_id", "area")],
                              "cardinality": "many_to_one", "source": SIM_SOURCE,
                              "review_status": "code_grounded",
                              "aggregation_rule": "snapshot의 UNIQUE(interval_end,fab_id,area) 기준. 이벤트 조인 후 snapshot WIP를 SUM하면 중복된다. 먼저 집계하거나 snapshot만 조회한다."})
    if simulation:
        notes += ["실제 공장 측정값이 아니라 생성된 시뮬레이션이다.",
                  "area 값은 photo/etch 등 생성기 용어이며 Dry_Etch 등의 모델 area와 동일 키가 아니다.",
                  "합성 toolgroup/equipment_id를 model toolgroups와 이름 유사성만으로 JOIN하지 않는다."]
        if raw_simulation:
            notes += ["raw event가 원장이다. snapshot/projection은 이 테이블에서 재생성 가능한 파생 데이터로 취급한다.",
                      "created_at이 아니라 event_time을 공정 발생 시각으로 사용한다."]
    elif report:
        notes += ["AutoSched 시뮬레이션 보고서이며 실시간 운영 측정값이 아니다.",
                  "WarmUp은 일반 성능 집계에서 제외한다. period/relative가 다른 행의 중복 집계를 피한다."]
    else:
        notes += ["모델 입력/정책/계획으로 관측된 현재 공장 상태를 추론하지 않는다."]
    return {"description": info[0], "aliases": list(info[1]), "metrics": metrics,
            "relationships": relationships,
            "semantics": {"version": VERSION, "managed_by": "code", "review_status": "code_grounded" if known else "unreviewed",
                          "source": source if known else None, "grain": info[2],
                          "default_time_column": "event_time" if raw_simulation and "event_time" in names else
                                                 "interval_end" if simulation and "interval_end" in names else
                                                 "report_time" if report and "report_time" in names else None,
                          "column_meanings": descriptions, "notes": notes,
                          "trust_weight": 2 if known else 1}}


def enrich(entry: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(entry)
    defaults = definition(result["logical_table"], result["columns"])
    for key, value in defaults.items():
        if (key == "metrics" and key in result
                and result.get("semantics", {}).get("managed_by") == "editor"):
            continue
        if not result.get(key) or (key == "description" and result[key] == result["logical_table"].replace("_", " ")):
            result[key] = value
    for column in result["columns"]:
        meaning = defaults["semantics"]["column_meanings"].get(column["name"], {})
        editorial = result.get("semantics", {}).get("column_meanings", {})
        if isinstance(editorial, dict):
            override = editorial.get(column["name"])
            if isinstance(override, dict) and isinstance(override.get("description"), str):
                meaning = {**meaning, "description": override["description"]}
        if not column.get("description"):
            column["description"] = meaning.get("description", "")
    # A catalog definition for another FAB variant cannot advertise absent columns.
    names = {column["name"] for column in result["columns"]}
    result["metrics"] = [metric for metric in result.get("metrics", []) if metric.get("column") in names]
    return result
