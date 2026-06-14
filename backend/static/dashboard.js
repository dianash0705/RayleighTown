const statusEl = document.getElementById("status");
const summaryWidgets = document.getElementById("summaryWidgets");
const mainWidgets = document.getElementById("mainWidgets");
const chartWidget = document.getElementById("chartWidget");
const listsWidget = document.getElementById("listsWidget");
const statActiveNow = document.getElementById("statActiveNow");
const statActive24h = document.getElementById("statActive24h");
const statActiveWeek = document.getElementById("statActiveWeek");
const statHighConfidence = document.getElementById("statHighConfidence");
const volumeChart = document.getElementById("volumeChart");
const topEventsList = document.getElementById("topEventsList");
const topEndpointsList = document.getElementById("topEndpointsList");
const recentAlertsWidget = document.getElementById("recentAlertsWidget");
const recentAlertsList = document.getElementById("recentAlertsList");
const recentAlertsViewAll = document.getElementById("recentAlertsViewAll");
const volumeChartSubtitle = document.getElementById("volumeChartSubtitle");
const topEventsSubtitle = document.getElementById("topEventsSubtitle");
const topEndpointsSubtitle = document.getElementById("topEndpointsSubtitle");
const recentAlertsSubtitle = document.getElementById("recentAlertsSubtitle");
const statWindowCaption = document.getElementById("statWindowCaption");

