from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx
from psycopg import Error as PsycopgError

from app.config import get_settings
from app.db.read_only import ReadOnlyQueryExecutor, SqlValidationError

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
    source: Literal["explicit_user", "request_context", "alias_match", "parser", "llm_inference"]
    confidence: float
    raw_text: str


@dataclass(frozen=True)
class QueryPlan:
    query_type: QueryType
    template_id: str | None
    fab_id: str | None = None
    data_source_type: Literal["operational_report", "model_master", "release_plan"] | None = None
    slots: dict[str, QuerySlot] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    source_tables: list[str] = field(default_factory=list)
    select_items: list[str] = field(default_factory=list)
    filters: list[dict[str, str]] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    order_by: list[str] = field(default_factory=list)
    aggregation: str | None = None
    expected_result_shape: str | None = None
    chart_intent: dict[str, str] | None = None


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

FAB_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:fab|FAB)\s*[-_ ]?(1[0-3])(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])fab(1[0-3])(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
PRODUCT_PATTERN = re.compile(r"\b(?:product|part|route_product)[-_ ]?([eE]?\d+)\b", re.IGNORECASE)
ROUTE_PATTERN = re.compile(r"\broute[-_ ]?product[-_ ]?([eE]?\d+)\b", re.IGNORECASE)
TOOLGROUP_PATTERN = re.compile(r"\b[A-Z][A-Za-z]{1,8}_[A-Z]{2}_[0-9]{1,3}\b")
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
                "y": {"type": "string"},
                "x_title": {"type": "string"},
                "y_title": {"type": "string"},
                "series": {"type": "string"},
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
                            "schema_context": schema_context,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "type": "json_schema",
                    "name": "fab_text2sql_direct_sql",
                    "strict": True,
                    "schema": TEXT2SQL_OUTPUT_SCHEMA,
                },
            },
        }
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        url = (
            f"{self.endpoint}/openai/deployments/{self.model}/chat/completions"
            f"?api-version={self.api_version}"
        )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        output_text = _extract_chat_completion_content(response.json())
        return json.loads(output_text)


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
    execute: bool | None = None,
    llm_client: Text2SQLClient | None = None,
) -> Text2SQLResult:
    result = plan_text2sql(question, fab=fab, llm_client=llm_client)
    if result.status != "succeeded" or not result.sql:
        return result

    settings = get_settings()
    should_execute = execute if execute is not None else bool(settings.postgres_dsn)
    if not should_execute:
        return result

    try:
        execution = ReadOnlyQueryExecutor().execute(result.sql)
    except (RuntimeError, SqlValidationError, PsycopgError) as exc:
        if result.query_type == "status" and _is_missing_autosched_table_error(exc):
            return _operational_data_unavailable(result.plan.fab_id, result.plan.slots) if result.plan else result
        return Text2SQLResult(
            status="failed",
            query_type=result.query_type,
            answer="SQL은 생성됐지만 데이터베이스 조회를 완료하지 못했습니다.",
            sql=result.sql,
            confidence=0.2,
            limitations=[*result.limitations, str(exc)],
            plan=result.plan,
        )

    return Text2SQLResult(
        status="succeeded",
        query_type=result.query_type,
        answer=_summarize_execution(result, execution.rows),
        sql=result.sql,
        rows=execution.rows,
        columns=execution.columns,
        row_count=execution.row_count,
        confidence=result.confidence,
        limitations=result.limitations,
        plan=result.plan,
    )


