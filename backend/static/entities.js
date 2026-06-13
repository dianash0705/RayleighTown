const statusEl = document.getElementById("status");
const summaryRow = document.getElementById("summaryRow");
const chipEntityCount = document.getElementById("chipEntityCount");
const chipAlertCount = document.getElementById("chipAlertCount");
const chipActiveCount = document.getElementById("chipActiveCount");
const chipAlertsLabel = document.getElementById("chipAlertsLabel");
const table = document.getElementById("entitiesTable");
const tbody = table.querySelector("tbody");
const sortableHeaders = Array.from(table.querySelectorAll("th.sortable"));

let currentEntities = [];
let sortState = {
  key: "alertsLastWeek",
  direction: "desc",
};

let timeRangeControls = null;
if (window.TimeRangeControls) {
  timeRangeControls = window.TimeRangeControls.init({
    onChange: loadEntities,
    applyButtonId: "applyTimeRange",
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function displayValue(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return escapeHtml(value);
}

function getSortValue(entity, key) {
  const value = entity[key];
  if (value === null || value === undefined || value === "") {
    return key === "alertsLastWeek" ? -1 : "";
  }

  if (key === "endpointID" || key === "name" || key === "ip") {
    return String(value).toLowerCase();
  }

  const number = Number(value);
  if (Number.isFinite(number)) {
    return number;
  }

  return String(value).toLowerCase();
}

function compareEntities(left, right) {
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

function updateSummary(entities) {
  const totalAlerts = entities.reduce((sum, entity) => sum + Number(entity.alertsLastWeek || 0), 0);
  const activeEntities = entities.filter((entity) => Number(entity.alertsLastWeek) > 0).length;

  chipEntityCount.textContent = String(entities.length);
  chipAlertCount.textContent = String(totalAlerts);
  chipActiveCount.textContent = String(activeEntities);
  summaryRow.hidden = entities.length === 0;
}

function buildAlertsUrl(endpointID) {
  const params = new URLSearchParams();
  params.set("endpointID", endpointID);
  return "/alerts?" + params.toString();
}

function renderRows(entities) {
  tbody.innerHTML = "";

  for (const entity of entities) {
    const tr = document.createElement("tr");
    const alertsUrl = buildAlertsUrl(entity.endpointID);
    const alertCount = Number(entity.alertCount ?? entity.alertsLastWeek ?? 0);

    tr.innerHTML =
      "<td><a class='entity-link' href='" + escapeHtml(alertsUrl) + "'>" + displayValue(entity.endpointID) + "</a></td>" +
      "<td>" + displayValue(entity.name) + "</td>" +
      "<td>" + displayValue(entity.ip) + "</td>" +
      "<td><span class='entity-alert-count" + (alertCount > 0 ? " has-alerts" : "") + "'>" + alertCount + "</span></td>";

    tbody.appendChild(tr);
  }

  table.hidden = entities.length === 0;
}

function sortAndRenderEntities() {
  const sortedEntities = [...currentEntities].sort(compareEntities);
  renderRows(sortedEntities);
  updateSortIndicators();
  updateSummary(sortedEntities);
}

function setSort(key) {
  if (sortState.key === key) {
    sortState.direction = sortState.direction === "asc" ? "desc" : "asc";
  } else {
    sortState.key = key;
    sortState.direction = key === "alertsLastWeek" ? "desc" : "asc";
  }

  sortAndRenderEntities();
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

async function loadEntities() {
  statusEl.textContent = "Loading...";
  table.hidden = true;
  tbody.innerHTML = "";

  try {
    const query = timeRangeControls ? "?" + timeRangeControls.buildQueryParams().toString() : "";
    const response = await fetch("/api/entities" + query);
    const data = await response.json();

    if (!response.ok) {
      statusEl.textContent = data.error || "Failed to load entities";
      summaryRow.hidden = true;
      return;
    }

    if (chipAlertsLabel && timeRangeControls) {
      chipAlertsLabel.textContent = "Alerts (" + timeRangeControls.getLabel().toLowerCase() + ")";
    }

    statusEl.textContent = "Found " + data.count + " entit" + (data.count === 1 ? "y" : "ies");
    currentEntities = Array.isArray(data.entities) ? data.entities : [];
    sortAndRenderEntities();
  } catch (error) {
    statusEl.textContent = "Network error";
    summaryRow.hidden = true;
  }
}

loadEntities();
