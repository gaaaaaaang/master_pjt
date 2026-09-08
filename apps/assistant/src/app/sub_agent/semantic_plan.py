"""A typed structure is grounded and checked before asking the model for SQL."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColumnRef(StrictModel):
    table: str
    column: str


class Aggregate(StrictModel):
    source: ColumnRef
    function: Literal["count", "count_rows", "count_distinct", "sum", "avg", "min", "max"]
    alias: str


class Predicate(StrictModel):
    source: ColumnRef
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "like", "ilike", "in", "is_null", "not_null"]
    values: list[str]


class Join(StrictModel):
    left: ColumnRef
    right: ColumnRef
    kind: Literal["inner", "left"]


class SortItem(StrictModel):
    output: str
    direction: Literal["asc", "desc"]


class SemanticPlan(StrictModel):
    supported: bool
    reason: str
    tables: list[str]
    projections: list[ColumnRef]
    aggregates: list[Aggregate]
    group_by: list[ColumnRef]
    filters: list[Predicate]
    joins: list[Join]
    latest_by: list[ColumnRef]
    time_basis: str
    result_grain: str
    limitations: list[str]
    order_by: list[SortItem] = Field(default_factory=list)
    result_limit: int | None = Field(default=None, ge=1, le=200)
    latest_scope: Literal["global", "filtered"] = "global"

    @classmethod
    def strict_response_schema(cls) -> dict[str, Any]:
        """Require new fields from the model while allowing older saved plans to load."""
        schema = cls.model_json_schema()

        def visit(node):
            if isinstance(node, dict):
                node.pop("default", None)
                if "properties" in node:
                    node["required"] = list(node["properties"])
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)

        visit(schema)
        return schema


PLAN_PROMPT = """Plan a semiconductor FAB read-only query BEFORE generating SQL.
Return the structured semantic plan only. Never include SQL expressions in table/column names.
Use only exact physical tables and columns in schema_context. Choose the smallest sufficient
set; projections and group_by refer to source columns. Declare EVERY source table and every
join key and every static filter. SQL must not add undeclared row restrictions. Separate predicates, dimensions and aggregates. Preserve explicit FAB, process,
For a row count use function=count_rows and source.column="*" (SQL COUNT(*)).
With joins it counts joined result rows, not non-null keys or distinct source entities.
Use function=count for COUNT(column), which excludes nulls; these are different contracts.
product, metric, and time constraints. Current question overrides old conversation tasks.
For ranking requests, declare order_by in priority order, including explicit tie breakers.
Each sort output is a projected source column name or a declared aggregate alias.
Declare result_limit for a requested top-N count (1..200); otherwise use null. Use [] for
order_by when no ordering is requested. SQL must preserve these output contracts.
Use observed value_domains for exact category spelling; model area names and simulation
area values are different. Preserve explicit user literals even if they yield an empty set.
Do not add timestamp/FAB output columns when the user requests only a value or metric columns.
Use metadata definitions and grain: a period-average WIP is not a current WIP, averages and
percentages cannot be summed into totals, snapshot WIP cannot be summed across timestamps.
For an unspecified timestamp basis, use metadata default_time_column and state it in
time_basis/limitations. Explicit interval_start/interval_end requests take precedence.
If request_requirements.snapshot_time_filter_columns contains metadata_default, both
range boundaries use the table's default_time_column; do not mix interval_start and interval_end.
Report the chosen time basis and result grain. For a whole-factory snapshot count use SUM
across disjoint areas at the same timestamp, not AVG. For area-average questions use AVG.
An explicit time-average or AVG request may average snapshot mean columns over equally
spaced intervals; label it an unweighted time-average, not a pooled lot-weighted mean.
Do not GROUP BY the measurement itself when asked for one average per area. If the time
scope is absent, state the chosen available-period scope rather than silently listing all rows.
For latest/current snapshot requests, put the source timestamp in latest_by (usually
interval_end). This means the global maximum timestamp of that source table. SQL must use
timestamp = (SELECT MAX(timestamp) FROM the same table), not merely ORDER BY or LIMIT.
latest_scope=global means no extra filter inside MAX (apart from the bound FAB).
Use latest_scope=filtered when requesting the latest observation within selected entities
or a period: MAX must then preserve all static filters for that source table.
When also displaying that timestamp alongside a total, declare MAX(timestamp) as an
aggregate, not an ungrouped projection. Projections represent output columns, not filters.
Do not put dynamic MAX expressions into static filters. For period averages/trends,
latest_by is empty unless the user explicitly asks to restrict to the latest snapshot.
schema_context.request_requirements are explicit output constraints from the current question.
Preserve them. 'Sum area-level WIP to a whole-factory total' describes the input grain;
it does NOT request GROUP BY area. A whole-factory total is one result per requested time,
whereas 'show totals per area' explicitly requests an area dimension.
Synthetic events and model toolgroup identities are different; never join them by similar names.
When counting model settings that match toolgroups, preserve the matching existence condition.
Counting DISTINCT setting identities avoids join fanout; it does not make the matching join optional.
Never silently replace report data with synthetic data; disclose the selected source.
If data or required meaning is absent, supported=false with a precise reason. Do not infer
access policy from prior assistant failures. Do not invent units or join relationships.
"""


def request_requirements(question: str) -> dict[str, Any]:
    """Conservative explicit constraints; leave ambiguous intent to the planner."""
    q = question.casefold()
    whole = bool(re.search(r"공장\s*전체|fab\s*전체|whole[- ]factory|factory[- ]wide|전체\s*wip\s*합계", q))
    total = any(term in q for term in ("합계", "합산", "총합", "total", "sum"))
    breakdown = bool(re.search(r"영역별로|공정별로|각\s*(?:영역|공정)|per area|by area", q))
    requirements = {}
    if whole and total and not breakdown:
        requirements.update({"whole_factory_total": True,
                             "forbidden_output_dimensions": ["area", "toolgroup", "equipment_id"],
                             "snapshot_wip_function": "sum" if "wip" in q or "재공" in q else None})
    ranking = re.search(r"(?:상위|하위|\btop|\bbottom)\s*(\d+)(?!\d|\s*(?:[.%]|퍼센트|percent))|가장\s*(?:큰|높은|작은|낮은)[^.!?\n\d]{0,30}(\d+)\s*개", q)
    if ranking:
        requirements["result_limit"] = int(next(group for group in ranking.groups() if group is not None))
    if re.search(r"(?:합계|평균|개수|건수|총합|값)\s*(?:하나|한\s*(?:개|값))\s*만|\bone value only\b", q):
        requirements["output_column_count"] = 1
    if re.search(r"행\s*(?:의\s*)?(?:개수|수)|\b(?:row count|number of rows|how many rows)\b", q) and not re.search(r"설정|distinct|서로\s*다른|중복|count\s*\(", q):
        requirements["count_result_rows"] = True
        null_filters = []
        column = r"(?<![a-z0-9_])([a-z][a-z0-9_]*)\s*(?:가|이)?\s*"
        for operator, suffix in (
            ('not_null', r"(?:is\s+not\s+null\b|null\s*(?:이|가)?\s*아닌(?=\s|행|$))"),
            ('is_null', r"(?:is\s+null\b|null\s*인(?=\s|행|$))"),
        ):
            for match in re.finditer(column + suffix, q):
                if not re.search(r"제외|except|exclud", q[match.end():match.end() + 20]):
                    null_filters.append({'column': match.group(1), 'operator': operator})
        if null_filters:
            requirements['explicit_null_filters'] = null_filters
    if re.search(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}|오늘|어제|최근\s*\d+|지난", q):
        time_columns = set(re.findall(r"(?<![a-z0-9_])(interval_start|interval_end)(?![a-z0-9_])", q))
        if re.search(r"시작\s*(?:시각|시간|기준|이|한)|interval\s+start", q):
            time_columns.add('interval_start')
        if re.search(r"종료\s*(?:시각|시간|기준|가|된)|interval\s+end", q):
            time_columns.add('interval_end')
        requirements['snapshot_time_filter_columns'] = sorted(time_columns) or ['metadata_default']
    if re.search(r"설정.{0,16}(?:건수|개수|행\s*수)", q) and "조인 결과" not in q:
        requirements["count_model_settings"] = True
    if (requirements.get("count_model_settings") and re.search(r"\b(?:pm|breakdown)\b", q)
            and re.search(r"매칭|연결|일치|match|join", q)
            and re.search(r"설비군|장비군|toolgroups?", q)):
        requirements["require_toolgroup_matching"] = True
    return requirements


def _latest_expression(value: str, ref: ColumnRef) -> bool:
    """Recognize only an exact same-source MAX subquery; never execute plan text."""
    from sqlglot import exp, parse_one
    from sqlglot.errors import SqlglotError

    if not value.lstrip().startswith("("):
        return False
    try:
        node = parse_one(value, read="postgres")
    except SqlglotError:
        return False
    if not isinstance(node, exp.Subquery) or not isinstance(node.this, exp.Select):
        return False
    query = node.this
    sources = list(query.find_all(exp.Table))
    projections = query.selects
    if (len(sources) != 1 or f"{sources[0].db}.{sources[0].name}" != ref.table
            or len(projections) != 1 or not isinstance(projections[0], exp.Max)
            or not isinstance(projections[0].this, exp.Column)
            or projections[0].this.name != ref.column
            or any(query.args.get(key) for key in ("joins", "group", "having", "limit"))):
        return False
    where = query.args.get("where")
    if where is None:
        return True
    condition = where.this
    return (isinstance(condition, exp.EQ) and isinstance(condition.this, exp.Column)
            and condition.this.name == "fab_id" and isinstance(condition.expression, exp.Literal)
            and condition.expression.this == sources[0].db)


def validate_plan(raw: dict[str, Any], context: dict[str, Any]) -> SemanticPlan:
    plan = SemanticPlan.model_validate(raw)
    if not plan.supported:
        return plan
    # Models sometimes express the same latest intent as an eq placeholder.
    # Normalize only known markers, never arbitrary SQL strings or real timestamps.
    latest = list(plan.latest_by)
    filters = []
    for predicate in plan.filters:
        if predicate.source in plan.latest_by and predicate.operator == "eq" and not predicate.values:
            continue
        if (predicate.operator == "eq" and len(predicate.values) == 1
                and (predicate.values[0].strip(" <>[]{}").casefold() in {"latest", "latest_snapshot", "최신"}
                     or (predicate.source in plan.latest_by
                         and predicate.values[0].strip(" $<>[]{}").casefold() == f"latest_{predicate.source.column}")
                     or _latest_expression(predicate.values[0], predicate.source))):
            if predicate.source not in latest:
                latest.append(predicate.source)
        else:
            filters.append(predicate)
    plan = plan.model_copy(update={"latest_by": latest, "filters": filters})
    output_count = context.get("request_requirements", {}).get("output_column_count")
    if output_count == 1:
        # Optional provenance columns belong in evidence when the user explicitly
        # asks for one value. Preserve all filters and the actual metric.
        core_projections = [ref for ref in plan.projections if ref not in latest and ref.column != "fab_id"]
        core_aggregates = [a for a in plan.aggregates if not (a.source in latest and a.function in {"min", "max"})]
        if len(core_projections) + len(core_aggregates) == 1 and not plan.group_by:
            plan = plan.model_copy(update={"projections": core_projections, "aggregates": core_aggregates})
        if len(plan.projections) + len(plan.aggregates) != 1:
            raise ValueError("Question explicitly requests one output value")
    # A latest timestamp is constant within the selected snapshot. Display it
    # alongside totals as MAX, preserving the requested output without invalid
    # ungrouped SQL or relaxing the exact aggregate contract.
    if plan.aggregates:
        projections, aggregates = [], list(plan.aggregates)
        renamed_outputs = {}
        for ref in plan.projections:
            if ref in plan.latest_by and ref not in plan.group_by:
                if not any(a.source == ref and a.function == "max" for a in aggregates):
                    aliases = {a.alias for a in aggregates}
                    alias = f"latest_{ref.column}"
                    while alias in aliases:
                        alias += "_value"
                    aggregates.append(Aggregate(source=ref, function="max", alias=alias))
                renamed_outputs[ref.column] = next(a.alias for a in aggregates if a.source == ref and a.function == "max")
            else:
                projections.append(ref)
        plan = plan.model_copy(update={"projections": projections, "aggregates": aggregates,
            "order_by": [item.model_copy(update={"output": renamed_outputs.get(item.output, item.output)})
                         for item in plan.order_by]})
    tables = context["tables"]
    if not plan.tables or len(plan.tables) != len(set(plan.tables)):
        raise ValueError("Plan must declare distinct source tables")
    if set(plan.tables) - tables.keys():
        raise ValueError("Plan uses a table absent from the grounded FAB schema")
    if not plan.projections and not plan.aggregates:
        raise ValueError("Plan has no result columns")
    output_names = [ref.column for ref in plan.projections] + [a.alias for a in plan.aggregates]
    for item in plan.order_by:
        if output_names.count(item.output) != 1:
            raise ValueError("Sort output must identify one declared output column or aggregate alias")
    if plan.result_limit is not None and not plan.order_by:
        raise ValueError("A planned top-N result requires an explicit ordering")
    requirements = context.get("request_requirements", {})
    explicit_filters = list(plan.filters)
    for constraint in requirements.get('explicit_null_filters', []):
        sources = [table for table in plan.tables if constraint['column'] in tables[table]]
        if len(sources) != 1:
            raise ValueError('Explicit NULL row-count filter needs one unambiguous source column')
        ref = ColumnRef(table=sources[0], column=constraint['column'])
        existing = [p for p in explicit_filters if p.source == ref and p.operator in {'is_null', 'not_null'}]
        if any(p.operator != constraint['operator'] for p in existing):
            raise ValueError('Plan NULL predicate contradicts the explicit row-count request')
        if not existing:
            explicit_filters.append(Predicate(source=ref, operator=constraint['operator'], values=[]))
    plan = plan.model_copy(update={'filters': explicit_filters})
    for predicate in plan.filters:
        if (requirements.get('snapshot_time_filter_columns')
                and predicate.operator in {'gt', 'gte', 'lt', 'lte'}
                and predicate.source.column in {'interval_start', 'interval_end'}):
            entry = context.get('table_details', {}).get(predicate.source.table, {})
            default_time = entry.get('semantics', {}).get('default_time_column')
            allowed_time = requirements['snapshot_time_filter_columns']
            if allowed_time == ['metadata_default']:
                allowed_time = [default_time] if default_time else ['interval_start', 'interval_end']
            if predicate.source.column not in allowed_time:
                raise ValueError(f'Use {allowed_time} consistently for snapshot range bounds; plan mixes timestamp bases')
    if requirements.get("result_limit") is not None and plan.result_limit != requirements["result_limit"]:
        raise ValueError("Question requests a specific top-N result count; preserve it in result_limit")
    if requirements.get("whole_factory_total"):
        forbidden = set(requirements["forbidden_output_dimensions"])
        if any(ref.column in forbidden for ref in [*plan.group_by, *plan.projections]):
            raise ValueError("Question requests a whole-factory total; area/toolgroup output changes its grain")
        if not plan.aggregates:
            raise ValueError("Whole-factory total requires aggregation")
        if (requirements.get("snapshot_wip_function") == "sum" and any(
            "wip_lots" in context["tables"].get(table, []) for table in plan.tables
        ) and not any(a.function == "sum" and a.source.column == "wip_lots" for a in plan.aggregates)):
            raise ValueError("Whole-factory snapshot WIP requires SUM(wip_lots), not average or row count")
    if requirements.get("count_result_rows"):
        aggregates = []
        for aggregate in plan.aggregates:
            if aggregate.function == "count":
                entry = context.get("table_details", {}).get(aggregate.source.table, {})
                column = next((c for c in entry.get("columns", []) if c["name"] == aggregate.source.column), {})
                null_filtered = any(p.source == aggregate.source and p.operator == "is_null" for p in plan.filters)
                nonnull_filtered = any(p.source == aggregate.source and p.operator == "not_null" for p in plan.filters)
                if null_filtered or (column.get("nullable") is True and not nonnull_filtered):
                    aggregate = aggregate.model_copy(update={"function": "count_rows",
                        "source": ColumnRef(table=aggregate.source.table, column="*")})
            aggregates.append(aggregate)
        plan = plan.model_copy(update={"aggregates": aggregates})
    refs = [*plan.projections, *plan.group_by, *plan.latest_by,
            *(aggregate.source for aggregate in plan.aggregates if aggregate.function != "count_rows"),
            *(predicate.source for predicate in plan.filters),
            *(join.left for join in plan.joins), *(join.right for join in plan.joins)]
    for ref in refs:
        if ref.table not in plan.tables or ref.column not in tables.get(ref.table, []):
            raise ValueError(f"Unknown plan column: {ref.table}.{ref.column}")
    for predicate in plan.filters:
        if predicate.operator in {"is_null", "not_null"}:
            valid = len(predicate.values) == 0
        elif predicate.operator == "in":
            valid = len(predicate.values) > 0
        else:
            valid = len(predicate.values) == 1
        if not valid:
            raise ValueError(f"Invalid value count for predicate: {predicate.operator}")
    for latest in plan.latest_by:
        entry = context.get("table_details", {}).get(latest.table, {})
        column = next((c for c in entry.get("columns", []) if c["name"] == latest.column), {})
        if column.get("type") and not any(token in column["type"] for token in ("timestamp", "date")):
            raise ValueError("Latest snapshot selection requires a timestamp/date column")
    for aggregate in plan.aggregates:
        if aggregate.function == "count_rows":
            if (aggregate.source.column != "*" or aggregate.source.table not in plan.tables
                    ):
                raise ValueError("count_rows requires a declared source table and the '*' row marker")
            continue
        entry = context.get("table_details", {}).get(aggregate.source.table, {})
        metric = next((m for m in entry.get("metrics", []) if m.get("column") == aggregate.source.column), {})
        if aggregate.function == "sum" and metric.get("kind") in {"ratio", "mean", "period_mean", "score"}:
            raise ValueError(f"SUM is incompatible with metric meaning: {aggregate.source.column}")
        column = next((c for c in entry.get("columns", []) if c["name"] == aggregate.source.column), {})
        dtype = column.get("type", "")
        if aggregate.function in {"sum", "avg"} and dtype and not any(
            token in dtype for token in ("int", "numeric", "decimal", "real", "double", "float")
        ):
            raise ValueError(f"Numeric aggregation on nonnumeric column: {aggregate.source.column}")
    edges: dict[str, set[str]] = {table: set() for table in plan.tables}
    for join in plan.joins:
        left, right = join.left.table, join.right.table
        if left == right:
            raise ValueError("Self joins need an alias-aware plan, which is not supported yet")
        kinds = {context.get("table_details", {}).get(ref, {}).get("data_source_type") for ref in (left, right)}
        if "simulation_snapshot" in kinds and "model_master" in kinds:
            raise ValueError("Synthetic and model identities have no verified join mapping")
        edges[left].add(right)
        edges[right].add(left)
    visited, pending = set(), [plan.tables[0]]
    while pending:
        table = pending.pop()
        if table not in visited:
            visited.add(table)
            pending.extend(edges[table] - visited)
    if visited != set(plan.tables):
        raise ValueError("Multiple source tables require explicit connected joins")
    declared_pairs = {
        frozenset(((join.left.table, join.left.column), (join.right.table, join.right.column)))
        for join in plan.joins
    }
    for source_ref in plan.tables:
        for relationship in context.get("table_details", {}).get(source_ref, {}).get("relationships", []):
            target_ref = relationship.get("target_table_ref")
            if (requirements.get("require_toolgroup_matching")
                    and relationship.get("target_logical_table") == "toolgroups"
                    and target_ref not in plan.tables):
                raise ValueError("Matching model settings to toolgroups requires the metadata relationship target")
            if target_ref not in edges[source_ref]:
                continue
            required_pairs = {
                frozenset(((source_ref, key["source"]), (target_ref, key["target"])))
                for key in relationship.get("keys", [])
            }
            if required_pairs and not required_pairs <= declared_pairs:
                raise ValueError("Plan omits keys required by the metadata relationship")
            if relationship.get("cardinality") == "many_to_many" and requirements.get("count_model_settings"):
                for aggregate in plan.aggregates:
                    if (aggregate.source.table == source_ref
                            and aggregate.function in {"count", "count_rows", "count_distinct"}
                            and (aggregate.function != "count_distinct" or aggregate.source.column != "source_row_id")):
                        raise ValueError("Counting model settings across area fanout requires COUNT(DISTINCT source_row_id)")
            if relationship.get("cardinality") in {"many_to_one", "many_to_one_if_target_unique"}:
                target = context.get("table_details", {}).get(target_ref, {})
                snapshot_metrics = {m["column"] for m in target.get("metrics", []) if m.get("kind") == "snapshot_count"}
                if any(a.function == "sum" and a.source.table == target_ref
                       and a.source.column in snapshot_metrics for a in plan.aggregates):
                    raise ValueError("SUM of snapshot counts after a many-to-one detail join duplicates the snapshot measure")
    return plan


def validate_sql_plan(sql: str, plan: SemanticPlan) -> None:
    """Check physical sources and aggregate/dimension output lineage against the plan.

    This checks structure, not general equivalence of arbitrary SQL predicates.
    """
    from sqlglot import exp, parse_one
    from sqlglot.errors import SqlglotError
    from sqlglot.optimizer.scope import Scope, build_scope

    try:
        tree = parse_one(sql, read="postgres")
    except SqlglotError as exc:
        raise ValueError(f"SQL cannot be parsed for semantic validation: {exc}") from exc
    physical = {f"{t.db}.{t.name}" for t in tree.find_all(exp.Table) if t.db}
    if physical != set(plan.tables):
        raise ValueError("SQL source tables differ from the validated plan")
    scope = build_scope(tree)
    if scope is None:
        raise ValueError("SQL has no query scope")

    scopes_by_expression = {id(item.expression): item for item in scope.traverse()}

    def active_sources(owner):
        return {alias: pair[1] for alias, pair in owner.selected_sources.items()}

    def origin(column, owner, seen):
        matches = [(alias, source) for alias, source in active_sources(owner).items()
                   if not column.table or alias == column.table]
        if len(matches) != 1:
            return None
        _, source = matches[0]
        if isinstance(source, exp.Table):
            return (f"{source.db}.{source.name}", column.name)
        if isinstance(source, Scope):
            marker = (id(source), column.name)
            if marker in seen:
                return None
            for projection in source.expression.selects:
                if projection.alias_or_name == column.name:
                    value = projection.this if isinstance(projection, exp.Alias) else projection
                    if isinstance(value, exp.Column):
                        return origin(value, source, seen | {marker})
            if len(active_sources(source)) == 1 and any(isinstance(p, exp.Star) for p in source.expression.selects):
                return origin(exp.column(column.name), source, seen | {marker})
        return None

    def output_aggregates(expression, owner, seen):
        found = set()
        for aggregate in expression.find_all(exp.AggFunc):
            # Aggregate in a scalar subquery cannot stand in for an outer aggregate.
            if aggregate.find_ancestor(exp.Select) is not owner.expression:
                continue
            output = expression.this if isinstance(expression, exp.Alias) else expression
            while isinstance(output, exp.Paren):
                output = output.this
            if output is not aggregate:
                raise ValueError("SQL transforms a planned aggregate output with an undeclared expression")
            value = aggregate.this
            if (isinstance(aggregate, exp.Count)
                    and (isinstance(value, exp.Star)
                         or isinstance(value, exp.Literal) and not value.is_string and value.this == '1')):
                row_sources = {a.source.table for a in plan.aggregates if a.function == "count_rows"}
                physical_sources = {f'{source.db}.{source.name}' for source in active_sources(owner).values()
                                    if isinstance(source, exp.Table)}
                if len(row_sources) == 1 and row_sources <= physical_sources:
                    found.add(('count_rows', next(iter(row_sources)), '*'))
                continue
            distinct = isinstance(value, exp.Distinct)
            if distinct:
                if (not isinstance(aggregate, exp.Count) or len(value.expressions) != 1
                        or not isinstance(value.expressions[0], exp.Column)):
                    raise ValueError("SQL changes the planned aggregate input with DISTINCT or an expression")
            elif not isinstance(value, exp.Column):
                raise ValueError("SQL changes the planned aggregate input with an undeclared expression")
            cols = list(value.find_all(exp.Column)) if value is not None else []
            if len(cols) == 1:
                source = origin(cols[0], owner, set())
                if source:
                    function = aggregate.key.lower()
                    if function == "count" and distinct:
                        function = "count_distinct"
                    found.add((function, *source))
        # Follow output aliases from used CTEs/subqueries, not unused WITH clauses.
        for column in expression.find_all(exp.Column):
            if column.find_ancestor(exp.Select) is not owner.expression:
                continue
            for alias, source in active_sources(owner).items():
                if column.table and column.table != alias:
                    continue
                if not isinstance(source, Scope) or (id(source), column.name) in seen:
                    continue
                for projection in source.expression.selects:
                    if projection.alias_or_name == column.name:
                        inherited = output_aggregates(projection, source, seen | {(id(source), column.name)})
                        if inherited:
                            output = expression.this if isinstance(expression, exp.Alias) else expression
                            while isinstance(output, exp.Paren):
                                output = output.this
                            if not isinstance(output, exp.Column):
                                raise ValueError("SQL transforms a planned aggregate through a derived output alias")
                        found |= inherited
        return found

    actual = set()
    for projection in scope.expression.selects:
        actual |= output_aggregates(projection, scope, set())
    expected = {(a.function, a.source.table, a.source.column) for a in plan.aggregates}
    duplicate_latest = sum(a.function == "max" and a.source in plan.latest_by
                           and a.source in plan.group_by and a.source in plan.projections for a in plan.aggregates)
    if plan.order_by:
        def order_key(expression, seen=None):
            seen = seen or set()
            if isinstance(expression, exp.Literal) and expression.is_int:
                index = int(expression.this) - 1
                if 0 <= index < len(scope.expression.selects):
                    return order_key(scope.expression.selects[index], seen)
                return None
            if isinstance(expression, exp.Alias):
                return order_key(expression.this, seen)
            if isinstance(expression, exp.Column) and not expression.table and expression.name not in seen:
                projection = next((p for p in scope.expression.selects
                                   if isinstance(p, exp.Alias) and p.alias == expression.name), None)
                if projection is not None:
                    return order_key(projection, seen | {expression.name})
            aggregates = output_aggregates(expression, scope, set())
            if len(aggregates) == 1:
                return ("aggregate", *next(iter(aggregates)))
            if isinstance(expression, exp.Column):
                ref = origin(expression, scope, set())
                return ("column", *ref) if ref else None
            return None

        expected_order = []
        for item in plan.order_by:
            aggregate = next((a for a in plan.aggregates if a.alias == item.output), None)
            if aggregate:
                key = ("aggregate", aggregate.function, aggregate.source.table, aggregate.source.column)
            else:
                ref = next(ref for ref in plan.projections if ref.column == item.output)
                key = ("column", ref.table, ref.column)
            expected_order.append((key, item.direction))
        ordering = scope.expression.args.get("order")
        actual_order = [(order_key(item.this), "desc" if item.args.get("desc") else "asc")
                        for item in ordering.expressions] if ordering else []
        if expected_order != actual_order:
            raise ValueError("SQL ordering or tie breakers differ from the validated plan")
    if plan.result_limit is not None:
        limit = scope.expression.args.get("limit")
        value = limit.expression if limit else None
        if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) != plan.result_limit:
            raise ValueError("SQL result limit differs from the validated top-N plan")
        offset = scope.expression.args.get("offset")
        if offset and (not isinstance(offset.expression, exp.Literal) or offset.expression.this != "0"):
            raise ValueError("SQL OFFSET changes the planned top-N result")
    # Under a mandatory global-latest predicate, a grouped timestamp is the same
    # value as MAX(timestamp). The mandatory predicate is still checked below.
    direct_group = scope.expression.args.get("group")
    direct_group_refs = {origin(c, scope, set()) for c in direct_group.find_all(exp.Column)} if direct_group else set()
    direct_outputs = {
        origin(p.this if isinstance(p, exp.Alias) else p, scope, set())
        for p in scope.expression.selects
        if isinstance(p.this if isinstance(p, exp.Alias) else p, exp.Column)
    }
    fixed_latest = {(r.table, r.column) for r in plan.latest_by}
    for function, table, column in expected - actual:
        if function == "max" and (table, column) in fixed_latest & direct_group_refs & direct_outputs:
            actual.add((function, table, column))
    if expected != actual:
        raise ValueError(f"SQL output aggregates differ from the plan: missing {sorted(expected - actual)}, extra {sorted(actual - expected)}")
    named_metrics = {a.alias: (a.function, a.source.table, a.source.column) for a in plan.aggregates}
    for projection in scope.expression.selects:
        # Equivalent aliases are fine, but a declared metric name must not label
        # another metric. Set-based aggregate comparison alone misses this swap.
        named_metric = named_metrics.get(projection.alias_or_name)
        value = projection.this if isinstance(projection, exp.Alias) else projection
        fixed_timestamp = (named_metric and named_metric[0] == 'max'
                           and named_metric[1:] in fixed_latest & direct_group_refs
                           and isinstance(value, exp.Column) and origin(value, scope, set()) == named_metric[1:])
        if named_metric and not fixed_timestamp and output_aggregates(projection, scope, set()) != {named_metric}:
            raise ValueError("SQL output alias labels a different planned metric")
    projected = set()
    for projection in scope.expression.selects:
        for column in projection.find_all(exp.Column):
            if column.find_ancestor(exp.Select) is scope.expression and not column.find_ancestor(exp.AggFunc):
                ref = origin(column, scope, set())
                if ref:
                    projected.add(ref)
    expected_projections = {(ref.table, ref.column) for ref in plan.projections}
    if expected_projections - projected:
        raise ValueError(f"SQL omitted planned output dimensions: {sorted(expected_projections - projected)}")
    if len(scope.expression.selects) != len(plan.projections) + len(plan.aggregates) - duplicate_latest:
        raise ValueError("SQL output column count differs from the validated plan")

    group_scope = scope
    while (not group_scope.expression.args.get('group') and len(active_sources(group_scope)) == 1
           and not any(isinstance(projection, exp.AggFunc) or projection.find(exp.AggFunc)
                       for projection in group_scope.expression.selects)):
        source = next(iter(active_sources(group_scope).values()))
        if not isinstance(source, Scope):
            break
        group_scope = source
    effective_group = group_scope.expression.args.get('group')
    actual_group = set()
    if effective_group:
        for item in effective_group.expressions:
            expression = item
            if isinstance(item, exp.Literal) and item.is_int:
                index = int(item.this) - 1
                if 0 <= index < len(group_scope.expression.selects):
                    expression = group_scope.expression.selects[index]
            elif isinstance(item, exp.Column) and not item.table:
                expression = next((p for p in group_scope.expression.selects
                                   if isinstance(p, exp.Alias) and p.alias == item.name), item)
            for column in expression.find_all(exp.Column):
                ref = origin(column, group_scope, set())
                if ref:
                    actual_group.add(ref)
    expected_group = {(ref.table, ref.column) for ref in plan.group_by}
    if actual_group != expected_group:
        raise ValueError(f"SQL grouping differs from the planned grain: expected {sorted(expected_group)}, actual {sorted(actual_group)}")
    group = scope.expression.args.get("group")
    if group:
        grouped = {origin(column, scope, set()) for column in group.find_all(exp.Column)}
        metric_refs = {(a.source.table, a.source.column) for a in plan.aggregates}
        if (grouped & metric_refs) - fixed_latest:
            raise ValueError("SQL groups by the metric being aggregated, changing the result grain")

    def scalar(value):
        if isinstance(value, exp.Cast):
            return scalar(value.this)
        if isinstance(value, exp.Literal):
            return str(value.this)
        if isinstance(value, exp.Boolean):
            return str(value.this).lower()
        if isinstance(value, exp.Neg) and isinstance(value.this, exp.Literal):
            return '-' + str(value.this.this)
        return None

    operator_names = {exp.EQ: 'eq', exp.NEQ: 'ne', exp.GT: 'gt', exp.GTE: 'gte',
                      exp.LT: 'lt', exp.LTE: 'lte', exp.Like: 'like', exp.ILike: 'ilike'}
    reverse = {'gt': 'lt', 'gte': 'lte', 'lt': 'gt', 'lte': 'gte', 'eq': 'eq', 'ne': 'ne'}

    def mandatory_atoms(node):
        if isinstance(node, exp.Paren):
            return mandatory_atoms(node.this)
        if isinstance(node, exp.And):
            return mandatory_atoms(node.this) + mandatory_atoms(node.expression)
        # A match under OR does not prove the predicate restricts every output row.
        if isinstance(node, exp.Not) and isinstance(node.this, exp.Is):
            return [node]
        return [node]

    def scalar_max(query, visited=None):
        visited = visited or set()
        if (id(query) in visited or not isinstance(query, exp.Select) or len(query.selects) != 1
                or any(query.args.get(key) for key in ('group', 'having', 'joins', 'limit', 'offset', 'distinct'))):
            return None
        owner = scopes_by_expression.get(id(query)) or build_scope(query)
        if owner is None:
            return None
        value = query.selects[0]
        value = value.this if isinstance(value, exp.Alias) else value
        if isinstance(value, exp.Max) and isinstance(value.this, exp.Column):
            return query, owner, value
        sources = active_sources(owner)
        if isinstance(value, exp.Column) and not query.args.get('where') and len(sources) == 1:
            alias, source = next(iter(sources.items()))
            if (isinstance(source, Scope) and (not value.table or value.table == alias)
                    and len(source.expression.selects) == 1
                    and source.expression.selects[0].alias_or_name == value.name):
                return scalar_max(source.expression, visited | {id(query)})
        return None

    def enforced_predicates(owner, seen):
        if id(owner) in seen:
            return set()
        seen = seen | {id(owner)}
        predicates = set()
        where = owner.expression.args.get('where')
        if where:
            for atom in mandatory_atoms(where.this):
                atom_predicates = set()
                op = operator_names.get(type(atom))
                if op:
                    left, right = atom.this, atom.expression
                    if not isinstance(left, exp.Column) and isinstance(right, exp.Column) and op in reverse:
                        left, right, op = right, left, reverse[op]
                    value = scalar(right)
                    if isinstance(left, exp.Column) and value is not None:
                        ref = origin(left, owner, set())
                        if ref:
                            atom_predicates.add((*ref, op, (value,)))
                    elif op == 'eq' and isinstance(left, exp.Column) and isinstance(right, exp.Subquery):
                        resolved = scalar_max(right.this)
                        if resolved is None:
                            raise ValueError("SQL predicate is not a verifiable latest snapshot expression")
                        inner, inner_scope, maxima = resolved
                        if (isinstance(maxima, exp.Max) and isinstance(maxima.this, exp.Column)
                                and not any(inner.args.get(key) for key in ('group', 'having', 'joins', 'limit'))):
                            inner_tables = list(inner.find_all(exp.Table))
                            ref = origin(left, owner, set())
                            # FAB-bound tables may repeat the same FAB predicate in
                            # the MAX subquery. Other filters would change its scope.
                            inner_where = inner.args.get('where')
                            fab_only = False
                            if inner_where and len(inner_tables) == 1:
                                condition = inner_where.this
                                fab_only = (isinstance(condition, exp.EQ)
                                            and isinstance(condition.this, exp.Column)
                                            and condition.this.name == 'fab_id'
                                            and scalar(condition.expression) == inner_tables[0].db)
                            permitted_scope = inner_where is None or fab_only
                            if ref and inner_scope and plan.latest_scope == 'filtered':
                                actual_filters = enforced_predicates(inner_scope, seen)
                                expected_filters = {
                                    (p.source.table, p.source.column, p.operator,
                                     tuple(sorted(p.values)) if p.operator == 'in' else tuple(p.values))
                                    for p in plan.filters if p.source.table == ref[0]
                                }
                                redundant_fab = (ref[0], 'fab_id', 'eq', (ref[0].split('.')[0],))
                                atoms = mandatory_atoms(inner_where.this) if inner_where else []
                                permitted_scope = (actual_filters - {redundant_fab} == expected_filters - {redundant_fab}
                                                   and len(atoms) == len(actual_filters))
                            if (ref and len(inner_tables) == 1
                                    and permitted_scope and inner_scope
                                    and origin(maxima.this, inner_scope, set()) == ref
                                    and ref == (f'{inner_tables[0].db}.{inner_tables[0].name}', maxima.this.name)):
                                atom_predicates.add((*ref, 'latest', ()))
                elif isinstance(atom, exp.In) and isinstance(atom.this, exp.Column):
                    ref = origin(atom.this, owner, set())
                    values = [scalar(item) for item in atom.expressions]
                    if ref and values and all(value is not None for value in values):
                        atom_predicates.add((*ref, 'in', tuple(sorted(values))))
                elif isinstance(atom, exp.Is) and isinstance(atom.this, exp.Column) and isinstance(atom.expression, exp.Null):
                    ref = origin(atom.this, owner, set())
                    if ref:
                        atom_predicates.add((*ref, 'is_null', ()))
                elif isinstance(atom, exp.Not) and isinstance(atom.this, exp.Is):
                    null_check = atom.this
                    if isinstance(null_check.this, exp.Column) and isinstance(null_check.expression, exp.Null):
                        ref = origin(null_check.this, owner, set())
                        if ref:
                            atom_predicates.add((*ref, 'not_null', ()))
                if not atom_predicates:
                    raise ValueError("SQL predicate does not match a static filter or latest snapshot contract")
                predicates |= atom_predicates
        # A sole derived source carries its WHERE constraints to the output. For
        # outer joins/unions the same implication needs a stronger lineage proof.
        if len(active_sources(owner)) == 1 and not owner.expression.args.get('joins'):
            source = next(iter(active_sources(owner).values()))
            if isinstance(source, Scope):
                predicates |= enforced_predicates(source, seen)
        return predicates

    actual_filters = enforced_predicates(scope, set())
    for latest in plan.latest_by:
        if (latest.table, latest.column, 'latest', ()) not in actual_filters:
            raise ValueError(f'SQL does not select the latest snapshot: {latest.table}.{latest.column}')
    for predicate in plan.filters:
        values = tuple(sorted(predicate.values)) if predicate.operator == 'in' else tuple(predicate.values)
        expected_filter = (predicate.source.table, predicate.source.column, predicate.operator, values)
        if expected_filter not in actual_filters:
            raise ValueError(f'SQL does not enforce planned predicate: {expected_filter}')

    allowed_filters = {
        (p.source.table, p.source.column, p.operator,
         tuple(sorted(p.values)) if p.operator == 'in' else tuple(p.values))
        for p in plan.filters
    }
    allowed_filters |= {(ref.table, ref.column, 'latest', ()) for ref in plan.latest_by}
    allowed_filters |= {(ref, 'fab_id', 'eq', (ref.split('.')[0],)) for ref in plan.tables}
    if actual_filters - allowed_filters:
        raise ValueError(f'SQL adds unplanned predicates: {sorted(actual_filters - allowed_filters)}')

    join_scope = scope
    while not join_scope.expression.args.get('joins') and len(active_sources(join_scope)) == 1:
        source = next(iter(active_sources(join_scope).values()))
        if not isinstance(source, Scope):
            break
        join_scope = source
    actual_join_keys = set()
    for join in join_scope.expression.args.get('joins', []):
        side = str(join.args.get('side') or 'inner').lower()
        condition = join.args.get('on')
        if condition is None or side not in {'inner', 'left'}:
            raise ValueError('SQL join requires supported explicit ON keys')
        target = join.this
        right_table = f'{target.db}.{target.name}' if isinstance(target, exp.Table) else None
        keys_for_join = set()
        for atom in mandatory_atoms(condition):
            if not isinstance(atom, exp.EQ) or not isinstance(atom.this, exp.Column) or not isinstance(atom.expression, exp.Column):
                continue
            left, right = origin(atom.this, join_scope, set()), origin(atom.expression, join_scope, set())
            if left and right and left[0] != right[0]:
                if side == 'inner':
                    pair = tuple(sorted((left, right)))
                elif right_table == right[0]:
                    pair = (left, right)
                elif right_table == left[0]:
                    pair = (right, left)
                else:
                    continue
                keys_for_join.add((side, pair))
        if not keys_for_join:
            raise ValueError('SQL join has no verifiable mandatory equality keys')
        actual_join_keys |= keys_for_join
    expected_join_keys = set()
    for join in plan.joins:
        pair = ((join.left.table, join.left.column), (join.right.table, join.right.column))
        if join.kind == 'inner':
            pair = tuple(sorted(pair))
        expected_join_keys.add((join.kind, pair))
    if actual_join_keys != expected_join_keys:
        raise ValueError('SQL join keys or join type differ from the validated plan')
