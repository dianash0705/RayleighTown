from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json

from log_registry import (
    all_event_names,
    resolve_native_event_ids_for_name,
    resolve_native_event_ids_matching_pattern,
)


@dataclass(frozen=True)
class FilterRule:
    field: str
    operator: str
    value: str = ""
    values: tuple[str, ...] = ()

    def resolved_values(self) -> tuple[str, ...]:
        if self.values:
            return self.values
        if self.operator in {"in", "not_in"}:
            return _split_filter_values(self.value)
        if self.value and "," in self.value:
            return _split_filter_values(self.value)
        if self.value:
            return (self.value,)
        return ()


@dataclass(frozen=True)
class AlertQueryFilters:
    rules: tuple[FilterRule, ...] = ()
    min_confidence: int | None = None
    window_start_ms: int | None = None
    window_end_ms: int | None = None
    sort_key: str = "confidence"
    sort_direction: str = "desc"


ALLOWED_SORT_KEYS = {
    "alertID",
    "nativeEventID",
    "eventName",
    "endpointID",
    "tsBegin",
    "tsEnd",
    "periodTs",
    "confidence",
}

FILTER_FIELDS = {
    "endpointID",
    "nativeEventID",
    "eventName",
    "confidence",
    "periodTs",
    "alertID",
}

FILTER_OPERATORS = {
    "eq",
    "ne",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "like",
    "not_like",
}

NUMERIC_FIELDS = {"nativeEventID", "confidence", "periodTs", "alertID"}
STRING_FIELDS = {"endpointID", "eventName"}

LIST_VALUE_SEPARATORS = (",", ";")


def _split_filter_values(value: str) -> tuple[str, ...]:
    normalized = value
    for separator in LIST_VALUE_SEPARATORS[1:]:
        normalized = normalized.replace(separator, LIST_VALUE_SEPARATORS[0])
    return tuple(part.strip() for part in normalized.split(LIST_VALUE_SEPARATORS[0]) if part.strip())


def _maybe_coerce_list_operator(
    field: str,
    operator: str,
    value: str,
) -> tuple[str, str, tuple[str, ...]]:
    values = _split_filter_values(value) if value else ()
    if len(values) <= 1:
        return operator, value, ()

    if field in NUMERIC_FIELDS | STRING_FIELDS and operator in {"eq", "ne"}:
        return ("in" if operator == "eq" else "not_in"), value, values

    if operator in {"in", "not_in"}:
        return operator, value, values

    return operator, value, values


