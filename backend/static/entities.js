const statusEl = document.getElementById("status");
const summaryRow = document.getElementById("summaryRow");
const chipEntityCount = document.getElementById("chipEntityCount");
const chipAlertCount = document.getElementById("chipAlertCount");
const chipActiveCount = document.getElementById("chipActiveCount");
const chipAlertsLabel = document.getElementById("chipAlertsLabel");
const listHeader = document.getElementById("entitiesListHeader");
const list = document.getElementById("entitiesList");
const sortableHeaders = Array.from(listHeader.querySelectorAll(".av-col.sortable"));

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

const ENTITY_ACTIVE_WINDOW_MS = 24 * 60 * 60 * 1000;

function entityHealthState(lastSeenAt) {
  if (lastSeenAt === null || lastSeenAt === undefined || lastSeenAt === "") {
    return { level: "never", label: "Never seen" };
  }

  const seenMs = Number(lastSeenAt);
  if (!Number.isFinite(seenMs)) {
    return { level: "never", label: "Never seen" };
  }

  const ageMs = Date.now() - seenMs;
  const relativeLabel = window.formatRelativeTimestamp
    ? formatRelativeTimestamp(lastSeenAt)
    : "Last seen";

  if (ageMs <= ENTITY_ACTIVE_WINDOW_MS) {
    return { level: "active", label: relativeLabel };
  }

  return { level: "stale", label: relativeLabel };
}

function renderEntityStatusCell(entity) {
  const health = entityHealthState(entity.lastSeenAt);
  return (
    "<span class='entity-health'>" +
      "<span class='health-dot health-dot--" + health.level + "' aria-hidden='true'></span>" +
      "<span class='entity-health-label'>" + escapeHtml(health.label) + "</span>" +
    "</span>"
  );
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

  if (key === "lastSeenAt") {
    const seen = Number(value);
    return Number.isFinite(seen) ? seen : -1;
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
    const indicator = header.querySelector(".av-sort-indicator");
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
  list.innerHTML = "";

  for (const entity of entities) {
    const row = document.createElement("div");
    row.className = "av-row av-entity-row";
    const alertsUrl = buildAlertsUrl(entity.endpointID);
    const alertCount = Number(entity.alertCount ?? entity.alertsLastWeek ?? 0);

    const primaryName = entity.name ? escapeHtml(entity.name) : displayValue(entity.endpointID);
    row.innerHTML =
      "<div class='av-row-head av-grid-entities' role='row'>" +
        "<div role='cell'><a class='entity-link' href='" + escapeHtml(alertsUrl) + "'>" + primaryName + "</a></div>" +
        "<div role='cell'><span class='entity-id'>" + displayValue(entity.endpointID) + "</span></div>" +
        "<div role='cell'>" + displayValue(entity.ip) + "</div>" +
        "<div role='cell'>" + renderEntityStatusCell(entity) + "</div>" +
        "<div role='cell'><span class='entity-alert-count" + (alertCount > 0 ? " has-alerts" : "") + "'>" + alertCount + "</span></div>" +
      "</div>";

    list.appendChild(row);
  }

  listHeader.hidden = entities.length === 0;
  list.hidden = entities.length === 0;
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
  header.addEventListener("click", function () {
    setSort(header.dataset.sortKey);
  });
}

async function loadEntities() {
  if (window.PageStatus) {
    PageStatus.showLoading(statusEl, "Loading entities...");
  } else {
    statusEl.textContent = "Loading...";
  }
  listHeader.hidden = true;
  list.hidden = true;
  list.innerHTML = "";

  try {
    const query = timeRangeControls ? "?" + timeRangeControls.buildQueryParams().toString() : "";
    const response = await fetch("/api/entities" + query);
    const data = await response.json();

    if (!response.ok) {
      summaryRow.hidden = true;
      if (window.PageStatus) {
        PageStatus.showError(statusEl, data.error || "Failed to load entities");
      } else {
        statusEl.textContent = data.error || "Failed to load entities";
      }
      return;
    }

    if (chipAlertsLabel && timeRangeControls) {
      chipAlertsLabel.textContent = "Alerts (" + timeRangeControls.getLabel().toLowerCase() + ")";
    }

    currentEntities = Array.isArray(data.entities) ? data.entities : [];
    sortAndRenderEntities();

    if (currentEntities.length === 0) {
      listHeader.hidden = true;
      list.hidden = true;
      if (window.PageStatus) {
        PageStatus.showEmpty(statusEl, {
          message: "No entities yet. Registered endpoints will appear here once an agent uploads logs.",
        });
      } else {
        statusEl.textContent = "No entities found";
      }
      return;
    }

    if (window.PageStatus) {
      PageStatus.showSuccess(statusEl, "Found " + data.count + " entit" + (data.count === 1 ? "y" : "ies"));
    } else {
      statusEl.textContent = "Found " + data.count + " entit" + (data.count === 1 ? "y" : "ies");
    }
  } catch (error) {
    summaryRow.hidden = true;
    if (window.PageStatus) {
      PageStatus.showError(statusEl, "Network error while loading entities.");
    } else {
      statusEl.textContent = "Network error";
    }
  }
}

loadEntities();
