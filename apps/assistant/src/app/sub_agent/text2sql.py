from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import httpx
from psycopg import Error as PsycopgError

from app.agents.usage import model_call, model_http_client, reported_usage
from app.config import get_settings
from app.db.fab_catalog import comparison_fabs, normalize_fab, resolve_fab, table_pattern, table_ref
from app.db.metadata_catalog import load_fab_catalog
from app.db.read_only import ReadOnlyQueryExecutor, SqlValidationError
from app.db.schema_retrieval import mentions_table, select_catalog
from app.sub_agent.semantic_plan import (
    PLAN_PROMPT,
    SemanticPlan,
    request_requirements,
    validate_plan,
    validate_sql_plan,
)
from app.sub_agent.semantic_sql import compile_single_table
from app.sub_agent.snapshot_queries import (
    METRICS,
    SLOT_METRICS,
    ambiguous_stock_total,
    build_snapshot_query,
    comparison_ranges,
    period_aggregates,
    product_grain_unavailable,
    requires_flexible_aggregation,
    simulation_areas,
)

QueryType = Literal[
    "status",
    "master_data_lookup",
    "release_plan_lookup",
    "trend",
    "unsupported",
]
Text2SQLStatus = Literal[
    "succeeded",
    "needs_clarification",
    "unsupported",
    "data_unavailable",
    "failed",
]


@dataclass(frozen=True)
class QuerySlot:
    value: str
    source: Literal["explicit_user", "request_context", "alias_match", "parser", "llm_inference", "conversation_context"]
    confidence: float
    raw_text: str


@dataclass(frozen=True)
class QueryPlan:
    query_type: QueryType
    template_id: str | None
    fab_id: str | None = None
    data_source_type: Literal[
        "operational_report", "model_master", "release_plan", "simulation_snapshot", "mixed"
    ] | None = None
    slots: dict[str, QuerySlot] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    source_tables: list[str] = field(default_factory=list)
    select_items: list[str] = field(default_factory=list)
    filters: list[dict[str, str]] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    order_by: list[str] = field(default_factory=list)
    aggregation: str | None = None
    expected_result_shape: str | None = None
    chart_intent: dict[str, Any] | None = None
    semantic_plan: dict[str, Any] = field(default_factory=dict)
    grounding: dict[str, Any] = field(default_factory=dict)
    generation_attempts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Text2SQLResult:
    status: Text2SQLStatus
    query_type: QueryType
    answer: str
    sql: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    confidence: float = 0.0
    limitations: list[str] = field(default_factory=list)
    plan: QueryPlan | None = None


class Text2SQLClient(Protocol):
    def create_sql(
        self,
        *,
        question: str,
        query_type: QueryType,
        fab_id: str,
        slots: dict[str, QuerySlot],
        schema_context: dict[str, Any],
    ) -> dict[str, Any]: ...


ALLOWED_FABS: set[str] = {"fab10", "fab11", "fab12", "fab13"}
ROUTE_TABLES_BY_FAB: dict[str, set[str]] = {
    "fab10": {"route_product_3", "route_product_4"},
    "fab11": {f"route_product_{idx}" for idx in range(1, 11)},
    "fab12": {"route_product_3", "route_product_4", "route_product_e3"},
    "fab13": {
        *(f"route_product_{idx}" for idx in range(1, 11)),
        "route_product_e1",
        "route_product_e2",
        "route_product_e3",
    },
}

PRODUCT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:product|part|route_product|제품)[-_ ]?([eE]?\d+|[A-Za-z])(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
ROUTE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])route[-_ ]?product[-_ ]?([eE]?\d+)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
PERIOD_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])period[_ ]?(\d+)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
TOOLGROUP_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])([A-Z][A-Za-z]{1,8}[\s_-]+[A-Z]{2}"
    r"(?:[\s_-]+[0-9]{1,3})+)(?![A-Za-z0-9_])"
)
TYPE_PREFIX_PATTERN = re.compile(r"\b([A-Z][A-Z]{1,8})[\s_-]+([A-Z]{2})\b")
LOT_PATTERN = re.compile(r"\b(?:init_)?lot[_-][A-Za-z0-9_-]+\b", re.IGNORECASE)
TABLE_REF_PATTERN = re.compile(
    r"\b(?:from|join)\s+((?:\"[^\"]+\"|[a-zA-Z_][\w]*)\s*\.\s*(?:\"[^\"]+\"|[a-zA-Z_][\w]*))",
    re.IGNORECASE,
)

AREA_ALIASES = {
    "dry etch": "Dry_Etch",
    "dry_etch": "Dry_Etch",
    "드라이에치": "Dry_Etch",
    "wet etch": "Wet_Etch",
    "wet_etch": "Wet_Etch",
    "웻에치": "Wet_Etch",
    "diffusion": "Diffusion",
    "확산": "Diffusion",
    "implant": "Implant",
    "photo": "Photo",
    "litho": "Photo",
    "tf met": "TF_Met",
    "tf_met": "TF_Met",
    "def met": "Def_Met",
    "def_met": "Def_Met",
    "cmp": "CMP",
}
SIMULATION_AREA_ALIASES = {
    "cmp": "cmp",
    "deposition": "deposition",
    "etch": "etch",
    "implant": "implant",
    "metrology": "metrology",
    "photo": "photo",
}

STATUS_TERMS = {
    "wip",
    "현재",
    "상태",
    "가동률",
    "util",
    "utilization",
    "cycle",
    "cycle time",
    "ontime",
    "on-time",
    "queue",
    "큐",
}
ROUTE_TERMS = {"route", "라우트", "공정", "step", "스텝", "단계"}
TOOLGROUP_TERMS = {"toolgroup", "tool group", "툴그룹", "설비군", "area", "영역"}
RELEASE_TERMS = {"release", "lotrelease", "릴리즈", "due", "duedate", "납기"}
PM_BREAKDOWN_TERMS = {"pm", "breakdown", "고장", "장애", "setup", "셋업", "transport", "이송"}
TREND_TERMS = {
    "차트",
    "chart",
    "추이",
    "추세",
    "트렌드",
    "날짜 기준",
    "일자별",
    "일별",
    "주별",
    "월별",
    "라인차트",
    "라인 차트",
    "line chart",
    "그래프",
    "시각화",
}
DATE_AMBIGUITY_TERMS = {
    "최근",
    "latest",
    "recent",
    "지난주",
    "이번주",
    "일별",
    "날짜 기준",
    "date basis",
}
DATE_GRAIN_TERMS = {
    "일별": "day",
    "daily": "day",
    "주별": "week",
    "weekly": "week",
    "월별": "month",
    "monthly": "month",
}
POLICY_LOOKUP_TERMS = {
    "policy",
    "정책",
    "목록",
    "list",
    "mean",
    "mttr",
    "mttf",
    "event",
    "이벤트",
    "조회",
}
OPERATIONAL_STATUS_HINTS = {
    "현재",
    "상태",
    "비율",
    "rate",
    "높은 순",
    "상위",
    "진행",
    "설비",
    "station",
    "tool",
}
DATE_BASIS_ALIASES = {
    "start_date": "start_date",
    "start date": "start_date",
    "투입일": "start_date",
    "릴리즈일": "start_date",
    "due_date": "due_date",
    "due date": "due_date",
    "duedate": "due_date",
    "납기": "due_date",
    "납기일": "due_date",
    "report_time": "report_time",
    "report time": "report_time",
    "리포트 시간": "report_time",
    "보고 시간": "report_time",
    "startdate": "startdate",
    "compdate": "compdate",
}
METRIC_ALIASES = {
    "queue_time": "avg_queue_minutes",
    "avg_queue_minutes": "avg_queue_minutes",
    "avg_cycle_hours": "avg_cycle_hours",
    "utilization_percent": "util_percent",
    "정비 시간": "pm_minutes",
    "정비시간": "pm_minutes",
    "pm 시간": "pm_minutes",
    "pm_minutes": "pm_minutes",
    "downtime": "down_minutes",
    "down_minutes": "down_minutes",
    "down_percent": "down_percent",
    "다운 시간": "down_minutes",
    "비가동 시간": "down_minutes",
    "비가동시간": "down_minutes",
    "대기 시간": "avg_queue_minutes",
    "대기시간": "avg_queue_minutes",
    "queue time": "avg_queue_minutes",
    "wip": "wiplotavg",
    "재공": "wiplotavg",
    "현재 wip": "wiplotcur",
    "util": "util_percent",
    "utilization": "util_percent",
    "가동률": "util_percent",
    "cycle time": "cycleavg",
    "cycle": "cycleavg",
    "ct": "cycleavg",
    "ontime": "ontime_percent",
    "on-time": "ontime_percent",
    "납기 준수": "ontime_percent",
    "lot completion": "lotcomps",
    "lot completions": "lotcomps",
    "completion": "lotcomps",
    "처리량": "lotcomps",
    "생산량": "lotcomps",
    "완료 lot": "lotcomps",
    "완료lot": "lotcomps",
    "완료 로트": "lotcomps",
    "lot_completions": "lotcomps",
    "lot_starts": "lotstarts",
    "투입량": "lotstarts",
    "투입 lot": "lotstarts",
    "투입lot": "lotstarts",
    "투입 로트": "lotstarts",
    "throughput": "lotcomps",
    "output": "lotcomps",
    "온도": "temperature_c",
    "temperature_c": "temperature_c",
    "습도": "humidity_percent",
    "humidity_percent": "humidity_percent",
    "불량 ppm": "defect_ppm",
    "defect_ppm": "defect_ppm",
    "병목 점수": "bottleneck_score",
    "bottleneck_score": "bottleneck_score",
    "대기 lot": "queue_lots",
    "대기로트": "queue_lots",
    "queue_lots": "queue_lots",
    "down": "down_percent",
    "고장": "down_percent",
    "비가동": "down_percent",
    "비가동률": "down_percent",
    "pm": "pm_percent",
    "현재 상태": "curstate",
    "current state": "curstate",
    "진행 step": "curstep",
    "수율": "yield_percent",
    "yield_percent": "yield_percent",
    "yield": "yield_percent",
}


SCHEMA_CATALOG: dict[str, dict[str, list[str]]] = {
    "operational_report": {
        "autosched_perf": [
            "source_row_id",
            "source_file",
            "report_time",
            "period",
            "relative",
            "lotstarts",
            "lotcomps",
            "wiplotavg",
            "ontime_percent",
            "cycleavg",
        ],
        "autosched_stngrp": [
            "source_row_id",
            "source_file",
            "report_time",
            "period",
            "relative",
            "stngrp",
            "lotcomps",
            "util_percent",
            "wiplotavg",
            "proc_percent",
            "down_percent",
            "pm_percent",
        ],
        "autosched_stn": [
            "source_row_id",
            "source_file",
            "report_time",
            "period",
            "relative",
            "stn",
            "lotcomps",
            "util_percent",
            "wiplotavg",
            "curstate",
            "down_percent",
            "pm_percent",
            "proc_percent",
        ],
        "autosched_part": [
            "source_row_id",
            "source_file",
            "report_time",
            "period",
            "relative",
            "part",
            "lotstarts",
            "lotcomps",
            "wiplotavg",
            "wiplotcur",
            "ontime_percent",
            "cycleavg",
        ],
        "autosched_lot": [
            "source_row_id",
            "source_file",
            "part",
            "lot",
            "startdate",
            "compdate",
            "duedate",
            "stepcomps",
            "curstn",
            "curstep",
            "cyclemax",
            "xtheormax",
        ],
    },
    "model_master": {
        "toolgroups": [
            "source_row_id",
            "area",
            "toolgroup",
            "number_of_tools",
            "toolgrouplocation",
            "dispatching",
            "ranking_1",
            "ranking_2",
            "ranking_3",
            "tool_wake_up_ranking",
        ],
        "pm": ["source_row_id", "pm_event_name", "type_name", "pm_type", "mean", "ttr_units"],
        "breakdown": [
            "source_row_id",
            "down_event_name",
            "type_name",
            "down_type",
            "mttf",
            "mttr",
            "mttr_units",
        ],
        "setups": [
            "source_row_id",
            "setup_group_name",
            "current_setup",
            "new_setup",
            "setup_time",
            "st_units",
            "minmal_number_of_runs",
        ],
        "transport": [
            "source_row_id",
            "from_location",
            "to_location",
            "transporttime_distribution",
            "mean",
            "tt_units",
        ],
    },
    "release_plan": {
        "lotrelease": [
            "source_row_id",
            "product_name",
            "route_name",
            "lot_name_type",
            "priority",
            "wafers_per_lot",
            "start_date",
            "release_distribution",
            "release_interval",
            "lots_per_release",
            "due_date",
            "release_scenario",
        ],
        "lotrelease_variable_due_dates": [
            "source_row_id",
            "product_name",
            "route_name",
            "lot_name_type",
            "priority",
            "wafers_per_lot",
            "start_date",
            "due_date",
            "release_scenario",
        ],
        "lotrelease_engineering": [
            "source_row_id",
            "product_name",
            "route_name",
            "lot_name_type",
            "priority",
            "start_date",
            "due_date",
            "release_scenario",
        ],
    },
}

TEXT2SQL_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "supported": {"type": "boolean"},
        "sql": {"type": "string"},
        "source_tables": {"type": "array", "items": {"type": "string"}},
        "select_items": {"type": "array", "items": {"type": "string"}},
        "filters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "field": {"type": "string"},
                    "operator": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["field", "operator", "value"],
            },
        },
        "group_by": {"type": "array", "items": {"type": "string"}},
        "order_by": {"type": "array", "items": {"type": "string"}},
        "aggregation": {"type": ["string", "null"]},
        "expected_result_shape": {"type": ["string", "null"]},
        "chart_intent": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "type": {"type": "string"},
                "x": {"type": "string"},
                "y": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                },
                "x_title": {"type": "string"},
                "y_title": {"type": "string"},
                "series": {"type": ["string", "null"]},
            },
            "required": ["type", "x", "y", "x_title", "y_title", "series"],
        },
        "answer": {"type": "string"},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": [
        "supported",
        "sql",
        "source_tables",
        "select_items",
        "filters",
        "group_by",
        "order_by",
        "aggregation",
        "expected_result_shape",
        "chart_intent",
        "answer",
        "limitations",
        "confidence",
    ],
}


class OpenAIText2SQLClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        endpoint: str | None = None,
        api_version: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.openai_api_key
        self.model = model or settings.openai_model
        self.endpoint = (endpoint or settings.openai_endpoint).rstrip("/")
        self.api_version = api_version or settings.openai_api_version
        self.timeout_seconds = timeout_seconds

    def create_sql(
        self,
        *,
        question: str,
        query_type: QueryType,
        fab_id: str,
        slots: dict[str, QuerySlot],
        schema_context: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        schema_context["request_requirements"] = request_requirements(question)
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": _system_prompt(),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": question,
                            "query_type": query_type,
                            "fab_id": fab_id,
                            "slots": _serialize_slots(slots),
                            "schema_context": _compact_model_context(schema_context),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "fab_text2sql_direct_sql",
                    "strict": True,
                    "schema": TEXT2SQL_OUTPUT_SCHEMA,
                },
            },
        }
        if schema_context.get("table_details"):
            planning_payload = {
                **payload,
                "messages": [{"role": "system", "content": PLAN_PROMPT}, payload["messages"][1]],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "fab_semantic_plan", "strict": True,
                    "schema": SemanticPlan.strict_response_schema(),
                }},
            }
            raw_plan = self._complete_payload(planning_payload)
            schema_context["proposed_semantic_plan"] = raw_plan
            try:
                semantic_plan = validate_plan(raw_plan, schema_context)
            except ValueError as exc:
                return {"supported": False, "answer": "SQL 작성 전 조회 계획 검증에 실패했습니다.",
                        "limitations": [str(exc)], "failure_stage": "semantic_plan"}
            if not semantic_plan.supported:
                return {"supported": False, "answer": semantic_plan.reason,
                        "limitations": semantic_plan.limitations}
            schema_context["validated_semantic_plan"] = semantic_plan.model_dump()
            compiled = compile_single_table(semantic_plan) if query_type != "trend" else None
            if compiled is not None:
                columns = [p.column for p in semantic_plan.projections] + [a.alias for a in semantic_plan.aggregates]
                schema_context.setdefault("grounding", {})["sql_generation_mode"] = "compiled_validated_plan"
                return {
                    "supported": True, "sql": compiled, "source_tables": semantic_plan.tables,
                    "select_items": columns, "filters": [],
                    "group_by": [p.column for p in semantic_plan.group_by],
                    "order_by": [f"{s.output} {s.direction}" for s in semantic_plan.order_by],
                    "aggregation": semantic_plan.aggregates[0].function if semantic_plan.aggregates else None,
                    "expected_result_shape": semantic_plan.result_grain, "chart_intent": None,
                    "answer": "검증된 의미 계획을 읽기 전용 SQL로 변환했습니다.",
                    "limitations": semantic_plan.limitations, "confidence": 0.9,
                }
            payload["messages"].append({
                "role": "user",
                "content": "Generate SQL faithful to this validated plan. Preserve its tables, columns, "
                           "joins, filters, aggregation and grain:\n" + semantic_plan.model_dump_json(),
            })
        return self._complete_payload(payload)

    def _complete_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = payload.get("response_format", {}).get("json_schema", {}).get("name", "text2sql")
        with model_call(kind, self.model):
            return self._send_payload(payload)

    def _send_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        url = (
            f"{self.endpoint}/openai/deployments/{self.model}/chat/completions"
            f"?api-version={self.api_version}"
        )
        with model_http_client(self.timeout_seconds, endpoint=url) as client:
            response = client.post(url, headers=headers, json=payload)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = response.text.strip()
                if len(detail) > 1000:
                    detail = f"{detail[:1000]}..."
                raise RuntimeError(
                    f"LLM API returned HTTP {response.status_code}: {detail or 'empty response body'}"
                ) from exc
        body = response.json()
        reported_usage(body.get("usage"))
        output_text = _extract_chat_completion_content(body)
        return json.loads(output_text)


