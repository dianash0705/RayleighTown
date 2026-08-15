const statusEl = document.getElementById("whitelistStatus");
const list = document.getElementById("whitelistList");
const emptyEl = document.getElementById("whitelistEmpty");
const chipCount = document.getElementById("chipWhitelistCount");
const addForm = document.getElementById("addWhitelistForm");
const addPatternPanel = document.getElementById("addPatternPanel");
const openAddPattern = document.getElementById("openAddPattern");
const cancelAddPattern = document.getElementById("cancelAddPattern");
const addEventType = document.getElementById("addEventType");
const addLimitEndpoint = document.getElementById("addLimitEndpoint");
const addEndpointWrap = document.getElementById("addEndpointWrap");
const addEndpoint = document.getElementById("addEndpoint");
const addIdentityFields = document.getElementById("addIdentityFields");
const addMatchPeriod = document.getElementById("addMatchPeriod");
const addPeriodWrap = document.getElementById("addPeriodWrap");
const addPeriodSeconds = document.getElementById("addPeriodSeconds");
const addNote = document.getElementById("addNote");

let seriesCatalog = [];
let pageReady = false;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function humanizeFieldName(name) {
  return String(name || "")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function setStatus(message, isError) {
  if (!statusEl) {
    return;
  }
  statusEl.textContent = message || "";
  statusEl.classList.toggle("is-error", Boolean(isError));
}

function formatPeriodMs(periodMs) {
  if (periodMs == null || !Number.isFinite(Number(periodMs))) {
    return "Any";
  }
  const seconds = Number(periodMs) / 1000;
  if (seconds < 90) {
    return "~" + Math.round(seconds) + "s";
  }
  if (seconds < 3600) {
    return "~" + (seconds / 60).toFixed(seconds % 60 === 0 ? 0 : 1) + "m";
  }
  return "~" + (seconds / 3600).toFixed(seconds % 3600 === 0 ? 0 : 1) + "h";
}

function formatWhen(createdAt) {
  if (!createdAt) {
    return "—";
  }
  if (typeof formatRelativeTimestamp === "function") {
    return formatRelativeTimestamp(createdAt);
  }
  return new Date(createdAt).toLocaleDateString();
}

function matchSummary(entry) {
  const identity = entry.seriesIdentity && typeof entry.seriesIdentity === "object"
    ? entry.seriesIdentity
    : {};
  const keys = Object.keys(identity);
  if (!entry.seriesKey && keys.length === 0) {
    return { label: "Any series", details: null };
  }
  if (keys.length === 0) {
    return {
      label: "Specific series",
      details: "<code class='whitelist-series'>" + escapeHtml(entry.seriesKey || "") + "</code>",
    };
  }
  const chips = keys
    .sort()
    .map(function (key) {
      return (
        "<span class='whitelist-chip'><span class='whitelist-chip-key'>" +
        escapeHtml(humanizeFieldName(key)) +
        "</span> " +
        escapeHtml(identity[key]) +
        "</span>"
      );
    })
    .join("");
  return {
    label: keys.length === 1 ? "1 field" : keys.length + " fields",
    details: chips,
  };
}

function renderRows(entries) {
  list.innerHTML = "";
  chipCount.textContent = String(entries.length);
  emptyEl.hidden = entries.length > 0;
  list.hidden = entries.length === 0;

  for (const entry of entries) {
    const row = document.createElement("div");
    row.className = "av-row av-whitelist-row";
    const scopeLabel = entry.scope === "organization"
      ? "Org-wide"
      : (entry.endpointName || entry.endpointID || "Endpoint");
    const eventLabel = (entry.eventName || ("Event " + entry.nativeEventID)) +
      " · " + entry.nativeEventID;
    const match = matchSummary(entry);

    row.innerHTML =
      "<div class='av-row-head av-grid-whitelist' role='row'>" +
        "<div role='cell' class='whitelist-note-cell'><strong>" + escapeHtml(entry.note || "") + "</strong></div>" +
        "<div role='cell'>" + escapeHtml(eventLabel) +
          (entry.logSource ? "<div class='muted-line'>" + escapeHtml(entry.logSource) + "</div>" : "") +
        "</div>" +
        "<div role='cell'>" + escapeHtml(scopeLabel) + "</div>" +
        "<div role='cell' class='whitelist-match-cell'>" +
          "<button type='button' class='whitelist-match-toggle' data-expand-match aria-expanded='false'>" +
            escapeHtml(match.label) +
            (match.details ? " <span class='whitelist-match-caret'>▾</span>" : "") +
          "</button>" +
          (match.details
            ? "<div class='whitelist-match-details' hidden>" + match.details + "</div>"
            : "") +
        "</div>" +
        "<div role='cell'>" + escapeHtml(formatPeriodMs(entry.periodMs)) + "</div>" +
        "<div role='cell'>" + escapeHtml(formatWhen(entry.createdAt)) + "</div>" +
        "<div role='cell'>" + escapeHtml(entry.createdByName || "—") + "</div>" +
        "<div role='cell' class='admin-actions-col whitelist-actions-cell'>" +
          "<button type='button' class='row-action danger' data-delete-id='" +
          escapeHtml(entry.whitelistID) +
          "'>Remove</button>" +
        "</div>" +
      "</div>";
    list.appendChild(row);
  }
}

function selectedCatalogEntry() {
  const value = addEventType.value;
  if (!value) {
    return null;
  }
  const parts = value.split(":");
  const logID = Number(parts[0]);
  const nativeEventID = Number(parts[1]);
  return seriesCatalog.find(function (item) {
    return Number(item.logID) === logID && Number(item.nativeEventID) === nativeEventID;
  }) || null;
}

function renderIdentityFields() {
  const entry = selectedCatalogEntry();
  addIdentityFields.innerHTML = "";
  if (!entry || !Array.isArray(entry.fields) || entry.fields.length === 0) {
    addIdentityFields.innerHTML =
      "<p class='muted-line'>No identity fields — mutes this event type.</p>";
    return;
  }
  for (const field of entry.fields) {
    const label = document.createElement("label");
    label.className = "whitelist-field";
    const title = document.createElement("span");
    title.className = "whitelist-field-label whitelist-field-label-source";
    title.textContent = humanizeFieldName(field);
    title.title = field;
    const input = document.createElement("input");
    input.type = "text";
    input.dataset.identityField = field;
    if (field === "image") {
      input.placeholder = "C:\\App\\bin.exe";
    } else if (field === "TargetUserName") {
      input.placeholder = "SYSTEM";
    }
    label.appendChild(title);
    label.appendChild(input);
    addIdentityFields.appendChild(label);
  }
}

function collectSeriesIdentity() {
  const identity = {};
  for (const input of addIdentityFields.querySelectorAll("input[data-identity-field]")) {
    const value = input.value.trim();
    if (value) {
      identity[input.dataset.identityField] = value;
    }
  }
  return identity;
}

function syncEndpointUi() {
  const limited = addLimitEndpoint.checked;
  addEndpointWrap.classList.toggle("is-disabled", !limited);
  addEndpointWrap.setAttribute("aria-disabled", limited ? "false" : "true");
  addEndpointWrap.title = limited ? "" : "Check “Limit to one endpoint” to enable";
  addEndpoint.required = limited;
  addEndpoint.disabled = !limited;
}

function syncPeriodUi() {
  const limited = addMatchPeriod.checked;
  addPeriodWrap.classList.toggle("is-disabled", !limited);
  addPeriodWrap.setAttribute("aria-disabled", limited ? "false" : "true");
  addPeriodWrap.title = limited ? "" : "Check “Limit to a specific period” to enable";
  addPeriodSeconds.required = limited;
  addPeriodSeconds.disabled = !limited;
  if (!limited) {
    addPeriodSeconds.value = "";
  }
}

function resetAddForm() {
  addForm.reset();
  addLimitEndpoint.checked = false;
  addMatchPeriod.checked = false;
  addPeriodSeconds.value = "";
  syncEndpointUi();
  syncPeriodUi();
  renderIdentityFields();
}

function setAddPatternOpen(open) {
  addPatternPanel.hidden = !open;
  openAddPattern.hidden = open;
  openAddPattern.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    addNote.focus();
  }
}

