const form = document.getElementById("queryForm");
const toggleFiltersButton = document.getElementById("toggleFilters");
const endpointInput = document.getElementById("endpointID");
const eventNameInput = document.getElementById("eventName");
const nativeEventIDInput = document.getElementById("nativeEventID");
const minConfidenceInput = document.getElementById("minConfidence");
const filterRulesList = document.getElementById("filterRulesList");
const addFilterRuleButton = document.getElementById("addFilterRule");
const resetFiltersButton = document.getElementById("resetFilters");
const activeFilterChips = document.getElementById("activeFilterChips");
const statusEl = document.getElementById("status");
const summaryRow = document.getElementById("summaryRow");
const chipAlertCount = document.getElementById("chipAlertCount");
const chipHighConfidenceCount = document.getElementById("chipHighConfidenceCount");
const chipPeriodCount = document.getElementById("chipPeriodCount");
const chipEndpointCount = document.getElementById("chipEndpointCount");
const table = document.getElementById("alertsTable");
const tbody = table.querySelector("tbody");
const sortableHeaders = Array.from(table.querySelectorAll("th.sortable"));
const TABLE_COLSPAN = 7;
const SAME_DISPLAY_TIME_BUCKET_MS = 500;

let currentAlerts = [];
let expandedAlertId = null;
let sortState = {
  key: "confidence",
  direction: "desc",
};

let timeRangeControls = null;
if (window.TimeRangeControls) {
  timeRangeControls = window.TimeRangeControls.init({
    defaultPreset: "all",
    onChange: function () {
      renderActiveFilterChips();
      loadAlerts();
    },
    applyButtonId: "applyTimeRange",
  });
}

const FILTER_FIELD_OPTIONS = [
  { value: "endpointID", label: "Endpoint ID", type: "string" },
  { value: "nativeEventID", label: "Event ID", type: "numeric" },
  { value: "eventName", label: "Event name", type: "string" },
  { value: "confidence", label: "Confidence", type: "numeric" },
  { value: "periodTs", label: "Period (ms)", type: "numeric" },
  { value: "alertID", label: "Alert ID", type: "numeric" },
];

const FILTER_OPERATOR_OPTIONS = {
  string: [
    { value: "eq", label: "is" },
    { value: "ne", label: "is not" },
    { value: "in", label: "is one of" },
    { value: "not_in", label: "is not one of" },
    { value: "like", label: "contains" },
    { value: "not_like", label: "does not contain" },
  ],
  numeric: [
    { value: "eq", label: "is" },
    { value: "ne", label: "is not" },
    { value: "in", label: "is one of" },
    { value: "not_in", label: "is not one of" },
    { value: "gt", label: "greater than" },
    { value: "gte", label: "at least" },
    { value: "lt", label: "less than" },
    { value: "lte", label: "at most" },
  ],
};

function getFieldType(field) {
  const match = FILTER_FIELD_OPTIONS.find((option) => option.value === field);
  return match ? match.type : "string";
}

function getOperatorOptions(field) {
  return FILTER_OPERATOR_OPTIONS[getFieldType(field)] || FILTER_OPERATOR_OPTIONS.string;
}

function syncFilterRuleOperators(row) {
  const fieldSelect = row.querySelector(".filter-rule-field");
  const operatorSelect = row.querySelector(".filter-rule-operator");
  const previousOperator = operatorSelect.value;
  const options = getOperatorOptions(fieldSelect.value);

  operatorSelect.innerHTML = "";
  for (const option of options) {
    const element = document.createElement("option");
    element.value = option.value;
    element.textContent = option.label;
    operatorSelect.appendChild(element);
  }

  if (options.some((option) => option.value === previousOperator)) {
    operatorSelect.value = previousOperator;
  }
}

function createFilterRuleRow(rule) {
  const row = document.createElement("div");
  row.className = "filter-rule-row";

  const fieldSelect = document.createElement("select");
  fieldSelect.className = "filter-rule-field";
  for (const option of FILTER_FIELD_OPTIONS) {
    const element = document.createElement("option");
    element.value = option.value;
    element.textContent = option.label;
    fieldSelect.appendChild(element);
  }
  fieldSelect.value = rule && rule.field ? rule.field : "nativeEventID";

  const operatorSelect = document.createElement("select");
  operatorSelect.className = "filter-rule-operator";

  const valueInput = document.createElement("input");
  valueInput.className = "filter-rule-value";
  valueInput.type = "text";
  valueInput.placeholder = "Value or comma-separated list";
  valueInput.value = rule && rule.values && rule.values.length
    ? rule.values.join(", ")
    : (rule && rule.value ? String(rule.value) : "");

  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "secondary-button filter-rule-remove";
  removeButton.textContent = "Remove";
  removeButton.addEventListener("click", function () {
    row.remove();
  });

  fieldSelect.addEventListener("change", function () {
    syncFilterRuleOperators(row);
  });

  row.appendChild(fieldSelect);
  row.appendChild(operatorSelect);
  row.appendChild(valueInput);
  row.appendChild(removeButton);
  syncFilterRuleOperators(row);

  if (rule && rule.operator) {
    operatorSelect.value = rule.operator;
  }

  return row;
}

function addFilterRuleRow(rule) {
  filterRulesList.appendChild(createFilterRuleRow(rule));
}

function collectFilterRulesFromDom() {
  const rules = [];
  for (const row of filterRulesList.querySelectorAll(".filter-rule-row")) {
    const field = row.querySelector(".filter-rule-field").value;
    const operator = row.querySelector(".filter-rule-operator").value;
    const value = row.querySelector(".filter-rule-value").value.trim();
    if (!value) {
      continue;
    }
    if (operator === "in" || operator === "not_in") {
      const values = value.split(",").map(function (part) {
        return part.trim();
      }).filter(Boolean);
      if (values.length) {
        rules.push({ field: field, operator: operator, values: values });
      }
      continue;
    }
    if (value.indexOf(",") >= 0) {
      const values = value.split(",").map(function (part) {
        return part.trim();
      }).filter(Boolean);
      if (values.length > 1) {
        rules.push({ field: field, operator: "in", values: values });
        continue;
      }
    }
    rules.push({ field: field, operator: operator, value: value });
  }
  return rules;
}

function appendLegacyFilterRules(rules) {
  const endpointID = endpointInput.value.trim();
  const eventName = eventNameInput.value.trim();
  const nativeEventID = nativeEventIDInput.value.trim();

  function hasRule(field, operator, value) {
    return rules.some(function (rule) {
      return rule.field === field && rule.operator === operator && rule.value === value;
    });
  }

  if (endpointID && !hasRule("endpointID", "eq", endpointID)) {
    rules.push({ field: "endpointID", operator: "eq", value: endpointID });
  }
  if (eventName && !hasRule("eventName", "eq", eventName)) {
    rules.push({ field: "eventName", operator: "eq", value: eventName });
  }
  if (nativeEventID && !hasRule("nativeEventID", "eq", nativeEventID)) {
    rules.push({ field: "nativeEventID", operator: "eq", value: nativeEventID });
  }

  return rules;
}