def _parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _parse_iso_ms(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    normalized = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _normalize_operator(operator: str) -> str:
    normalized = (operator or "eq").strip().lower()
    aliases = {
        "is": "eq",
        "is_not": "ne",
        "not": "ne",
        "is_one_of": "in",
        "one_of": "in",
        "is_not_one_of": "not_in",
        "not_one_of": "not_in",
        "contains": "like",
        "not_contains": "not_like",
    }
    return aliases.get(normalized, normalized)


def _normalize_field(field: str) -> str:
    mapping = {
        "eventid": "nativeEventID",
        "nativeeventid": "nativeEventID",
        "endpointid": "endpointID",
        "eventname": "eventName",
        "periodts": "periodTs",
        "alertid": "alertID",
    }
    cleaned = (field or "").strip()
    return mapping.get(cleaned.lower(), cleaned)


def parse_filter_rules(raw_filters) -> tuple[FilterRule, ...]:
    if raw_filters in (None, ""):
        return ()

    payload = raw_filters
    if isinstance(raw_filters, str):
        try:
            payload = json.loads(raw_filters)
        except json.JSONDecodeError as error:
            raise ValueError("Invalid filters JSON.") from error

    if not isinstance(payload, list):
        raise ValueError("Filters must be a JSON array.")

    rules: list[FilterRule] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each filter rule must be an object.")

        field = _normalize_field(str(item.get("field", "")).strip())
        operator = _normalize_operator(str(item.get("operator") or item.get("op") or "eq"))
        value = str(item.get("value", "")).strip()
        raw_values = item.get("values")
        values: tuple[str, ...] = ()
        if isinstance(raw_values, list):
            values = tuple(str(entry).strip() for entry in raw_values if str(entry).strip())
        elif operator in {"in", "not_in"} and value:
            values = _split_filter_values(value)
        elif value:
            operator, value, parsed_values = _maybe_coerce_list_operator(field, operator, value)
            if parsed_values:
                values = parsed_values

        if field not in FILTER_FIELDS:
            raise ValueError(f"Unsupported filter field: {field}")
        if operator not in FILTER_OPERATORS:
            raise ValueError(f"Unsupported filter operator: {operator}")

        if operator in {"in", "not_in"}:
            if not values:
                continue
            rules.append(FilterRule(field=field, operator=operator, value=value, values=values))
            continue

        if value == "":
            continue

        if field in NUMERIC_FIELDS and operator in {"like", "not_like"}:
            raise ValueError(f"Operator '{operator}' is not supported for field '{field}'.")

        rules.append(FilterRule(field=field, operator=operator, value=value, values=values))

    return tuple(rules)


def _legacy_rules_from_args(args) -> tuple[FilterRule, ...]:
    rules: list[FilterRule] = []

    endpoint_id = (args.get("endpointID") or "").strip()
    if endpoint_id:
        rules.append(FilterRule(field="endpointID", operator="eq", value=endpoint_id))

    native_event_id = args.get("nativeEventID")
    if native_event_id not in (None, ""):
        rules.append(FilterRule(field="nativeEventID", operator="eq", value=str(native_event_id).strip()))

    event_name = (args.get("eventName") or "").strip()
    if event_name:
        rules.append(FilterRule(field="eventName", operator="eq", value=event_name))

    return tuple(rules)


def resolve_time_window(args, *, default_preset: str = "last_week") -> tuple[int | None, int | None, str]:
    """Shared time-window resolution for dashboard, entities, and alerts."""
    time_preset = (args.get("timePreset") or default_preset).strip().lower()
    now = datetime.now(timezone.utc)
    window_end_ms = _parse_iso_ms(args.get("timeTo"))
    window_start_ms = _parse_iso_ms(args.get("timeFrom"))

    if window_start_ms is None and window_end_ms is None:
        if time_preset == "last_week":
            window_start_ms = int((now - timedelta(days=7)).timestamp() * 1000)
            window_end_ms = int(now.timestamp() * 1000)
        elif time_preset == "all":
            window_start_ms = None
            window_end_ms = None
        elif time_preset == "custom":
            window_start_ms = int((now - timedelta(days=7)).timestamp() * 1000)
            window_end_ms = int(now.timestamp() * 1000)
        else:
            window_start_ms = int((now - timedelta(hours=24)).timestamp() * 1000)
            window_end_ms = int(now.timestamp() * 1000)
    elif window_start_ms is None:
        window_start_ms = 0
    elif window_end_ms is None:
        window_end_ms = int(now.timestamp() * 1000)

    return window_start_ms, window_end_ms, time_preset


def build_alert_filters(args) -> AlertQueryFilters:
    window_start_ms, window_end_ms, _time_preset = resolve_time_window(args, default_preset="all")

    sort_key = (args.get("sort") or "confidence").strip()
    if sort_key not in ALLOWED_SORT_KEYS:
        sort_key = "confidence"

    sort_direction = (args.get("order") or "desc").strip().lower()
    if sort_direction not in {"asc", "desc"}:
        sort_direction = "desc"

    rules = parse_filter_rules(args.get("filters"))
    if not rules:
        rules = _legacy_rules_from_args(args)

    return AlertQueryFilters(
        rules=rules,
        min_confidence=_parse_int(args.get("minConfidence")),
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
        sort_key=sort_key,
        sort_direction=sort_direction,
    )


def _sql_like_pattern(value: str) -> str:
    escaped = (
        value.strip()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    if "%" not in value and "_" not in value:
        return f"%{escaped}%"
    return escaped


def _event_names_to_native_ids(names: tuple[str, ...]) -> set[int]:
    native_event_ids: set[int] = set()
    for name in names:
        native_event_ids.update(resolve_native_event_ids_for_name(name))
    return native_event_ids


def _event_name_rule_to_native_ids(rule: FilterRule) -> set[int] | None:
    if rule.operator == "eq":
        return resolve_native_event_ids_for_name(rule.value)
    if rule.operator == "in":
        return _event_names_to_native_ids(rule.resolved_values())
    if rule.operator == "ne":
        matches = resolve_native_event_ids_for_name(rule.value)
        all_ids: set[int] = set()
        for config_name in all_event_names():
            all_ids.update(resolve_native_event_ids_for_name(config_name))
        return all_ids - matches
    if rule.operator == "not_in":
        matches = _event_names_to_native_ids(rule.resolved_values())
        all_ids: set[int] = set()
        for config_name in all_event_names():
            all_ids.update(resolve_native_event_ids_for_name(config_name))
        return all_ids - matches
    if rule.operator == "like":
        return resolve_native_event_ids_matching_pattern(_sql_like_pattern(rule.value))
    if rule.operator == "not_like":
        matches = resolve_native_event_ids_matching_pattern(_sql_like_pattern(rule.value))
        all_ids: set[int] = set()
        for config_name in all_event_names():
            all_ids.update(resolve_native_event_ids_for_name(config_name))
        return all_ids - matches
    return None


def apply_filter_rules(filters: AlertQueryFilters) -> tuple[list[str], list]:
    clauses: list[str] = []
    params: list = []

    for rule in filters.rules:
        if rule.field == "eventName":
            native_event_ids = _event_name_rule_to_native_ids(rule)
            if native_event_ids is None:
                continue
            if not native_event_ids:
                clauses.append("1 = 0")
                continue
            placeholders = ", ".join("?" for _ in native_event_ids)
            if rule.operator in {"eq", "like", "in"}:
                clauses.append(f"g.nativeEventID IN ({placeholders})")
            else:
                clauses.append(f"g.nativeEventID NOT IN ({placeholders})")
            params.extend(sorted(native_event_ids))
            continue

        column_map = {
            "endpointID": "g.endpointID",
            "nativeEventID": "g.nativeEventID",
            "confidence": "g.confidence",
            "periodTs": "g.periodTs",
            "alertID": "g.alertGroupID",
        }
        column = column_map[rule.field]

        if rule.field in NUMERIC_FIELDS:
            if rule.operator in {"like", "not_like"}:
                continue
            if rule.operator == "in":
                numbers = []
                for entry in rule.resolved_values():
                    number = _parse_float(entry)
                    if number is not None:
                        numbers.append(int(number) if rule.field == "nativeEventID" else number)
                if not numbers:
                    continue
                placeholders = ", ".join("?" for _ in numbers)
                clauses.append(f"{column} IN ({placeholders})")
                params.extend(numbers)
                continue
            if rule.operator == "not_in":
                numbers = []
                for entry in rule.resolved_values():
                    number = _parse_float(entry)
                    if number is not None:
                        numbers.append(int(number) if rule.field == "nativeEventID" else number)
                if not numbers:
                    continue
                placeholders = ", ".join("?" for _ in numbers)
                clauses.append(f"{column} NOT IN ({placeholders})")
                params.extend(numbers)
                continue

            resolved_values = rule.resolved_values()
            if len(resolved_values) > 1 and rule.operator in {"eq", "ne"}:
                numbers = []
                for entry in resolved_values:
                    number = _parse_float(entry)
                    if number is not None:
                        numbers.append(int(number) if rule.field == "nativeEventID" else number)
                if not numbers:
                    continue
                placeholders = ", ".join("?" for _ in numbers)
                if rule.operator == "eq":
                    clauses.append(f"{column} IN ({placeholders})")
                else:
                    clauses.append(f"{column} NOT IN ({placeholders})")
                params.extend(numbers)
                continue

            number = _parse_float(rule.value)
            if number is None:
                continue
            if rule.operator == "eq":
                clauses.append(f"{column} = ?")
                params.append(number if rule.field != "nativeEventID" else int(number))
            elif rule.operator == "ne":
                clauses.append(f"{column} != ?")
                params.append(number if rule.field != "nativeEventID" else int(number))
            elif rule.operator == "gt":
                clauses.append(f"{column} > ?")
                params.append(number)
            elif rule.operator == "gte":
                clauses.append(f"{column} >= ?")
                params.append(number)
            elif rule.operator == "lt":
                clauses.append(f"{column} < ?")
                params.append(number)
            elif rule.operator == "lte":
                clauses.append(f"{column} <= ?")
                params.append(number)
            continue

        if rule.field in STRING_FIELDS:
            if rule.operator == "in":
                values = rule.resolved_values()
                if not values:
                    continue
                placeholders = ", ".join("?" for _ in values)
                clauses.append(f"{column} IN ({placeholders})")
                params.extend(values)
                continue
            if rule.operator == "not_in":
                values = rule.resolved_values()
                if not values:
                    continue
                placeholders = ", ".join("?" for _ in values)
                clauses.append(f"{column} NOT IN ({placeholders})")
                params.extend(values)
                continue
            if rule.operator == "eq":
                clauses.append(f"{column} = ?")
                params.append(rule.value)
            elif rule.operator == "ne":
                clauses.append(f"{column} != ?")
                params.append(rule.value)
            elif rule.operator == "like":
                clauses.append(f"{column} LIKE ? ESCAPE '\\'")
                params.append(_sql_like_pattern(rule.value))
            elif rule.operator == "not_like":
                clauses.append(f"{column} NOT LIKE ? ESCAPE '\\'")
                params.append(_sql_like_pattern(rule.value))

    if filters.min_confidence is not None:
        clauses.append("g.confidence >= ?")
        params.append(filters.min_confidence)

    if filters.window_start_ms is not None and filters.window_end_ms is not None:
        clauses.append("g.tsBegin <= ?")
        clauses.append("g.tsEnd >= ?")
        params.extend([filters.window_end_ms, filters.window_start_ms])

    return clauses, params
