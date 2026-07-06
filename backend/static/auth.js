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

  function currentPageKey() {
    const explicit = (document.body.dataset.page || "").trim();
    if (explicit) {
      return explicit;
    }
    const path = window.location.pathname.replace(/\/+$/, "") || "/";
    if (path === "/" || path === "/dashboard") {
      return "dashboard";
    }
    if (path === "/alerts") {
      return "alerts";
    }
    if (path === "/entities") {
      return "entities";
    }
    if (path === "/whitelist") {
      return "whitelist";
    }
    if (path === "/admin") {
      return "admin";
    }
    return "";
  }

  function injectMoreMenu(nav) {
    const page = currentPageKey();
    const secondaryActive = page === "entities" || page === "whitelist";

    const wrap = document.createElement("div");
    wrap.className = "nav-more" + (secondaryActive ? " is-active" : "");

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "nav-link nav-more-toggle" + (secondaryActive ? " is-active" : "");
    toggle.setAttribute("aria-haspopup", "true");
    toggle.setAttribute("aria-expanded", "false");
    toggle.innerHTML = "More <span class='nav-more-caret' aria-hidden='true'>▾</span>";

    const menu = document.createElement("div");
    menu.className = "nav-more-menu";
    menu.hidden = true;
    menu.setAttribute("role", "menu");

    const items = [
      { href: "/entities", label: "Entities", key: "entities" },
      { href: "/whitelist", label: "Whitelist", key: "whitelist" },
    ];
    for (const item of items) {
      const link = document.createElement("a");
      link.className = "nav-more-item" + (page === item.key ? " is-active" : "");
      link.href = item.href;
      link.textContent = item.label;
      link.setAttribute("role", "menuitem");
      if (page === item.key) {
        link.setAttribute("aria-current", "page");
      }
      menu.appendChild(link);
    }

    function setOpen(open) {
      menu.hidden = !open;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      wrap.classList.toggle("is-open", open);
    }

    toggle.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      setOpen(menu.hidden);
    });

    document.addEventListener("pointerdown", function (event) {
      if (!wrap.contains(event.target)) {
        setOpen(false);
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    });

    wrap.appendChild(toggle);
    wrap.appendChild(menu);
    nav.appendChild(wrap);
  }

  function injectNav(account) {
    const nav = document.querySelector(".page-nav");
    if (!nav) {
      return;
    }

    injectMoreMenu(nav);

    const onAdminPage = currentPageKey() === "admin";

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
