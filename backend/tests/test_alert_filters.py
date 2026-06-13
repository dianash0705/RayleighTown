"""Tests for advanced alert filter rules."""

import pytest

from alert_filters import (
    AlertQueryFilters,
    FilterRule,
    apply_filter_rules,
    build_alert_filters,
    parse_filter_rules,
)


class _Args(dict):
    def get(self, key, default=None):
        return super().get(key, default)


@pytest.mark.unit
class TestParseFilterRules:
    def test_parses_multiple_rules_with_aliases(self):
        payload = [
            {"field": "nativeEventID", "operator": "ne", "value": "1"},
            {"field": "nativeEventID", "operator": "is_not", "value": "2"},
            {"field": "endpointID", "operator": "contains", "value": "srv"},
        ]
        rules = parse_filter_rules(payload)

        assert len(rules) == 3
        assert rules[0] == FilterRule(field="nativeEventID", operator="ne", value="1")
        assert rules[1].operator == "ne"
        assert rules[2].operator == "like"

    def test_rejects_invalid_field(self):
        with pytest.raises(ValueError, match="Unsupported filter field"):
            parse_filter_rules([{"field": "unknown", "operator": "eq", "value": "1"}])

    def test_rejects_like_on_numeric_field(self):
        with pytest.raises(ValueError, match="not supported"):
            parse_filter_rules(
                [{"field": "nativeEventID", "operator": "like", "value": "1"}]
            )


@pytest.mark.unit
class TestBuildAlertFilters:
    def test_prefers_json_filters_over_legacy_args(self):
        args = _Args(
            {
                "filters": '[{"field":"nativeEventID","operator":"ne","value":"1"}]',
                "nativeEventID": "99",
            }
        )
        filters = build_alert_filters(args)

        assert filters.rules == (FilterRule(field="nativeEventID", operator="ne", value="1"),)

    def test_legacy_args_become_eq_rules(self):
        args = _Args({"endpointID": "host-a", "nativeEventID": "7"})
        filters = build_alert_filters(args)

        assert FilterRule(field="endpointID", operator="eq", value="host-a") in filters.rules
        assert FilterRule(field="nativeEventID", operator="eq", value="7") in filters.rules


@pytest.mark.unit
class TestApplyFilterRules:
    def test_builds_numeric_ne_clauses(self):
        filters = AlertQueryFilters(
            rules=(
                FilterRule(field="nativeEventID", operator="ne", value="1"),
                FilterRule(field="nativeEventID", operator="ne", value="2"),
            )
        )
        clauses, params = apply_filter_rules(filters)

        assert "g.nativeEventID != ?" in clauses
        assert params == [1, 2]

    def test_builds_string_like_clause(self):
        filters = AlertQueryFilters(
            rules=(FilterRule(field="endpointID", operator="like", value="srv"),)
        )
        clauses, params = apply_filter_rules(filters)

        assert "g.endpointID LIKE ? ESCAPE" in clauses[0]
        assert params == ["%srv%"]

    def test_builds_numeric_in_clause(self):
        filters = AlertQueryFilters(
            rules=(
                FilterRule(
                    field="nativeEventID",
                    operator="in",
                    value="",
                    values=("1", "2", "3"),
                ),
            )
        )
        clauses, params = apply_filter_rules(filters)

        assert "g.nativeEventID IN (?, ?, ?)" in clauses[0]
        assert params == [1, 2, 3]

    def test_eq_with_comma_values_builds_in_clause(self):
        filters = AlertQueryFilters(
            rules=(FilterRule(field="nativeEventID", operator="eq", value="2720,2726"),)
        )
        clauses, params = apply_filter_rules(filters)

        assert "g.nativeEventID IN (?, ?)" in clauses[0]
        assert params == [2720, 2726]

    def test_parses_in_operator_with_comma_values(self):
        rules = parse_filter_rules(
            [{"field": "nativeEventID", "operator": "in", "value": "1, 2, 3"}]
        )
        assert rules[0].operator == "in"
        assert rules[0].resolved_values() == ("1", "2", "3")

    def test_coerces_eq_with_comma_separated_values_to_in(self):
        rules = parse_filter_rules(
            [{"field": "nativeEventID", "operator": "eq", "value": "2720,2726"}]
        )
        assert len(rules) == 1
        assert rules[0].operator == "in"
        assert rules[0].resolved_values() == ("2720", "2726")
