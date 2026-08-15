const statusEl = document.getElementById("adminStatus");
const usersList = document.getElementById("usersList");
const endpointsList = document.getElementById("endpointsList");
const addUserForm = document.getElementById("addUserForm");
const addEndpointForm = document.getElementById("addEndpointForm");
const newUserAdminWrap = document.getElementById("newUserAdminWrap");

let account = window.CURRENT_ACCOUNT || null;
// endpointID -> { secret, context } for inline secret expansion in the table.
let expandedSecretById = {};

const SECRET_PANEL_COPY = {
  register: {
    hint: "Endpoint registered — copy the ID and secret into the agent's config.",
    note: "Put both into <code>agent_config.json</code> on that machine.",
  },
  reset: {
    hint: "Secret reset — the previous secret no longer works.",
    note: "Update the agent with the new ID and secret below.",
  },
  show: {
    hint: "Keep these credentials safe — anyone with them can upload as this endpoint.",
    note: "Use both values in <code>agent_config.json</code>.",
  },
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function showStatus(message, isError) {
  statusEl.textContent = message;
  statusEl.classList.toggle("is-error", Boolean(isError));
  if (message && !isError) {
    setTimeout(() => {
      if (statusEl.textContent === message) {
        statusEl.textContent = "";
      }
    }, 4000);
  }
}

function displayValue(value) {
  return value === null || value === undefined || value === "" ? "-" : escapeHtml(value);
}

function roleLabel(user) {
  if (user.isSuperAdmin) return "Super admin";
  if (user.isAdmin) return "Admin";
  return "Member";
}

async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "Request failed.");
  }
  return data;
}

function renderUsers(users) {
  usersList.innerHTML = "";
  if (users.length === 0) {
    usersList.innerHTML = "<p class='detail-empty'>No users yet.</p>";
    return;
  }

  for (const user of users) {
    const actions = [];
    const isSelf = account && user.accountID === account.accountID;

    if (account && account.isSuperAdmin && !user.isSuperAdmin) {
      const label = user.isAdmin ? "Demote" : "Make admin";
      actions.push(
        "<button type='button' class='secondary-button row-action' data-action='toggle-admin' data-id='" +
          user.accountID + "' data-admin='" + (user.isAdmin ? "0" : "1") + "'>" + label + "</button>",
      );
    }
    if (!user.isSuperAdmin && !isSelf) {
      actions.push(
        "<button type='button' class='secondary-button row-action danger' data-action='delete-user' data-id='" +
          user.accountID + "'>Delete</button>",
      );
    }

    const row = document.createElement("div");
    row.className = "av-row av-admin-row";
    row.innerHTML =
      "<div class='av-row-head av-grid-admin-users' role='row'>" +
        "<div role='cell'>" + displayValue(user.name) + "</div>" +
        "<div role='cell'><span class='role-badge" + (user.isAdmin ? " is-admin" : "") + "'>" + roleLabel(user) + "</span></div>" +
        "<div role='cell' class='admin-actions-col'><div class='admin-row-actions'>" +
          (actions.join("") || "<span class='muted'>&mdash;</span>") +
        "</div></div>" +
      "</div>";
    usersList.appendChild(row);
  }
}

function renderSecretPanel(endpointId, secret, context) {
  const copy = SECRET_PANEL_COPY[context] || SECRET_PANEL_COPY.show;
  const safeId = escapeHtml(endpointId);
  const safeSecret = escapeHtml(secret);
  const nextSteps = context === "register"
    ? "<ol class='endpoint-next-steps'>" +
        "<li>Copy the Endpoint ID and secret into <code>agent_config.json</code> on the target machine.</li>" +
        "<li>Install or restart the log agent with the updated config.</li>" +
        "<li>Open <a href='/entities'>Entities</a> and confirm the endpoint shows as active.</li>" +
      "</ol>"
    : "";
  return (
    "<div class='endpoint-secret-panel'>" +
      "<p class='endpoint-secret-hint'>" + escapeHtml(copy.hint) + "</p>" +
      "<div class='endpoint-secret-grid'>" +
        "<div>" +
          "<span class='secret-label'><b>Endpoint ID</b></span>" +
          "<div class='secret-value-row'>" +
            "<code class='endpoint-secret-value'>" + safeId + "</code>" +
            "<button type='button' class='secondary-button copy-button'>Copy</button>" +
          "</div>" +
        "</div>" +
        "<div>" +
          "<span class='secret-label'><b>Secret</b></span>" +
          "<div class='secret-value-row'>" +
            "<code class='endpoint-secret-value'>" + safeSecret + "</code>" +
            "<button type='button' class='secondary-button copy-button'>Copy</button>" +
          "</div>" +
        "</div>" +
      "</div>" +
      nextSteps +
      "<p class='endpoint-secret-note'>" + copy.note + "</p>" +
    "</div>"
  );
}

