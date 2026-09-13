"""Shared entity and output requirements for operational record lookups.

These describe a request, not a database schema or a canned answer. Storage and
business meanings are resolved from the current catalog by the semantic planner.
"""
from __future__ import annotations

import re

ENTITY_COLUMNS = {
    "lot_id": {"lot_id", "lot", "lot_name"},
    "equipment_id": {"equipment_id", "equipment", "stn", "equipment_name"},
    "resource_group": {"resource_group", "resource_group_id", "toolgroup", "stngrp"},
}
TOKEN = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
LABELS = {
    "lot_id": r"LOT(?:[_ ]?ID)?|로트|랏",
    "equipment_id": r"EQUIPMENT(?:[_ ]?ID)?|장비|설비",
    "resource_group": r"RESOURCE[_ ]GROUP(?:[_ ]?ID)?|자원\s*그룹|설비군|장비군|toolgroup",
}
FACETS = {
    "history": r"이력|내역|history|audit",
    "reason": r"사유|reason(?:\s*code)?",
    "setter": r"설정자|등록자|설정한\s*(?:사람|담당자)|누가|set\s+by|created\s+by",
    "availability": r"빈\s*자원|사용\s*가능|가용\s*자원|available|idle\s*(?:resource|equipment)",
    "work_code": r"작업\s*코드|work[_ ]code|operation[_ ]code",
    "mapping": r"매핑|mapping",
}
FACET_COLUMNS = {
    "work_code": {"work_code", "operation_code", "job_code"},
    "setter": {"set_by", "created_by", "setter_id", "operator_id", "registered_by", "user_id"},
    "reason": {"reason", "reason_code", "hold_reason", "down_reason", "hold_reason_code", "down_reason_code"},
    "availability": {"is_available", "availability_state", "available"},
}


def facet_columns(kind: str, table: str, context: dict) -> set[str]:
    names = set(context.get("tables", {}).get(table, []))
    editorial = context.get("table_details", {}).get(table, {}).get("semantics", {}).get("facet_columns", {}).get(kind, [])
    return names & (FACET_COLUMNS.get(kind, set()) | set(editorial))


def current_filter_columns(context: dict, request: dict) -> set[str]:
    """A current-entity query may filter its requested scope/time, not its outcome."""
    columns = {"fab_id"}
    for kind in request.get("entities", {}):
        columns.update(ENTITY_COLUMNS[kind])
    for entry in context.get("table_details", {}).values():
        semantics = entry.get("semantics", {})
        columns.update(v for k, v in semantics.get("entity_columns", {}).items() if k in request.get("entities", {}))
        if semantics.get("default_time_column"):
            columns.add(semantics["default_time_column"])
        columns.update(c["name"] for c in entry.get("columns", []) if any(t in c.get("type", "") for t in ("timestamp", "date")))
    columns.update({"event_time", "interval_start", "interval_end", "report_time", "observed_at"})
    for key in ("area", "product", "route", "toolgroup", "line"):
        if key in context.get("slots", {}):
            columns.add(key)
    return columns


def entity_literals(question: str) -> dict[str, str]:
    found = {}
    for kind, label in LABELS.items():
        # Require a digit or separator in a lexical ID; ordinary English words
        # after labels (e.g. 'equipment utilization') are not identifiers.
        patterns = [rf"(?:{label})(?![A-Za-z0-9_])\s*[:=]?\s*[`\"']?({TOKEN})",
                    rf"(?<![A-Za-z0-9_.-])({TOKEN})[`\"']?\s+(?:{label})(?![A-Za-z0-9_])"]
        for index, pattern in enumerate(patterns):
            match = re.search(pattern, question, re.IGNORECASE)
            if (match and not match[1].isdigit()
                    and re.search(r"\d" if index else r"\d|[_-]", match[1])):
                value = match[1].rstrip('.')
                if not re.fullmatch(r"fab[_-]?\d+", value, re.IGNORECASE):
                    found[kind] = value
                    break
    # Preserve opaque compound IDs before Korean particles. Entity labels are
    # preferred; a bare ID is typed as LOT only in an explicit restriction context.
    if "lot_id" not in found and re.search(r"제한|hold", question, re.IGNORECASE):
        match = re.search(rf"(?<![A-Za-z0-9_.-])({TOKEN})(?=의|에|를|을|\s)", question)
        if match and len(re.findall(r"[-_]", match[1])) >= 2 and re.search(r"\d", match[1]):
            found["lot_id"] = match[1]
    return found


