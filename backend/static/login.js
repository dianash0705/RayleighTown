const loginForm = document.getElementById("loginForm");
const registerForm = document.getElementById("registerForm");
const showLoginBtn = document.getElementById("showLogin");
const showRegisterBtn = document.getElementById("showRegister");
const statusEl = document.getElementById("authStatus");
const recentOrgsList = document.getElementById("recentOrgsList");

const RECENT_ORGS_KEY = "rayleightown.recentOrgs";
const MAX_RECENT_ORGS = 8;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function loadRecentOrgs() {
  try {
    const raw = localStorage.getItem(RECENT_ORGS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
  } catch (error) {
    return [];
  }
}

function renderRecentOrgsDatalist(orgs) {
  if (!recentOrgsList) {
    return;
  }
  recentOrgsList.innerHTML = orgs
    .map(function (org) {
      return "<option value='" + escapeHtml(org) + "'></option>";
    })
    .join("");
}

function rememberRecentOrg(name) {
  const normalized = String(name || "").trim();
  if (!normalized) {
    return;
  }
  const orgs = loadRecentOrgs().filter(function (org) {
    return org.toLowerCase() !== normalized.toLowerCase();
  });
  orgs.unshift(normalized);
  localStorage.setItem(RECENT_ORGS_KEY, JSON.stringify(orgs.slice(0, MAX_RECENT_ORGS)));
  renderRecentOrgsDatalist(orgs.slice(0, MAX_RECENT_ORGS));
}

function setMode(mode) {
  const isLogin = mode === "login";
  loginForm.hidden = !isLogin;
  registerForm.hidden = isLogin;
  showLoginBtn.classList.toggle("is-active", isLogin);
  showRegisterBtn.classList.toggle("is-active", !isLogin);
  showLoginBtn.setAttribute("aria-selected", String(isLogin));
  showRegisterBtn.setAttribute("aria-selected", String(!isLogin));
  statusEl.textContent = "";
  statusEl.classList.remove("is-error");
}

showLoginBtn.addEventListener("click", () => setMode("login"));
showRegisterBtn.addEventListener("click", () => setMode("register"));

function showError(message) {
  statusEl.textContent = message;
  statusEl.classList.add("is-error");
}

function mapAuthError(status, data) {
  const message = String(data.error || "").trim();
  if (status === 401) {
    return "Wrong organization, username, or password. Check all three and try again.";
  }
  if (status === 404 || /organization/i.test(message)) {
    return "Organization not found. Check the spelling or create a new organization.";
  }
  if (status === 409) {
    return message || "That username is already taken in this organization.";
  }
  if (status === 400) {
    return message || "Please check the form and try again.";
  }
  return message || "Something went wrong. Please try again.";
}

async function submitAuth(url, body, button) {
  statusEl.textContent = "";
  statusEl.classList.remove("is-error");
  button.disabled = true;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showError(mapAuthError(response.status, data));
      return;
    }
    rememberRecentOrg(body.organizationName);
    window.location.replace("/");
  } catch (error) {
    showError("Network error. Check your connection and try again.");
  } finally {
    button.disabled = false;
  }
}

loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  submitAuth(
    "/api/auth/login",
    {
      organizationName: document.getElementById("loginOrg").value.trim(),
      username: document.getElementById("loginUser").value.trim(),
      password: document.getElementById("loginPassword").value,
    },
    loginForm.querySelector("button[type=submit]"),
  );
});

registerForm.addEventListener("submit", (event) => {
  event.preventDefault();
  submitAuth(
    "/api/auth/register",
    {
      organizationName: document.getElementById("registerOrg").value.trim(),
      username: document.getElementById("registerUser").value.trim(),
      password: document.getElementById("registerPassword").value,
    },
    registerForm.querySelector("button[type=submit]"),
  );
});

renderRecentOrgsDatalist(loadRecentOrgs());