def _compact_model_context(context: dict[str, Any]) -> dict[str, Any]:
    """Keep grounding evidence, omit duplicate descriptions and audit-only rankings."""
    compact = deepcopy(context)
    compact.pop("grounding", None)
    for detail in compact.get("table_details", {}).values():
        detail.get("semantics", {}).pop("column_meanings", None)
    for feedback in compact.get("execution_feedback", []):
        feedback.pop("previous_plan", None)
    return compact


def generate_sql(
    question: str,
    schema_context: str | None = None,
    *,
    llm_client: Text2SQLClient | None = None,
) -> str:
    del schema_context
    result = plan_text2sql(question, llm_client=llm_client)
    if not result.sql:
        raise ValueError(result.answer)
    return result.sql


def execute_read_only(sql: str) -> list[dict[str, Any]]:
    return ReadOnlyQueryExecutor().execute(sql).rows


def answer_question(
    question: str,
    *,
    fab: str | None = None,
    line: str | None = None,
    process: str | None = None,
    product: str | None = None,
    route: str | None = None,
    equipment: str | None = None,
    date_basis: str | None = None,
    metric: str | None = None,
    execute: bool | None = None,
    query_type: QueryType | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    execution_feedback: list[dict[str, Any]] | None = None,
    execution_context: dict[str, Any] | None = None,
    llm_client: Text2SQLClient | None = None,
    deterministic_only: bool = False,
) -> Text2SQLResult:
    settings = get_settings()
    should_execute = execute if execute is not None else bool(settings.postgres_dsn)
    database_catalog = None
    if should_execute and llm_client is None and not deterministic_only:
        resolution = resolve_fab(question, fab, conversation_history)
        targets = comparison_fabs(question, conversation_history) or ([resolution.fab_id] if resolution.fab_id else [])
        if targets and all(target in ALLOWED_FABS for target in targets):
            try:
                database_catalog = {}
                for target in targets:
                    database_catalog.update(load_fab_catalog(target))
            except (RuntimeError, PsycopgError):
                # A failed discovery (including a partially loaded multi-FAB catalog)
                # is not an authoritative empty catalog or an unsupported question.
                return Text2SQLResult(
                    status="failed", query_type=query_type or "status",
                    answer="DB 연결 또는 테이블 목록 조회에 실패해 공정 데이터를 확인하지 못했습니다. DB 연결 상태를 확인한 뒤 다시 시도해주세요.",
                    limitations=["DB 조회 장애로 관측값을 확보하지 못했습니다. 지원하지 않는 질문이나 데이터 부재로 판단할 수 없습니다."],
                )
    result = plan_text2sql(
        question,
        fab=fab,
        line=line,
        process=process,
        product=product,
        route=route,
        equipment=equipment,
        date_basis=date_basis,
        metric=metric,
        query_type=query_type,
        conversation_history=conversation_history,
        execution_feedback=execution_feedback,
        execution_context=execution_context,
        llm_client=llm_client,
        deterministic_only=deterministic_only,
        discover_schema=should_execute and llm_client is None and not deterministic_only,
        database_catalog=database_catalog,
    )
    if result.status != "succeeded" or not result.sql:
        return result

    if not should_execute:
        return result

    try:
        executor = ReadOnlyQueryExecutor()
        execution = executor.execute(result.sql)
    except (RuntimeError, SqlValidationError, PsycopgError) as exc:
        if result.query_type == "status" and _is_missing_autosched_table_error(exc):
            return _operational_data_unavailable(result.plan.fab_id, result.plan.slots) if result.plan else result
        if (result.plan and result.plan.data_source_type == "simulation_snapshot"
                and _looks_like_missing_relation_error(str(exc))):
            return _simulation_data_unavailable(result, error=str(exc))
        return Text2SQLResult(
            status="failed",
            query_type=result.query_type,
            answer="SQL은 생성됐지만 데이터베이스 조회를 완료하지 못했습니다.",
            sql=result.sql,
            confidence=0.2,
            limitations=[*result.limitations, str(exc)],
            plan=result.plan,
        )

    empty_aggregate = bool(execution.rows) and all(
        row.get("area_count") == 0 or row.get("observation_count") == 0 for row in execution.rows
    )
    if ((execution.row_count == 0 or empty_aggregate) and result.plan
            and result.plan.data_source_type == "simulation_snapshot"):
        return _simulation_data_unavailable(result)

    limitations = list(result.limitations)
    interval_lengths = [float(row[key]) for row in execution.rows
                        for key in ("observation_minutes_min", "observation_minutes_max")
                        if row.get(key) is not None]
    if interval_lengths and min(interval_lengths) != max(interval_lengths):
        limitations.append(
            f"원자료의 관측 구간 길이가 최소 {min(interval_lengths):g}분, 최대 {max(interval_lengths):g}분으로 다릅니다. "
            "이 구간들의 단순 평균 차이를 동일한 관측 조건의 공정 개선·악화나 인과 효과로 해석하지 마세요."
        )
    if execution.row_count == execution.limit:
        limitations.append(
            f"조회 결과가 반환 한도 {execution.limit}행에 도달했습니다. 전체 결과가 아닐 수 있으므로 기간을 줄이거나 집계 단위를 넓혀주세요."
        )
    if execution.row_count == 0:
        limitations = [*limitations, *_empty_result_limitations(result)]

    rows = execution.rows
    if result.plan and result.plan.data_source_type == "simulation_snapshot":
        rows = [
            {key: value.astimezone(ZoneInfo("Asia/Seoul"))
             if isinstance(value, datetime) and value.tzinfo is not None else value
             for key, value in row.items()}
            for row in rows
        ]
    return Text2SQLResult(
        status="succeeded",
        query_type=result.query_type,
        answer=_summarize_execution(result, rows),
        sql=result.sql,
        rows=rows,
        columns=execution.columns,
        row_count=execution.row_count,
        confidence=result.confidence,
        limitations=limitations,
        plan=result.plan,
    )