function clearFilterRules() {
  filterRulesList.innerHTML = "";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function getSortValue(alert, key) {
  const value = alert[key];
  if (value === null || value === undefined || value === "") {
    if (key === "nativeEventID" || key === "alertID") {
      return Number.MAX_SAFE_INTEGER;
    }
    return "";
  }

  if (key === "endpointID" || key === "eventName") {
    return String(value).toLowerCase();
  }

  const number = Number(value);
  if (Number.isFinite(number)) {
    return number;
  }

  return String(value).toLowerCase();
}

function compareAlerts(left, right) {
  const leftValue = getSortValue(left, sortState.key);
  const rightValue = getSortValue(right, sortState.key);

  if (leftValue < rightValue) {
    return sortState.direction === "asc" ? -1 : 1;
  }

  if (leftValue > rightValue) {
    return sortState.direction === "asc" ? 1 : -1;
  }

  return 0;
}

function updateSortIndicators() {
  for (const header of sortableHeaders) {
    const indicator = header.querySelector(".sort-indicator");
    const key = header.dataset.sortKey;
    if (!indicator) {
      continue;
    }

    if (key === sortState.key) {
      indicator.textContent = sortState.direction === "asc" ? "▲" : "▼";
    } else {
      indicator.textContent = "";
    }
  }
}

function sortAndRenderAlerts() {
  const sortedAlerts = [...currentAlerts].sort(compareAlerts);
  renderRows(sortedAlerts);
  updateSortIndicators();
  updateSummary(sortedAlerts);
}

function updateSummary(alerts) {
  const uniquePeriods = new Set(alerts.map((alert) => alert.periodTs)).size;
  const uniqueEndpoints = new Set(alerts.map((alert) => alert.endpointID)).size;
  const highConfidence = alerts.filter((alert) => Number(alert.confidence) >= 80).length;

  chipAlertCount.textContent = String(alerts.length);
  chipHighConfidenceCount.textContent = String(highConfidence);
  chipPeriodCount.textContent = String(uniquePeriods);
  chipEndpointCount.textContent = String(uniqueEndpoints);
  summaryRow.hidden = alerts.length === 0;
}

function setSort(key) {
  if (sortState.key === key) {
    sortState.direction = sortState.direction === "asc" ? "desc" : "asc";
  } else {
    sortState.key = key;
    sortState.direction = key === "confidence" || key === "tsEnd" ? "desc" : "asc";
  }

  syncUrlFromFilters();
  sortAndRenderAlerts();
}

for (const header of sortableHeaders) {
  const button = header.querySelector("button");
  if (!button) {
    continue;
  }

  button.addEventListener("click", function () {
    setSort(header.dataset.sortKey);
  });
}

const timestampFormatter = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  fractionalSecondDigits: 3,
  hour12: false,
});

function formatExactTimestamp(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return String(value);
  }

  const date = new Date(number);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  return timestampFormatter.format(date);
}

function renderTimestampCell(value) {
  const exact = formatExactTimestamp(value);
  const relative = formatRelativeTimestamp(value);
  const number = Number(value);
  let iso = "";
  if (Number.isFinite(number)) {
    const date = new Date(number);
    if (!Number.isNaN(date.getTime())) {
      iso = date.toISOString();
    }
  }
  return "<time class='timestamp-cell' datetime='" + escapeHtml(iso) + "' title='" + escapeHtml(exact) + "'>" + escapeHtml(relative) + "</time>";
}

function formatPeriod(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) {
    return value;
  }

  const units = [
    { label: "day", ms: 24 * 60 * 60 * 1000 },
    { label: "hour", ms: 60 * 60 * 1000 },
    { label: "minute", ms: 60 * 1000 },
    { label: "second", ms: 1000 },
  ];

  for (const unit of units) {
    if (number >= unit.ms) {
      const scaled = number / unit.ms;
      let rounded;
      if (unit.label === "second") {
        const roundedHalfSecond = Math.round(scaled * 2) / 2;
        rounded = Number.isInteger(roundedHalfSecond)
          ? roundedHalfSecond.toFixed(0)
          : roundedHalfSecond.toFixed(1);
      } else {
        rounded = scaled >= 10 ? scaled.toFixed(0) : scaled.toFixed(1);
      }
      const suffix = Number(rounded) === 1 ? unit.label : unit.label + "s";
      return rounded + " " + suffix;
    }
  }

  return Math.round(number).toLocaleString() + " ms";
}

function periodIcon() {
  return "<span class='window-icon' aria-hidden='true'><svg viewBox='0 0 24 24'><path d='M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm1 5v5.2l4 2.4-1 1.7-5-3V7z'/></svg></span>";
}

function confidenceIcon() {
  return "<span class='window-icon' aria-hidden='true'><svg viewBox='0 0 24 24'><path d='M12 2 3 7v10l9 5 9-5V7l-9-5zm0 3 6 3.3V15l-6 3.3L6 15V8.3L12 5z'/></svg></span>";
}

function formatConfidence(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return value;
  }

  return Math.max(0, Math.min(100, Math.round(number))) + "%";
}

function confidenceLevel(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "confidence-low";
  }
  if (number >= 80) {
    return "confidence-high";
  }
  if (number >= 50) {
    return "confidence-medium";
  }
  return "confidence-low";
}

function renderConfidenceBadge(value) {
  const level = confidenceLevel(value);
  return (
    "<span class='confidence-badge " + level + "'>" +
      confidenceIcon() +
      "<span class='confidence-value'>" + formatConfidence(value) + "</span>" +
    "</span>"
  );
}

function renderEventCell(alert) {
  const eventName = alert.eventName || ("Event " + alert.nativeEventID);
  const eventId = alert.nativeEventID ?? "-";
  const source = alert.logSource ? "<span class='event-source'>" + escapeHtml(alert.logSource) + "</span>" : "";
  return (
    "<div class='event-cell'>" +
      "<span class='event-name'>" + escapeHtml(eventName) + "</span>" +
      "<span class='event-id-label'>ID " + escapeHtml(eventId) + "</span>" +
      source +
    "</div>"
  );
}

function renderDetailFields(fields) {
  if (!Array.isArray(fields) || !fields.length) {
    return "<p class='detail-empty'>No parsed event fields available.</p>";
  }

  return (
    "<dl class='detail-grid'>" +
      fields.map(function (item) {
        const emphasis = item.emphasis ? " detail-emphasis" : "";
        return (
          "<div class='detail-item" + emphasis + "'>" +
            "<dt>" + escapeHtml(item.label) + "</dt>" +
            "<dd>" + escapeHtml(item.value) + "</dd>" +
          "</div>"
        );
      }).join("") +
    "</dl>"
  );
}

