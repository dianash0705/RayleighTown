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
  statActiveWeek.textContent = String(summary.activeLastWeek ?? 0);
  statHighConfidence.textContent = String(summary.highConfidenceLastWeek ?? 0);
  summaryWidgets.hidden = false;
}

function compactChartLabel(label) {
  const parts = String(label).trim().split(/\s+/);
  return parts.length > 1 ? parts[parts.length - 1] : label;
}

function renderVolumeChart(timeline) {
  const points = Array.isArray(timeline) ? timeline : [];
  const width = 320;
  const height = 180;

  if (!points.length) {
    volumeChart.innerHTML =
      "<text x='" + (width / 2) + "' y='" + (height / 2) + "' text-anchor='middle' class='chart-empty'>No activity</text>";
    return;
  }

  const padding = { top: 14, right: 10, bottom: 34, left: 34 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const maxCount = Math.max(...points.map((point) => Number(point.count) || 0), 1);
  const yMax = Math.max(4, Math.ceil(maxCount * 1.15));
  const barGap = 6;
  const barWidth = (plotWidth - barGap * (points.length - 1)) / points.length;

  const yTicks = 3;
  const gridLines = [];
  for (let index = 0; index <= yTicks; index += 1) {
    const value = Math.round((yMax / yTicks) * index);
    const y = padding.top + plotHeight - (value / yMax) * plotHeight;
    gridLines.push(
      "<line x1='" + padding.left + "' y1='" + y + "' x2='" + (width - padding.right) + "' y2='" + y + "' class='chart-grid-line'></line>" +
      "<text x='" + (padding.left - 6) + "' y='" + (y + 3) + "' text-anchor='end' class='chart-axis-label chart-axis-label-compact'>" + value + "</text>"
    );
  }

  const bars = points.map(function (point, index) {
    const count = Number(point.count) || 0;
    const barHeight = (count / yMax) * plotHeight;
    const x = padding.left + index * (barWidth + barGap);
    const y = padding.top + plotHeight - barHeight;
    const shortLabel = compactChartLabel(point.label);
    return (
      "<rect class='chart-bar' x='" + x + "' y='" + y + "' width='" + barWidth + "' height='" + barHeight + "' rx='4'>" +
        "<title>" + escapeHtml(point.label) + ": " + count + " alerts</title>" +
      "</rect>" +
      "<text x='" + (x + barWidth / 2) + "' y='" + (height - 10) + "' text-anchor='middle' class='chart-axis-label chart-axis-label-compact'>" + escapeHtml(shortLabel) + "</text>" +
      (count > 0
        ? "<text x='" + (x + barWidth / 2) + "' y='" + Math.max(y - 4, padding.top + 8) + "' text-anchor='middle' class='chart-bar-label chart-bar-label-compact'>" + count + "</text>"
        : "")
    );
  }).join("");

  volumeChart.innerHTML =
    gridLines.join("") +
    "<line x1='" + padding.left + "' y1='" + padding.top + "' x2='" + padding.left + "' y2='" + (padding.top + plotHeight) + "' class='chart-axis-line'></line>" +
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
      const alertsUrl = "/alerts?nativeEventID=" + encodeURIComponent(event.nativeEventID) + "&timePreset=last_week";
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
  params.set("timePreset", "last_week");
  params.set("endpointID", alert.endpointID);
  if (alert.nativeEventID !== null && alert.nativeEventID !== undefined) {
    params.set("nativeEventID", String(alert.nativeEventID));
  }
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
    const endpointLabel = alert.name
      ? "Endpoint ID " + alert.endpointID + " · " + alert.name
      : "Endpoint ID " + alert.endpointID;
    const endedRelative = formatRelativeTimestamp(alert.tsEnd);
    const endedCaption = endedRelative ? "Ended " + endedRelative : "Recently active";

    li.innerHTML =
      "<a class='recent-alert-card' href='" + escapeHtml(triageUrl) + "'>" +
        "<div class='recent-alert-main'>" +
          "<span class='recent-alert-event'>" + escapeHtml(alert.eventName) + "</span>" +
          "<span class='recent-alert-meta'>" + escapeHtml(endpointLabel) + " · Event ID " + escapeHtml(alert.nativeEventID) + "</span>" +
          "<span class='recent-alert-time'>" + escapeHtml(endedCaption) + "</span>" +
        "</div>" +
        "<span class='recent-alert-confidence'>" + escapeHtml(alert.confidence) + "%</span>" +
      "</a>";

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
      const alertsUrl = "/alerts?endpointID=" + encodeURIComponent(endpoint.endpointID) + "&timePreset=last_week";
      const nameMeta = endpoint.name
        ? "<span class='ranked-meta'>" + escapeHtml(endpoint.name) + "</span>"
        : "<span class='ranked-meta'>No hostname mapped</span>";
      return (
        "<div class='ranked-main'>" +
          "<a class='ranked-link' href='" + escapeHtml(alertsUrl) + "'>Endpoint ID " + escapeHtml(endpoint.endpointID) + "</a>" +
          nameMeta +
        "</div>" +
        "<span class='ranked-count'>" + escapeHtml(endpoint.alertCount) + "</span>"
      );
    }
  );
}

function syncChartHeight() {
  if (!mainWidgets || mainWidgets.hidden || !chartWidget || !listsWidget) {
    return;
  }

  chartWidget.style.height = "";
  const sideHeight = listsWidget.offsetHeight;
  if (sideHeight <= 0) {
    return;
  }

  // Match the left card to the right stack; the chart area fills only what's left below the header.
  chartWidget.style.height = sideHeight + "px";
}

async function loadDashboard() {
  statusEl.textContent = "Loading dashboard...";
  summaryWidgets.hidden = true;
  recentAlertsWidget.hidden = true;
  mainWidgets.hidden = true;

  try {
    const response = await fetch("/api/dashboard");
    const data = await response.json();

    if (!response.ok) {
      statusEl.textContent = data.error || "Failed to load dashboard";
      return;
    }

    statusEl.textContent = "";
    renderSummary(data.summary || {});
    renderRecentHighConfidenceAlerts(data.recentHighConfidenceAlerts || []);
    renderVolumeChart(data.timeline || []);
    renderTopEvents(data.topEvents || []);
    renderTopEndpoints(data.topEndpoints || []);
    mainWidgets.hidden = false;
    requestAnimationFrame(function () {
      requestAnimationFrame(syncChartHeight);
    });
    if (typeof ResizeObserver !== "undefined") {
      if (!window.__dashboardHeightObserver) {
        window.__dashboardHeightObserver = new ResizeObserver(syncChartHeight);
        window.__dashboardHeightObserver.observe(listsWidget);
      }
    } else {
      window.addEventListener("resize", syncChartHeight);
    }
  } catch (error) {
    statusEl.textContent = "Network error while loading dashboard";
  }
}

loadDashboard();
