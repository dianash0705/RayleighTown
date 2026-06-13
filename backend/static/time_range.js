(function () {
  const PRESET_LABELS = {
    last_24h: "Last 24 hours",
    last_week: "Last week",
    all: "All time",
    custom: "Custom range",
  };

  function toIsoFromLocalInput(value) {
    if (!value) {
      return null;
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return null;
    }
    return date.toISOString();
  }

  function initTimeRangeControls(options) {
    const opts = options || {};
    const root = document.getElementById(opts.rootId || "timeRange");
    const toggle = document.getElementById(opts.toggleId || "timeRangeToggle");
    const labelEl = document.getElementById(opts.labelId || "timeRangeLabel");
    const popover = document.getElementById(opts.popoverId || "timeRangePopover");
    const customWrap = document.getElementById(opts.customWrapId || "timeRangeCustom");
    const timeFromInput = document.getElementById(opts.fromId || "timeFrom");
    const timeToInput = document.getElementById(opts.toId || "timeTo");
    const applyButton = document.getElementById(opts.applyButtonId || "applyTimeRange");
    const onChange = typeof opts.onChange === "function" ? opts.onChange : function () {};

    if (!root || !toggle || !popover) {
      return {
        buildQueryParams: function () {
          return new URLSearchParams();
        },
        getLabel: function () {
          return PRESET_LABELS.last_week;
        },
      };
    }

    const presetButtons = Array.prototype.slice.call(
      popover.querySelectorAll("[data-preset]")
    );
    let currentPreset = opts.defaultPreset || "last_week";

    function syncUi() {
      if (labelEl) {
        labelEl.textContent = PRESET_LABELS[currentPreset] || PRESET_LABELS.last_week;
      }
      for (const button of presetButtons) {
        button.classList.toggle("is-active", button.dataset.preset === currentPreset);
      }
      if (customWrap) {
        customWrap.hidden = currentPreset !== "custom";
      }
    }

    function openPopover() {
      popover.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
    }

    function closePopover() {
      popover.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
    }

    function isOpen() {
      return !popover.hidden;
    }

    function buildQueryParams() {
      const params = new URLSearchParams();
      params.set("timePreset", currentPreset);
      if (currentPreset === "custom") {
        const timeFrom = toIsoFromLocalInput(timeFromInput && timeFromInput.value);
        const timeTo = toIsoFromLocalInput(timeToInput && timeToInput.value);
        if (timeFrom) {
          params.set("timeFrom", timeFrom);
        }
        if (timeTo) {
          params.set("timeTo", timeTo);
        }
      }
      return params;
    }

    function getLabel() {
      return PRESET_LABELS[currentPreset] || PRESET_LABELS.last_week;
    }

    toggle.addEventListener("click", function (event) {
      event.stopPropagation();
      if (isOpen()) {
        closePopover();
      } else {
        openPopover();
      }
    });

    for (const button of presetButtons) {
      button.addEventListener("click", function () {
        currentPreset = button.dataset.preset;
        syncUi();
        if (currentPreset === "custom") {
          if (timeFromInput) {
            timeFromInput.focus();
          }
          return;
        }
        closePopover();
        onChange();
      });
    }

    if (applyButton) {
      applyButton.addEventListener("click", function () {
        closePopover();
        onChange();
      });
    }

    document.addEventListener("click", function (event) {
      if (isOpen() && !root.contains(event.target)) {
        closePopover();
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && isOpen()) {
        closePopover();
        toggle.focus();
      }
    });

    syncUi();

    return {
      buildQueryParams: buildQueryParams,
      getLabel: getLabel,
    };
  }

  window.TimeRangeControls = {
    init: initTimeRangeControls,
    presetLabels: PRESET_LABELS,
  };
})();