function computeExpectedTickTimes(tsBeginMs, tsEndMs, periodMs, phaseRad) {
  const period = Number(periodMs);
  const phase = Number(phaseRad);
  const start = Number(tsBeginMs);
  const end = Number(tsEndMs);
  if (!Number.isFinite(period) || period <= 0 || !Number.isFinite(phase) || !Number.isFinite(start) || !Number.isFinite(end) || end < start) {
    return [];
  }

  const phaseOffsetMs = (phase / (2 * Math.PI)) * period;
  const targetMs = ((phaseOffsetMs % period) + period) % period;
  const positionMs = start % period;
  let advanceMs = targetMs - positionMs;
  if (advanceMs < 0) {
    advanceMs += period;
  }

  const ticks = [];
  let tickMs = start + advanceMs;
  while (tickMs <= end) {
    ticks.push(tickMs);
    tickMs += period;
  }
  return ticks;
}

function firstExpectedTickMs(tsBeginMs, periodMs, phaseRad) {
  const ticks = computeExpectedTickTimes(tsBeginMs, tsBeginMs, periodMs, phaseRad);
  if (ticks.length) {
    return ticks[0];
  }
  const period = Number(periodMs);
  const phase = Number(phaseRad);
  const start = Number(tsBeginMs);
  if (!Number.isFinite(period) || period <= 0 || !Number.isFinite(phase) || !Number.isFinite(start)) {
    return start;
  }
  const phaseOffsetMs = ((phase / (2 * Math.PI)) * period + period) % period;
  const positionMs = start % period;
  let advanceMs = phaseOffsetMs - positionMs;
  if (advanceMs < 0) {
    advanceMs += period;
  }
  return start + advanceMs;
}

function offsetWithinCycle(timestampMs, periodMs, phaseRad) {
  const period = Number(periodMs);
  const phase = Number(phaseRad);
  const timestamp = Number(timestampMs);
  if (!Number.isFinite(period) || period <= 0 || !Number.isFinite(timestamp)) {
    return 0;
  }
  const phaseOffsetMs = ((phase / (2 * Math.PI)) * period + period) % period;
  const positionMs = ((timestamp % period) + period) % period;
  let distanceMs = positionMs - phaseOffsetMs;
  if (distanceMs < 0) {
    distanceMs += period;
  }
  return Math.max(0, Math.min(1, distanceMs / period));
}

function buildStretchedTimelineLayout(matchedEvents, periodMs, phaseRad, tsBeginMs, tsEndMs) {
  const period = Number(periodMs);
  const start = Number(tsBeginMs);
  const end = Number(tsEndMs);
  if (!Number.isFinite(period) || period <= 0 || !matchedEvents.length) {
    return null;
  }

  const cycleWidthPx = 112;
  const laneHeightPx = 24;
  const paddingPx = 28;
  const anchorTick = firstExpectedTickMs(start, period, phaseRad);

  const slotLanes = new Map();
  const expectedTicks = computeExpectedTickTimes(start, end, period, phaseRad);
  const tickPositions = expectedTicks.map(function (tickMs) {
    return {
      tickMs: tickMs,
      leftPx: 0,
      cycleIndex: 0,
    };
  });

  const placedEvents = matchedEvents.map(function (eventItem, index) {
    const timestamp = Number(eventItem.timestamp);
    const cycleIndex = Math.round((timestamp - anchorTick) / period);
    const offsetRatio = offsetWithinCycle(timestamp, period, phaseRad);
    const slotKey = displayTimeSlotKey(timestamp, anchorTick, period);
    const lane = slotLanes.get(slotKey) || 0;
    slotLanes.set(slotKey, lane + 1);

    return {
      eventItem: eventItem,
      index: index,
      cycleIndex: cycleIndex,
      offsetRatio: offsetRatio,
      lane: lane,
      leftPx: 0,
      topPx: 12 + lane * laneHeightPx,
    };
  });

  const minCycleIndex = Math.min(
    0,
    ...tickPositions.map(function (tick) {
      return Math.round((tick.tickMs - anchorTick) / period);
    }),
    ...placedEvents.map(function (item) {
      return item.cycleIndex;
    }),
  );
  const cycleShift = minCycleIndex < 0 ? -minCycleIndex : 0;

  for (const tick of tickPositions) {
    const cycleIndex = Math.round((tick.tickMs - anchorTick) / period) + cycleShift;
    tick.cycleIndex = cycleIndex;
    tick.leftPx = paddingPx + cycleIndex * cycleWidthPx;
  }

  for (const placed of placedEvents) {
    const cycleIndex = placed.cycleIndex + cycleShift;
    placed.cycleIndex = cycleIndex;
    placed.leftPx = paddingPx + cycleIndex * cycleWidthPx + placed.offsetRatio * (cycleWidthPx * 0.82);
  }

  const cycleCount = Math.max(
    tickPositions.length ? tickPositions[tickPositions.length - 1].cycleIndex + 1 : 1,
    ...placedEvents.map(function (item) {
      return item.cycleIndex + 1;
    }),
    1,
  );
  const maxLane = Math.max(...placedEvents.map(function (item) {
    return item.lane;
  }), 0);
  const canvasWidthPx = paddingPx * 2 + cycleCount * cycleWidthPx;
  const canvasHeightPx = Math.max(88, 48 + (maxLane + 1) * laneHeightPx);

  return {
    cycleWidthPx: cycleWidthPx,
    canvasWidthPx: canvasWidthPx,
    canvasHeightPx: canvasHeightPx,
    tickPositions: tickPositions,
    placedEvents: placedEvents,
  };
}

function timelinePercent(timestampMs, tsBeginMs, tsEndMs) {
  const span = tsEndMs - tsBeginMs;
  if (span <= 0) {
    return 0;
  }
  const ratio = (Number(timestampMs) - tsBeginMs) / span;
  return Math.max(0, Math.min(100, ratio * 100)).toFixed(2);
}

function formatTimelineAxisLabel(value) {
  const exact = formatExactTimestamp(value);
  const parts = exact.split(", ");
  if (parts.length >= 2) {
    return parts[0] + " " + parts[1];
  }
  return exact;
}

function formatShortTime(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return String(value);
  }
  const date = new Date(number);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function displayTimeSlotKey(timestamp, anchorTick, period) {
  const timestampMs = Number(timestamp);
  const cycleIndex = Math.round((timestampMs - anchorTick) / period);
  const offsetMs = ((timestampMs - anchorTick) % period + period) % period;
  const bucket = Math.floor(offsetMs / SAME_DISPLAY_TIME_BUCKET_MS);
  return cycleIndex + ":" + bucket;
}