async function deleteEntry(whitelistId) {
  if (!window.confirm("Remove this whitelist entry?")) {
    return;
  }
  setStatus("Removing…");
  try {
    const response = await fetch("/api/whitelist/" + encodeURIComponent(whitelistId), {
      method: "DELETE",
    });
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok) {
      setStatus(data.error || "Failed to remove entry.", true);
      return;
    }
    setStatus("Whitelist entry removed.");
    await loadWhitelist();
  } catch (error) {
    setStatus("Network error while removing entry.", true);
  }
}

async function loadWhitelist() {
  const response = await fetch("/api/whitelist");
  const data = await response.json().catch(function () { return {}; });
  if (!response.ok) {
    throw new Error(data.error || "Failed to load whitelist.");
  }
  renderRows(Array.isArray(data.entries) ? data.entries : []);
}

async function loadEventCatalog() {
  const response = await fetch("/api/meta");
  const meta = await response.json().catch(function () { return {}; });
  if (!response.ok) {
    throw new Error(meta.error || "Failed to load event types.");
  }
  seriesCatalog = Array.isArray(meta.seriesFieldCatalog) ? meta.seriesFieldCatalog : [];
  addEventType.innerHTML = "";
  if (seriesCatalog.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No event types available";
    addEventType.appendChild(option);
    addEventType.disabled = true;
  } else {
    addEventType.disabled = false;
    for (const item of seriesCatalog) {
      const option = document.createElement("option");
      option.value = item.logID + ":" + item.nativeEventID;
      option.textContent =
        (item.eventName || ("Event " + item.nativeEventID)) +
        " · " + item.nativeEventID +
        (item.logSource ? " (" + item.logSource + ")" : "");
      addEventType.appendChild(option);
    }
  }
  renderIdentityFields();
}