function renderEndpoints(endpoints) {
  endpointsList.innerHTML = "";
  if (endpoints.length === 0) {
    endpointsList.innerHTML = "<p class='detail-empty'>No endpoints registered yet.</p>";
    return;
  }
  for (const endpoint of endpoints) {
    const endpointId = endpoint.endpointID;
    const id = escapeHtml(endpointId);
    const lastSeen = endpoint.lastSeenAt
      ? (window.formatRelativeTimestamp ? formatRelativeTimestamp(endpoint.lastSeenAt) : endpoint.lastSeenAt)
      : "Never";
    const expanded = expandedSecretById[endpointId];
    const isExpanded = Boolean(expanded);

    const actions = [];
    if (endpoint.hasSecret) {
      actions.push(
        "<button type='button' class='secondary-button row-action' data-action='toggle-secret' data-id='" + id + "'>" +
          (isExpanded ? "Hide secret" : "Show secret") +
        "</button>",
      );
    }
    actions.push(
      "<button type='button' class='secondary-button row-action' data-action='reset-secret' data-id='" + id + "'>Reset secret</button>",
    );
    actions.push(
      "<button type='button' class='secondary-button row-action danger' data-action='delete-endpoint' data-id='" + id + "'>Delete</button>",
    );

    const row = document.createElement("div");
    row.className = "av-row av-admin-row endpoint-row" + (isExpanded ? " is-secret-expanded" : "");
    row.dataset.endpointId = endpointId;
    row.innerHTML =
      "<div class='av-row-head av-grid-admin-endpoints' role='row'>" +
        "<div role='cell'><code>" + displayValue(endpointId) + "</code></div>" +
        "<div role='cell'>" + displayValue(endpoint.displayName) + "</div>" +
        "<div role='cell'>" + displayValue(endpoint.hostname) + "</div>" +
        "<div role='cell'>" + displayValue(endpoint.ip) + "</div>" +
        "<div role='cell'>" + escapeHtml(lastSeen) + "</div>" +
        "<div role='cell' class='admin-actions-col'><div class='admin-row-actions'>" + actions.join("") + "</div></div>" +
      "</div>" +
      "<div class='av-row-detail endpoint-secret-detail'" + (isExpanded ? "" : " hidden") + ">" +
        (isExpanded ? renderSecretPanel(endpointId, expanded.secret, expanded.context) : "") +
      "</div>";
    endpointsList.appendChild(row);
  }
}

function collapseAllEndpointSecrets(exceptId) {
  for (const otherId of Object.keys(expandedSecretById)) {
    if (otherId !== exceptId) {
      collapseEndpointSecret(otherId);
    }
  }
}

function expandEndpointSecret(endpointId, secret, context) {
  collapseAllEndpointSecrets(endpointId);
  expandedSecretById[endpointId] = { secret, context };
  const mainRow = endpointsList.querySelector(".endpoint-row[data-endpoint-id='" + endpointId + "']");
  if (!mainRow) {
    return;
  }
  const detail = mainRow.querySelector(".endpoint-secret-detail");
  if (!detail) {
    return;
  }
  mainRow.classList.add("is-secret-expanded");
  detail.hidden = false;
  detail.innerHTML = renderSecretPanel(endpointId, secret, context);
  const toggleBtn = mainRow.querySelector("button[data-action='toggle-secret']");
  if (toggleBtn) {
    toggleBtn.textContent = "Hide secret";
  }
  detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function scrollToEndpointSecret(endpointId) {
  const mainRow = endpointsList.querySelector(".endpoint-row[data-endpoint-id='" + endpointId + "']");
  const detail = mainRow ? mainRow.querySelector(".endpoint-secret-detail") : null;
  if (detail && !detail.hidden) {
    detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function collapseEndpointSecret(endpointId) {
  delete expandedSecretById[endpointId];
  const mainRow = endpointsList.querySelector(".endpoint-row[data-endpoint-id='" + endpointId + "']");
  if (!mainRow) {
    return;
  }
  const detail = mainRow.querySelector(".endpoint-secret-detail");
  if (!detail) {
    return;
  }
  mainRow.classList.remove("is-secret-expanded");
  detail.hidden = true;
  detail.innerHTML = "";
  const toggleBtn = mainRow.querySelector("button[data-action='toggle-secret']");
  if (toggleBtn) {
    toggleBtn.textContent = "Show secret";
  }
}

async function loadUsers() {
  try {
    const data = await api("/api/admin/users");
    renderUsers(data.users || []);
  } catch (error) {
    showStatus(error.message, true);
  }
}

async function loadEndpoints() {
  try {
    const data = await api("/api/admin/endpoints");
    renderEndpoints(data.endpoints || []);
  } catch (error) {
    showStatus(error.message, true);
  }
}

addUserForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = document.getElementById("newUserName").value.trim();
  const password = document.getElementById("newUserPassword").value;
  const makeAdmin = document.getElementById("newUserAdmin").checked;
  try {
    await api("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: name, password, isAdmin: makeAdmin }),
    });
    addUserForm.reset();
    showStatus("User added.", false);
    loadUsers();
  } catch (error) {
    showStatus(error.message, true);
  }
});

addEndpointForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = document.getElementById("newEndpointName").value.trim();
  if (!name) {
    showStatus("Endpoint name is required.", true);
    return;
  }
  try {
    const data = await api("/api/admin/endpoints", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    addEndpointForm.reset();
    expandedSecretById = {
      [data.endpoint.endpointID]: {
        secret: data.endpoint.secret,
        context: "register",
      },
    };
    await loadEndpoints();
    scrollToEndpointSecret(data.endpoint.endpointID);
  } catch (error) {
    showStatus(error.message, true);
  }
});

async function copyText(text, button) {
  const original = button.textContent;
  try {
    await navigator.clipboard.writeText(text);
  } catch (error) {
    const helper = document.createElement("textarea");
    helper.value = text;
    helper.style.position = "fixed";
    helper.style.opacity = "0";
    document.body.appendChild(helper);
    helper.select();
    try {
      document.execCommand("copy");
    } catch (fallbackError) {
      document.body.removeChild(helper);
      return;
    }
    document.body.removeChild(helper);
  }
  button.textContent = "Copied";
  button.classList.add("is-copied");
  setTimeout(() => {
    button.textContent = original;
    button.classList.remove("is-copied");
  }, 1500);
}

document.body.addEventListener("click", async (event) => {
  const copyButton = event.target.closest("button.copy-button");
  if (copyButton) {
    const code = copyButton.closest(".secret-value-row")?.querySelector("code");
    if (code) {
      copyText(code.textContent, copyButton);
    }
    return;
  }

  const button = event.target.closest("button[data-action]");
  if (!button) {
    return;
  }
  const action = button.dataset.action;
  const id = button.dataset.id;
  try {
    if (action === "delete-user") {
      if (!confirm("Delete this user?")) return;
      await api("/api/admin/users/" + id, { method: "DELETE" });
      showStatus("User deleted.", false);
      loadUsers();
    } else if (action === "toggle-admin") {
      await api("/api/admin/users/" + id + "/admin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ isAdmin: button.dataset.admin === "1" }),
      });
      loadUsers();
    } else if (action === "toggle-secret") {
      if (expandedSecretById[id]) {
        collapseEndpointSecret(id);
        return;
      }
      const data = await api("/api/admin/endpoints/" + encodeURIComponent(id) + "/secret");
      expandEndpointSecret(id, data.endpoint.secret, "show");
    } else if (action === "reset-secret") {
      if (!confirm("Reset this endpoint's secret? The current secret stops working immediately and the agent must be updated.")) return;
      const data = await api("/api/admin/endpoints/" + encodeURIComponent(id) + "/secret/reset", { method: "POST" });
      expandedSecretById = {
        [id]: { secret: data.endpoint.secret, context: "reset" },
      };
      await loadEndpoints();
      scrollToEndpointSecret(id);
      showStatus("Secret reset.", false);
    } else if (action === "delete-endpoint") {
      if (!confirm("Delete this endpoint? Its agent will no longer be able to upload.")) return;
      delete expandedSecretById[id];
      await api("/api/admin/endpoints/" + encodeURIComponent(id), { method: "DELETE" });
      showStatus("Endpoint deleted.", false);
      loadEndpoints();
    }
  } catch (error) {
    showStatus(error.message, true);
  }
});

document.addEventListener("auth:ready", (event) => {
  account = event.detail;
  newUserAdminWrap.hidden = !account.isSuperAdmin;
  loadUsers();
  loadEndpoints();
});

if (account) {
  newUserAdminWrap.hidden = !account.isSuperAdmin;
  loadUsers();
  loadEndpoints();
}