function formatDurationSpan(durationMs) {
  const ms = Math.max(0, Number(durationMs) || 0);
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (ms >= day) {
    const days = ms / day;
    const rounded = days >= 10 ? Math.round(days) : Math.round(days * 10) / 10;
    return rounded + (rounded === 1 ? " day" : " days");
  }
  if (ms >= hour) {
    const hours = Math.round((ms / hour) * 10) / 10;
    return hours + (hours === 1 ? " hour" : " hours");
  }
  if (ms >= minute) {
    const minutes = Math.round(ms / minute);
    return minutes + (minutes === 1 ? " minute" : " minutes");
  }
  const seconds = Math.round(ms / 1000);
  return seconds + (seconds === 1 ? " second" : " seconds");
}

function computeActivityBounds(windows) {
  const begins = windows.map(function (windowAlert) {
    return Number(windowAlert.tsBegin);
  }).filter(Number.isFinite);
  const ends = windows.map(function (windowAlert) {
    return Number(windowAlert.tsEnd);
  }).filter(Number.isFinite);
  if (!begins.length || !ends.length) {
    return null;
  }
  return {
    tsBegin: Math.min.apply(null, begins),
    tsEnd: Math.max.apply(null, ends),
  };
}

function buildOverviewTimelineLayout(span, windows) {
  const fullSpanMs = span.tsEnd - span.tsBegin;
  if (!Number.isFinite(fullSpanMs) || fullSpanMs <= 0 || !windows.length) {
    return null;
  }

  const activity = computeActivityBounds(windows);
  if (!activity) {
    return null;
  }

  const MIN_REGION_PX = 56;
  const MIN_ACTIVITY_SHARE = 0.42;
  const PX_PER_HOUR = 3.5;
  let canvasWidthPx = Math.max(960, (fullSpanMs / (60 * 60 * 1000)) * PX_PER_HOUR);

  const activitySpanMs = Math.max(1, activity.tsEnd - activity.tsBegin);
  const activityShare = activitySpanMs / fullSpanMs;
  if (activityShare < MIN_ACTIVITY_SHARE) {
    canvasWidthPx = Math.max(canvasWidthPx, canvasWidthPx * (MIN_ACTIVITY_SHARE / activityShare));
  }

  for (const windowAlert of windows) {
    const regionMs = Number(windowAlert.tsEnd) - Number(windowAlert.tsBegin);
    if (regionMs <= 0) {
      continue;
    }
    const regionPx = (regionMs / fullSpanMs) * canvasWidthPx;
    if (regionPx < MIN_REGION_PX) {
      canvasWidthPx = Math.max(canvasWidthPx, canvasWidthPx * (MIN_REGION_PX / regionPx));
    }
  }

  function toPx(timestampMs) {
    return ((Number(timestampMs) - span.tsBegin) / fullSpanMs) * canvasWidthPx;
  }

  const focusLeftPx = toPx(activity.tsBegin);
  const focusWidthPx = Math.max(MIN_REGION_PX, toPx(activity.tsEnd) - focusLeftPx);

  return {
    canvasWidthPx: Math.ceil(canvasWidthPx),
    toPx: toPx,
    activity: activity,
    focusLeftPx: focusLeftPx,
    focusWidthPx: focusWidthPx,
  };
}

function formatActivitySummary(windows) {
  const activity = computeActivityBounds(windows);
  if (!activity) {
    return "";
  }
  const duration = formatDurationSpan(activity.tsEnd - activity.tsBegin);
  return (
    formatTimelineAxisLabel(activity.tsBegin) +
    " → " +
    formatTimelineAxisLabel(activity.tsEnd) +
    " · lasted " + duration +
    " · " + windows.length + " window" + (windows.length === 1 ? "" : "s")
  );
}

function computeOverviewTimeSpan(detail, windows) {
  const context = detail.overviewContext || {};
  const contextBegin = Number(context.tsBegin);
  const contextEnd = Number(context.tsEnd);
  if (Number.isFinite(contextBegin) && Number.isFinite(contextEnd) && contextEnd > contextBegin) {
    return { tsBegin: contextBegin, tsEnd: contextEnd };
  }

  const candidatesBegin = [Number(detail.tsBegin)];
  const candidatesEnd = [Number(detail.tsEnd)];
  for (const windowAlert of windows) {
    candidatesBegin.push(Number(windowAlert.tsBegin));
    candidatesEnd.push(Number(windowAlert.tsEnd));
  }
  const activityBegin = Math.min.apply(null, candidatesBegin.filter(Number.isFinite));
  const activityEnd = Math.max.apply(null, candidatesEnd.filter(Number.isFinite));
  if (!Number.isFinite(activityBegin) || !Number.isFinite(activityEnd) || activityEnd <= activityBegin) {
    return null;
  }

  const oneDayMs = 24 * 60 * 60 * 1000;
  const center = (activityBegin + activityEnd) / 2;
  return {
    tsBegin: center - oneDayMs / 2,
    tsEnd: center + oneDayMs / 2,
  };
}

function renderFact(label, value, opts) {
  const options = opts || {};
  const safeValue = value === null || value === undefined || value === ""
    ? "—"
    : escapeHtml(String(value));
  const strong = options.strong ? " is-strong" : "";
  return (
    "<div class='alert-fact" + strong + "'>" +
      "<dt>" + escapeHtml(label) + "</dt>" +
      "<dd>" + safeValue + "</dd>" +
    "</div>"
  );
}

function renderAlertHeader(detail, windows) {
  const activity = computeActivityBounds(windows);
  const eventName = detail.eventName || ("Event " + detail.nativeEventID);
  const period = formatPeriod(detail.periodTs);
  const matchedCount = Number(detail.contributingEventCount) || 0;
  const firstSeen = activity ? formatTimelineAxisLabel(activity.tsBegin) : null;
  const lastSeen = activity ? formatTimelineAxisLabel(activity.tsEnd) : null;
  const duration = activity ? formatDurationSpan(activity.tsEnd - activity.tsBegin) : null;

  return (
    "<header class='alert-head'>" +
      "<div class='alert-head-confidence'>" +
        renderConfidenceBadge(detail.confidence) +
      "</div>" +
      "<div class='alert-head-signal'>" +
        "<h3 class='alert-head-title'>" + escapeHtml(eventName) + "</h3>" +
        "<p class='alert-head-subtitle'>" +
          "<span class='alert-head-period'>Repeats every " + escapeHtml(period) + "</span>" +
          " · ID " + escapeHtml(detail.nativeEventID ?? "-") +
          (detail.logSource ? " · " + escapeHtml(detail.logSource) : "") +
        "</p>" +
      "</div>" +
      "<button type='button' class='alert-collapse' aria-label='Collapse alert'>" +
        "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M7 14l5-5 5 5z'/></svg>" +
        "<span>Collapse</span>" +
      "</button>" +
    "</header>" +
    "<dl class='alert-fact-grid'>" +
      renderFact("Endpoint", detail.name || detail.endpointID, { strong: true }) +
      renderFact("Endpoint ID", detail.endpointID) +
      renderFact("Hostname", detail.hostname) +
      renderFact("IP address", detail.ip) +
      renderFact("First seen", firstSeen) +
      renderFact("Last seen", lastSeen) +
      renderFact("Duration", duration) +
      renderFact("Matched events", matchedCount, { strong: true }) +
    "</dl>"
  );
}