async function loadEndpoints() {
  let list = [];
  try {
    const adminResponse = await fetch("/api/admin/endpoints");
    if (adminResponse.ok) {
      const payload = await adminResponse.json();
      list = Array.isArray(payload.endpoints) ? payload.endpoints : [];
    }
  } catch (error) {
    list = [];
  }

  if (list.length === 0) {
    const entitiesResponse = await fetch("/api/entities?timePreset=all");
    const entitiesPayload = await entitiesResponse.json().catch(function () { return {}; });
    list = (entitiesPayload.entities || []).map(function (entity) {
      return {
        endpointID: entity.endpointID,
        displayName: entity.name || entity.endpointID,
      };
    });
  }

  addEndpoint.innerHTML = "";
  if (list.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No endpoints registered";
    addEndpoint.appendChild(option);
    addEndpoint.disabled = true;
  } else {
    for (const endpoint of list) {
      const option = document.createElement("option");
      option.value = endpoint.endpointID;
      option.textContent = endpoint.displayName || endpoint.name || endpoint.endpointID;
      addEndpoint.appendChild(option);
    }
  }
  syncEndpointUi();
}

async function bootPage() {
  if (pageReady) {
    return;
  }
  pageReady = true;
  syncEndpointUi();
  syncPeriodUi();
  setStatus("Loading…");
  try {
    const results = await Promise.allSettled([
      loadWhitelist(),
      loadEventCatalog(),
      loadEndpoints(),
    ]);
    const failures = results.filter(function (result) { return result.status === "rejected"; });
    if (failures.length === results.length) {
      setStatus(failures[0].reason.message || "Failed to load whitelist page.", true);
      return;
    }
    if (failures.length) {
      setStatus(failures.map(function (result) { return result.reason.message; }).join(" · "), true);
      return;
    }
    setStatus("");
  } catch (error) {
    setStatus(error.message || "Failed to load whitelist page.", true);
  }
}

addEventType.addEventListener("change", renderIdentityFields);
addLimitEndpoint.addEventListener("change", syncEndpointUi);
addMatchPeriod.addEventListener("change", syncPeriodUi);

addForm.addEventListener("submit", async function (event) {
  event.preventDefault();
  const catalogEntry = selectedCatalogEntry();
  if (!catalogEntry) {
    setStatus("Select an event type.", true);
    return;
  }
  const note = addNote.value.trim();
  if (!note) {
    setStatus("A note is required.", true);
    return;
  }

  const body = {
    scope: addLimitEndpoint.checked ? "endpoint" : "organization",
    logID: catalogEntry.logID,
    nativeEventID: catalogEntry.nativeEventID,
    seriesIdentity: collectSeriesIdentity(),
    note: note,
  };
  if (addLimitEndpoint.checked) {
    body.endpointID = addEndpoint.value;
    if (!body.endpointID) {
      setStatus("Select an endpoint.", true);
      return;
    }
  }
  if (addMatchPeriod.checked) {
    const seconds = Number(addPeriodSeconds.value);
    if (!Number.isFinite(seconds) || seconds <= 0) {
      setStatus("Enter a valid period in seconds.", true);
      return;
    }
    body.periodMs = seconds * 1000;
  }

  setStatus("Adding expected pattern…");
  const submitButton = addForm.querySelector("button[type='submit']");
  submitButton.disabled = true;
  try {
    const response = await fetch("/api/whitelist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok) {
      setStatus(data.error || "Failed to add whitelist entry.", true);
      return;
    }
    resetAddForm();
    setAddPatternOpen(false);
    setStatus("Pattern added to whitelist.");
    await loadWhitelist();
  } catch (error) {
    setStatus("Network error while adding whitelist entry.", true);
  } finally {
    submitButton.disabled = false;
  }
});

openAddPattern.addEventListener("click", function () {
  setAddPatternOpen(true);
});

cancelAddPattern.addEventListener("click", function () {
  resetAddForm();
  setAddPatternOpen(false);
  setStatus("");
});

list.addEventListener("click", function (event) {
  const expandButton = event.target.closest("[data-expand-match]");
  if (expandButton) {
    const cell = expandButton.closest(".whitelist-match-cell");
    const details = cell ? cell.querySelector(".whitelist-match-details") : null;
    if (!details) {
      return;
    }
    const open = details.hidden;
    details.hidden = !open;
    expandButton.setAttribute("aria-expanded", open ? "true" : "false");
    expandButton.classList.toggle("is-open", open);
    return;
  }

  const button = event.target.closest("button[data-delete-id]");
  if (!button) {
    return;
  }
  deleteEntry(button.dataset.deleteId);
});

document.addEventListener("auth:ready", bootPage);
if (window.CURRENT_ACCOUNT) {
  bootPage();
}