def plan_text2sql(
    question: str,
    *,
    fab: str | None = None,
    llm_client: Text2SQLClient | None = None,
) -> Text2SQLResult:
    normalized = _normalize_question(question)
    slots = _extract_slots(question, normalized, fab=fab)
    fab_id = slots.get("fab_id").value if "fab_id" in slots else None
    query_type = _classify_query_type(normalized)

    if not fab_id:
        return _clarification(
            query_type=query_type,
            answer="어느 FAB을 조회할까요? 현재 조회 가능한 대상은 fab10, fab11, fab12, fab13입니다.",
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

    schema_context = _schema_context_for_question(query_type, slots, fab_id)
    if not schema_context["tables"]:
        return Text2SQLResult(
            status="unsupported",
            query_type=query_type,
            answer="현재 허용된 schema catalog로 이 질문의 SQL을 만들 수 없습니다.",
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

    return _result_from_llm_output(llm_output, query_type, slots, fab_id, schema_context)


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
    try:
        ReadOnlyQueryExecutor(dsn="postgresql://validation-only").validate(sql)
        _validate_sql_tables(sql, set(schema_context["allowed_table_refs"]))
    except (SqlValidationError, ValueError) as exc:
        return Text2SQLResult(
            status="failed",
            query_type=query_type,
            answer="LLM이 만든 SQL이 read-only allowlist 검증을 통과하지 못했습니다.",
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
            ),
        )

    source_tables = list(llm_output.get("source_tables") or _extract_table_refs(sql))
    chart_intent = llm_output.get("chart_intent")
    plan = QueryPlan(
        query_type=query_type,
        template_id=None,
        fab_id=fab_id,
        data_source_type=schema_context["data_source_type"],
        slots=slots,
        limitations=_base_limitations(query_type, schema_context["data_source_type"])
        + list(llm_output.get("limitations") or []),
        source_tables=source_tables,
        select_items=list(llm_output.get("select_items") or []),
        filters=list(llm_output.get("filters") or []),
        group_by=list(llm_output.get("group_by") or []),
        order_by=list(llm_output.get("order_by") or []),
        aggregation=llm_output.get("aggregation"),
        expected_result_shape=llm_output.get("expected_result_shape"),
        chart_intent=chart_intent if isinstance(chart_intent, dict) else None,
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
) -> dict[str, Any]:
    data_source_type = _data_source_type(query_type)
    tables: dict[str, list[str]] = {}
    if data_source_type == "model_master":
        tables.update(SCHEMA_CATALOG["model_master"])
        product = _slot_value(slots, "product") or _slot_value(slots, "route")
        if product:
            route_table = _route_table_for_product(product, fab_id)
            if route_table:
                tables[route_table] = [
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
    else:
        tables.update(SCHEMA_CATALOG[data_source_type])

    return {
        "dialect": "postgresql",
        "fab_id": fab_id,
        "data_source_type": data_source_type,
        "tables": {f"{fab_id}.{table}": columns for table, columns in tables.items()},
        "allowed_table_refs": [f"{fab_id}.{table}" for table in tables],
        "rules": [
            "Return exactly one SELECT or WITH query.",
            "Use only schema-qualified table names from allowed_table_refs.",
            "Do not write DDL, DML, COPY, INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, SET, or comments.",
            "Always include a deterministic ORDER BY when using LIMIT.",
            "Never scan release-plan tables without a selective product, route, scenario, or date predicate.",
            "For current status, use AutoSched tables only; never infer live status from General Data.",
        ],
    }


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
6. For SC-001 current status, prefer latest non-WarmUp operational report rows.
7. For trend chart requests, include chart_intent with the output x/y column aliases.
"""


def _data_source_type(
    query_type: QueryType,
) -> Literal["operational_report", "model_master", "release_plan"]:
    if query_type == "status":
        return "operational_report"
    if query_type in {"release_plan_lookup", "trend"}:
        return "release_plan"
    return "model_master"


def _base_limitations(query_type: QueryType, data_source_type: str) -> list[str]:
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
    for table_ref in TABLE_REF_PATTERN.findall(sql):
        refs.append(table_ref.replace(" ", "").replace('"', "").lower())
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
    if not rows:
        return "조회는 성공했지만 조건에 맞는 행이 없습니다."
    if planned.query_type == "master_data_lookup":
        return f"General Data 기준으로 {len(rows)}개 행을 조회했습니다."
    if planned.query_type == "release_plan_lookup":
        return f"Release plan 기준으로 {len(rows)}개 행을 조회했습니다."
    if planned.query_type == "trend":
        total = sum(int(row.get("lot_count", 0)) for row in rows if "lot_count" in row)
        if total:
            return f"lotrelease를 집계했습니다. 총 {total}건이며 날짜 포인트는 {len(rows)}개입니다."
        return f"Release plan 추세 기준으로 {len(rows)}개 행을 조회했습니다."
    if planned.query_type == "status":
        return f"AutoSched report 기준으로 {len(rows)}개 상태 행을 조회했습니다."
    return f"{len(rows)}개 행을 조회했습니다."


def _is_missing_autosched_table_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "autosched_" in message and ("does not exist" in message or "undefinedtable" in message)


def _extract_slots(
    question: str,
    normalized_question: str,
    *,
    fab: str | None,
) -> dict[str, QuerySlot]:
    slots: dict[str, QuerySlot] = {}

    context_fab = _normalize_fab(fab) if fab else None
    if context_fab:
        slots["fab_id"] = QuerySlot(context_fab, "request_context", 0.9, fab or context_fab)

    parsed_fab = _parse_fab(question)
    if parsed_fab:
        slots["fab_id"] = QuerySlot(parsed_fab, "explicit_user", 1.0, parsed_fab)

    product = _parse_product(question)
    if product:
        slots["product"] = QuerySlot(product, "parser", 0.9, product)
        slots["route"] = QuerySlot(f"Route_{product}", "parser", 0.8, product)

    route = _parse_route(question)
    if route:
        slots["route"] = QuerySlot(route, "parser", 0.9, route)
        slots["product"] = QuerySlot(route.removeprefix("Route_"), "parser", 0.8, route)

    toolgroup = _parse_toolgroup(question)
    if toolgroup:
        slots["toolgroup"] = QuerySlot(toolgroup, "parser", 0.95, toolgroup)

    lot_id = _parse_lot(question)
    if lot_id:
        slots["lot_id"] = QuerySlot(lot_id, "parser", 0.9, lot_id)

    area = _parse_area(normalized_question)
    if area:
        slots["area"] = QuerySlot(area, "alias_match", 0.85, area)

    release_scenario = _parse_release_scenario(question)
    if release_scenario:
        slots["release_scenario"] = QuerySlot(
            release_scenario, "parser", 0.75, release_scenario
        )

    return slots


def _classify_query_type(normalized_question: str) -> QueryType:
    if _contains_any(normalized_question, TREND_TERMS):
        return "trend"
    if _contains_any(normalized_question, RELEASE_TERMS):
        return "release_plan_lookup"
    if _contains_any(normalized_question, ROUTE_TERMS | TOOLGROUP_TERMS | PM_BREAKDOWN_TERMS):
        if _contains_any(normalized_question, STATUS_TERMS) and not _looks_like_master_lookup(
            normalized_question
        ):
            return "status"
        return "master_data_lookup"
    if _contains_any(normalized_question, STATUS_TERMS):
        return "status"
    return "unsupported"


def _looks_like_master_lookup(normalized_question: str) -> bool:
    return any(term in normalized_question for term in ("어떤", "목록", "list", "구성", "보여", "조회"))


def _has_selective_release_constraint(slots: dict[str, QuerySlot]) -> bool:
    return any(key in slots for key in ("product", "route", "release_scenario"))


def _slot_value(slots: dict[str, QuerySlot], key: str) -> str | None:
    slot = slots.get(key)
    return slot.value if slot else None


def _normalize_question(question: str) -> str:
    return question.casefold().replace("-", "_")


def _contains_any(value: str, terms: set[str]) -> bool:
    return any(term in value for term in terms)


def _normalize_fab(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip().lower().replace(" ", "")
    if raw in ALLOWED_FABS:
        return raw
    if raw in {"10", "11", "12", "13"}:
        return f"fab{raw}"
    return None


def _parse_fab(question: str) -> str | None:
    match = FAB_PATTERN.search(question)
    if not match:
        return None
    number = match.group(1) or match.group(2)
    return f"fab{number}"


def _parse_product(question: str) -> str | None:
    match = PRODUCT_PATTERN.search(question)
    if not match:
        return None
    return f"Product_{match.group(1).lower()}"


def _parse_route(question: str) -> str | None:
    match = ROUTE_PATTERN.search(question)
    if not match:
        return None
    return f"Route_Product_{match.group(1).lower()}"


def _parse_toolgroup(question: str) -> str | None:
    match = TOOLGROUP_PATTERN.search(question)
    return match.group(0) if match else None


def _parse_lot(question: str) -> str | None:
    match = LOT_PATTERN.search(question)
    return match.group(0) if match else None


def _parse_area(normalized_question: str) -> str | None:
    normalized = normalized_question.replace("-", "_")
    for alias, canonical in AREA_ALIASES.items():
        if alias in normalized:
            return canonical
    return None


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