function renderOverviewTimeline(detail, windows) {
  const span = computeOverviewTimeSpan(detail, windows);
  if (!span || !windows.length) {
    return "<p class='detail-empty'>No observation windows available.</p>";
  }

  const layout = buildOverviewTimelineLayout(span, windows);
  if (!layout) {
    return "<p class='detail-empty'>No observation windows available.</p>";
  }

  const MIN_REGION_PX = 56;
  const regionsMarkup = windows.map(function (windowAlert, index) {
    const leftPx = layout.toPx(windowAlert.tsBegin);
    const widthPx = Math.max(MIN_REGION_PX, layout.toPx(windowAlert.tsEnd) - leftPx);
    const label = "Window " + (index + 1) + ": " +
      formatTimelineAxisLabel(windowAlert.tsBegin) + " to " + formatTimelineAxisLabel(windowAlert.tsEnd) +
      ", confidence " + formatConfidence(windowAlert.confidence);
    return (
      "<button type='button' class='overview-window-region' data-window-index='" + index + "' style='left:" + leftPx.toFixed(2) + "px;width:" + widthPx.toFixed(2) + "px' title='" + escapeHtml(label) + "' aria-label='" + escapeHtml(label) + "'>" +
        "<span class='overview-window-region-label'>W" + (index + 1) + "</span>" +
      "</button>"
    );
  }).join("");

  const tickCount = Math.min(8, Math.max(3, Math.floor(layout.canvasWidthPx / 180)));
  const tickMarkup = [];
  for (let index = 0; index <= tickCount; index += 1) {
    const ratio = index / tickCount;
    const tickMs = span.tsBegin + (span.tsEnd - span.tsBegin) * ratio;
    const leftPx = layout.toPx(tickMs);
    tickMarkup.push(
      "<span class='overview-timeline-tick' style='left:" + leftPx.toFixed(2) + "px' aria-hidden='true'></span>" +
      "<span class='overview-timeline-tick-label' style='left:" + leftPx.toFixed(2) + "px'>" + escapeHtml(formatTimelineAxisLabel(tickMs)) + "</span>"
    );
  }

  return (
    "<div class='overview-timeline-shell' data-focus-left='" + layout.focusLeftPx.toFixed(2) + "' data-focus-width='" + layout.focusWidthPx.toFixed(2) + "'>" +
      "<div class='overview-timeline-scroll' tabindex='0' role='region' aria-label='Alert activity overview timeline'>" +
        "<div class='overview-timeline-canvas' style='width:" + layout.canvasWidthPx + "px'>" +
          "<div class='overview-timeline-track' style='width:" + layout.canvasWidthPx + "px' aria-hidden='true'></div>" +
          "<div class='overview-activity-focus' style='left:" + layout.focusLeftPx.toFixed(2) + "px;width:" + layout.focusWidthPx.toFixed(2) + "px' aria-hidden='true'></div>" +
          tickMarkup.join("") +
          regionsMarkup +
        "</div>" +
      "</div>" +
    "</div>"
  );
}

function renderEventInspector(eventItem) {
  if (!eventItem) {
    return "";
  }
  const parsed = eventItem.parsedDetails || {};
  const fields = Array.isArray(parsed.fields) ? parsed.fields : [];
  const title = parsed.title || ("Event " + (eventItem.nativeEventID ?? "-"));
  return (
    "<div class='event-inspector'>" +
      "<h4 class='event-inspector-title'>" + escapeHtml(title) + "</h4>" +
      "<p class='detail-meta event-inspector-meta'>" +
        escapeHtml(formatExactTimestamp(eventItem.timestamp)) +
        " · match " + escapeHtml(formatConfidence(eventItem.matchConfidence)) +
        " · internal ID " + escapeHtml(eventItem.internalEventID) +
      "</p>" +
      renderDetailFields(fields) +
    "</div>"
  );
}

function renderMatchedEventsTimeline(matchedEvents, windowAlert, periodMs, windowIndex) {
  if (!Array.isArray(matchedEvents) || !matchedEvents.length) {
    return "<p class='detail-empty'>No matched events above threshold for this window.</p>";
  }

  const tsBegin = Number(windowAlert.tsBegin);
  const tsEnd = Number(windowAlert.tsEnd);
  const span = tsEnd - tsBegin;
  if (!Number.isFinite(span) || span <= 0) {
    return "<p class='detail-empty'>Timeline unavailable for this window range.</p>";
  }

  const layout = buildStretchedTimelineLayout(
    matchedEvents,
    periodMs,
    windowAlert.phase,
    tsBegin,
    tsEnd,
  );

  if (!layout) {
    return "<p class='detail-empty'>Timeline unavailable for this period.</p>";
  }

  const expectedMarkup = layout.tickPositions.map(function (tick) {
    return (
      "<span class='event-timeline-expected' style='left:" + tick.leftPx + "px' title='Expected tick " + (tick.cycleIndex + 1) + "' aria-hidden='true'></span>"
    );
  }).join("");

  const markersMarkup = layout.placedEvents.map(function (placed) {
    const eventItem = placed.eventItem;
    const confidence = Number(eventItem.matchConfidence) || 0;
    const level = confidenceLevel(confidence);
    const timeLabel = formatShortTime(eventItem.timestamp);
    const label = formatExactTimestamp(eventItem.timestamp) +
      " · match " + formatConfidence(confidence) +
      " · ID " + eventItem.internalEventID;
    return (
      "<button type='button' class='event-timeline-marker " + level + "' data-window-index='" + windowIndex + "' data-event-index='" + placed.index + "' style='left:" + placed.leftPx + "px;top:" + placed.topPx + "px' title='" + escapeHtml(label) + "' aria-label='" + escapeHtml(label) + "'>" +
        "<span class='event-timeline-marker-dot' aria-hidden='true'></span>" +
        "<span class='event-timeline-marker-time'>" + escapeHtml(timeLabel) + "</span>" +
      "</button>"
    );
  }).join("");

  return (
    "<div class='event-timeline-shell' data-window-index='" + windowIndex + "'>" +
      "<div class='event-timeline-scroll' tabindex='0' role='region' aria-label='Horizontally scrollable event timeline'>" +
        "<div class='event-timeline-canvas' style='width:" + layout.canvasWidthPx + "px;height:" + layout.canvasHeightPx + "px'>" +
          "<div class='event-timeline-track' style='width:" + (layout.canvasWidthPx - 56) + "px' aria-hidden='true'></div>" +
          expectedMarkup +
          markersMarkup +
        "</div>" +
      "</div>" +
      "<div class='event-inspector-host' hidden></div>" +
    "</div>"
  );
}

