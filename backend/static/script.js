const form = document.getElementById("queryForm");
const endpointInput = document.getElementById("endpointID");
const statusEl = document.getElementById("status");
const summaryRow = document.getElementById("summaryRow");
const chipAlertCount = document.getElementById("chipAlertCount");
const chipWindowCount = document.getElementById("chipWindowCount");
const chipPeriodCount = document.getElementById("chipPeriodCount");
const chipEventCount = document.getElementById("chipEventCount");
const table = document.getElementById("alertsTable");
const tbody = table.querySelector("tbody");
const sortableHeaders = Array.from(table.querySelectorAll("th.sortable"));

let currentAlerts = [];
let sortState = {
  key: "tsBegin",
  direction: "asc",
};

function getSortValue(alert, key) {
  const value = alert[key];
  if (value === null || value === undefined || value === "") {
    return key === "nativeEventID" ? Number.MAX_SAFE_INTEGER : "";
  }

  if (key === "endpointID") {
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
  const totalWindows = alerts.reduce((sum, alert) => {
    return sum + (Array.isArray(alert.windows) ? alert.windows.length : 0);
  }, 0);
  const uniquePeriods = new Set(alerts.map((alert) => alert.periodTs)).size;
  const uniqueEventIDs = new Set(alerts.map((alert) => alert.nativeEventID)).size;

  chipAlertCount.textContent = String(alerts.length);
  chipWindowCount.textContent = String(totalWindows);
  chipPeriodCount.textContent = String(uniquePeriods);
  chipEventCount.textContent = String(uniqueEventIDs);
  summaryRow.hidden = alerts.length === 0;
}

function setSort(key) {
  if (sortState.key === key) {
    sortState.direction = sortState.direction === "asc" ? "desc" : "asc";
  } else {
    sortState.key = key;
    sortState.direction = key === "tsBegin" ? "asc" : "desc";
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

const timestampFormatter = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  fractionalSecondDigits: 3,
  hour12: false,
});

function formatTimestamp(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return value;
  }

  const date = new Date(number);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return timestampFormatter.format(date);
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

function formatWindowCount(count) {
  return count === 1 ? "1 window" : count + " windows";
}

function renderWindowCards(windows) {
  if (!windows.length) {
    return "-";
  }

  return "<details><summary><span class='summary-row'><span class='window-pill'>" +
    windows.length +
    "</span><span class='summary-hint'>" +
    formatWindowCount(windows.length) +
    "</span><span class='summary-chevron'>▸</span></span></summary><div class='window-list'>" +
    windows.map(function (windowAlert, index) {
      const windowLabel = "Window " + (index + 1);
      return "<div class='window-card'>" +
        "<div class='window-card-header'>" +
          "<div class='window-card-title'>" + windowLabel + "</div>" +
          "<div class='window-card-meta'>Confidence " + formatConfidence(windowAlert.confidence) + "</div>" +
        "</div>" +
        "<div class='window-card-body'>" +
          "<span class='time-range'>" + formatTimestamp(windowAlert.tsBegin) + " → " + formatTimestamp(windowAlert.tsEnd) + "</span>" +
        "</div>" +
      "</div>";
    }).join("") +
    "</div></details>";
}

function renderRows(alerts) {
  tbody.innerHTML = "";
  for (const alert of alerts) {
    const tr = document.createElement("tr");
    const windows = Array.isArray(alert.windows) ? alert.windows : [];
    const windowsHtml = renderWindowCards(windows);
    tr.innerHTML =
      "<td>" + alert.alertID + "</td>" +
      "<td>" + (alert.nativeEventID ?? "-") + "</td>" +
      "<td>" + alert.endpointID + "</td>" +
      "<td>" + formatTimestamp(alert.tsBegin) + "</td>" +
      "<td>" + formatTimestamp(alert.tsEnd) + "</td>" +
      "<td><span class='period-badge'>" + periodIcon() + formatPeriod(alert.periodTs) + "</span></td>" +
      "<td><span class='confidence-badge'>" + confidenceIcon() + formatConfidence(alert.confidence) + "</span></td>" +
      "<td>" + windowsHtml + "</td>";
    tbody.appendChild(tr);
  }
  table.hidden = alerts.length === 0;
}

form.addEventListener("submit", async function (event) {
  event.preventDefault();
  const endpointID = endpointInput.value.trim();
  if (!endpointID) {
    return;
  }

  statusEl.textContent = "Loading...";
  table.hidden = true;
  tbody.innerHTML = "";

  try {
    const response = await fetch("/api/alerts?endpointID=" + encodeURIComponent(endpointID));
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
  }
});