const form = document.getElementById("queryForm");
const toggleFiltersButton = document.getElementById("toggleFilters");
const timePresetInput = document.getElementById("timePreset");
const timeFromWrap = document.getElementById("timeFromWrap");
const timeToWrap = document.getElementById("timeToWrap");
const timeFromInput = document.getElementById("timeFrom");
const timeToInput = document.getElementById("timeTo");
const endpointInput = document.getElementById("endpointID");
const eventNameInput = document.getElementById("eventName");
const nativeEventIDInput = document.getElementById("nativeEventID");
const minConfidenceInput = document.getElementById("minConfidence");
const resetFiltersButton = document.getElementById("resetFilters");
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

let currentAlerts = [];
let expandedAlertId = null;
let sortState = {
  key: "confidence",
  direction: "desc",
};

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

function renderEndpointLine(detail) {
  const parts = ["Endpoint ID: " + escapeHtml(detail.endpointID)];
  if (detail.hostname) {
    parts.push(escapeHtml(detail.hostname));
  }
  if (detail.ip) {
    parts.push(escapeHtml(detail.ip));
  }
  return parts.join(" · ");
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

function renderExpandedPanel(detail) {
  const eventBits = [
    escapeHtml(detail.eventName || ("Event " + detail.nativeEventID)),
    "ID " + escapeHtml(detail.nativeEventID ?? "-"),
    detail.logSource ? escapeHtml(detail.logSource) : null,
  ].filter(Boolean);

  const windows = Array.isArray(detail.windows) ? detail.windows : [];
  const eventDetails = detail.eventDetails || {};
  const contributingCount = detail.contributingEventCount || 0;

  return (
    "<div class='alert-detail-panel'>" +
      "<section class='detail-section'>" +
        "<h3>Endpoint</h3>" +
        "<p class='detail-line'>" + renderEndpointLine(detail) + "</p>" +
      "</section>" +
      "<section class='detail-section'>" +
        "<h3>Event</h3>" +
        "<p class='detail-line'>" + eventBits.join(" · ") + "</p>" +
        renderDetailFields(eventDetails.fields) +
        "<p class='detail-meta'>" + contributingCount + " contributing event(s) stored for future series mapping.</p>" +
      "</section>" +
      "<section class='detail-section'>" +
        "<h3>Contributing Windows (" + windows.length + ")</h3>" +
        "<div class='window-list'>" +
          windows.map(function (windowAlert, index) {
            return (
              "<div class='window-card'>" +
                "<div class='window-card-header'>" +
                  "<div class='window-card-title'>Window " + (index + 1) + "</div>" +
                  "<div class='window-card-meta'>Confidence " + formatConfidence(windowAlert.confidence) + "</div>" +
                "</div>" +
                "<div class='window-card-body'>" +
                  renderTimestampCell(windowAlert.tsBegin) +
                  "<span class='time-separator'>→</span>" +
                  renderTimestampCell(windowAlert.tsEnd) +
                "</div>" +
              "</div>"
            );
          }).join("") +
        "</div>" +
      "</section>" +
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
  } catch (error) {
    detailRow.innerHTML = "<td colspan='" + TABLE_COLSPAN + "'><div class='detail-error'>Network error while loading details.</div></td>";
  }
}

function setRowExpandedState(row, expanded) {
  row.classList.toggle("is-expanded", expanded);
  row.setAttribute("aria-expanded", expanded ? "true" : "false");
}

function toggleExpandedRow(alertId, hostRow) {
  const existingDetailRow = tbody.querySelector("tr.detail-row[data-alert-id='" + alertId + "']");
  if (expandedAlertId === alertId) {
    expandedAlertId = null;
    if (existingDetailRow) {
      existingDetailRow.remove();
    }
    setRowExpandedState(hostRow, false);
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
    tr.className = "alert-row";
    tr.dataset.alertId = String(alert.alertID);
    tr.setAttribute("role", "button");
    tr.setAttribute("tabindex", "0");
    tr.setAttribute("aria-expanded", "false");
    tr.setAttribute("aria-label", "Alert " + alert.alertID + ", click to expand");
    tr.innerHTML =
      "<td>" + renderConfidenceBadge(alert.confidence) + "</td>" +
      "<td>" + renderEventCell(alert) + "</td>" +
      "<td>" + escapeHtml(alert.endpointID) + "</td>" +
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

function updateCustomTimeVisibility() {
  const isCustom = timePresetInput.value === "custom";
  timeFromWrap.hidden = !isCustom;
  timeToWrap.hidden = !isCustom;
}

function setFiltersExpanded(expanded) {
  form.hidden = !expanded;
  form.classList.toggle("is-collapsed", !expanded);
  toggleFiltersButton.setAttribute("aria-expanded", expanded ? "true" : "false");
  toggleFiltersButton.querySelector(".filter-toggle-label").textContent = expanded ? "Hide filters" : "Show filters";
  toggleFiltersButton.querySelector(".filter-toggle-chevron").textContent = expanded ? "▾" : "▸";
}

timePresetInput.addEventListener("change", updateCustomTimeVisibility);

toggleFiltersButton.addEventListener("click", function () {
  setFiltersExpanded(form.classList.contains("is-collapsed"));
});

function toIsoFromLocalInput(value) {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date.toISOString();
}

function buildQueryParams() {
  const params = new URLSearchParams();
  params.set("timePreset", timePresetInput.value);
  params.set("sort", sortState.key);
  params.set("order", sortState.direction);

  const endpointID = endpointInput.value.trim();
  const eventName = eventNameInput.value.trim();
  const nativeEventID = nativeEventIDInput.value.trim();
  const minConfidence = minConfidenceInput.value.trim();

  if (endpointID) {
    params.set("endpointID", endpointID);
  }
  if (eventName) {
    params.set("eventName", eventName);
  }
  if (nativeEventID) {
    params.set("nativeEventID", nativeEventID);
  }
  if (minConfidence) {
    params.set("minConfidence", minConfidence);
  }

  if (timePresetInput.value === "custom") {
    const timeFrom = toIsoFromLocalInput(timeFromInput.value);
    const timeTo = toIsoFromLocalInput(timeToInput.value);
    if (timeFrom) {
      params.set("timeFrom", timeFrom);
    }
    if (timeTo) {
      params.set("timeTo", timeTo);
    }
  }

  return params;
}

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

async function loadAlerts() {
  statusEl.textContent = "Loading...";
  table.hidden = true;
  tbody.innerHTML = "";

  try {
    const response = await fetch("/api/alerts?" + buildQueryParams().toString());
    const data = await response.json();

    if (!response.ok) {
      statusEl.textContent = data.error || "Failed to load alerts";
      summaryRow.hidden = true;
      return;
    }

    statusEl.textContent = "Found " + data.count + " alert(s)";
    currentAlerts = Array.isArray(data.alerts) ? data.alerts : [];
    sortAndRenderAlerts();
  } catch (error) {
    statusEl.textContent = "Network error";
    summaryRow.hidden = true;
  }
}

form.addEventListener("submit", function (event) {
  event.preventDefault();
  loadAlerts();
});

resetFiltersButton.addEventListener("click", function () {
  timePresetInput.value = "all";
  timeFromInput.value = "";
  timeToInput.value = "";
  endpointInput.value = "";
  eventNameInput.value = "";
  nativeEventIDInput.value = "";
  minConfidenceInput.value = "";
  sortState = { key: "confidence", direction: "desc" };
  updateCustomTimeVisibility();
  loadAlerts();
});

function applyQueryParamsFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const endpointID = params.get("endpointID");
  const timePreset = params.get("timePreset");
  const nativeEventID = params.get("nativeEventID");
  const minConfidence = params.get("minConfidence");
  let hasFilters = false;

  if (endpointID) {
    endpointInput.value = endpointID;
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

  if (timePreset && timePresetInput.querySelector('option[value="' + timePreset + '"]')) {
    timePresetInput.value = timePreset;
    updateCustomTimeVisibility();
    hasFilters = true;
  }

  if (hasFilters) {
    setFiltersExpanded(true);
  }
}

updateCustomTimeVisibility();
setFiltersExpanded(false);
applyQueryParamsFromUrl();
loadMeta();
loadAlerts();