function renderWindowRail(windows) {
  return windows.map(function (windowAlert, index) {
    const range = formatTimelineAxisLabel(windowAlert.tsBegin) + " → " + formatTimelineAxisLabel(windowAlert.tsEnd);
    return (
      "<button type='button' class='window-pill' data-window-index='" + index + "'>" +
        "<span class='window-pill-index'>W" + (index + 1) + "</span>" +
        "<span class='window-pill-range'>" + escapeHtml(range) + "</span>" +
        "<span class='window-pill-conf " + confidenceLevel(windowAlert.confidence) + "'>" + escapeHtml(formatConfidence(windowAlert.confidence)) + "</span>" +
      "</button>"
    );
  }).join("");
}

function renderWindowDetailPanel(windowAlert, detail, windowIndex) {
  const matchedEvents = Array.isArray(windowAlert.matchedEvents) ? windowAlert.matchedEvents : [];
  const eventCount = matchedEvents.length;
  return (
    "<div class='window-detail' data-window-index='" + windowIndex + "'>" +
      "<div class='window-detail-bar'>" +
        "<span class='window-detail-bar-title'>Window " + (windowIndex + 1) + " · " + eventCount + " event" + (eventCount === 1 ? "" : "s") + "</span>" +
        "<span class='window-detail-bar-meta'>" + escapeHtml(formatTimelineAxisLabel(windowAlert.tsBegin)) + " → " + escapeHtml(formatTimelineAxisLabel(windowAlert.tsEnd)) + "</span>" +
      "</div>" +
      renderMatchedEventsTimeline(matchedEvents, windowAlert, detail.periodTs, windowIndex) +
    "</div>"
  );
}

function bindAlertDetailPanel(container, detail) {
  const windows = Array.isArray(detail.windows) ? detail.windows : [];
  const windowDetailHost = container.querySelector(".window-detail-host");
  const rail = container.querySelector(".alert-windows-rail");
  const disclosure = container.querySelector(".alert-disclosure");
  if (!windowDetailHost) {
    return;
  }

  let selectedIndex = -1;

  function markActive(windowIndex) {
    if (rail) {
      for (const pill of rail.querySelectorAll(".window-pill")) {
        pill.classList.toggle("is-active", Number(pill.dataset.windowIndex) === windowIndex);
      }
    }
    for (const region of container.querySelectorAll(".overview-window-region")) {
      region.classList.toggle("is-selected", Number(region.dataset.windowIndex) === windowIndex);
    }
  }

  function showWindow(windowIndex) {
    const windowAlert = windows[windowIndex];
    if (!windowAlert) {
      return;
    }
    selectedIndex = windowIndex;
    markActive(windowIndex);
    windowDetailHost.hidden = false;
    windowDetailHost.innerHTML = renderWindowDetailPanel(windowAlert, detail, windowIndex);
    bindWindowEventTimeline(windowDetailHost, windowAlert, windowIndex);
    windowDetailHost.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function hideWindow() {
    selectedIndex = -1;
    markActive(-1);
    windowDetailHost.hidden = true;
    windowDetailHost.innerHTML = "";
  }

  if (rail) {
    rail.addEventListener("click", function (event) {
      const pill = event.target.closest(".window-pill");
      if (!pill) {
        return;
      }
      const windowIndex = Number(pill.dataset.windowIndex);
      if (windowIndex === selectedIndex) {
        hideWindow();
      } else {
        showWindow(windowIndex);
      }
    });
  }

  container.addEventListener("click", function (event) {
    const region = event.target.closest(".overview-window-region");
    if (!region) {
      return;
    }
    showWindow(Number(region.dataset.windowIndex));
  });

  if (disclosure) {
    disclosure.addEventListener("toggle", function () {
      if (!disclosure.open) {
        return;
      }
      const scroll = disclosure.querySelector(".overview-timeline-scroll");
      const shell = disclosure.querySelector(".overview-timeline-shell");
      if (!scroll || !shell) {
        return;
      }
      const focusLeft = Number(shell.dataset.focusLeft);
      if (Number.isFinite(focusLeft)) {
        scroll.scrollLeft = Math.max(0, focusLeft - scroll.clientWidth * 0.15);
      }
    });
  }
}

function bindWindowEventTimeline(host, windowAlert, windowIndex) {
  const matchedEvents = Array.isArray(windowAlert.matchedEvents) ? windowAlert.matchedEvents : [];
  const timelineShell = host.querySelector(".event-timeline-shell[data-window-index='" + windowIndex + "']");
  if (!timelineShell) {
    return;
  }

  const inspectorHost = timelineShell.querySelector(".event-inspector-host");
  timelineShell.addEventListener("click", function (event) {
    const marker = event.target.closest(".event-timeline-marker");
    if (!marker) {
      return;
    }
    const eventIndex = Number(marker.dataset.eventIndex);
    const eventItem = matchedEvents[eventIndex];
    if (!eventItem || !inspectorHost) {
      return;
    }

    for (const button of timelineShell.querySelectorAll(".event-timeline-marker.is-selected")) {
      button.classList.remove("is-selected");
    }
    marker.classList.add("is-selected");
    inspectorHost.hidden = false;
    inspectorHost.innerHTML = renderEventInspector(eventItem);
  });
}

function renderExpandedPanel(detail) {
  const windows = Array.isArray(detail.windows) ? detail.windows : [];
  const windowLabel = windows.length === 1 ? "1 window" : windows.length + " windows";
  const hasWindows = windows.length > 0;

  return (
    "<div class='alert-detail-panel'>" +
      renderAlertHeader(detail, windows) +
      (hasWindows
        ? "<details class='alert-disclosure'>" +
            "<summary><span class='alert-disclosure-label'>Activity timeline</span><span class='alert-disclosure-hint'>" + windowLabel + "</span></summary>" +
            "<div class='alert-disclosure-body'>" +
              renderOverviewTimeline(detail, windows) +
            "</div>" +
          "</details>" +
          "<section class='alert-windows'>" +
            "<div class='alert-windows-rail'>" + renderWindowRail(windows) + "</div>" +
            "<div class='window-detail-host' hidden></div>" +
          "</section>"
        : "<p class='detail-empty'>No observation windows available.</p>") +
    "</div>"
  );
}

async function loadAlertDetail(alertId, detailRow) {
  detailRow.innerHTML = "<td colspan='" + TABLE_COLSPAN + "'><div class='detail-loading'>Loading alert details...</div></td>";
  try {
    const response = await fetch("/api/alerts/" + encodeURIComponent(alertId));
    const data = await response.json();
    if (!response.ok) {
      detailRow.innerHTML = "<td colspan='" + TABLE_COLSPAN + "'><div class='detail-error'>" + escapeHtml(data.error || "Failed to load details") + "</div></td>";
      return;
    }
    detailRow.innerHTML = "<td colspan='" + TABLE_COLSPAN + "'>" + renderExpandedPanel(data) + "</td>";
    const panel = detailRow.querySelector(".alert-detail-panel");
    if (panel) {
      bindAlertDetailPanel(panel, data);
      const collapseButton = panel.querySelector(".alert-collapse");
      if (collapseButton) {
        collapseButton.addEventListener("click", collapseExpandedRow);
      }
    }
  } catch (error) {
    detailRow.innerHTML = "<td colspan='" + TABLE_COLSPAN + "'><div class='detail-error'>Network error while loading details.</div></td>";
  }
}

function setRowExpandedState(row, expanded) {
  row.classList.toggle("is-expanded", expanded);
  row.setAttribute("aria-expanded", expanded ? "true" : "false");
}

function collapseExpandedRow() {
  if (expandedAlertId === null) {
    return;
  }
  const collapsingId = expandedAlertId;
  const detailRow = tbody.querySelector("tr.detail-row[data-alert-id='" + collapsingId + "']");
  if (detailRow) {
    detailRow.remove();
  }
  for (const row of tbody.querySelectorAll("tr.alert-row.is-expanded")) {
    setRowExpandedState(row, false);
  }
  expandedAlertId = null;
  const hostRow = tbody.querySelector("tr.alert-row[data-alert-id='" + collapsingId + "']");
  if (hostRow) {
    hostRow.focus();
  }
}

function toggleExpandedRow(alertId, hostRow) {
  if (expandedAlertId === alertId) {
    collapseExpandedRow();
    return;
  }

  for (const openRow of tbody.querySelectorAll("tr.detail-row")) {
    openRow.remove();
  }
  for (const row of tbody.querySelectorAll("tr.alert-row.is-expanded")) {
    setRowExpandedState(row, false);
  }

  expandedAlertId = alertId;
  const detailRow = document.createElement("tr");
  detailRow.className = "detail-row";
  detailRow.dataset.alertId = String(alertId);
  hostRow.insertAdjacentElement("afterend", detailRow);
  setRowExpandedState(hostRow, true);
  loadAlertDetail(alertId, detailRow);
}

function renderRows(alerts) {
  expandedAlertId = null;
  tbody.innerHTML = "";

  for (const alert of alerts) {
    const tr = document.createElement("tr");
    const isHighConfidence = Number(alert.confidence) >= 80;
    tr.className = "alert-row" + (isHighConfidence ? " alert-row-high-confidence" : "");
    tr.dataset.alertId = String(alert.alertID);
    tr.setAttribute("role", "button");
    tr.setAttribute("tabindex", "0");
    tr.setAttribute("aria-expanded", "false");
    tr.setAttribute("aria-label", "Alert " + alert.alertID + ", click to expand");
    const endpointUrl = buildEndpointAlertsUrl(alert.endpointID);
    tr.innerHTML =
      "<td>" + renderConfidenceBadge(alert.confidence) + "</td>" +
      "<td>" + renderEventCell(alert) + "</td>" +
      "<td><a class='endpoint-link' href='" + escapeHtml(endpointUrl) + "' title='" + escapeHtml(alert.endpointID) + "' onclick='event.stopPropagation()'>" + escapeHtml(alert.name || alert.endpointID) + "</a></td>" +
      "<td><span class='period-badge'>" + periodIcon() + formatPeriod(alert.periodTs) + "</span></td>" +
      "<td>" + renderTimestampCell(alert.tsBegin) + "</td>" +
      "<td>" + renderTimestampCell(alert.tsEnd) + "</td>" +
      "<td>" + escapeHtml(alert.alertID) + "</td>";

    tr.addEventListener("click", function () {
      toggleExpandedRow(alert.alertID, tr);
    });

    tr.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleExpandedRow(alert.alertID, tr);
      }
    });

    tbody.appendChild(tr);
  }

  table.hidden = alerts.length === 0;
}