def record_requirements(question: str, slots: dict | None = None) -> dict:
    entities = entity_literals(question)
    for key in ENTITY_COLUMNS:
        slot = (slots or {}).get(key)
        value = slot.get("value") if isinstance(slot, dict) else getattr(slot, "value", None)
        if value and key not in entities:
            entities[key] = str(value)
    facets = [key for key, pattern in FACETS.items() if re.search(pattern, question, re.IGNORECASE)]
    current = bool(re.search(r"현재|지금|최신|current|latest", question, re.IGNORECASE))
    if entities and current:
        facets.append("latest_record")
    return {"entities": entities, "facets": facets, "current": current,
            "registered": bool(re.search(r"등록|registry|registered|master", question, re.IGNORECASE)),
            "record_lookup": bool(entities or facets)}


def validate_record_plan(plan, context: dict) -> None:
    """Check entity constraints and coverage against the plan, before SQL exists."""
    request = context.get("request_requirements", {}).get("business_request", {})
    if not request.get("record_lookup"):
        return
    if "work_code" in request.get("facets", []) and not any(facet_columns("work_code", table, context) for table in plan.tables):
        raise ValueError("No selected source defines work codes. Do not substitute event types, toolgroups or steps. Use supported=false if no catalog source has that meaning.")
    if ("availability" in request.get("facets", [])
            and not any(facet_columns("availability", table, context) for table in plan.tables)
            and any(p.source.column not in current_filter_columns(context, request) for p in plan.filters)):
        raise ValueError("No source defines availability. Do not invent idle/available predicates on event status. Return scoped recent records as partial context or supported=false.")
    if request.get("registered") and "mapping" in request.get("facets", []):
        kinds = {context.get("table_details", {}).get(table, {}).get("data_source_type") for table in plan.tables}
        if kinds == {"simulation_snapshot"}:
            raise ValueError("Event occurrences cannot stand in for registered mappings. Inspect model/master area-to-toolgroup or route-step relationships and disclose their scope.")
    presence = [context.get("table_details", {}).get(table, {}).get("entity_presence", {}).get("matches") for table in plan.tables]
    matching = [table for table, entry in context.get("table_details", {}).items()
                if entry.get("entity_presence", {}).get("matches") is True]
    if presence and all(value is False for value in presence) and matching:
        raise ValueError(f"Selected source has no rows for this entity; inspect matching sources: {matching}")
    for kind, value in request.get("entities", {}).items():
        columns = ENTITY_COLUMNS[kind]
        # Editorial catalogs can register a source-specific entity column.
        editorial = {table: detail.get("semantics", {}).get("entity_columns", {}).get(kind)
                     for table, detail in context.get("table_details", {}).items()}
        matched = any(
            (p.source.column in columns or p.source.column == editorial.get(p.source.table))
            and p.operator in {"eq", "in"} and p.values == [value]
            for p in plan.filters
        )
        if not matched:
            raise ValueError(f"Missing exact entity predicate for {kind}={value}; do not substitute FAB-wide or group-level data")
    by_facet = {item.requirement: item for item in plan.answer_coverage}
    missing = set(request.get("facets", [])) - by_facet.keys()
    if missing:
        raise ValueError(f"Declare answer_coverage for every requested facet: {sorted(missing)}")
    selected = {(p.table, p.column) for p in plan.projections}
    selected |= {(a.source.table, a.source.column) for a in plan.aggregates}
    for item in plan.answer_coverage:
        if item.status == "available":
            if not item.columns or any((c.table, c.column) not in selected for c in item.columns):
                raise ValueError(f"Coverage {item.requirement} must reference actual selected evidence columns")
            if item.requirement in FACET_COLUMNS and not any(c.column in facet_columns(item.requirement, c.table, context) for c in item.columns):
                raise ValueError(f"No business field grounds {item.requirement}; declare partial/unavailable instead of relabeling event fields")
        else:
            if any((c.table, c.column) not in selected for c in item.columns):
                raise ValueError("Partial coverage must reference actual selected columns")
            if not item.reason.strip():
                raise ValueError("Unavailable/partial facets require a precise limitation")
    # Latest within one selected entity is different from a global snapshot.
    if request.get("entities") and plan.latest_by and plan.latest_scope != "filtered":
        raise ValueError("Latest entity observation requires latest_scope=filtered")
    if (request.get("current") and set(request.get("entities", {})) & {"lot_id", "equipment_id"}
            and "history" not in request.get("facets", [])
            and any(p.source.column not in current_filter_columns(context, request) for p in plan.filters)):
        raise ValueError("Check the latest entity record before filtering its event/state; a historic failure is not the current state")


