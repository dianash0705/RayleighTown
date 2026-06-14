(function () {
  window.PageStatus = {
    clear: function (el) {
      if (!el) {
        return;
      }
      el.hidden = false;
      el.textContent = "";
    },

    showLoading: function (el, message) {
      if (!el) {
        return;
      }
      el.hidden = false;
      el.textContent = message || "Loading...";
    },

    showSuccess: function (el, message) {
      if (!el) {
        return;
      }
      el.hidden = false;
      el.textContent = message || "";
    },

    showError: function (el, message) {
      if (!el) {
        return;
      }
      el.hidden = false;
      el.textContent = message || "Something went wrong.";
    },

    showEmpty: function (el, options) {
      if (!el) {
        return;
      }
      const opts = options || {};
      el.hidden = false;
      el.textContent = opts.message || opts.title || "Nothing here yet";
    },
  };
})();