function buildEndpointAlertsUrl(endpointID) {
  const params = new URLSearchParams();
  params.set("endpointID", endpointID);
  return "/alerts?" + params.toString();
}

function collectActiveFilterDescriptors() {
  const chips = [];
  if (timeRangeControls) {
    const timeParams = timeRangeControls.buildQueryParams();
    const preset = timeParams.get("timePreset") || "all";
    if (preset !== "all") {
      chips.push({
        key: "time",
        label: "Time: " + timeRangeControls.getLabel(),
      });
    }
  }

  const endpointID = endpointInput.value.trim();
  if (endpointID) {
    chips.push({ key: "endpointID", label: "Endpoint: " + endpointID });
  }

  const eventName = eventNameInput.value.trim();
  if (eventName) {
    chips.push({ key: "eventName", label: "Event: " + eventName });
  }

  const nativeEventID = nativeEventIDInput.value.trim();
  if (nativeEventID) {
    chips.push({ key: "nativeEventID", label: "Event ID: " + nativeEventID });
  }

  const minConfidence = minConfidenceInput.value.trim();
  if (minConfidence) {
    chips.push({ key: "minConfidence", label: "Min confidence: " + minConfidence });
  }

  for (const rule of collectFilterRulesFromDom()) {
    const valueLabel = rule.values ? rule.values.join(", ") : rule.value;
    chips.push({
      key: "rule:" + rule.field + ":" + rule.operator + ":" + valueLabel,
      label: rule.field + " " + rule.operator + " " + valueLabel,
      rule: rule,
    });
  }

  return chips;
}

function renderActiveFilterChips() {
  if (!activeFilterChips) {
    return;
  }
  const descriptors = collectActiveFilterDescriptors();
  activeFilterChips.innerHTML = "";
  if (!descriptors.length) {
    activeFilterChips.hidden = true;
    return;
  }

  for (const descriptor of descriptors) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "filter-chip";
    button.dataset.chipKey = descriptor.key;
    if (descriptor.rule) {
      button.dataset.chipRule = JSON.stringify(descriptor.rule);
    }
    button.innerHTML =
      "<span>" + escapeHtml(descriptor.label) + "</span>" +
      "<span class='filter-chip-remove' aria-hidden='true'>&times;</span>";
    activeFilterChips.appendChild(button);
  }
  activeFilterChips.hidden = false;
}

