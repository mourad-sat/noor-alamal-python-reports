/* The filter form only changes presentation; all authorization stays server-side. */
(function () {
  "use strict";
  function initializePrintFilters() {
    var form = document.querySelector(".print-picker");
    if (!form) return;
    var mode = form.querySelector('select[name="mode"]');
    var center = form.querySelector('select[name="center_id"]');
    if (!mode || !center) return;
    function update(reset) {
      var choice = mode.value;
      form.querySelectorAll("[data-print-filter]").forEach(function (label) {
        var name = label.getAttribute("data-print-filter");
        label.hidden = !(name === "center" && (choice === "center" || choice === "staff") ||
                         name === "staff" && choice === "staff" ||
                         name === "track" && choice === "track");
      });
      if (reset && choice !== "center") center.value = "0";
    }
    mode.addEventListener("change", function () { update(true); });
    update(false);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializePrintFilters);
  } else {
    initializePrintFilters();
  }
}());