def plan_text2sql(
    question: str,
    *,
    fab: str | None = None,
    line: str | None = None,
    process: str | None = None,
    product: str | None = None,
    route: str | None = None,
    equipment: str | None = None,
    date_basis: str | None = None,
    metric: str | None = None,
    query_type: QueryType | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    execution_feedback: list[dict[str, Any]] | None = None,
    execution_context: dict[str, Any] | None = None,
    llm_client: Text2SQLClient | None = None,
    deterministic_only: bool = False,
    discover_schema: bool = False,
    database_catalog: dict[str, dict[str, Any]] | None = None,
) -> Text2SQLResult:
    normalized = _normalize_question(question)
    if re.search(r"(?:행|레코드|테이블|데이터).{0,30}(?:삭제|비워|비우|드롭|삽입)(?:해|하|줘)", normalized):
        return Text2SQLResult(status="unsupported", query_type="unsupported",
                              answer="이 Text2SQL agent는 읽기 전용 조회만 지원합니다. 데이터 변경은 실행하지 않습니다.")
    slots = _extract_slots(
        question,
        normalized,
        fab=fab,
        process=process,
        product=product,
        route=route,
        equipment=equipment,
        date_basis=date_basis,
        metric=metric,
    )
    slots = inherit_followup_metrics(question, slots, conversation_history or [])
    slots = inherit_followup_period(question, slots, conversation_history or [])
    slots = inherit_followup_areas(question, slots, conversation_history or [])
    slots = inherit_result_areas(question, slots, conversation_history or [])
    targets = comparison_fabs(question, conversation_history)
    if targets:
        from app.sub_agent.fab_comparison import plan_comparison
        return plan_comparison(question, targets, slots, database_catalog or {})
    resolution = resolve_fab(question, fab, conversation_history)
    explicit_line = re.search(r"(?<![a-z0-9_])([a-z0-9_-]+)\s*라인(?!\s*차트)|라인\s+([a-z0-9_-]+)", question, re.IGNORECASE)
    requested_line = next((v for v in explicit_line.groups() if v), None) if explicit_line else line
    if requested_line:
        slots["line"] = QuerySlot(requested_line, "explicit_user" if explicit_line else "request_context", 1.0, requested_line)
    slots.pop("fab_id", None)
    if resolution.fab_id:
        slots["fab_id"] = QuerySlot(
            resolution.fab_id, resolution.source,
            1.0 if resolution.source == "explicit_user" else 0.9, resolution.raw_text,
        )
    # The graph passes its grounded scope alongside the original question so
    # sequential attempts retain the same approved request context.
    planner_fab = (execution_context or {}).get("scope", {}).get("fab_id", {})
    resolved_fab = _normalize_fab(str(planner_fab.get("value") or ""))
    if resolved_fab and resolution.source != "explicit_user":
        slots["fab_id"] = QuerySlot(
            resolved_fab, "request_context", 1.0, str(planner_fab.get("raw_text") or resolved_fab),
        )
    fab_id = slots.get("fab_id").value if "fab_id" in slots else None
    query_type = query_type or _classify_query_type(normalized)
    if (query_type == "unsupported" and slots.get("metrics")
            and slots["metrics"].source == "conversation_context"
            and re.search(r"조회|보여|알려|현황|상태|어때|\bshow\b", normalized)
            and not re.search(r"삭제|수정|변경|중지|정지|\b(?:delete|update|drop|stop)\b", normalized)):
        query_type = "status"

    if not fab_id:
        return _clarification(
            query_type=query_type,
            answer=resolution.clarification or "조회할 FAB을 지정해주세요.",
            slots=slots,
        )

    if "unresolved_area_reference" in slots:
        return _clarification(
            query_type=query_type, fab_id=fab_id, slots=slots,
            answer="어느 공정을 뜻하는지 확인이 필요합니다. 공정명을 지정하거나 직전 결과 전체라면 '그 공정들'이라고 말씀해주세요.",
        )

    if _has_invalid_explicit_date_range(question) or _has_invalid_calendar_period(question):
        return _clarification(
            query_type=query_type,
            answer=(
                "날짜 범위가 올바르지 않습니다. 시작일과 종료일을 YYYY-MM-DD 형식으로 "
                "지정하고 시작일이 종료일보다 늦지 않게 입력해주세요."
            ),
            fab_id=fab_id,
            data_source_type=_data_source_type(query_type, slots),
            slots=slots,
        )

    if _has_overlapping_comparison_date_ranges(question, normalized):
        return _clarification(
            query_type=query_type,
            answer=(
                "비교할 두 날짜 범위가 겹칩니다. 중복 집계를 피하려면 서로 겹치지 않는 "
                "두 기간을 지정해주세요."
            ),
            fab_id=fab_id,
            data_source_type=_data_source_type(query_type, slots),
            slots=slots,
        )

    requested_top_n = _parse_requested_top_n(normalized)
    if requested_top_n is not None and not 1 <= requested_top_n <= 200:
        return _clarification(
            query_type=query_type,
            answer="상위/하위 조회 개수는 1~200 사이로 지정해주세요.",
            fab_id=fab_id,
            data_source_type=_data_source_type(query_type, slots),
            slots=slots,
        )

    if (len(_csv_slot(slots, "metrics")) > 1 and not _parse_metrics(normalized)
            and (slots.get("ranking_direction") or slots.get("threshold_metric")
                 or re.search(r"가장|제일|상위|하위", normalized))):
        return _clarification(
            query_type=query_type,
            answer="앞서 여러 지표를 조회했습니다. 순위나 조건을 적용할 지표를 하나 지정해주세요.",
            fab_id=fab_id, slots=slots,
        )

    threshold_metric = _slot_value(slots, "threshold_metric")
    threshold_value = _slot_value(slots, "threshold_value")
    threshold_unit = _slot_value(slots, "threshold_unit")
    percent_metrics = {
        "util_percent", "ontime_percent", "down_percent", "pm_percent", "proc_percent",
        "yield_percent", "humidity_percent", "utilization_percent",
    }
    if threshold_unit == "percent" and threshold_metric not in percent_metrics:
        return _clarification(
            query_type=query_type,
            answer="요청한 지표는 percent 단위가 아닙니다. 지표의 원래 단위로 임계값을 지정해주세요.",
            fab_id=fab_id,
            data_source_type=_data_source_type(query_type, slots),
            slots=slots,
        )
    if (
        threshold_metric in percent_metrics
        and threshold_value is not None
        and not 0 <= float(threshold_value) <= 100
    ):
        return _clarification(
            query_type=query_type,
            answer="percent 지표의 임계값은 0~100 범위로 지정해주세요.",
            fab_id=fab_id,
            data_source_type=_data_source_type(query_type, slots),
            slots=slots,
        )

    if _needs_date_basis_clarification(query_type, normalized, slots):
        return _clarification(
            query_type=query_type,
            answer=(
                "기간/날짜 기준이 모호합니다. lotrelease는 start_date 기준인지 "
                "due_date 기준인지 지정해주세요."
            ),
            fab_id=fab_id,
            data_source_type="release_plan",
            slots=slots,
        )

    if query_type == "release_plan_lookup" and not _has_selective_release_constraint(slots):
        return _clarification(
            query_type=query_type,
            answer="release plan은 범위가 넓습니다. product, route, release scenario 중 하나를 지정해주세요.",
            fab_id=fab_id,
            data_source_type="release_plan",
            slots=slots,
        )

    if database_catalog is not None and llm_client is None:
        if query_type == "master_data_lookup":
            equipment_counts = _grounded_equipment_counts(question, slots, fab_id, database_catalog)
            if equipment_counts:
                return equipment_counts
        if query_type in {"status", "trend"} and product_grain_unavailable(
            question, {key:slot.value for key,slot in slots.items()}, database_catalog
        ):
            reason = (f"{fab_id.upper()}의 요청 지표는 현재 공정 영역별 시뮬레이션 관측값으로 저장되어 있고 제품 구분이 없습니다. "
                      "제품별 수치를 계산하려면 제품 구분이 있는 관측 자료가 필요합니다. 공정 영역별 지표는 조회할 수 있습니다.")
            return Text2SQLResult(status="data_unavailable", query_type=query_type, answer=reason,
                limitations=[reason], plan=QueryPlan(query_type=query_type, template_id=None, fab_id=fab_id,
                                                     slots=slots, limitations=[reason], data_source_type="simulation_snapshot"))
        if (table_ref(fab_id, "live_process_snapshots") in database_catalog
                and ambiguous_stock_total(question, {key:slot.value for key,slot in slots.items()})):
            return _clarification(
                query_type=query_type, fab_id=fab_id, slots=slots,
                data_source_type="simulation_snapshot",
                answer="WIP·대기 LOT는 시점별 재고이므로 기간 내 값을 누적하면 같은 재고를 여러 번 셀 수 있습니다. 기간 평균과 마지막 관측 시점의 합계 중 어느 값을 조회할까요?",
            )
        if requested_line and not any(
            col["name"] in {"line", "line_id", "line_name"}
            for entry in database_catalog.values() for col in entry.get("columns", [])
        ):
            reason = (f"{fab_id.upper()} {requested_line}라인을 구분하는 컬럼이 현재 데이터에 없어 "
                      "해당 라인의 수치를 조회할 수 없습니다. 공정 영역(etch·photo·cmp 등) 또는 FAB 전체로 조회 범위를 지정해주세요.")
            return Text2SQLResult(
                status="data_unavailable", query_type=query_type, answer=reason,
                limitations=[reason], plan=QueryPlan(query_type=query_type, template_id=None,
                                                    fab_id=fab_id, slots=slots, limitations=[reason]),
            )
        snapshot = build_snapshot_query(
            question, query_type, fab_id,
            {key: slot.value for key, slot in slots.items()}, database_catalog,
            row_limit=get_settings().db_max_rows,
        )
        if snapshot:
            snapshot_slots = dict(slots)
            if snapshot.area:
                snapshot_slots["area"] = QuerySlot(snapshot.area, "alias_match", 0.95, snapshot.area)
            return _deterministic_result(
                query_type=query_type, fab_id=fab_id, slots=snapshot_slots,
                template_id="deterministic_simulation_observations",
                table=table_ref(fab_id, "live_process_snapshots"), sql=snapshot.sql,
                columns=snapshot.columns, filters=[], order_by=[],
                expected_result_shape=snapshot.shape, chart_intent=snapshot.chart,
                data_source_type="simulation_snapshot", additional_limitations=snapshot.limitations,
            )

    explicit_simulation = _is_explicit_simulation_request(normalized)
    catalog_queue = _is_unavailable_queue_metric_request(normalized) and any(
        entry["data_source_type"] == "simulation_snapshot"
        for entry in (database_catalog or {}).values()
    )
    if (query_type in {"status", "trend"}
            and _is_unavailable_queue_metric_request(normalized)
            and not explicit_simulation and not catalog_queue):
        return Text2SQLResult(
            status="data_unavailable",
            query_type=query_type,
            answer="현재 AutoSched report catalog에는 직접 조회 가능한 Queue Time 지표가 없습니다.",
            confidence=0.95,
            limitations=[
                "Queue Time을 수치로 답하려면 queue-time snapshot 또는 operation 간 대기시간 파생 계약이 필요합니다."
            ],
            plan=QueryPlan(
                query_type=query_type,
                template_id=None,
                fab_id=fab_id,
                data_source_type="operational_report",
                slots=slots,
            ),
        )
    if (
        explicit_simulation
        and database_catalog is None
        and not discover_schema
        and query_type in {"status", "trend"}
        and _is_unavailable_queue_metric_request(normalized)
        and not requires_flexible_aggregation(question)
    ):
        deterministic_simulation = _deterministic_simulation_snapshot_query(
            slots, fab_id
        )
        if deterministic_simulation:
            return deterministic_simulation

    explicit_catalog_table = any(
        mentions_table(normalized, ref, entry["logical_table"])
        and entry["logical_table"] not in SCHEMA_CATALOG.get(_data_source_type(query_type, slots), {})
        for ref, entry in (database_catalog or {}).items()
    )
    if ((llm_client is None or deterministic_only)
            and not explicit_simulation and not explicit_catalog_table and not catalog_queue
            and not requires_flexible_aggregation(question)
            and not (query_type == "master_data_lookup" and re.search(
                r"개수|건수|몇\s*(?:개|건)|행\s*수|count\s*\(|합계|총합|평균|최댓값|최솟값", normalized))):
        deterministic = _deterministic_fast_path(query_type, slots, fab_id)
        if deterministic and (
            database_catalog is None or deterministic.status == "needs_clarification"
            or (deterministic.sql and set(_extract_table_refs(deterministic.sql)) <= set(database_catalog))
        ):
            return deterministic
    if deterministic_only:
        return Text2SQLResult(
            status="unsupported",
            query_type=query_type,
            answer=(
                "현재 deterministic Text2SQL contract가 다음 질문 유형을 지원하지 않습니다: "
                f"{question}"
            ),
            confidence=0.95,
            limitations=["deterministic_only 모드에서는 LLM SQL 생성을 호출하지 않습니다."],
            plan=QueryPlan(
                query_type=query_type,
                template_id=None,
                fab_id=fab_id,
                data_source_type=_data_source_type(query_type, slots),
                slots=slots,
            ),
        )

    if database_catalog is None and discover_schema:
        try:
            database_catalog = load_fab_catalog(fab_id)
        except (RuntimeError, PsycopgError) as exc:
            return Text2SQLResult(
                status="failed", query_type=query_type,
                answer="이번 요청의 테이블 목록을 확인하지 못했습니다. DB 연결 상태를 확인해주세요.",
                limitations=[str(exc)], plan=QueryPlan(query_type, None, fab_id=fab_id, slots=slots),
            )
    schema_context = _schema_context_for_question(query_type, slots, fab_id, database_catalog)
    if database_catalog:
        explicit_sources = [ref for ref, entry in database_catalog.items()
                            if mentions_table(normalized, ref, entry["logical_table"])]
        if explicit_sources:
            schema_context["primary_table_refs"] = explicit_sources
        elif explicit_simulation or catalog_queue:
            schema_context["primary_table_refs"] = [
                ref for ref, entry in database_catalog.items()
                if entry["data_source_type"] == "simulation_snapshot"
            ]
    schema_context["conversation_history"] = conversation_history or []
    schema_context["execution_feedback"] = execution_feedback or []
    schema_context["execution_context"] = execution_context or {}
    full_schema_context = schema_context
    if database_catalog:
        selected, grounding = select_catalog(
            question, schema_context["table_details"],
            primary_refs=schema_context["primary_table_refs"],
        )
        schema_context = {**schema_context,
                          "tables": {ref: schema_context["tables"][ref] for ref in selected},
                          "table_details": selected, "allowed_table_refs": list(selected),
                          "table_patterns": {ref: schema_context["table_patterns"][ref] for ref in selected},
                          "grounding": grounding}
    if not schema_context["tables"]:
        return Text2SQLResult(
            status="unsupported",
            query_type=query_type,
            answer="현재 테이블 목록에서 요청한 지표와 조회 대상을 연결할 정보를 찾지 못했습니다.",
            confidence=0.3,
            limitations=["지원 table catalog에 매핑되는 대상이 없습니다."],
            plan=QueryPlan(query_type=query_type, template_id=None, fab_id=fab_id, slots=slots),
        )

    try:
        llm_output = (llm_client or OpenAIText2SQLClient()).create_sql(
            question=question,
            query_type=query_type,
            fab_id=fab_id,
            slots=slots,
            schema_context=schema_context,
        )
    except (RuntimeError, httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return Text2SQLResult(
            status="failed",
            query_type=query_type,
            answer="LLM Text2SQL 호출을 완료하지 못했습니다.",
            confidence=0.0,
            limitations=[str(exc)],
            plan=QueryPlan(
                query_type=query_type,
                template_id=None,
                fab_id=fab_id,
                data_source_type=schema_context["data_source_type"],
                slots=slots,
                source_tables=schema_context["allowed_table_refs"],
            ),
        )

    result = _result_from_llm_output(llm_output, query_type, slots, fab_id, schema_context)
    attempts = []

    def record_attempt(candidate, context, action):
        attempts.append({"attempt": len(attempts) + 1, "action": action,
                         "status": candidate.status, "sql": candidate.sql,
                         "issues": candidate.limitations,
                         "tables": list(context["tables"]),
                         "semantic_plan": context.get("proposed_semantic_plan", {}),
                         "grounding": context.get("grounding", {})})

    record_attempt(result, schema_context, "focused_generation")
    validation_failure = result.status == "failed" or llm_output.get("failure_stage") == "semantic_plan"
    can_broaden = len(schema_context["tables"]) < len(full_schema_context["tables"])
    if database_catalog and (validation_failure or (result.status == "unsupported" and can_broaden)):
        # Two candidate attempts total: repair a grounded structure failure in place,
        # or broaden on a retrieval miss. Neither action grants new DB permissions.
        stage = "validation_repair" if validation_failure else "schema_retrieval"
        retry_context = {**(schema_context if validation_failure else full_schema_context), "execution_feedback": [
            *(execution_feedback or []),
            {"stage": stage, "status": result.status,
             "reason": result.answer, "issues": result.limitations,
             "previous_sql": result.sql,
             "previous_plan": schema_context.get("proposed_semantic_plan", {}),
             "action": "Repair the reported structural error using the same schema."
             if validation_failure else "Broadened to all actual FAB tables after the focused attempt."},
        ]}
        retry_context.pop("validated_semantic_plan", None)
        retry_context.pop("proposed_semantic_plan", None)
        try:
            output = (llm_client or OpenAIText2SQLClient()).create_sql(
                question=question, query_type=query_type, fab_id=fab_id,
                slots=slots, schema_context=retry_context,
            )
            result = _result_from_llm_output(output, query_type, slots, fab_id, retry_context)
            record_attempt(result, retry_context, stage)
        except (RuntimeError, httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            attempts.append({"attempt": 2, "action": stage, "status": "transport_failure",
                             "issues": [str(exc)]})
    if result.plan:
        result = replace(result, plan=replace(result.plan, generation_attempts=attempts))
    return result


def _deterministic_fast_path(
    query_type: QueryType,
    slots: dict[str, QuerySlot],
    fab_id: str,
) -> Text2SQLResult | None:
    if query_type == "status":
        return _deterministic_status_query(slots, fab_id)
    if query_type == "trend":
        return _deterministic_trend_query(slots, fab_id)
    if query_type == "master_data_lookup":
        return _deterministic_master_query(slots, fab_id)
    if query_type == "release_plan_lookup":
        return _deterministic_release_lookup(slots, fab_id)
    return None


def _grounded_equipment_counts(
    question: str,
    slots: dict[str, QuerySlot],
    fab_id: str,
    catalog: dict[str, dict[str, Any]],
) -> Text2SQLResult | None:
    """Sum model equipment counts by their exact stored area, without collapsing names."""
    normalized = question.casefold()
    if not re.search(r"(?:설비|장비)\s*대수|number_of_tools|tool\s*counts?", normalized):
        return None
    if not re.search(r"공정\s*(?:영역)?별|영역별|by\s+area|per\s+area", normalized):
        return None
    if re.search(r"현재|지금|실시간|가동|고장|available|running|current|now|평균|최대|최소|상위|하위", normalized):
        return None
    if any(key in slots for key in ("area", "areas", "product", "products", "toolgroup", "toolgroups", "route", "line", "date_start", "relative_period", "threshold_metric", "top_n")):
        return None
    table = table_ref(fab_id, "toolgroups")
    entry = catalog.get(table)
    if not entry or not {"area", "number_of_tools"} <= {column["name"] for column in entry["columns"]}:
        return None
    return _deterministic_result(
        query_type="master_data_lookup", fab_id=fab_id, slots=slots,
        template_id="deterministic_master_area_equipment_counts", table=table,
        sql=f"SELECT area, SUM(number_of_tools) AS total_number_of_tools FROM {table} GROUP BY area ORDER BY area ASC",
        columns=["area", "total_number_of_tools"], filters=[], order_by=["area"],
        expected_result_shape="rows", data_source_type="model_master",
        additional_limitations=["설비 대수는 모델 입력에 정의된 값이며 현재 가동 중인 실제 설비 대수가 아닙니다. 공정 영역 이름은 원자료 그대로 구분합니다."],
    )


def _deterministic_master_query(
    slots: dict[str, QuerySlot],
    fab_id: str,
) -> Text2SQLResult | None:
    domain = _slot_value(slots, "master_domain")
    product = _slot_value(slots, "product")
    area = _slot_value(slots, "area")
    toolgroup = _slot_value(slots, "toolgroup")

    if product:
        route_table = _route_table_for_product(product, fab_id)
        if not route_table:
            return Text2SQLResult(
                status="unsupported",
                query_type="master_data_lookup",
                answer=f"{fab_id}에는 {product} route table이 없습니다.",
                confidence=0.98,
                limitations=["현재 경로 테이블 목록에서 요청한 대상을 찾지 못했습니다."],
                plan=QueryPlan(
                    query_type="master_data_lookup",
                    template_id=None,
                    fab_id=fab_id,
                    data_source_type="model_master",
                    slots=slots,
                ),
            )
        columns = [
            "route", "step", "step_description", "area", "toolgroup",
            "processing_unit", "mean", "pt_units",
        ]
        predicates = []
        if area:
            predicates.append(f"area ILIKE {_sql_literal('%' + area + '%')}")
        if toolgroup:
            predicates.append(f"toolgroup ILIKE {_sql_literal('%' + toolgroup + '%')}")
        predicate = " AND ".join(predicates) if predicates else "TRUE"
        table = table_ref(fab_id, route_table)
        sql = _select_sql(
            columns=columns,
            table=table,
            predicate=predicate,
            order_by=["source_row_id"],
            limit=100,
        )
        return _deterministic_result(
            query_type="master_data_lookup",
            fab_id=fab_id,
            slots=slots,
            template_id="deterministic_master_route_steps",
            table=table,
            sql=sql,
            columns=columns,
            filters=[],
            order_by=["source_row_id"],
            expected_result_shape="rows",
        )

    if domain == "pm":
        columns = ["p.pm_event_name", "p.type_name", "p.pm_type", "p.mean", "p.ttr_units"]
        table = table_ref(fab_id, 'pm')
        extra_tables: tuple[str, ...] = ()
        if area:
            toolgroups = table_ref(fab_id, 'toolgroups')
            sql = (
                f"SELECT {', '.join(columns)}\n"
                f"FROM {table} p\n"
                f"JOIN {toolgroups} t ON t.toolgroup ILIKE p.type_name || '%'\n"
                f"WHERE t.area ILIKE {_sql_literal('%' + area + '%')}\n"
                "ORDER BY p.type_name, p.pm_event_name\n"
                "LIMIT 200"
            )
            extra_tables = (toolgroups,)
        else:
            sql = _select_sql(
                columns=columns,
                table=f"{table} p",
                predicate="TRUE",
                order_by=["p.type_name", "p.pm_event_name"],
                limit=200,
            )
        return _deterministic_result(
            query_type="master_data_lookup",
            fab_id=fab_id,
            slots=slots,
            template_id="deterministic_master_pm",
            table=table,
            additional_tables=extra_tables,
            sql=sql,
            columns=[column.removeprefix("p.") for column in columns],
            filters=[],
            order_by=["type_name", "pm_event_name"],
            expected_result_shape="rows",
        )

    if domain == "breakdown":
        table = table_ref(fab_id, 'breakdown')
        raw_columns = [
            "down_event_name", "type_name", "down_type", "mttf", "mttr", "mttr_units"
        ]
        type_prefix = _slot_value(slots, "type_prefix") or (
            _toolgroup_to_type_prefix(toolgroup) if toolgroup else None
        )
        additional_tables = ()
        if type_prefix:
            toolgroups = table_ref(fab_id, 'toolgroups')
            columns = [f"b.{column}" for column in raw_columns]
            sql = (
                f"SELECT {', '.join(columns)}\n"
                f"FROM {table} b\n"
                "WHERE EXISTS (\n"
                f"    SELECT 1 FROM {toolgroups} t\n"
                f"    WHERE t.toolgroup ILIKE {_sql_literal(type_prefix + '%')}\n"
                "      AND b.type_name ILIKE t.area || '%'\n"
                ")\n"
                "ORDER BY b.type_name, b.down_event_name\n"
                "LIMIT 200"
            )
            additional_tables = (toolgroups,)
        else:
            columns = raw_columns
            sql = _select_sql(
                columns=columns,
                table=table,
                predicate="TRUE",
                order_by=["type_name", "down_event_name"],
                limit=200,
            )
        return _deterministic_result(
            query_type="master_data_lookup",
            fab_id=fab_id,
            slots=slots,
            template_id="deterministic_master_breakdown",
            table=table,
            additional_tables=additional_tables,
            sql=sql,
            columns=raw_columns,
            filters=[],
            order_by=["type_name", "down_event_name"],
            expected_result_shape="rows",
        )

    if domain:
        return None

    table = table_ref(fab_id, 'toolgroups')
    columns = [
        "area", "toolgroup", "number_of_tools", "toolgrouplocation", "dispatching",
        "ranking_1", "ranking_2", "ranking_3", "tool_wake_up_ranking",
    ]
    predicates = []
    if area:
        predicates.append(f"area ILIKE {_sql_literal('%' + area + '%')}")
    if toolgroup:
        predicates.append(f"toolgroup ILIKE {_sql_literal('%' + toolgroup + '%')}")
    predicate = " AND ".join(predicates) if predicates else "TRUE"
    sql = _select_sql(
        columns=columns,
        table=table,
        predicate=predicate,
        order_by=["area", "toolgroup"],
        limit=50,
    )
    return _deterministic_result(
        query_type="master_data_lookup",
        fab_id=fab_id,
        slots=slots,
        template_id="deterministic_master_toolgroups",
        table=table,
        sql=sql,
        columns=columns,
        filters=[],
        order_by=["area", "toolgroup"],
        expected_result_shape="rows",
    )


def _deterministic_release_lookup(
    slots: dict[str, QuerySlot],
    fab_id: str,
) -> Text2SQLResult:
    table = table_ref(fab_id, 'lotrelease')
    columns = [
        "product_name", "route_name", "lot_name_type", "priority", "wafers_per_lot",
        "start_date", "due_date", "release_scenario",
    ]
    predicates = []
    product = _slot_value(slots, "product")
    route = _slot_value(slots, "route")
    if product:
        predicates.append(f"product_name ILIKE {_sql_literal('%' + product + '%')}")
    if route:
        predicates.append(f"route_name ILIKE {_sql_literal('%' + route + '%')}")
    if scenario := _slot_value(slots, "release_scenario"):
        predicates.append(f"release_scenario ILIKE {_sql_literal('%' + scenario + '%')}")
    date_basis = _slot_value(slots, "date_basis")
    if date_basis in {"start_date", "due_date"}:
        if date_start := _slot_value(slots, "date_start"):
            predicates.append(f"{date_basis} >= {_sql_literal(date_start)}::date")
        if date_end := _slot_value(slots, "date_end"):
            predicates.append(f"{date_basis} < {_sql_literal(date_end)}::date")
    sql = _select_sql(
        columns=columns,
        table=table,
        predicate=" AND ".join(predicates),
        order_by=["start_date NULLS LAST", "due_date NULLS LAST", "source_row_id"],
        limit=100,
    )
    return _deterministic_result(
        query_type="release_plan_lookup",
        fab_id=fab_id,
        slots=slots,
        template_id="deterministic_release_lookup",
        table=table,
        sql=sql,
        columns=columns,
        filters=[],
        order_by=["start_date", "due_date", "source_row_id"],
        expected_result_shape="rows",
    )


def _deterministic_status_query(
    slots: dict[str, QuerySlot],
    fab_id: str,
) -> Text2SQLResult:
    lot_id = _slot_value(slots, "lot_id")
    toolgroup = _slot_value(slots, "toolgroup")
    toolgroups = _csv_slot(slots, "toolgroups")
    product = _slot_value(slots, "product")
    products = _csv_slot(slots, "products")
    area = _slot_value(slots, "area")
    top_n = int(_slot_value(slots, "top_n") or 0)
    ranking_direction = _slot_value(slots, "ranking_direction")
    threshold_metric = _slot_value(slots, "threshold_metric")
    threshold_operator = _slot_value(slots, "threshold_operator")
    threshold_value = _slot_value(slots, "threshold_value")
    filters: list[dict[str, str]] = []

    requested_metrics = _csv_slot(slots, "metrics")
    if (
        {"util_percent", "cycleavg"} <= set(requested_metrics)
        and not lot_id
        and not toolgroup
        and not product
    ):
        return _deterministic_cross_source_impact_baseline(slots, fab_id, area=area)

    if lot_id:
        table = "autosched_lot"
        columns = [
            "part", "lot", "startdate", "compdate", "duedate", "stepcomps", "curstn",
            "curstep", "cyclemax", "xtheormax",
        ]
        predicate = f"lot = {_sql_literal(lot_id)}"
        order_by = ["source_row_id DESC"]
        filters.append({"field": "lot", "operator": "=", "value": lot_id})
    elif len(toolgroups) > 1:
        table = "autosched_stn"
        metrics = _csv_slot(slots, "metrics") or [
            _slot_value(slots, "metric") or "util_percent"
        ]
        metrics = [
            metric for metric in metrics if metric in _operational_numeric_metrics()
        ]
        columns = list(dict.fromkeys(["report_time", "period", "stn", *metrics]))
        station_predicate = (
            "lower(stn) IN ("
            + ", ".join(_sql_literal(item.casefold()) for item in toolgroups)
            + ")"
        )
        predicate = _current_report_predicate(fab_id, table, station_predicate)
        order_by = ["stn", "source_row_id DESC"]
        filters.append(
            {"field": "stn", "operator": "IN", "value": ",".join(toolgroups)}
        )
    elif toolgroup:
        table = "autosched_stn"
        columns = [
            "report_time", "period", "stn", "lotcomps", "util_percent", "wiplotavg",
            "curstate", "down_percent", "pm_percent", "proc_percent",
        ]
        predicate = _current_report_predicate(
            fab_id,
            table,
            f"lower(stn) = {_sql_literal(toolgroup.casefold())}",
        )
        order_by = ["report_time DESC NULLS LAST", "source_row_id DESC"]
        filters.append({"field": "stn", "operator": "=", "value": toolgroup})
    elif len(products) > 1:
        table = "autosched_part"
        metrics = _csv_slot(slots, "metrics") or [
            _slot_value(slots, "metric") or "wiplotavg"
        ]
        metrics = [
            metric for metric in metrics if metric in _operational_numeric_metrics()
        ]
        columns = list(dict.fromkeys(["report_time", "period", "part", *metrics]))
        parts = [_product_to_part_alias(item) or item for item in products]
        part_predicate = (
            "lower(part) IN ("
            + ", ".join(_sql_literal(part.casefold()) for part in parts)
            + ")"
        )
        predicate = _current_report_predicate(fab_id, table, part_predicate)
        order_by = ["part", "source_row_id DESC"]
        filters.append({"field": "part", "operator": "IN", "value": ",".join(parts)})
    elif product:
        table = "autosched_part"
        columns = [
            "report_time", "period", "part", "lotstarts", "lotcomps", "wiplotavg",
            "wiplotcur", "ontime_percent", "cycleavg",
        ]
        part = _product_to_part_alias(product) or product
        predicate = _current_report_predicate(
            fab_id,
            table,
            f"lower(part) = {_sql_literal(part.casefold())}",
        )
        order_by = ["report_time DESC NULLS LAST", "source_row_id DESC"]
        filters.append({"field": "part", "operator": "=", "value": part})
    elif area:
        table = "autosched_stngrp"
        columns = [
            "report_time", "period", "stngrp", "lotcomps", "util_percent", "wiplotavg",
            "proc_percent", "down_percent", "pm_percent",
        ]
        predicate = _current_report_predicate(
            fab_id,
            table,
            f"stngrp ILIKE {_sql_literal('%' + area + '%')}",
        )
        order_by = [
            "report_time DESC NULLS LAST", "wiplotavg DESC NULLS LAST",
            "util_percent DESC NULLS LAST",
        ]
        filters.append({"field": "stngrp", "operator": "ILIKE", "value": area})
    else:
        table = "autosched_perf"
        columns = [
            "report_time", "period", "lotstarts", "lotcomps", "wiplotavg",
            "ontime_percent", "cycleavg",
        ]
        predicate = _current_report_predicate(fab_id, table)
        order_by = ["report_time DESC NULLS LAST", "source_row_id DESC"]

    explicit_metrics = set(requested_metrics)
    if metric := _slot_value(slots, "metric"):
        explicit_metrics.add(metric)
    if threshold_metric:
        explicit_metrics.add(threshold_metric)
    available_columns = set(SCHEMA_CATALOG["operational_report"][table])
    unavailable_metrics = sorted(explicit_metrics - available_columns)
    if unavailable_metrics:
        unavailable = ", ".join(unavailable_metrics)
        return Text2SQLResult(
            status="data_unavailable",
            query_type="status",
            answer=(
                f"요청한 지표({unavailable})는 {table} 데이터에서 조회할 수 없습니다. "
                "대상 또는 조회 지표를 변경해주세요."
            ),
            confidence=0.95,
            limitations=[
                "선택된 대상 테이블에 없는 지표 조건을 제거한 SQL은 실행하지 않았습니다."
            ],
            plan=QueryPlan(
                query_type="status",
                template_id=None,
                fab_id=fab_id,
                data_source_type="operational_report",
                slots=slots,
            ),
        )

    ranking_metric = threshold_metric or _slot_value(slots, "metric")
    if ranking_direction in {"ASC", "DESC"} and not ranking_metric:
        return _clarification(
            query_type="status",
            answer="상위/하위 순위를 계산할 지표를 지정해주세요.",
            fab_id=fab_id,
            data_source_type="operational_report",
            slots=slots,
        )

    if (
        threshold_metric in columns
        and threshold_operator in {">=", "<=", ">", "<"}
        and threshold_value is not None
    ):
        predicate += f"\n  AND {threshold_metric} {threshold_operator} {threshold_value}"
        filters.append(
            {
                "field": threshold_metric,
                "operator": threshold_operator,
                "value": threshold_value,
            }
        )
    if ranking_direction in {"ASC", "DESC"} and ranking_metric in columns:
        order_by = [
            f"{ranking_metric} {ranking_direction} NULLS LAST",
            *[item for item in order_by if not item.startswith(f"{ranking_metric} ")],
        ]
    default_limit = (
        20
        if table in {"autosched_stngrp", "autosched_stn"}
        or len(products) > 1
        or len(toolgroups) > 1
        else 1
    )
    sql = _select_sql(
        columns=columns,
        table=table_ref(fab_id, table),
        predicate=predicate,
        order_by=order_by,
        limit=top_n if 1 <= top_n <= 200 else default_limit,
    )
    filters = [
        {"field": "relative", "operator": "=", "value": "Y"},
        {"field": "period", "operator": "<>", "value": "WarmUp"},
        *filters,
    ] if table != "autosched_lot" else filters
    return _deterministic_result(
        query_type="status",
        fab_id=fab_id,
        slots=slots,
        template_id=(
            "deterministic_status_autosched_part_multi"
            if table == "autosched_part" and len(products) > 1
            else "deterministic_status_autosched_stn_multi"
            if table == "autosched_stn" and len(toolgroups) > 1
            else f"deterministic_status_{table}"
        ),
        table=table_ref(fab_id, table),
        sql=sql,
        columns=columns,
        filters=filters,
        order_by=order_by,
        expected_result_shape="rows",
    )


def _deterministic_cross_source_impact_baseline(
    slots: dict[str, QuerySlot],
    fab_id: str,
    *,
    area: str | None,
) -> Text2SQLResult:
    perf_table = table_ref(fab_id, 'autosched_perf')
    utilization_table = table_ref(fab_id, 'autosched_stngrp')
    area_predicate = (
        f"\n  AND s.stngrp ILIKE {_sql_literal('%' + area + '%')}" if area else ""
    )
    sql = (
        "WITH perf AS (\n"
        "    SELECT report_time, period, cycleavg, lotcomps, ontime_percent\n"
        f"    FROM {perf_table}\n"
        "    WHERE relative = 'Y' AND period <> 'WarmUp'\n"
        "    ORDER BY report_time DESC NULLS LAST, source_row_id DESC\n"
        "    LIMIT 1\n"
        "), utilization AS (\n"
        "    SELECT AVG(s.util_percent) AS util_percent\n"
        f"    FROM {utilization_table} s\n"
        "    CROSS JOIN perf p\n"
        "    WHERE s.relative = 'Y'\n"
        "      AND s.period = p.period\n"
        "      AND s.report_time = p.report_time"
        f"{area_predicate}\n"
        ")\n"
        "SELECT p.report_time, p.period, u.util_percent, p.cycleavg, "
        "p.lotcomps, p.ontime_percent\n"
        "FROM perf p\n"
        "CROSS JOIN utilization u\n"
        "LIMIT 1"
    )
    filters = [
        {"field": "relative", "operator": "=", "value": "Y"},
        {"field": "period", "operator": "<>", "value": "WarmUp"},
    ]
    if area:
        filters.append({"field": "stngrp", "operator": "ILIKE", "value": area})
    return _deterministic_result(
        query_type="status",
        fab_id=fab_id,
        slots=slots,
        template_id="deterministic_status_cross_source_impact_baseline",
        table=perf_table,
        additional_tables=(utilization_table,),
        sql=sql,
        columns=[
            "report_time",
            "period",
            "util_percent",
            "cycleavg",
            "lotcomps",
            "ontime_percent",
        ],
        filters=filters,
        group_by=[],
        order_by=["report_time DESC", "source_row_id DESC"],
        aggregation="AVG(util_percent)",
        expected_result_shape="single_row",
    )


def _deterministic_trend_query(
    slots: dict[str, QuerySlot],
    fab_id: str,
) -> Text2SQLResult | None:
    if "release_table" in slots:
        return _deterministic_release_trend(slots, fab_id)

    products = _csv_slot(slots, "products")
    toolgroups = _csv_slot(slots, "toolgroups")
    periods = _csv_slot(slots, "periods")
    metrics = _csv_slot(slots, "metrics") or [_slot_value(slots, "metric") or "wiplotavg"]
    metrics = [metric for metric in metrics if metric in _operational_numeric_metrics()]
    if not metrics:
        return None
    if "comparison_date_ranges" in slots:
        return _deterministic_date_range_comparison(slots, fab_id, metrics)
    date_start = _slot_value(slots, "date_start")
    date_end = _slot_value(slots, "date_end")

    if len(products) > 1:
        table = "autosched_part"
        parts = [_product_to_part_alias(product) or product for product in products]
        columns = ["part", *metrics]
        part_predicate = f"lower(part) IN ({', '.join(_sql_literal(part.casefold()) for part in parts)})"
        filters = [{"field": "part", "operator": "IN", "value": ",".join(parts)}]
        if date_start or date_end:
            predicates = ["period <> 'WarmUp'", part_predicate]
            if date_start:
                predicates.append(f"report_time::date >= {_sql_literal(date_start)}::date")
                filters.append({"field": "report_time::date", "operator": ">=", "value": date_start})
            if date_end:
                predicates.append(f"report_time::date < {_sql_literal(date_end)}::date")
                filters.append({"field": "report_time::date", "operator": "<", "value": date_end})
            select_items = [
                "part",
                *(
                    f"AVG(NULLIF({metric}::text, '')::numeric) AS {metric}"
                    for metric in metrics
                ),
            ]
            sql = (
                f"SELECT {', '.join(select_items)}\n"
                f"FROM {table_ref(fab_id, table)}\n"
                f"WHERE {' AND '.join(predicates)}\n"
                "GROUP BY part\n"
                "ORDER BY part"
            )
            return _deterministic_result(
                query_type="trend",
                fab_id=fab_id,
                slots=slots,
                template_id="deterministic_compare_autosched_part_date_range",
                table=table_ref(fab_id, table),
                sql=sql,
                columns=columns,
                filters=filters,
                group_by=["part"],
                order_by=["part"],
                aggregation="AVG",
                expected_result_shape="comparison",
                chart_intent=_chart_intent("grouped_bar", "part", metrics),
            )
        predicate = _current_report_predicate(fab_id, table) + f"\n  AND {part_predicate}"
        order_by = ["part"]
        x = "part"
    elif len(toolgroups) > 1:
        table = "autosched_stn"
        date_expression, date_column = _date_bucket(
            "report_time", _slot_value(slots, "date_grain")
        )
        select_items = [f"{date_expression} AS {date_column}", "stn"]
        select_items.extend(f"AVG({metric}) AS {metric}" for metric in metrics)
        station_predicate = (
            "lower(stn) IN ("
            + ", ".join(_sql_literal(item.casefold()) for item in toolgroups)
            + ")"
        )
        predicate = f"period <> 'WarmUp'\n  AND {station_predicate}"
        filters = [
            {"field": "period", "operator": "<>", "value": "WarmUp"},
            {"field": "stn", "operator": "IN", "value": ",".join(toolgroups)},
        ]
        if date_start:
            predicate += f"\n  AND report_time::date >= {_sql_literal(date_start)}::date"
            filters.append(
                {"field": "report_time::date", "operator": ">=", "value": date_start}
            )
        if date_end:
            predicate += f"\n  AND report_time::date < {_sql_literal(date_end)}::date"
            filters.append(
                {"field": "report_time::date", "operator": "<", "value": date_end}
            )
        group_by = [date_expression, "stn"]
        sql = (
            f"SELECT {', '.join(select_items)}\n"
            f"FROM {table_ref(fab_id, table)}\n"
            f"WHERE {predicate}\n"
            f"GROUP BY {', '.join(group_by)}\n"
            f"ORDER BY {date_column} ASC, stn"
        )
        return _deterministic_result(
            query_type="trend",
            fab_id=fab_id,
            slots=slots,
            template_id="deterministic_trend_autosched_stn_multi",
            table=table_ref(fab_id, table),
            sql=sql,
            columns=[date_column, "stn", *metrics],
            filters=filters,
            group_by=group_by,
            order_by=[f"{date_column} ASC", "stn"],
            aggregation="AVG",
            expected_result_shape="time_series",
            chart_intent=_chart_intent("line", date_column, metrics, series="stn"),
        )
    elif len(periods) > 1:
        table = "autosched_perf"
        columns = ["period", *metrics]
        predicate = f"period IN ({', '.join(_sql_literal(period) for period in periods)})"
        order_by = ["period"]
        x = "period"
        filters = [{"field": "period", "operator": "IN", "value": ",".join(periods)}]
    else:
        area = _slot_value(slots, "area")
        table = "autosched_stngrp" if area else "autosched_perf"
        category = ["stngrp"] if area else []
        date_expression, date_column = _date_bucket("report_time", _slot_value(slots, "date_grain"))
        select_items = [f"{date_expression} AS {date_column}", *category]
        select_items.extend(f"AVG({metric}) AS {metric}" for metric in metrics)
        predicate = "period <> 'WarmUp'"
        filters = [{"field": "period", "operator": "<>", "value": "WarmUp"}]
        if date_start:
            predicate += f"\n  AND report_time::date >= {_sql_literal(date_start)}::date"
            filters.append({"field": "report_time::date", "operator": ">=", "value": date_start})
        if date_end:
            predicate += f"\n  AND report_time::date < {_sql_literal(date_end)}::date"
            filters.append({"field": "report_time::date", "operator": "<", "value": date_end})
        if area:
            predicate += f"\n  AND stngrp ILIKE {_sql_literal('%' + area + '%')}"
            filters.append({"field": "stngrp", "operator": "ILIKE", "value": area})
        group_by = [date_expression, *category]
        sql = (
            f"SELECT {', '.join(select_items)}\n"
            f"FROM {table_ref(fab_id, table)}\n"
            f"WHERE {predicate}\n"
            f"GROUP BY {', '.join(group_by)}\n"
            f"ORDER BY {date_column} ASC{', stngrp' if area else ''}"
        )
        return _deterministic_result(
            query_type="trend",
            fab_id=fab_id,
            slots=slots,
            template_id=f"deterministic_trend_{table}",
            table=table_ref(fab_id, table),
            sql=sql,
            columns=[date_column, *category, *metrics],
            filters=filters,
            group_by=group_by,
            order_by=[f"{date_column} ASC", *( ["stngrp"] if area else [])],
            aggregation="AVG",
            expected_result_shape="time_series",
            chart_intent=_chart_intent("line", date_column, metrics, series="stngrp" if area else None),
        )

    sql = _select_sql(
        columns=columns,
        table=table_ref(fab_id, table),
        predicate=predicate,
        order_by=order_by,
        limit=200,
    )
    return _deterministic_result(
        query_type="trend",
        fab_id=fab_id,
        slots=slots,
        template_id=f"deterministic_compare_{table}",
        table=table_ref(fab_id, table),
        sql=sql,
        columns=columns,
        filters=filters,
        order_by=order_by,
        expected_result_shape="comparison",
        chart_intent=_chart_intent("grouped_bar", x, metrics),
    )


def _deterministic_simulation_snapshot_query(
    slots: dict[str, QuerySlot],
    fab_id: str,
) -> Text2SQLResult | None:
    area = _slot_value(slots, "area")
    metrics = [
        "avg_queue_minutes",
        "wip_lots",
        "queue_lots",
        "utilization_percent",
    ]
    table = table_ref(fab_id, "live_process_snapshots")
    area_predicate = f"\n  AND s.area = {_sql_literal(area)}" if area else ""
    filters = [
        {"field": "interval_end", "operator": ">", "value": "latest - 24 hours"},
        {"field": "interval_end", "operator": "<=", "value": "latest"},
    ]
    if area:
        filters.append({"field": "area", "operator": "=", "value": area})
    sql = (
        "WITH latest AS (\n"
        f"  SELECT MAX(interval_end) AS max_interval_end FROM {table}\n"
        ")\n"
        f"SELECT s.interval_end, s.area, {', '.join(f's.{metric}' for metric in metrics)}\n"
        f"FROM {table} s\n"
        "CROSS JOIN latest l\n"
        "WHERE s.interval_end > l.max_interval_end - INTERVAL '24 hours'\n"
        "  AND s.interval_end <= l.max_interval_end"
        f"{area_predicate}\n"
        "ORDER BY s.interval_end ASC, s.area ASC"
    )
    return _deterministic_result(
        query_type="trend",
        fab_id=fab_id,
        slots=slots,
        template_id="deterministic_simulation_snapshot_queue_trend",
        table=table,
        sql=sql,
        columns=["interval_end", "area", *metrics],
        filters=filters,
        group_by=[],
        order_by=["interval_end ASC", "area ASC"],
        aggregation=None,
        expected_result_shape="time_series",
        chart_intent=_chart_intent(
            "line",
            "interval_end",
            ["avg_queue_minutes", "wip_lots", "queue_lots", "utilization_percent"],
            series="area",
        ),
        data_source_type="simulation_snapshot",
        additional_limitations=[
            "live_process_snapshots의 area 값은 cmp/deposition/etch/implant/metrology/photo이며 model master의 Dry_Etch/Wet_Etch와 자동 매핑하지 않습니다."
        ],
    )


def _deterministic_date_range_comparison(
    slots: dict[str, QuerySlot],
    fab_id: str,
    metrics: list[str],
) -> Text2SQLResult | None:
    ranges = _comparison_ranges_from_slot(slots)
    if len(ranges) != 2:
        return None

    area = _slot_value(slots, "area")
    table = "autosched_stngrp" if area else "autosched_perf"
    range_predicates = []
    case_branches = []
    labels = []
    filters = [{"field": "period", "operator": "<>", "value": "WarmUp"}]
    for start, end in ranges:
        predicate = (
            f"report_time::date >= {_sql_literal(start)}::date AND "
            f"report_time::date < {_sql_literal(end)}::date"
        )
        inclusive_end = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
        label = f"{start}~{inclusive_end}"
        range_predicates.append(f"({predicate})")
        case_branches.append(f"WHEN {predicate} THEN {_sql_literal(label)}")
        labels.append(label)
    if area:
        filters.append({"field": "stngrp", "operator": "ILIKE", "value": area})

    comparison_expression = "CASE " + " ".join(case_branches) + " END"
    select_items = [f"{comparison_expression} AS comparison_period"]
    select_items.extend(
        f"AVG(NULLIF({metric}::text, '')::numeric) AS {metric}" for metric in metrics
    )
    predicate = "period <> 'WarmUp'\n  AND (" + " OR ".join(range_predicates) + ")"
    if area:
        predicate += f"\n  AND stngrp ILIKE {_sql_literal('%' + area + '%')}"
    sql = (
        f"SELECT {', '.join(select_items)}\n"
        f"FROM {table_ref(fab_id, table)}\n"
        f"WHERE {predicate}\n"
        f"GROUP BY {comparison_expression}\n"
        "ORDER BY comparison_period"
    )
    return _deterministic_result(
        query_type="trend",
        fab_id=fab_id,
        slots=slots,
        template_id="deterministic_compare_explicit_date_ranges",
        table=table_ref(fab_id, table),
        sql=sql,
        columns=["comparison_period", *metrics],
        filters=filters,
        group_by=[comparison_expression],
        order_by=["comparison_period"],
        aggregation="AVG",
        expected_result_shape="comparison",
        chart_intent=_chart_intent("grouped_bar", "comparison_period", metrics),
        additional_limitations=[
            f"두 기간은 각각 {labels[0]}, {labels[1]} 범위로 집계했습니다."
        ],
    )


def _deterministic_release_trend(
    slots: dict[str, QuerySlot],
    fab_id: str,
) -> Text2SQLResult | None:
    date_basis = _slot_value(slots, "date_basis")
    if date_basis not in {"start_date", "due_date"}:
        return None
    predicates = []
    route = _slot_value(slots, "route")
    product = _slot_value(slots, "product")
    if route:
        predicates.append(f"route_name ILIKE {_sql_literal('%' + route + '%')}")
    elif product:
        predicates.append(f"product_name ILIKE {_sql_literal('%' + product + '%')}")
    date_start = _slot_value(slots, "date_start")
    date_end = _slot_value(slots, "date_end")
    if date_start:
        predicates.append(f"{date_basis} >= {_sql_literal(date_start)}::date")
    if date_end:
        predicates.append(f"{date_basis} < {_sql_literal(date_end)}::date")
    where = " AND ".join(predicates) if predicates else "TRUE"
    date_expression, date_column = _date_bucket(
        date_basis, _slot_value(slots, "date_grain"), prefix="release"
    )
    sql = (
        f"SELECT {date_expression} AS {date_column}, COUNT(*)::bigint AS lot_count\n"
        f"FROM {table_ref(fab_id, 'lotrelease')}\n"
        f"WHERE {where}\n"
        f"GROUP BY {date_expression}\n"
        f"ORDER BY {date_column} ASC"
    )
    return _deterministic_result(
        query_type="trend",
        fab_id=fab_id,
        slots=slots,
        template_id="deterministic_lotrelease_daily",
        table=table_ref(fab_id, 'lotrelease'),
        sql=sql,
        columns=[date_column, "lot_count"],
        filters=[],
        group_by=[date_expression],
        order_by=[f"{date_column} ASC"],
        aggregation="COUNT",
        expected_result_shape="time_series",
        chart_intent=_chart_intent("line", date_column, ["lot_count"]),
    )


def _deterministic_result(
    *,
    query_type: QueryType,
    fab_id: str,
    slots: dict[str, QuerySlot],
    template_id: str,
    table: str,
    additional_tables: tuple[str, ...] = (),
    sql: str,
    columns: list[str],
    filters: list[dict[str, str]],
    order_by: list[str],
    expected_result_shape: str,
    group_by: list[str] | None = None,
    aggregation: str | None = None,
    chart_intent: dict[str, Any] | None = None,
    additional_limitations: list[str] | None = None,
    data_source_type: Literal[
        "operational_report", "model_master", "release_plan", "simulation_snapshot", "mixed"
    ] | None = None,
) -> Text2SQLResult:
    ReadOnlyQueryExecutor(dsn="postgresql://validation-only").validate(sql)
    chart_intent = _enrich_chart_intent(
        chart_intent,
        slots,
        aggregation=aggregation,
    )
    source_tables = [table, *additional_tables]
    _validate_sql_tables(sql, set(source_tables))
    _validate_explicit_periods(sql, slots)
    if data_source_type:
        resolved_data_source_type = data_source_type
    elif table == table_ref(fab_id, "lotrelease"):
        resolved_data_source_type = "release_plan"
    elif ".autosched_" in table:
        resolved_data_source_type = "operational_report"
    else:
        resolved_data_source_type = "model_master"
    limitations = [
        *_base_limitations(query_type, resolved_data_source_type),
        *(additional_limitations or []),
    ]
    plan = QueryPlan(
        query_type=query_type,
        template_id=template_id,
        fab_id=fab_id,
        data_source_type=resolved_data_source_type,
        slots=slots,
        limitations=limitations,
        source_tables=source_tables,
        select_items=columns,
        filters=filters,
        group_by=group_by or [],
        order_by=order_by,
        aggregation=aggregation,
        expected_result_shape=expected_result_shape,
        chart_intent=chart_intent,
    )
    return Text2SQLResult(
        status="succeeded",
        query_type=query_type,
        answer="검증된 deterministic query contract로 read-only SQL을 생성했습니다.",
        sql=sql,
        confidence=0.98,
        limitations=limitations,
        plan=plan,
    )


def _current_report_predicate(
    fab_id: str,
    table: str,
    extra: str | None = None,
) -> str:
    predicate = (
        "relative = 'Y'\n"
        "  AND period <> 'WarmUp'\n"
        "  AND report_time = (\n"
        f"      SELECT MAX(report_time) FROM {table_ref(fab_id, table)}\n"
        "      WHERE relative = 'Y' AND period <> 'WarmUp'\n"
        "  )"
    )
    return f"{predicate}\n  AND {extra}" if extra else predicate


def _select_sql(
    *,
    columns: list[str],
    table: str,
    predicate: str,
    order_by: list[str],
    limit: int,
) -> str:
    return (
        f"SELECT {', '.join(columns)}\n"
        f"FROM {table}\n"
        f"WHERE {predicate}\n"
        f"ORDER BY {', '.join(order_by)}\n"
        f"LIMIT {limit}"
    )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _csv_slot(slots: dict[str, QuerySlot], key: str) -> list[str]:
    return [item for item in (_slot_value(slots, key) or "").split(",") if item]


def _operational_numeric_metrics() -> set[str]:
    return {
        "lotstarts", "lotcomps", "wiplotavg", "wiplotcur", "ontime_percent",
        "cycleavg", "util_percent", "down_percent", "pm_percent", "proc_percent",
    }


def _chart_intent(
    chart_type: str,
    x: str,
    metrics: list[str],
    *,
    series: str | None = None,
) -> dict[str, Any]:
    return {
        "type": chart_type,
        "x": x,
        "y": metrics if len(metrics) > 1 else metrics[0],
        "x_title": x.replace("_", " ").title(),
        "y_title": " / ".join(metric.replace("_", " ").title() for metric in metrics),
        "series": series,
    }


def _result_from_llm_output(
    llm_output: dict[str, Any],
    query_type: QueryType,
    slots: dict[str, QuerySlot],
    fab_id: str,
    schema_context: dict[str, Any],
) -> Text2SQLResult:
    if not llm_output.get("supported", False):
        return Text2SQLResult(
            status="unsupported",
            query_type=query_type,
            answer=str(llm_output.get("answer") or "LLM이 지원 불가로 판단했습니다."),
            confidence=float(llm_output.get("confidence") or 0.3),
            limitations=list(llm_output.get("limitations") or []),
            plan=QueryPlan(
                query_type=query_type,
                template_id=None,
                fab_id=fab_id,
                data_source_type=schema_context["data_source_type"],
                slots=slots,
            ),
        )

    sql = str(llm_output.get("sql") or "").strip()
    sql_sources = _extract_table_refs(sql)
    source_kinds = {
        schema_context.get("table_details", {}).get(ref, {}).get(
            "data_source_type", schema_context["data_source_type"]
        ) for ref in sql_sources
    }
    actual_source_type = (next(iter(source_kinds)) if len(source_kinds) == 1
                          else "mixed" if source_kinds else schema_context["data_source_type"])
    try:
        ReadOnlyQueryExecutor(dsn="postgresql://validation-only").validate(sql)
        _validate_sql_tables(sql, set(schema_context["allowed_table_refs"]))
        _validate_explicit_periods(sql, slots)
        if schema_context.get("validated_semantic_plan"):
            validate_sql_plan(sql, SemanticPlan.model_validate(schema_context["validated_semantic_plan"]))
    except (SqlValidationError, ValueError) as exc:
        return Text2SQLResult(
            status="failed",
            query_type=query_type,
            answer="생성된 SQL이 요청한 조회 구조와 일치하지 않아 실행 전에 보정을 시도했지만 완료하지 못했습니다.",
            sql=sql or None,
            confidence=0.1,
            limitations=[str(exc)],
            plan=QueryPlan(
                query_type=query_type,
                template_id=None,
                fab_id=fab_id,
                data_source_type=schema_context["data_source_type"],
                slots=slots,
                source_tables=list(llm_output.get("source_tables") or []),
                semantic_plan=schema_context.get("validated_semantic_plan", {}),
                grounding=schema_context.get("grounding", {}),
            ),
        )

    source_tables = _extract_table_refs(sql)
    chart_intent, chart_notes = _normalize_chart_intent(
        llm_output.get("chart_intent"),
        slots,
        aggregation=llm_output.get("aggregation"),
    )
    plan = QueryPlan(
        query_type=query_type,
        template_id=None,
        fab_id=fab_id,
        data_source_type=actual_source_type,
        slots=slots,
        limitations=_base_limitations(query_type, actual_source_type)
        + chart_notes
        + list(llm_output.get("limitations") or []),
        source_tables=source_tables,
        select_items=list(llm_output.get("select_items") or []),
        filters=list(llm_output.get("filters") or []),
        group_by=list(llm_output.get("group_by") or []),
        order_by=list(llm_output.get("order_by") or []),
        aggregation=llm_output.get("aggregation"),
        expected_result_shape=llm_output.get("expected_result_shape"),
        chart_intent=chart_intent,
        semantic_plan=schema_context.get("validated_semantic_plan", {}),
        grounding=schema_context.get("grounding", {}),
    )
    return Text2SQLResult(
        status="succeeded",
        query_type=query_type,
        answer=str(llm_output.get("answer") or "LLM이 read-only SQL을 생성했습니다."),
        sql=sql,
        confidence=float(llm_output.get("confidence") or 0.65),
        limitations=plan.limitations,
        plan=plan,
    )


def _schema_context_for_question(
    query_type: QueryType,
    slots: dict[str, QuerySlot],
    fab_id: str,
    database_catalog: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    data_source_type = _data_source_type(query_type, slots)
    tables: dict[str, list[str]] = {}
    if data_source_type == "model_master":
        master_domain = _slot_value(slots, "master_domain")
        product = _slot_value(slots, "product") or _slot_value(slots, "route")
        route_table = _route_table_for_product(product, fab_id) if product else None
        if master_domain in SCHEMA_CATALOG["model_master"]:
            tables[master_domain] = SCHEMA_CATALOG["model_master"][master_domain]
            if "area" in slots or "toolgroup" in slots:
                tables["toolgroups"] = SCHEMA_CATALOG["model_master"]["toolgroups"]
        elif route_table:
            tables[route_table] = _route_columns()
        elif "area" in slots or "toolgroup" in slots:
            tables["toolgroups"] = SCHEMA_CATALOG["model_master"]["toolgroups"]
        else:
            tables.update(SCHEMA_CATALOG["model_master"])
        if route_table and route_table not in tables:
            tables[route_table] = _route_columns()
    else:
        primary_tables = _primary_tables_for_question(data_source_type, slots)
        if primary_tables:
            for table in primary_tables:
                tables[table] = SCHEMA_CATALOG[data_source_type][table]
        else:
            tables.update(SCHEMA_CATALOG[data_source_type])

    primary_table_refs = [
        table_ref(fab_id, table)
        for table in _primary_tables_for_question(data_source_type, slots)
        if table in tables
    ]
    if not primary_table_refs and len(tables) == 1:
        primary_table_refs = [table_ref(fab_id, next(iter(tables)))]
    context = {
        "dialect": "postgresql",
        "fab_id": fab_id,
        "data_source_type": data_source_type,
        "tables": {table_ref(fab_id, table): columns for table, columns in tables.items()},
        "table_patterns": {table_ref(fab_id, table): table_pattern(table) for table in tables},
        "allowed_table_refs": [table_ref(fab_id, table) for table in tables],
        "primary_table_refs": primary_table_refs,
        "slots": _serialize_slots(slots),
        "metric_catalog": _metric_catalog_for_tables(tables),
        "date_columns": _date_columns_for_tables(tables),
        "rules": [
            "Return exactly one SELECT or WITH query.",
            "Use only schema-qualified table names from allowed_table_refs.",
            "Do not write DDL, DML, COPY, INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, SET, or comments.",
            "Always include a deterministic ORDER BY when using LIMIT.",
            "Never scan release-plan tables without a selective product, route, scenario, or date predicate.",
            "For observed status use report/snapshot sources; never infer observed status from model input tables.",
            "Simulation snapshots/events are authorized sources but must be explicitly labeled as simulated data, not actual factory measurements.",
            "Prefer primary_table_refs when present; use other allowed tables only when the primary table cannot answer the question.",
            "Use only columns listed for the chosen table. Do not borrow columns from another table.",
            "PM type_name joins toolgroups.toolgroup. Breakdown type_name for area-scoped settings identifies toolgroups.area, not toolgroup; avoid multiplying settings when joining multiple toolgroups per area.",
            "If the user specified date_basis/date_start/date_end slots, preserve those exact constraints.",
            "For lotrelease trends, do not choose between start_date and due_date unless date_basis is explicit.",
        ],
    }
    if database_catalog is not None:
        available = {
            ref: entry for ref, entry in database_catalog.items()
            if ref == table_ref(fab_id, entry["logical_table"])
        }
        context.update({
            "tables": {ref: [column["name"] for column in entry["columns"]]
                       for ref, entry in available.items()},
            "table_details": available,
            "allowed_table_refs": list(available),
            "primary_table_refs": [ref for ref in primary_table_refs if ref in available],
            "table_patterns": {ref: entry["table_pattern"] for ref, entry in available.items()},
            "catalog_source": "current_database_and_agent_meta",
        })
    return context


def _system_prompt() -> str:
    return """You are the Text2SQL agent for a read-only semiconductor FAB analytics system.

Write the PostgreSQL SQL directly. Do not choose or mention named templates.
Use only the schema-qualified tables and columns supplied in schema_context.
Return only the structured JSON schema.

Hard rules:
1. Generate exactly one SELECT or WITH query.
2. Every table reference must be schema-qualified and present in allowed_table_refs.
3. Do not generate DDL, DML, COPY, comments, SET, locks, or multiple statements.
4. Preserve explicit user constraints. If the request cannot be answered from the allowed schema, set supported=false.
5. Add a LIMIT no higher than 200 unless the query is an aggregate time series.
6. For current status, prefer the latest available observations according to the supplied
   source semantics. Bind entity values and categorical filters from the supplied catalog,
   column descriptions and observed values. Do not assume a user-facing label is a stored value.
   On execution feedback, re-examine those bindings and joins using the catalog. Preserve
   explicit scope and predicates: an empty result does not authorize broadening the query.
7. For trend chart requests, include chart_intent with the output x/y column aliases. For a
   multi-metric comparison, set y to all requested numeric aliases and use grouped_bar unless the
   x-axis is temporal. Set series to a result column only when that column identifies categories.
8. Prefer schema_context.primary_table_refs when present.
9. Do not select columns that are absent from the selected table.
10. FAB is resolved before this call. Use exactly the bound schema and suffixed table names
    in allowed_table_refs (e.g. fab11.toolgroups_fab11). Never switch FABs or emit {fab} placeholders.
11. A validated plan is the output contract. Emit exactly its projections and aggregates,
    preserve order_by/result_limit, and add no undeclared filtering conditions.
12. For each latest_by column, use equality to a same-table SELECT MAX(column) subquery.
    ORDER BY timestamp DESC LIMIT 1 does not satisfy this contract. When latest_scope is
    global, MAX must have no filters except the bound fab_id, even when the outer query
    filters area or another entity. When latest_scope is filtered, MAX must repeat all
    static filters for that source from the plan. Do not choose the scope again.
"""


def _data_source_type(
    query_type: QueryType,
    slots: dict[str, QuerySlot] | None = None,
) -> Literal["operational_report", "model_master", "release_plan"]:
    if query_type == "status":
        return "operational_report"
    if query_type == "release_plan_lookup":
        return "release_plan"
    if query_type == "trend":
        if "release_table" in (slots or {}):
            return "release_plan"
        slot_values = " ".join(slot.value for slot in (slots or {}).values()).casefold()
        if any(term in slot_values for term in ("start_date", "due_date")):
            return "release_plan"
        return "operational_report"
    return "model_master"


def _route_columns() -> list[str]:
    return [
        "source_row_id",
        "route",
        "step",
        "step_description",
        "area",
        "toolgroup",
        "processing_unit",
        "mean",
        "pt_units",
        "setup",
        "setup_time",
        "rework_probability_in_percent",
        "cqt",
        "cqtunits",
    ]


def _primary_tables_for_question(
    data_source_type: str,
    slots: dict[str, QuerySlot],
) -> list[str]:
    if data_source_type == "operational_report":
        metric = _slot_value(slots, "metric")
        if "lot_id" in slots:
            return ["autosched_lot"]
        if "toolgroup" in slots and metric in {
            "curstate",
            "util_percent",
            "wiplotavg",
            "pm_percent",
            "down_percent",
        }:
            return ["autosched_stn"]
        if "product" in slots:
            return ["autosched_part"]
        if "area" in slots and metric in {
            "util_percent",
            "wiplotavg",
            "lotcomps",
            "pm_percent",
            "down_percent",
        }:
            return ["autosched_stngrp"]
        if metric in {"wiplotavg", "wiplotcur", "ontime_percent", "cycleavg", "lotcomps"}:
            return ["autosched_perf"]
    if data_source_type == "release_plan":
        return ["lotrelease", "lotrelease_variable_due_dates", "lotrelease_engineering"]
    if data_source_type == "model_master":
        master_domain = _slot_value(slots, "master_domain")
        if master_domain in SCHEMA_CATALOG["model_master"]:
            return [master_domain]
    return []


def _base_limitations(query_type: QueryType, data_source_type: str) -> list[str]:
    if data_source_type == "simulation_snapshot":
        return ["조회값은 생성된 시뮬레이션 공정 데이터이며 실제 공장 실측값이 아닙니다.",
                "집계는 선택한 snapshot/event의 시간과 행 단위를 기준으로 합니다."]
    if data_source_type == "mixed":
        return ["서로 다른 종류의 데이터를 결합한 조회입니다. 각 소스의 시간·집계 단위와 시뮬레이션 여부를 구분해야 합니다."]
    if data_source_type == "operational_report":
        return [
            "현재 상태 조회는 PostgreSQL에 적재된 AutoSched report 기준입니다.",
            "report_time은 시뮬레이션 report timestamp이며 실제 공장 실시간 clock이 아닐 수 있습니다.",
        ]
    if query_type == "trend":
        return [
            "현재 결과는 SMT2020 General Data의 release plan 기준이며 실시간 투입 실적이 아닙니다.",
        ]
    return [
        "현재 결과는 SMT2020 General Data 기반 simulation/model input 기준입니다.",
        "live/current factory state로 해석하면 안 됩니다.",
    ]


def _route_table_for_product(product: str, fab_id: str) -> str | None:
    token = product.lower().strip().replace(" ", "_").replace("-", "_")
    for prefix in ("product_", "route_product_", "route_"):
        if token.startswith(prefix):
            token = token.removeprefix(prefix)
            break
    table = f"route_product_{token}"
    return table if table in ROUTE_TABLES_BY_FAB[fab_id] else None


def _validate_sql_tables(sql: str, allowed_table_refs: set[str]) -> None:
    used = set(_extract_table_refs(sql))
    if not used:
        raise ValueError("SQL must reference at least one allowed table.")
    disallowed = sorted(used - allowed_table_refs)
    if disallowed:
        raise ValueError(f"SQL referenced non-allowlisted table: {disallowed[0]}")


def _extract_table_refs(sql: str) -> list[str]:
    refs = []
    for reference in TABLE_REF_PATTERN.findall(sql):
        refs.append(reference.replace(" ", "").replace('"', "").lower())
    return refs


def _operational_data_unavailable(
    fab_id: str,
    slots: dict[str, QuerySlot],
) -> Text2SQLResult:
    limitations = [
        "현재 PostgreSQL에는 AutoSched report table(autosched_*)이 적재되어 있지 않습니다.",
        "General Data는 model input/master data라서 WIP, 설비 현재 상태, utilization을 추정하지 않습니다.",
    ]
    return Text2SQLResult(
        status="data_unavailable",
        query_type="status",
        answer=(
            f"{fab_id}의 live/current operational status는 아직 조회할 수 없습니다. "
            "AutoSched report 적재 후 활성화해야 합니다."
        ),
        confidence=0.8,
        limitations=limitations,
        plan=QueryPlan(
            query_type="status",
            template_id=None,
            fab_id=fab_id,
            data_source_type="operational_report",
            slots=slots,
            limitations=limitations,
        ),
    )


def _simulation_data_unavailable(
    result: Text2SQLResult,
    *,
    error: str | None = None,
) -> Text2SQLResult:
    plan = result.plan
    fab_id = plan.fab_id if plan else None
    slots = plan.slots if plan else {}
    area = _slot_value(slots, "area")
    requested = f"{fab_id or '요청 FAB'}"
    if area:
        requested += f" area={area!r}"
    suggestions, diagnostics = _simulation_availability_suggestions(fab_id, area)
    condition = "최근 24시간" if plan and plan.template_id == "deterministic_simulation_snapshot_queue_trend" else "요청한 대상·기간·필터"
    reason = f"{requested}의 live_process_snapshots {condition} 조건에 맞는 행이 없습니다."
    if error and _looks_like_missing_relation_error(error):
        reason = f"{requested}의 live_process_snapshots 테이블을 찾을 수 없습니다."
    answer_parts = [reason]
    if suggestions:
        answer_parts.append("대신 확인할 수 있는 후보: " + " / ".join(suggestions))
    else:
        answer_parts.append(
            "같은 형식의 질문을 처리하려면 해당 FAB의 live_process_snapshots 적재 상태와 area 값을 먼저 확인해야 합니다."
        )
    limitations = [
        *result.limitations,
        reason,
        *diagnostics,
    ]
    if error and not _looks_like_missing_relation_error(error):
        limitations.append(error)
    return Text2SQLResult(
        status="data_unavailable",
        query_type=result.query_type,
        answer=" ".join(answer_parts),
        sql=result.sql,
        confidence=0.85,
        limitations=list(dict.fromkeys(limitations)),
        plan=plan,
    )


def _simulation_availability_suggestions(
    fab_id: str | None,
    area: str | None,
) -> tuple[list[str], list[str]]:
    suggestions: list[str] = []
    diagnostics: list[str] = []
    if not fab_id:
        return suggestions, diagnostics
    try:
        executor = ReadOnlyQueryExecutor()
        current_areas = executor.execute(
            "SELECT area, COUNT(*) AS rows "
            f"FROM {table_ref(fab_id, 'live_process_snapshots')} "
            "GROUP BY area ORDER BY area",
            limit=200,
        ).rows
    except (RuntimeError, SqlValidationError, PsycopgError):
        current_areas = []
    if current_areas:
        area_labels = ", ".join(
            f"{row['area']}({row['rows']}행)" for row in current_areas[:8]
        )
        diagnostics.append(f"{fab_id}에서 사용 가능한 simulation area: {area_labels}")
        suggestions.append(f"{fab_id}의 다른 area({', '.join(str(row['area']) for row in current_areas[:6])})")
    if area:
        available_fabs = []
        for candidate_fab in sorted(ALLOWED_FABS):
            try:
                rows = executor.execute(
                    "SELECT COUNT(*) AS rows "
                    f"FROM {table_ref(candidate_fab, 'live_process_snapshots')} "
                    f"WHERE area = {_sql_literal(area)}",
                    limit=1,
                ).rows
            except (RuntimeError, SqlValidationError, PsycopgError, UnboundLocalError):
                continue
            count = int(rows[0]["rows"]) if rows else 0
            if count:
                available_fabs.append(f"{candidate_fab}({count}행)")
        if available_fabs:
            diagnostics.append(
                f"area={area!r} 데이터가 있는 FAB: {', '.join(available_fabs)}"
            )
            if not any(item.startswith(f"{fab_id}(") for item in available_fabs):
                suggestions.append(f"같은 area={area!r}가 있는 FAB({', '.join(available_fabs)})")
    return suggestions, diagnostics


def _clarification(
    *,
    query_type: QueryType,
    answer: str,
    slots: dict[str, QuerySlot],
    fab_id: str | None = None,
    data_source_type: Literal["operational_report", "model_master", "release_plan"] | None = None,
) -> Text2SQLResult:
    return Text2SQLResult(
        status="needs_clarification",
        query_type=query_type,
        answer=answer,
        confidence=0.5,
        limitations=["필수 slot이 부족해서 SQL을 생성하지 않았습니다."],
        plan=QueryPlan(
            query_type=query_type,
            template_id=None,
            fab_id=fab_id,
            data_source_type=data_source_type,
            slots=slots,
        ),
    )


def _summarize_execution(planned: Text2SQLResult, rows: list[dict[str, Any]]) -> str:
    if rows and rows[0].get("fab"):
        from app.sub_agent.fab_comparison import comparison_summary, comparison_trend_summary
        from app.sub_agent.snapshot_queries import SLOT_METRICS
        selected = planned.plan.slots.get("metrics") if planned.plan else None
        metrics = {SLOT_METRICS.get(value, value) for value in selected.value.split(",")} if selected else None
        if summary := comparison_summary(rows, metrics=metrics) or comparison_trend_summary(rows):
            return summary
    if not rows:
        return "조회는 성공했지만 조건에 맞는 행이 없습니다."
    if planned.plan and planned.plan.data_source_type == "simulation_snapshot":
        return f"공정 데이터에서 {len(rows)}개 행을 조회했습니다."
    if planned.query_type == "master_data_lookup":
        return f"General Data 기준으로 {len(rows)}개 행을 조회했습니다."
    if planned.query_type == "release_plan_lookup":
        return f"Release plan 기준으로 {len(rows)}개 행을 조회했습니다."
    if planned.query_type == "trend":
        total = sum(int(row.get("lot_count", 0)) for row in rows if "lot_count" in row)
        if total:
            return (
                "Release plan 추세 기준으로 lotrelease를 집계했습니다. "
                f"총 {total}건이며 날짜 포인트는 {len(rows)}개입니다."
            )
        if planned.plan and planned.plan.data_source_type == "operational_report":
            return f"AutoSched report 추세 기준으로 {len(rows)}개 행을 조회했습니다."
        return f"Release plan 추세 기준으로 {len(rows)}개 행을 조회했습니다."
    if planned.query_type == "status":
        return f"AutoSched report 기준으로 {len(rows)}개 상태 행을 조회했습니다."
    return f"{len(rows)}개 행을 조회했습니다."


def _empty_result_limitations(result: Text2SQLResult) -> list[str]:
    if not result.plan or "relative_period" not in result.plan.slots:
        return []
    start = _slot_value(result.plan.slots, "date_start")
    end = _slot_value(result.plan.slots, "date_end")
    if start and end:
        return [
            (
                f"상대 기간은 현재 KST 기준 {start} 이상 {end} 미만으로 해석했습니다. "
                "적재된 스냅샷의 날짜 범위가 다르면 결과가 0건일 수 있습니다."
            )
        ]
    return ["상대 기간은 현재 KST 날짜를 기준으로 계산했으며 적재 스냅샷 범위와 다를 수 있습니다."]


def _is_missing_autosched_table_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "autosched_" in message and ("does not exist" in message or "undefinedtable" in message)


def _normalize_chart_intent(
    chart_intent: Any,
    slots: dict[str, QuerySlot],
    *,
    aggregation: str | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(chart_intent, dict):
        return None, []

    normalized = dict(chart_intent)
    y = normalized.get("y")
    if isinstance(y, str) and "," in y:
        normalized["y"] = [item.strip() for item in y.split(",") if item.strip()]

    products = [item for item in (_slot_value(slots, "products") or "").split(",") if item]
    periods = [item for item in (_slot_value(slots, "periods") or "").split(",") if item]
    y_fields = normalized.get("y")
    comparison_axis = "part" if len(products) > 1 else "period" if len(periods) > 1 else None
    if comparison_axis and isinstance(y_fields, list) and len(y_fields) > 1:
        changed = (
            normalized.get("type") != "grouped_bar"
            or normalized.get("x") != comparison_axis
        )
        normalized["type"] = "grouped_bar"
        normalized["x"] = comparison_axis
        normalized["series"] = None
        if changed:
            return _enrich_chart_intent(
                normalized, slots, aggregation=aggregation
            ), [
                "복수 대상·복수 지표 비교를 grouped_bar 차트로 정규화했습니다."
            ]
    return _enrich_chart_intent(normalized, slots, aggregation=aggregation), []


def _enrich_chart_intent(
    chart_intent: dict[str, Any] | None,
    slots: dict[str, QuerySlot],
    *,
    aggregation: str | None = None,
) -> dict[str, Any] | None:
    if not chart_intent or chart_intent.get("type") != "line":
        return chart_intent

    enriched = dict(chart_intent)
    x_field = str(enriched.get("x") or "")
    grain = _slot_value(slots, "date_grain")
    if grain not in {"day", "week", "month"}:
        grain = (
            "month"
            if x_field.endswith("_month")
            else "week"
            if x_field.endswith("_week")
            else "day"
            if x_field.endswith("_date")
            else None
        )
    if grain:
        enriched["grain"] = grain
    enriched["missing_policy"] = (
        "zero" if str(aggregation or "").casefold() == "count" else "gap"
    )
    if range_start := _slot_value(slots, "date_start"):
        enriched["range_start"] = range_start
    if range_end := _slot_value(slots, "date_end"):
        enriched["range_end_exclusive"] = range_end
    return enriched


def _product_to_part_alias(product: str) -> str | None:
    match = re.search(r"([eE]?\d+)$", product)
    if not match:
        return None
    return f"part_{match.group(1).lower()}"


def _toolgroup_to_type_prefix(toolgroup: str) -> str | None:
    match = re.match(r"([A-Za-z]+_[A-Za-z]+)(?:_\d+)?", toolgroup)
    return match.group(1) if match else None


def extract_query_slots(
    question: str,
    *,
    fab: str | None = None,
    process: str | None = None,
    product: str | None = None,
    route: str | None = None,
    equipment: str | None = None,
    date_basis: str | None = None,
    metric: str | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
) -> dict[str, QuerySlot]:
    """Extract shared FAB scope without an LLM call, schema lookup, or SQL execution."""
    slots = _extract_slots(
        question, _normalize_question(question), fab=fab, process=process,
        product=product, route=route, equipment=equipment, date_basis=date_basis, metric=metric,
    )
    slots = inherit_followup_metrics(question, slots, conversation_history or [])
    slots = inherit_followup_period(question, slots, conversation_history or [])
    slots = inherit_followup_areas(question, slots, conversation_history or [])
    return inherit_result_areas(question, slots, conversation_history or [])


PERIOD_SCOPE_KEYS = {"date_start", "date_end", "relative_period", "date_grain", "comparison_date_ranges", "periods"}


def inherit_result_areas(question, slots, history):
    """Resolve 'that process' only from complete, typed SQL result dimensions."""
    reference = re.search(r"(?:그|해당|이)\s*(?:(?:두|세)\s*)?공정(?:들)?|those\s+processes|that\s+process", question, re.IGNORECASE)
    if not reference or simulation_areas(question) or _parse_area(_normalize_question(question)):
        return slots
    result = None
    for turn in reversed(history):
        if turn.get("role") != "assistant":
            continue
        metadata = turn.get("metadata") or {}
        result = metadata.get("query_result_scope")
        if result or metadata.get("status") != "needs_clarification":
            break
    if not isinstance(result, dict) or result.get("source_type") != "text2sql_result" or result.get("status") != "succeeded" or result.get("complete") is not True:
        return slots
    fab = resolve_fab(question, _slot_value(slots, "fab_id"), history).fab_id
    areas = result.get("areas")
    if (result.get("fab") != fab or not isinstance(areas, list) or not areas
            or any(not isinstance(area, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", area) for area in areas)):
        return slots
    merged = {key:value for key,value in slots.items() if key not in {"area", "areas", "process"}}
    plural = bool(re.search(r"공정들|(?:두|세)\s*공정|those\s+processes", reference[0], re.IGNORECASE))
    stated_count = 2 if "두" in reference[0] else 3 if "세" in reference[0] else None
    if (len(areas) > 1 and not plural) or (stated_count and stated_count != len(areas)):
        merged["unresolved_area_reference"] = QuerySlot(",".join(areas), "tool_result_context", 1.0, reference[0])
    else:
        key = "area" if len(areas) == 1 else "areas"
        merged[key] = QuerySlot(",".join(areas), "tool_result_context", 1.0, reference[0])
    return merged


def inherit_followup_metrics(question, slots, history):
    """Keep the latest user-requested metric list when a follow-up refers to it."""
    if (_parse_metrics(_normalize_question(question))
            or re.search(r"다른\s*지표|새로운\s*지표", question)
            or not re.search(r"그\s*(?:두|지표|중|기간)|이\s*지표|같은|동일|그럼|그러면|이어서|그대로|추세도|추이도|those\s+metrics|same\s+metrics", question, re.IGNORECASE)):
        return slots
    prior = next((turn for turn in reversed(history) if turn.get("role") == "user"), None)
    if not prior:
        return slots
    metrics = (prior.get("metadata") or {}).get("query_metrics")
    if metrics is None:
        metrics = _parse_metrics(_normalize_question(str(prior.get("content", ""))))
    if not isinstance(metrics, list) or not metrics or any(not isinstance(metric, str) or metric not in set(METRIC_ALIASES.values()) for metric in metrics):
        return slots
    merged = dict(slots)
    merged["metrics"] = QuerySlot(",".join(dict.fromkeys(metrics)), "conversation_context", 0.9, str(prior.get("content", "")))
    merged["metric"] = QuerySlot(metrics[0], "conversation_context", 0.9, str(prior.get("content", "")))
    grouped = (prior.get("metadata") or {}).get("query_group_by_area")
    if grouped is None:
        grouped = bool(re.search(r"공정별|영역별|각\s*공정|by\s+area|per\s+area", str(prior.get("content", "")), re.IGNORECASE))
    if grouped and not re.search(r"전체|\bwhole\b", question, re.IGNORECASE):
        merged["group_by_area"] = QuerySlot("true", "conversation_context", 0.9, str(prior.get("content", "")))
    if not re.search(r"평균|average|\bmean\b|합계|누적|총합|\btotal\b|\bsum\b", question, re.IGNORECASE):
        saved = (prior.get("metadata") or {}).get("query_aggregations")
        if saved is None:
            saved = _flow_aggregation_contract(str(prior.get("content", "")), metrics)
        if isinstance(saved, dict) and saved and all(key in METRICS and isinstance(value, str) and value in {"AVG", "SUM"} for key, value in saved.items()):
            merged["metric_aggregations"] = QuerySlot(json.dumps(saved), "conversation_context", 0.9, str(prior.get("content", "")))
    return merged


def _flow_aggregation_contract(question: str, metrics: list[str]) -> dict[str, str]:
    if not re.search(r"평균|average|\bmean\b|합계|누적|총합|\btotal\b|\bsum\b", question, re.IGNORECASE):
        return {}
    canonical = list(dict.fromkeys(SLOT_METRICS.get(metric, metric) for metric in metrics))
    if any(metric not in METRICS for metric in canonical):
        return {}
    contract = period_aggregates(question.casefold(), canonical) or {}
    return {metric:operator for metric, operator in contract.items() if METRICS[metric][1] == "flow"}


def inherit_followup_areas(question, slots, history):
    """Preserve an explicit multi-area comparison through a referential follow-up."""
    if (simulation_areas(question) or _parse_area(_normalize_question(question))
            or re.search(r"전체|모든|공정별|영역별|\ball\b|by area", question, re.IGNORECASE)
            or not re.search(r"그\s*(?:두|공정|중)|그럼|그러면|같은|동일|이어서|same|those", question, re.IGNORECASE)):
        return slots
    prior = next((turn for turn in reversed(history) if turn.get("role") == "user"), None)
    if not prior:
        return slots
    areas = (prior.get("metadata") or {}).get("query_areas")
    if areas is None:
        areas = simulation_areas(str(prior.get("content", "")))
    if not isinstance(areas, list) or len(areas) < 2 or any(area not in {"cmp", "deposition", "etch", "implant", "metrology", "photo"} for area in areas):
        return slots
    merged = {key:value for key,value in slots.items() if key not in {"area", "process"}}
    merged["areas"] = QuerySlot(",".join(areas), "conversation_context", 0.9, str(prior.get("content", "")))
    return merged


def inherit_followup_period(question, slots, history):
    """Resolve follow-up time scope from the latest USER turn, never answer text."""
    referential_diagnosis = bool(
        re.search(r"왜|원인|이유|진단", question)
        and re.search(r"(?:그|같은|동일)\s*공정", question)
    )
    if not referential_diagnosis and not re.search(r"그\s*중|그\s*기간|같은\s*(?:기간|조건)|동일\s*(?:기간|조건)|그럼|그러면|이어서|다시|그대로", question):
        return slots
    if any(key in slots for key in ("date_start", "relative_period", "periods")) or re.search(r"지금|현재|오늘|today|now", question, re.IGNORECASE):
        return slots
    prior = next((turn for turn in reversed(history) if turn.get("role") == "user"), None)
    if not prior:
        return slots
    saved = (prior.get("metadata") or {}).get("query_period")
    if saved is None:
        saved = {key:slot.value for key, slot in extract_query_slots(str(prior.get("content", ""))).items() if key in PERIOD_SCOPE_KEYS}
    if not isinstance(saved, dict):
        return slots
    merged = dict(slots)
    for key, value in saved.items():
        if key in PERIOD_SCOPE_KEYS and isinstance(value, str):
            merged.setdefault(key, QuerySlot(value, "conversation_context", 0.9, str(prior.get("content", ""))))
    return merged


def _extract_slots(
    question: str,
    normalized_question: str,
    *,
    fab: str | None,
    process: str | None,
    product: str | None,
    route: str | None,
    equipment: str | None,
    date_basis: str | None,
    metric: str | None,
) -> dict[str, QuerySlot]:
    slots: dict[str, QuerySlot] = {}
    if re.search(r"공정별|영역별|각\s*공정|by\s+area|per\s+area", question, re.IGNORECASE):
        slots["group_by_area"] = QuerySlot("true", "parser", 0.9, question)

    context_fab = _normalize_fab(fab) if fab else None
    if context_fab:
        slots["fab_id"] = QuerySlot(context_fab, "request_context", 0.9, fab or context_fab)

    if process:
        context_area = _parse_area(_normalize_question(process))
        if not context_area:
            context_areas = simulation_areas(process)
            context_area = context_areas[0] if len(context_areas) == 1 else None
        context_toolgroup = _parse_toolgroup(process)
        if context_area:
            slots["area"] = QuerySlot(context_area, "request_context", 0.9, process)
        elif context_toolgroup:
            slots["toolgroup"] = QuerySlot(context_toolgroup, "request_context", 0.9, process)

    context_product = _parse_product(product or "")
    if context_product:
        slots["product"] = QuerySlot(context_product, "request_context", 0.9, product or "")
        slots["route"] = QuerySlot(
            f"Route_{context_product}", "request_context", 0.85, product or ""
        )
    context_route = _parse_route(route or "")
    if context_route:
        slots["route"] = QuerySlot(context_route, "request_context", 0.9, route or "")
        slots["product"] = QuerySlot(
            context_route.removeprefix("Route_"), "request_context", 0.85, route or ""
        )
    context_equipment = _parse_toolgroup(equipment or "")
    if context_equipment:
        slots["toolgroup"] = QuerySlot(
            context_equipment, "request_context", 0.9, equipment or ""
        )
    if date_basis in {"start_date", "due_date"}:
        slots["date_basis"] = QuerySlot(date_basis, "request_context", 0.9, date_basis)

    parsed_fab = _parse_fab(question)
    if parsed_fab:
        slots["fab_id"] = QuerySlot(parsed_fab, "explicit_user", 1.0, parsed_fab)

    products = _parse_products(question)
    if context_product and _looks_like_compare(normalized_question):
        products = list(dict.fromkeys([context_product, *products]))
    if products:
        product = products[0]
        slots["product"] = QuerySlot(product, "parser", 0.9, product)
        slots["products"] = QuerySlot(",".join(products), "parser", 0.9, ", ".join(products))
        slots["route"] = QuerySlot(f"Route_{product}", "parser", 0.8, product)

    periods = _parse_periods(question)
    if periods:
        slots["periods"] = QuerySlot(",".join(periods), "parser", 0.95, ", ".join(periods))

    route = _parse_route(question)
    if route:
        slots["route"] = QuerySlot(route, "parser", 0.9, route)
        slots["product"] = QuerySlot(route.removeprefix("Route_"), "parser", 0.8, route)

    toolgroup = None
    toolgroups = _parse_toolgroups(question)
    if toolgroups:
        toolgroup = toolgroups[0]
        slots["toolgroup"] = QuerySlot(toolgroup, "parser", 0.95, toolgroup)
        slots["toolgroups"] = QuerySlot(
            ",".join(toolgroups), "parser", 0.95, ", ".join(toolgroups)
        )

    type_prefix = _parse_type_prefix(question)
    if type_prefix and not toolgroup:
        slots["type_prefix"] = QuerySlot(type_prefix, "parser", 0.85, type_prefix)

    lot_id = _parse_lot(question)
    if lot_id:
        slots["lot_id"] = QuerySlot(lot_id, "parser", 0.9, lot_id)

    area = (
        _parse_simulation_area(normalized_question)
        if _is_explicit_simulation_request(normalized_question)
        else _parse_area(normalized_question)
    )
    if not area:
        detected_areas = simulation_areas(normalized_question)
        if len(detected_areas) == 1:
            area = detected_areas[0]
    if area:
        slots["area"] = QuerySlot(area, "alias_match", 0.85, area)
    detected_areas = simulation_areas(normalized_question)
    if len(detected_areas) > 1:
        slots.pop("area", None)
        slots["areas"] = QuerySlot(",".join(detected_areas), "explicit_user", 1.0, question)

    release_scenario = _parse_release_scenario(question)
    if release_scenario:
        slots["release_scenario"] = QuerySlot(
            release_scenario, "parser", 0.75, release_scenario
        )
    if _contains_any(normalized_question, RELEASE_TERMS):
        slots["release_table"] = QuerySlot("lotrelease", "parser", 0.85, "lotrelease")

    date_basis = _parse_date_basis(normalized_question)
    if date_basis:
        slots["date_basis"] = QuerySlot(date_basis, "parser", 0.9, date_basis)

    date_grain = _parse_date_grain(normalized_question)
    if date_grain:
        slots["date_grain"] = QuerySlot(date_grain, "parser", 0.85, date_grain)

    comparison_ranges = _parse_comparison_date_ranges(question, normalized_question)
    explicit_range = None if comparison_ranges else _parse_explicit_date_range(question)
    calendar_period = _parse_calendar_period(question, normalized_question)
    relative_period = None
    if comparison_ranges:
        serialized = "|".join(f"{start.isoformat()}/{end.isoformat()}" for start, end in comparison_ranges)
        slots["relative_period"] = QuerySlot(
            "explicit_period_comparison", "explicit_user", 1.0, question
        )
        slots["comparison_date_ranges"] = QuerySlot(
            serialized, "explicit_user", 1.0, question
        )
        slots["date_start"] = QuerySlot(
            comparison_ranges[0][0].isoformat(), "explicit_user", 1.0, question
        )
        slots["date_end"] = QuerySlot(
            comparison_ranges[-1][1].isoformat(), "explicit_user", 1.0, question
        )
    elif explicit_range or calendar_period:
        start, end, period_name = (
            (*explicit_range, "explicit_range") if explicit_range else calendar_period
        )
        source = "explicit_user" if explicit_range or period_name.startswith(("month_", "quarter_")) else "parser"
        slots["relative_period"] = QuerySlot(period_name, source, 1.0, question)
        slots["date_start"] = QuerySlot(start.isoformat(), source, 1.0, question)
        slots["date_end"] = QuerySlot(end.isoformat(), source, 1.0, question)
        if "date_grain" not in slots and (
            period_name.startswith(("quarter_", "month_range_"))
            or period_name in {"last_quarter", "this_quarter"}
        ):
            slots["date_grain"] = QuerySlot("month", "parser", 0.9, period_name)
    else:
        relative_period = _parse_relative_period(normalized_question)
    if not comparison_ranges and not explicit_range and not calendar_period and relative_period:
        slots["relative_period"] = QuerySlot(relative_period, "parser", 0.8, relative_period)
        start, end = _relative_period_bounds(relative_period)
        if start and end:
            slots["date_start"] = QuerySlot(start.isoformat(), "parser", 0.8, relative_period)
            slots["date_end"] = QuerySlot(end.isoformat(), "parser", 0.8, relative_period)

    parsed_metric = _parse_metric(normalized_question)
    resolved_metric = parsed_metric or metric
    if resolved_metric:
        source = "alias_match" if parsed_metric else "request_context"
        confidence = 0.8 if parsed_metric else 0.9
        slots["metric"] = QuerySlot(resolved_metric, source, confidence, resolved_metric)
    metrics = _parse_metrics(normalized_question)
    if not metrics and resolved_metric:
        metrics = [resolved_metric]
    if metrics:
        slots["metrics"] = QuerySlot(",".join(metrics), "alias_match", 0.85, ", ".join(metrics))
        if aggregates := _flow_aggregation_contract(question, metrics):
            slots["metric_aggregations"] = QuerySlot(json.dumps(aggregates), "parser", 0.9, question)

    threshold = _parse_metric_threshold(normalized_question)
    if not threshold and resolved_metric:
        bare_threshold = _parse_bare_threshold(normalized_question)
        if bare_threshold:
            operator, value, unit = bare_threshold
            threshold = resolved_metric, operator, value, unit
    if threshold:
        threshold_metric, threshold_operator, threshold_value, threshold_unit = threshold
        slots["threshold_metric"] = QuerySlot(
            threshold_metric, "parser", 0.95, threshold_metric
        )
        slots["threshold_operator"] = QuerySlot(
            threshold_operator, "parser", 0.95, threshold_operator
        )
        slots["threshold_value"] = QuerySlot(
            threshold_value, "explicit_user", 1.0, threshold_value
        )
        if threshold_unit:
            slots["threshold_unit"] = QuerySlot(
                threshold_unit, "explicit_user", 1.0, threshold_unit
            )
    ranking = _parse_top_n(normalized_question)
    if ranking:
        direction, count = ranking
        slots["ranking_direction"] = QuerySlot(direction, "parser", 0.95, direction)
        slots["top_n"] = QuerySlot(str(count), "explicit_user", 1.0, str(count))
    elif direction := _parse_ranking_direction(normalized_question):
        slots["ranking_direction"] = QuerySlot(direction, "parser", 0.9, direction)

    master_domain = _parse_master_domain(normalized_question)
    if master_domain:
        slots["master_domain"] = QuerySlot(master_domain, "parser", 0.8, master_domain)

    return slots


def _parse_metric_threshold(
    normalized_question: str,
) -> tuple[str, str, str, str | None] | None:
    operator_map = {"이상": ">=", "이하": "<=", "초과": ">", "미만": "<"}
    english_operator_map = {
        "at least": ">=",
        "at most": "<=",
        "above": ">",
        "over": ">",
        "below": "<",
        "under": "<",
    }
    for alias, metric in sorted(METRIC_ALIASES.items(), key=lambda item: -len(item[0])):
        symbolic_match = re.search(
            rf"{re.escape(alias)}\s*(>=|<=|>|<)\s*(\d+(?:\.\d+)?)\s*"
            r"(%|퍼센트|percent)?",
            normalized_question,
        )
        if symbolic_match:
            value = str(float(symbolic_match.group(2))).removesuffix(".0")
            unit = "percent" if symbolic_match.group(3) else None
            return metric, symbolic_match.group(1), value, unit
        english_match = re.search(
            rf"{re.escape(alias)}\s*(at least|at most|above|over|below|under)\s*"
            r"(\d+(?:\.\d+)?)\s*(%|percent)?",
            normalized_question,
        )
        if english_match:
            value = str(float(english_match.group(2))).removesuffix(".0")
            unit = "percent" if english_match.group(3) else None
            return metric, english_operator_map[english_match.group(1)], value, unit
        match = re.search(
            rf"{re.escape(alias)}\s*(?:이|가|은|는)?\s*"
            r"(\d+(?:\.\d+)?)\s*(%|퍼센트|percent)?\s*(이상|이하|초과|미만)",
            normalized_question,
        )
        if match:
            value = str(float(match.group(1))).removesuffix(".0")
            unit = "percent" if match.group(2) else None
            return metric, operator_map[match.group(3)], value, unit
    return None


def _parse_bare_threshold(
    normalized_question: str,
) -> tuple[str, str, str | None] | None:
    symbolic = re.search(
        r"(>=|<=|>|<)\s*(\d+(?:\.\d+)?)\s*(%|퍼센트|percent)?",
        normalized_question,
    )
    if symbolic:
        value = str(float(symbolic.group(2))).removesuffix(".0")
        unit = "percent" if symbolic.group(3) else None
        return symbolic.group(1), value, unit

    korean = re.search(
        r"(\d+(?:\.\d+)?)\s*(%|퍼센트|percent)?\s*(이상|이하|초과|미만)",
        normalized_question,
    )
    if korean:
        operators = {"이상": ">=", "이하": "<=", "초과": ">", "미만": "<"}
        value = str(float(korean.group(1))).removesuffix(".0")
        unit = "percent" if korean.group(2) else None
        return operators[korean.group(3)], value, unit
    return None


def _parse_top_n(normalized_question: str) -> tuple[str, int] | None:
    patterns = (
        (r"(?:상위|top)\s*(\d+)\s*(?:개|건)?", "DESC"),
        (r"(?:하위|bottom)\s*(\d+)\s*(?:개|건)?", "ASC"),
    )
    for pattern, direction in patterns:
        if match := re.search(pattern, normalized_question):
            count = int(match.group(1))
            if 1 <= count <= 200:
                return direction, count
    return None


def _parse_requested_top_n(normalized_question: str) -> int | None:
    match = re.search(r"(?:상위|하위|top|bottom)\s*(\d+)\s*(?:개|건)?", normalized_question)
    return int(match.group(1)) if match else None


def _parse_ranking_direction(normalized_question: str) -> str | None:
    if re.search(r"(?:높은|큰)\s*순|내림차순|상위", normalized_question):
        return "DESC"
    if re.search(r"(?:낮은|작은)\s*순|오름차순|하위", normalized_question):
        return "ASC"
    return None


def is_explicit_master_lookup(question: str) -> bool:
    normalized = _normalize_question(question)
    if any(term in normalized for term in (
        "왜", "원인", "진단", "사례", "영향", "병목", "설명", "정의", "차트", "그래프",
        "why", "cause", "diagnos", "impact", "explain", "chart", "graph",
    )):
        return False
    return (
        _classify_query_type(normalized) == "master_data_lookup"
        and any(term in normalized for term in ("목록", "리스트", "구성", "list", "설비 대수", "장비 대수", "설비대수", "장비대수", "number_of_tools"))
    )


def _classify_query_type(normalized_question: str) -> QueryType:
    if _contains_any(normalized_question, TREND_TERMS) or _looks_like_compare(normalized_question):
        return "trend"
    if _contains_any(normalized_question, RELEASE_TERMS):
        return "release_plan_lookup"
    if _contains_any(normalized_question, ROUTE_TERMS | TOOLGROUP_TERMS | PM_BREAKDOWN_TERMS):
        if _looks_like_operational_status(normalized_question):
            return "status"
        return "master_data_lookup"
    if _contains_any(normalized_question, STATUS_TERMS) or _parse_metrics(normalized_question):
        return "status"
    return "unsupported"


def _looks_like_master_lookup(normalized_question: str) -> bool:
    return any(term in normalized_question for term in ("어떤", "목록", "list", "구성", "보여", "조회"))


def _looks_like_compare(normalized_question: str) -> bool:
    if "비교" in normalized_question or "compare" in normalized_question or "versus" in normalized_question:
        return True
    return " vs " in f" {normalized_question} "


def _looks_like_operational_status(normalized_question: str) -> bool:
    has_metric = _parse_metric(normalized_question) is not None
    has_policy_lookup = _contains_any(normalized_question, POLICY_LOOKUP_TERMS)
    if has_metric and not has_policy_lookup:
        return True
    if has_metric and _contains_any(normalized_question, OPERATIONAL_STATUS_HINTS):
        return True
    return _contains_any(normalized_question, STATUS_TERMS) and not (
        has_policy_lookup and _contains_any(normalized_question, PM_BREAKDOWN_TERMS)
    )


def _has_selective_release_constraint(slots: dict[str, QuerySlot]) -> bool:
    return any(
        key in slots
        for key in ("product", "route", "release_scenario", "date_start", "date_end")
    )


def _slot_value(slots: dict[str, QuerySlot], key: str) -> str | None:
    slot = slots.get(key)
    return slot.value if slot else None


def _normalize_question(question: str) -> str:
    return question.casefold().replace("-", "_")


def _contains_any(value: str, terms: set[str]) -> bool:
    return any(term in value for term in terms)


def _normalize_fab(value: str | None) -> str | None:
    return normalize_fab(value)


def _parse_fab(question: str) -> str | None:
    return resolve_fab(question).fab_id


def _parse_product(question: str) -> str | None:
    products = _parse_products(question)
    return products[0] if products else None


def _parse_products(question: str) -> list[str]:
    return list(
        dict.fromkeys(
            f"Product_{match.group(1).lower()}" for match in PRODUCT_PATTERN.finditer(question)
        )
    )


def _parse_periods(question: str) -> list[str]:
    return list(
        dict.fromkeys(f"Period_{match.group(1)}" for match in PERIOD_PATTERN.finditer(question))
    )


def _parse_type_prefix(question: str) -> str | None:
    match = TYPE_PREFIX_PATTERN.search(question)
    return f"{match.group(1)}_{match.group(2)}" if match else None


def _parse_route(question: str) -> str | None:
    match = ROUTE_PATTERN.search(question)
    if not match:
        return None
    return f"Route_Product_{match.group(1).lower()}"


def _parse_toolgroup(question: str) -> str | None:
    toolgroups = _parse_toolgroups(question)
    return toolgroups[0] if toolgroups else None


def _parse_toolgroups(question: str) -> list[str]:
    return list(
        dict.fromkeys(
            re.sub(r"[\s-]+", "_", match.group(1))
            for match in TOOLGROUP_PATTERN.finditer(question)
        )
    )


def _parse_lot(question: str) -> str | None:
    match = LOT_PATTERN.search(question)
    return match.group(0) if match else None


def _parse_area(normalized_question: str) -> str | None:
    normalized = normalized_question.replace("-", "_")
    for alias, canonical in AREA_ALIASES.items():
        if alias in normalized:
            return canonical
    return None


def _parse_simulation_area(normalized_question: str) -> str | None:
    normalized = normalized_question.replace("-", "_")
    for alias, canonical in SIMULATION_AREA_ALIASES.items():
        if re.search(rf"(?<![a-z0-9_]){re.escape(alias)}(?![a-z0-9_])", normalized):
            return canonical
    return _parse_area(normalized_question)


def _parse_release_scenario(question: str) -> str | None:
    lowered = question.casefold()
    known = [
        "variable due dates",
        "high service level",
        "1000 wspw per product",
        "8 years",
    ]
    for scenario in known:
        if scenario in lowered:
            return scenario
    return None


def _parse_date_basis(normalized_question: str) -> str | None:
    normalized = normalized_question.replace("-", "_")
    for alias, column in DATE_BASIS_ALIASES.items():
        if alias in normalized:
            return column
    return None


def _parse_date_grain(normalized_question: str) -> str | None:
    for alias, grain in DATE_GRAIN_TERMS.items():
        if alias in normalized_question:
            return grain
    return None


def _parse_relative_period(normalized_question: str) -> str | None:
    if match := re.search(r"(?:최근|지난|last|past)\s*(\d+)\s*(?:시간|hours?)", normalized_question):
        return f"last_{match.group(1)}_hours"
    if "그저께" in normalized_question or "day before yesterday" in normalized_question:
        return "day_before_yesterday"
    if "어제" in normalized_question or "yesterday" in normalized_question:
        return "yesterday"
    if "오늘" in normalized_question or "today" in normalized_question:
        return "today"
    if re.search(r"지난\s*주", normalized_question) or "last week" in normalized_question:
        return "last_week"
    if re.search(r"이번\s*주", normalized_question) or "this week" in normalized_question:
        return "this_week"
    if re.search(r"(?:최근|지난)\s*(?:1\s*)?일주일", normalized_question):
        return "last_7_days"
    if match := re.search(r"(?:최근|지난|last|past)\s*(\d+)\s*(?:일|days?)", normalized_question):
        return f"last_{match.group(1)}_days"
    if "최근" in normalized_question or "recent" in normalized_question or "latest" in normalized_question:
        return "recent"
    return None


def _relative_period_bounds(relative_period: str) -> tuple[date | None, date | None]:
    today = datetime.now(tz=ZoneInfo("Asia/Seoul")).date()
    monday = today - timedelta(days=today.weekday())
    if relative_period in {"today", "yesterday", "day_before_yesterday"}:
        offset = {"today":0, "yesterday":1, "day_before_yesterday":2}[relative_period]
        start = today - timedelta(days=offset)
        return start, start + timedelta(days=1)
    if relative_period == "last_week":
        start = monday - timedelta(days=7)
        return start, monday
    if relative_period == "this_week":
        return monday, today + timedelta(days=1)
    if match := re.fullmatch(r"last_(\d+)_days", relative_period):
        days = int(match.group(1))
        if 1 <= days <= 366:
            return today - timedelta(days=days - 1), today + timedelta(days=1)
    return None, None


def _parse_explicit_date_range(question: str) -> tuple[date, date] | None:
    matches = re.findall(r"(?<!\d)(\d{4}[-/.]\d{2}[-/.]\d{2})(?!\d)", question)
    if not matches:
        return None
    try:
        if len(matches) == 1:
            start = date.fromisoformat(re.sub(r"[/.]", "-", matches[0]))
            return start, start + timedelta(days=1)
        start, inclusive_end = (
            date.fromisoformat(re.sub(r"[/.]", "-", value)) for value in matches[:2]
        )
    except ValueError:
        return None
    if start > inclusive_end:
        return None
    return start, inclusive_end + timedelta(days=1)


def _parse_comparison_date_ranges(
    question: str,
    normalized_question: str,
) -> list[tuple[date, date]]:
    if not _looks_like_compare(normalized_question):
        return []
    matches = re.findall(r"(?<!\d)(\d{4}[-/.]\d{2}[-/.]\d{2})(?!\d)", question)
    if len(matches) != 4:
        return comparison_ranges(normalized_question, {})
    try:
        values = [date.fromisoformat(re.sub(r"[/.]", "-", value)) for value in matches]
    except ValueError:
        return []
    ranges = [
        (values[0], values[1] + timedelta(days=1)),
        (values[2], values[3] + timedelta(days=1)),
    ]
    if any(start >= end for start, end in ranges):
        return []
    return ranges


def _comparison_ranges_from_slot(
    slots: dict[str, QuerySlot],
) -> list[tuple[str, str]]:
    serialized = _slot_value(slots, "comparison_date_ranges") or ""
    ranges = []
    for item in serialized.split("|"):
        if "/" not in item:
            continue
        start, end = item.split("/", 1)
        ranges.append((start, end))
    return ranges


def _has_invalid_explicit_date_range(question: str) -> bool:
    matches = re.findall(r"(?<!\d)(\d{4}[-/.]\d{2}[-/.]\d{2})(?!\d)", question)
    if not matches:
        return False
    if len(matches) > 2 and len(matches) != 4:
        return True
    try:
        values = [date.fromisoformat(re.sub(r"[/.]", "-", value)) for value in matches]
    except ValueError:
        return True
    return any(values[index] > values[index + 1] for index in range(0, len(values) - 1, 2))


def _has_overlapping_comparison_date_ranges(
    question: str,
    normalized_question: str,
) -> bool:
    ranges = _parse_comparison_date_ranges(question, normalized_question)
    return len(ranges) == 2 and ranges[0][1] > ranges[1][0]


def _has_invalid_calendar_period(question: str) -> bool:
    for number, unit in re.findall(r"(?:최근|지난|last|past)\s*(-?\d+)\s*(일|days?|시간|hours?)", question, flags=re.IGNORECASE):
        maximum = 24 * 366 if unit.casefold() in {"시간", "hour", "hours"} else 366
        if not 1 <= int(number) <= maximum:
            return True
    quarter_numbers = [
        *re.findall(r"(?<!\d)(\d+)\s*분기", question),
        *re.findall(r"\bq(\d+)\b", question, flags=re.IGNORECASE),
    ]
    if any(int(quarter) not in range(1, 5) for quarter in quarter_numbers):
        return True
    quarter_range = _parse_quarter_range(question)
    if quarter_range and quarter_range[0] > quarter_range[1]:
        return True
    months = re.findall(r"(\d{4})\s*년\s*(\d+)\s*월", question)
    if any(int(month) not in range(1, 13) for _, month in months):
        return True
    if len(months) >= 2:
        first = tuple(map(int, months[0]))
        last = tuple(map(int, months[-1]))
        return first > last
    return False


def _parse_calendar_period(
    question: str, normalized_question: str
) -> tuple[date, date, str] | None:
    quarter_range = _parse_quarter_range(question)
    if quarter_range:
        start, inclusive_end = quarter_range
        if start > inclusive_end:
            return None
        start_quarter = ((start.month - 1) // 3) + 1
        end_quarter = ((inclusive_end.month - 1) // 3) + 1
        period_name = (
            f"quarter_range_{start.year}_q{start_quarter}_"
            f"{inclusive_end.year}_q{end_quarter}"
        )
        return start, _add_months(inclusive_end, 3), period_name

    quarter_match = re.search(
        r"(?:(\d{4})\s*년?\s*([1-4])\s*분기|q([1-4])\s*(\d{4}))",
        question,
        flags=re.IGNORECASE,
    )
    if quarter_match:
        year = int(quarter_match.group(1) or quarter_match.group(4))
        quarter = int(quarter_match.group(2) or quarter_match.group(3))
        start = date(year, (quarter - 1) * 3 + 1, 1)
        return start, _add_months(start, 3), f"quarter_{year}_q{quarter}"

    month_matches = re.findall(r"(\d{4})\s*년\s*(1[0-2]|[1-9])\s*월", question)
    if month_matches:
        first_year, first_month = map(int, month_matches[0])
        last_year, last_month = map(int, month_matches[-1])
        start = date(first_year, first_month, 1)
        last_start = date(last_year, last_month, 1)
        if start > last_start:
            return None
        name = (
            f"month_{first_year}_{first_month:02d}"
            if len(month_matches) == 1
            else f"month_range_{first_year}_{first_month:02d}_{last_year}_{last_month:02d}"
        )
        return start, _add_months(last_start, 1), name

    today = datetime.now(tz=ZoneInfo("Asia/Seoul")).date()
    this_month = date(today.year, today.month, 1)
    this_quarter = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
    if (
        "지난 분기" in normalized_question
        or "지난분기" in normalized_question
        or "last quarter" in normalized_question
    ):
        start = _add_months(this_quarter, -3)
        return start, this_quarter, "last_quarter"
    if (
        "이번 분기" in normalized_question
        or "이번분기" in normalized_question
        or "this quarter" in normalized_question
    ):
        return this_quarter, _add_months(this_quarter, 3), "this_quarter"
    if "지난달" in normalized_question or "last month" in normalized_question:
        start = _add_months(this_month, -1)
        return start, this_month, "last_month"
    if "이번달" in normalized_question or "이번 달" in normalized_question or "this month" in normalized_question:
        return this_month, _add_months(this_month, 1), "this_month"
    return None


def _parse_quarter_range(question: str) -> tuple[date, date] | None:
    patterns = (
        re.compile(
            r"(\d{4})\s*년?\s*([1-9]\d*)\s*분기\s*(?:부터|에서|to|through|until|~|[-–—])\s*"
            r"(?:(\d{4})\s*년?\s*)?([1-9]\d*)\s*분기(?:까지)?",
            re.IGNORECASE,
        ),
        re.compile(
            r"q([1-9]\d*)\s*(\d{4})\s*(?:부터|to|through|until|~|[-–—])\s*"
            r"q([1-9]\d*)\s*(\d{4})(?:까지)?",
            re.IGNORECASE,
        ),
        re.compile(
            r"(\d{4})\s*q([1-9]\d*)\s*(?:부터|to|through|until|~|[-–—])\s*"
            r"(?:(\d{4})\s*)?q([1-9]\d*)(?:까지)?",
            re.IGNORECASE,
        ),
    )
    for index, pattern in enumerate(patterns):
        match = pattern.search(question)
        if not match:
            continue
        if index == 1:
            start_quarter, start_year, end_quarter, end_year = map(int, match.groups())
        else:
            start_year = int(match.group(1))
            start_quarter = int(match.group(2))
            end_year = int(match.group(3) or start_year)
            end_quarter = int(match.group(4))
        if start_quarter not in range(1, 5) or end_quarter not in range(1, 5):
            return None
        return (
            date(start_year, (start_quarter - 1) * 3 + 1, 1),
            date(end_year, (end_quarter - 1) * 3 + 1, 1),
        )
    return None


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    return date(month_index // 12, month_index % 12 + 1, 1)


def _date_bucket(
    column: str, grain: str | None, *, prefix: str = "report"
) -> tuple[str, str]:
    if grain == "month":
        return f"date_trunc('month', {column}::timestamp)::date", f"{prefix}_month"
    if grain == "week":
        return f"date_trunc('week', {column}::timestamp)::date", f"{prefix}_week"
    return f"{column}::date", f"{prefix}_date"


def _parse_metric(normalized_question: str) -> str | None:
    metrics = _parse_metrics(normalized_question)
    return metrics[0] if metrics else None


def _parse_metrics(normalized_question: str) -> list[str]:
    normalized = normalized_question.replace("-", " ")
    first_positions: dict[str, int] = {}
    for alias, column in METRIC_ALIASES.items():
        if alias == "가동률":
            match = re.search(r"(?<!비)가동률", normalized)
            position = match.start() if match else -1
        elif alias == "비가동":
            match = re.search(r"비가동(?!\s*시간)", normalized)
            position = match.start() if match else -1
        elif alias == "pm":
            match = re.search(r"(?<![a-z0-9_])pm(?![a-z0-9_]|\s*시간)", normalized)
            position = match.start() if match else -1
        elif alias == "down" or (len(alias) <= 2 and alias.isascii()):
            match = re.search(rf"(?<![a-z0-9_]){re.escape(alias)}(?![a-z0-9_])", normalized)
            position = match.start() if match else -1
        else:
            position = normalized.find(alias)
        if position >= 0:
            first_positions[column] = min(position, first_positions.get(column, position))
    return sorted(first_positions, key=first_positions.get)


def _validate_explicit_periods(sql: str, slots: dict[str, QuerySlot]) -> None:
    periods = [item for item in (_slot_value(slots, "periods") or "").split(",") if item]
    missing = [period for period in periods if period.casefold() not in sql.casefold()]
    if missing:
        raise ValueError(f"SQL omitted explicitly requested period values: {', '.join(missing)}")


def _is_unavailable_queue_metric_request(normalized_question: str) -> bool:
    queue_terms = ("queue time", "queue_time", "큐 타임", "큐타임", "큐 상태", "대기시간")
    return any(term in normalized_question for term in queue_terms)


def _looks_like_missing_relation_error(message: str) -> bool:
    normalized = message.casefold()
    return "does not exist" in normalized or "undefinedtable" in normalized


def _is_explicit_simulation_request(normalized_question: str) -> bool:
    return any(term in normalized_question for term in (
        "live_process_", "합성", "시뮬레이션", "simulation", "snapshot", "스냅샷",
    ))


def _parse_master_domain(normalized_question: str) -> str | None:
    if "breakdown" in normalized_question or "고장" in normalized_question or "장애" in normalized_question:
        return "breakdown"
    if re.search(r"(?<![a-z0-9_])pm(?:_fab\d+)?(?![a-z0-9_])", normalized_question):
        return "pm"
    if "setup" in normalized_question or "셋업" in normalized_question:
        return "setups"
    if "transport" in normalized_question or "이송" in normalized_question:
        return "transport"
    return None


def _needs_date_basis_clarification(
    query_type: QueryType,
    normalized_question: str,
    slots: dict[str, QuerySlot],
) -> bool:
    if query_type not in {"release_plan_lookup", "trend"}:
        return False
    if "date_basis" in slots:
        return False
    if not _contains_any(normalized_question, RELEASE_TERMS):
        return False
    return _contains_any(normalized_question, DATE_AMBIGUITY_TERMS | TREND_TERMS)


def _metric_catalog_for_tables(tables: dict[str, list[str]]) -> dict[str, list[str]]:
    supported_metrics = set(METRIC_ALIASES.values())
    return {
        table: [column for column in columns if column in supported_metrics]
        for table, columns in tables.items()
    }


def _date_columns_for_tables(tables: dict[str, list[str]]) -> dict[str, list[str]]:
    date_like = {"report_time", "start_date", "due_date", "startdate", "compdate", "duedate"}
    return {
        table: [column for column in columns if column in date_like]
        for table, columns in tables.items()
    }


def _serialize_slots(slots: dict[str, QuerySlot]) -> dict[str, dict[str, Any]]:
    return {
        key: {
            "value": slot.value,
            "source": slot.source,
            "confidence": slot.confidence,
            "raw_text": slot.raw_text,
        }
        for key, slot in slots.items()
    }


def _extract_chat_completion_content(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [
                item.get("text")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            if texts:
                return "".join(texts)

    if isinstance(response_json.get("output_text"), str):
        return response_json["output_text"]
    texts: list[str] = []
    for output in response_json.get("output", []):
        for content in output.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                texts.append(text)
    if not texts:
        raise RuntimeError("OpenAI response did not include output text.")
    return "".join(texts)