function clearFilterChip(key, ruleJson) {
  if (key === "time" && timeRangeControls) {
    timeRangeControls.setPreset("all", { timeFrom: "", timeTo: "" });
  } else if (key === "endpointID") {
    endpointInput.value = "";
  } else if (key === "eventName") {
    eventNameInput.value = "";
  } else if (key === "nativeEventID") {
    nativeEventIDInput.value = "";
  } else if (key === "minConfidence") {
    minConfidenceInput.value = "";
  } else if (key.startsWith("rule:") && ruleJson) {
    try {
      const targetRule = JSON.parse(ruleJson);
      for (const row of filterRulesList.querySelectorAll(".filter-rule-row")) {
        const field = row.querySelector(".filter-rule-field").value;
        const operator = row.querySelector(".filter-rule-operator").value;
        const value = row.querySelector(".filter-rule-value").value.trim();
        if (field === targetRule.field && operator === targetRule.operator && value === (targetRule.value || (targetRule.values || []).join(", "))) {
          row.remove();
          break;
        }
      }
    } catch (error) {
      // Ignore malformed chip metadata.
    }
  }
  renderActiveFilterChips();
  loadAlerts();
}

if (activeFilterChips) {
  activeFilterChips.addEventListener("click", function (event) {
    const chip = event.target.closest(".filter-chip");
    if (!chip) {
      return;
    }
    clearFilterChip(chip.dataset.chipKey, chip.dataset.chipRule);
  });
}

function syncUrlFromFilters() {
  const params = buildQueryParams();
  const query = params.toString();
  const nextUrl = query ? ("?" + query) : window.location.pathname;
  window.history.replaceState(null, "", nextUrl);
}

function buildQueryParams() {
  const params = new URLSearchParams();
  if (timeRangeControls) {
    for (const [key, value] of timeRangeControls.buildQueryParams()) {
      params.set(key, value);
    }
  }
  params.set("sort", sortState.key);
  params.set("order", sortState.direction);

  const minConfidence = minConfidenceInput.value.trim();
  const rules = appendLegacyFilterRules(collectFilterRulesFromDom());

  if (rules.length) {
    params.set("filters", JSON.stringify(rules));
  }
  if (minConfidence) {
    params.set("minConfidence", minConfidence);
  }

  return params;
}

async function loadAlerts() {
  if (window.PageStatus) {
    PageStatus.showLoading(statusEl, "Loading alerts...");
  } else {
    statusEl.textContent = "Loading...";
  }
  table.hidden = true;
  tbody.innerHTML = "";

  try {
    const response = await fetch("/api/alerts?" + buildQueryParams().toString());
    const data = await response.json();

    if (!response.ok) {
      summaryRow.hidden = true;
      if (window.PageStatus) {
        PageStatus.showError(statusEl, data.error || "Failed to load alerts");
      } else {
        statusEl.textContent = data.error || "Failed to load alerts";
      }
      renderActiveFilterChips();
      return;
    }

    syncUrlFromFilters();
    renderActiveFilterChips();
    currentAlerts = Array.isArray(data.alerts) ? data.alerts : [];
    sortAndRenderAlerts();

    if (currentAlerts.length === 0) {
      table.hidden = true;
      if (window.PageStatus) {
        PageStatus.showEmpty(statusEl, {
          message: "No alerts match these filters. Try widening the time range or clearing a filter.",
        });
      }
      return;
    }

    if (window.PageStatus) {
      PageStatus.showSuccess(statusEl, "Found " + data.count + " alert(s)");
    } else {
      statusEl.textContent = "Found " + data.count + " alert(s)";
    }
  } catch (error) {
    summaryRow.hidden = true;
    if (window.PageStatus) {
      PageStatus.showError(statusEl, "Network error while loading alerts.");
    } else {
      statusEl.textContent = "Network error";
    }
  }
}

function setFiltersExpanded(expanded) {
  form.hidden = !expanded;
  form.classList.toggle("is-collapsed", !expanded);
  toggleFiltersButton.setAttribute("aria-expanded", expanded ? "true" : "false");
  toggleFiltersButton.querySelector(".filter-toggle-label").textContent = expanded ? "Hide filters" : "Show filters";
  toggleFiltersButton.querySelector(".filter-toggle-chevron").textContent = expanded ? "▾" : "▸";
}

toggleFiltersButton.addEventListener("click", function () {
  setFiltersExpanded(form.hidden || form.classList.contains("is-collapsed"));
});

async function loadMeta() {
  try {
    const response = await fetch("/api/meta");
    const data = await response.json();
    if (!response.ok) {
      return;
    }

    for (const eventName of data.eventNames || []) {
      const option = document.createElement("option");
      option.value = eventName;
      option.textContent = eventName;
      eventNameInput.appendChild(option);
    }
  } catch (error) {
    // Meta is optional for rendering.
  }
}

form.addEventListener("submit", function (event) {
  event.preventDefault();
  loadAlerts();
});

resetFiltersButton.addEventListener("click", function () {
  if (timeRangeControls) {
    timeRangeControls.setPreset("all", { timeFrom: "", timeTo: "" });
  }
  endpointInput.value = "";
  eventNameInput.value = "";
  nativeEventIDInput.value = "";
  minConfidenceInput.value = "";
  clearFilterRules();
  sortState = { key: "confidence", direction: "desc" };
  setFiltersExpanded(false);
  loadAlerts();
});

addFilterRuleButton.addEventListener("click", function () {
  addFilterRuleRow();
});

function applyQueryParamsFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const endpointID = params.get("endpointID");
  const eventName = params.get("eventName");
  const nativeEventID = params.get("nativeEventID");
  const minConfidence = params.get("minConfidence");
  const sort = params.get("sort");
  const order = params.get("order");
  const rawFilters = params.get("filters");
  let hasFilters = false;

  if (timeRangeControls) {
    timeRangeControls.applyFromSearchParams(params);
    if (params.get("timePreset") && params.get("timePreset") !== "all") {
      hasFilters = true;
    }
  }

  if (endpointID) {
    endpointInput.value = endpointID;
    hasFilters = true;
  }

  if (eventName) {
    eventNameInput.value = eventName;
    hasFilters = true;
  }

  if (nativeEventID) {
    nativeEventIDInput.value = nativeEventID;
    hasFilters = true;
  }

  if (minConfidence) {
    minConfidenceInput.value = minConfidence;
    hasFilters = true;
  }

  if (sort) {
    sortState.key = sort;
  }
  if (order === "asc" || order === "desc") {
    sortState.direction = order;
  }

  if (rawFilters) {
    try {
      const parsed = JSON.parse(rawFilters);
      if (Array.isArray(parsed)) {
        clearFilterRules();
        for (const rule of parsed) {
          if (rule && rule.field && (rule.value || (rule.values && rule.values.length))) {
            addFilterRuleRow(rule);
          }
        }
        hasFilters = true;
      }
    } catch (error) {
      // Ignore malformed filter JSON in the URL.
    }
  }

  if (hasFilters) {
    setFiltersExpanded(true);
  }
}

setFiltersExpanded(false);
applyQueryParamsFromUrl();
loadMeta();
loadAlerts();
