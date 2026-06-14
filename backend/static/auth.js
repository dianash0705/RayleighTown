// Shared auth guard for the logged-in pages (dashboard, alerts, entities, admin).

(function () {
  const originalFetch = window.fetch.bind(window);

  window.fetch = async function (...args) {
    const response = await originalFetch(...args);
    if (response.status === 401) {
      window.location.replace("/login");
    }
    return response;
  };

  function timeOfDayGreeting(date) {
    const hour = date.getHours();
    if (hour >= 5 && hour < 12) {
      return "Good morning";
    }
    if (hour >= 12 && hour < 18) {
      return "Good afternoon";
    }
    if (hour >= 18 && hour < 22) {
      return "Good evening";
    }
    return "Good night";
  }

  function injectGreeting(account) {
    const header = document.querySelector(".header");
    if (!header) {
      return;
    }
    const greeting = document.createElement("p");
    greeting.className = "page-greeting";
    greeting.append(timeOfDayGreeting(new Date()) + ", ");
    const nameEl = document.createElement("strong");
    nameEl.textContent = account.name;
    greeting.appendChild(nameEl);
    header.appendChild(greeting);
  }

  function injectOrgBadge(account) {
    const titleBlock = document.querySelector(".title-block");
    if (!titleBlock || !account.organizationName) {
      return;
    }
    const badge = document.createElement("p");
    badge.className = "org-badge";
    badge.textContent = account.organizationName;
    const subtitle = titleBlock.querySelector(".subtitle");
    if (subtitle) {
      titleBlock.insertBefore(badge, subtitle);
    } else {
      titleBlock.appendChild(badge);
    }
  }

  function injectNav(account) {
    const nav = document.querySelector(".page-nav");
    if (!nav) {
      return;
    }

    const onAdminPage = document.body.dataset.page === "admin";

    if (account.isAdmin && !onAdminPage) {
      const adminLink = document.createElement("a");
      adminLink.className = "nav-link";
      adminLink.href = "/admin";
      adminLink.textContent = "Admin";
      nav.appendChild(adminLink);
    }

    const logoutButton = document.createElement("button");
    logoutButton.type = "button";
    logoutButton.className = "secondary-button nav-logout";
    logoutButton.textContent = "Log out";
    logoutButton.addEventListener("click", async function () {
      try {
        await originalFetch("/api/auth/logout", { method: "POST" });
      } finally {
        window.location.replace("/login");
      }
    });
    nav.appendChild(logoutButton);
  }

  async function init() {
    let account = null;
    try {
      const response = await originalFetch("/api/auth/me");
      if (!response.ok) {
        window.location.replace("/login");
        return;
      }
      const data = await response.json();
      account = data.account;
    } catch (error) {
      window.location.replace("/login");
      return;
    }

    window.CURRENT_ACCOUNT = account;

    if (document.body.dataset.requireAdmin === "true" && !account.isAdmin) {
      window.location.replace("/");
      return;
    }

    injectGreeting(account);
    injectOrgBadge(account);
    injectNav(account);
    document.dispatchEvent(new CustomEvent("auth:ready", { detail: account }));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