RECORD_PROMPT = """
Operational record requests:
- business_request.entities are opaque exact identifiers. Keep the complete value,
  including a FAB prefix. equipment_id is an individual tool; resource_group is a group.
- Select sources by entity grain and requested fields, not by status/master labels.
  Search event/history sources as well as current-state masters. An aggregate report
  cannot answer an individual entity request. Metadata absence is not proof of no rows.
  If entity_presence is false for every candidate, STILL create a normal SELECT plan
  on ONE suitable source with real projected status/time columns and exact ID filter.
  Execute it to establish an empty result. Do not return no projections, unrelated
  source joins, supported=false, or claim the entity never existed in the factory.
- When some requested fields are absent, query the relevant available records and
  declare each facet available/partial/unavailable in answer_coverage, with exact
  projected evidence columns and precise limitations. Do not discard useful history
  because a person's identity or reason code is absent. Never filter out missing setters.
  requirement must be the EXACT facet key in business_request.facets, not prose.
  With only actor_role/narrative available, setter/reason are PARTIAL or UNAVAILABLE,
  never fully available. Preserve all requested facet keys even when unsupported.
- For current-state/reason requests check the latest entity observation BEFORE
  restricting to a historical failure/hold type. A last event is not an authoritative
  current-state master. Keep its timestamp/type/status and disclose the distinction.
- actor_role is a role, not a person's identity; narrative is an event description,
  not necessarily a business reason. Utilization or a completed event does not prove
  immediate availability. A work code is not a toolgroup unless editorial metadata
  explicitly defines that equivalence. Do not relabel related data as the requested data.
  event_type is also NOT a work code. If no work_code/operation_code/job_code column
  or semantics.facet_columns.work_code binding exists, return supported=false for a
  work-code request. Do not offer event types/toolgroups as partial work codes.
- Use code/value domains and descriptions to resolve business categories. A generic
  concept such as metrology may span multiple stored categories and product routes;
  do not search only its English translation or arbitrarily restrict a route/product.
  Preserve an explicit area literal, but distinguish it from a translated concept.
  For REGISTERED mappings, use master/registry relations, not repeated event records.
  A model's area-to-toolgroup or route-step relation can be useful partial evidence;
  identify its actual scope and do not call it the official factory mapping.
- If the requested meaning truly has no supported source, supported=false and explain
  the precise missing source/definition. Do not claim missing entity rows without SQL.
- When an entity/group exists in an event schema but authoritative availability/current
  state does not, a bounded recent-record query is useful partial evidence. Label it
  recent records, not a complete per-device current-state list. Do not fabricate idle
  states absent from the observed domains or group event rows without aggregates.
- A limited result is a returned sample, never an exact total. Null fields are missing
  values, empty results are no matching records, and failed queries are neither.
"""