let timeRangeControls = null;
if (window.TimeRangeControls) {
  timeRangeControls = window.TimeRangeControls.init({
    onChange: loadDashboard,
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

function renderSummary(summary) {
  statActiveNow.textContent = String(summary.activeNow ?? 0);
  statActive24h.textContent = String(summary.activeLast24h ?? 0);
  statActiveWeek.textContent = String(summary.activeInWindow ?? summary.activeLastWeek ?? 0);
  statHighConfidence.textContent = String(summary.highConfidenceInWindow ?? summary.highConfidenceLastWeek ?? 0);
  summaryWidgets.hidden = false;
}

function updateRangeLabels(label) {
  const suffix = label ? " (" + label.toLowerCase() + ")" : "";
  if (statWindowCaption) {
    statWindowCaption.textContent = "Alerts overlapping the selected time range" + suffix;
  }
  if (volumeChartSubtitle) {
    volumeChartSubtitle.textContent = "Overlap count in the selected range" + suffix;
  }
  if (topEventsSubtitle) {
    topEventsSubtitle.textContent = "Top event types in the selected range" + suffix;
  }
  if (topEndpointsSubtitle) {
    topEndpointsSubtitle.textContent = "Endpoint IDs with the most alerts in the selected range" + suffix;
  }
  if (recentAlertsSubtitle) {
    recentAlertsSubtitle.textContent = "Top triage candidates in the selected range (80%+ confidence)" + suffix;
  }
}

function renderVolumeChart(timeline) {
  const points = Array.isArray(timeline) ? timeline : [];

  // Fixed internal coordinate system; the SVG scales uniformly to the card width
  // via preserveAspectRatio="xMidYMid meet" so bars never squash or stretch.
  const width = 720;
  const height = 300;
  volumeChart.setAttribute("viewBox", "0 0 " + width + " " + height);

  if (!points.length) {
    volumeChart.innerHTML =
      "<text x='" + (width / 2) + "' y='" + (height / 2) + "' text-anchor='middle' class='chart-empty'>No activity in this range</text>";
    return;
  }

  const padding = { top: 24, right: 24, bottom: 56, left: 48 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const maxCount = Math.max(...points.map((point) => Number(point.count) || 0), 1);
  const yMax = Math.max(4, Math.ceil(maxCount * 1.15));

  const slotWidth = plotWidth / points.length;
  const barWidth = Math.min(48, slotWidth * 0.62);
  const labelStep = points.length > 12 ? 2 : 1;

  const yTicks = 4;
  const gridLines = [];
  for (let index = 0; index <= yTicks; index += 1) {
    const value = Math.round((yMax / yTicks) * index);
    const y = padding.top + plotHeight - (value / yMax) * plotHeight;
    gridLines.push(
      "<line x1='" + padding.left + "' y1='" + y + "' x2='" + (width - padding.right) + "' y2='" + y + "' class='chart-grid-line'></line>" +
      "<text x='" + (padding.left - 10) + "' y='" + (y + 4) + "' text-anchor='end' class='chart-axis-label'>" + value + "</text>"
    );
  }

  const bars = points.map(function (point, index) {
    const count = Number(point.count) || 0;
    const barHeight = (count / yMax) * plotHeight;
    const slotCenter = padding.left + slotWidth * (index + 0.5);
    const x = slotCenter - barWidth / 2;
    const y = padding.top + plotHeight - barHeight;
    const showLabel = index % labelStep === 0;
    return (
      "<rect class='chart-bar' x='" + x + "' y='" + y + "' width='" + barWidth + "' height='" + Math.max(0, barHeight) + "' rx='5'>" +
        "<title>" + escapeHtml(point.label) + ": " + count + " alerts</title>" +
      "</rect>" +
      (showLabel
        ? "<text x='" + slotCenter + "' y='" + (height - padding.bottom + 22) + "' text-anchor='middle' class='chart-axis-label'>" + escapeHtml(point.label) + "</text>"
        : "") +
      (count > 0
        ? "<text x='" + slotCenter + "' y='" + Math.max(y - 8, padding.top + 12) + "' text-anchor='middle' class='chart-bar-label'>" + count + "</text>"
        : "")
    );
  }).join("");

  volumeChart.innerHTML =
    gridLines.join("") +
    "<line x1='" + padding.left + "' y1='" + (padding.top + plotHeight) + "' x2='" + (width - padding.right) + "' y2='" + (padding.top + plotHeight) + "' class='chart-axis-line'></line>" +
    bars;
}

function renderRankedList(container, items, emptyText, renderItem) {
  container.innerHTML = "";
  if (!items.length) {
    const li = document.createElement("li");
    li.className = "ranked-empty";
    li.textContent = emptyText;
    container.appendChild(li);
    return;
  }

  for (const item of items) {
    const li = document.createElement("li");
    li.className = "ranked-item";
    li.innerHTML = renderItem(item);
    container.appendChild(li);
  }
}

function renderTopEvents(events) {
  renderRankedList(
    topEventsList,
    events,
    "No alert events recorded in the past week.",
    function (event) {
      const alertsUrl = buildAlertsListUrl({ nativeEventID: String(event.nativeEventID) });
      return (
        "<div class='ranked-main'>" +
          "<a class='ranked-link' href='" + escapeHtml(alertsUrl) + "'>" + escapeHtml(event.eventName) + "</a>" +
          "<span class='ranked-meta'>Event ID " + escapeHtml(event.nativeEventID) + "</span>" +
        "</div>" +
        "<span class='ranked-count'>" + escapeHtml(event.alertCount) + "</span>"
      );
    }
  );
}

function buildAlertTriageUrl(alert) {
  const params = new URLSearchParams();
  params.set("minConfidence", "80");
  params.set("endpointID", alert.endpointID);
  if (alert.nativeEventID !== null && alert.nativeEventID !== undefined) {
    params.set("nativeEventID", String(alert.nativeEventID));
  }
  return "/alerts?" + params.toString();
}

function buildAlertsListUrl(extraParams) {
  const params = new URLSearchParams();
  if (extraParams) {
    for (const [key, value] of Object.entries(extraParams)) {
      if (key === "timePreset" || key === "timeFrom" || key === "timeTo") {
        continue;
      }
      params.set(key, value);
    }
  }
  return "/alerts?" + params.toString();
}

function buildEndpointAlertsUrl(endpointID) {
  const params = new URLSearchParams();
  params.set("endpointID", endpointID);
  return "/alerts?" + params.toString();
}

function renderRecentHighConfidenceAlerts(alerts) {
  recentAlertsList.innerHTML = "";
  const items = Array.isArray(alerts) ? alerts : [];

  if (!items.length) {
    const li = document.createElement("li");
    li.className = "recent-alert-empty";
    li.textContent = "No high-confidence alerts in the past week.";
    recentAlertsList.appendChild(li);
    recentAlertsWidget.hidden = false;
    return;
  }

  for (const alert of items) {
    const li = document.createElement("li");
    const triageUrl = buildAlertTriageUrl(alert);
    const endpointUrl = buildEndpointAlertsUrl(alert.endpointID);
    const endpointName = alert.name || ("Endpoint " + alert.endpointID);
    const endpointMeta = alert.name
      ? "<a class='endpoint-link' href='" + escapeHtml(endpointUrl) + "'>" + escapeHtml(alert.name) + "</a> · ID " + escapeHtml(alert.endpointID)
      : "<a class='endpoint-link' href='" + escapeHtml(endpointUrl) + "'>" + escapeHtml(endpointName) + "</a>";
    const endedRelative = formatRelativeTimestamp(alert.tsEnd);
    const endedCaption = endedRelative ? "Ended " + endedRelative : "Recently active";

    li.innerHTML =
      "<div class='recent-alert-card'>" +
        "<a class='recent-alert-main-link' href='" + escapeHtml(triageUrl) + "'>" +
          "<span class='recent-alert-event'>" + escapeHtml(alert.eventName) + "</span>" +
          "<span class='recent-alert-meta'>" + endpointMeta + " · Event ID " + escapeHtml(alert.nativeEventID) + "</span>" +
          "<span class='recent-alert-time'>" + escapeHtml(endedCaption) + "</span>" +
        "</a>" +
        "<a class='recent-alert-confidence' href='" + escapeHtml(triageUrl) + "'>" + escapeHtml(alert.confidence) + "%</a>" +
      "</div>";

    recentAlertsList.appendChild(li);
  }

  recentAlertsWidget.hidden = false;
}

function renderTopEndpoints(endpoints) {
  renderRankedList(
    topEndpointsList,
    endpoints,
    "No endpoint activity recorded in the past week.",
    function (endpoint) {
      const alertsUrl = buildAlertsListUrl({ endpointID: endpoint.endpointID });
      const primary = endpoint.name || endpoint.endpointID;
      const idMeta = endpoint.name
        ? "<span class='ranked-meta'>ID " + escapeHtml(endpoint.endpointID) + "</span>"
        : "<span class='ranked-meta'>No name set</span>";
      return (
        "<div class='ranked-main'>" +
          "<a class='ranked-link' href='" + escapeHtml(alertsUrl) + "'>" + escapeHtml(primary) + "</a>" +
          idMeta +
        "</div>" +
        "<span class='ranked-count'>" + escapeHtml(endpoint.alertCount) + "</span>"
      );
    }
  );
}

async function loadDashboard() {
  if (window.PageStatus) {
    PageStatus.showLoading(statusEl, "Loading dashboard...");
  } else {
    statusEl.textContent = "Loading dashboard...";
  }
  summaryWidgets.hidden = true;
  recentAlertsWidget.hidden = true;
  mainWidgets.hidden = true;

  try {
    const query = timeRangeControls ? "?" + timeRangeControls.buildQueryParams().toString() : "";
    const response = await fetch("/api/dashboard" + query);
    const data = await response.json();

    if (!response.ok) {
      if (window.PageStatus) {
        PageStatus.showError(statusEl, data.error || "Failed to load dashboard");
      } else {
        statusEl.textContent = data.error || "Failed to load dashboard";
      }
      return;
    }

    if (window.PageStatus) {
      PageStatus.clear(statusEl);
    } else {
      statusEl.textContent = "";
    }
    updateRangeLabels(timeRangeControls ? timeRangeControls.getLabel() : "Last week");
    if (recentAlertsViewAll) {
      recentAlertsViewAll.href = buildAlertsListUrl({ minConfidence: "80" });
    }
    renderSummary(data.summary || {});
    renderRecentHighConfidenceAlerts(data.recentHighConfidenceAlerts || []);
    renderVolumeChart(data.timeline || []);
    renderTopEvents(data.topEvents || []);
    renderTopEndpoints(data.topEndpoints || []);
    mainWidgets.hidden = false;
  } catch (error) {
    if (window.PageStatus) {
      PageStatus.showError(statusEl, "Network error while loading dashboard.");
    } else {
      statusEl.textContent = "Network error while loading dashboard";
    }
  }
}

loadDashboard();
