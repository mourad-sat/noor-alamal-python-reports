/* Dashboard-only appearance preference; never processes navigation or learner data. */
(function () {
  "use strict";
  function initDashboardTheme() {
    var root = document.querySelector(".pv2-dashboard");
    var toggle = document.querySelector("[data-dashboard-theme-toggle]");
    if (!root || !toggle) return;
    var preference = null;
    try { preference = window.localStorage.getItem("noor-dashboard-appearance"); }
    catch (ignore) { /* Restricted storage must not break navigation. */ }
    function apply(appearance) {
      root.setAttribute("data-dashboard-appearance", appearance);
      toggle.textContent = appearance === "dark" ? "☾" : "☀";
      toggle.setAttribute("aria-pressed", appearance === "dark" ? "true" : "false");
      toggle.setAttribute("title", appearance === "dark" ? "تفعيل الوضع الفاتح" : "تفعيل الوضع الداكن");
    }
    apply(preference === "light" ? "light" : "dark");
    toggle.addEventListener("click", function () {
      var next = root.getAttribute("data-dashboard-appearance") === "dark" ? "light" : "dark";
      apply(next);
      try { window.localStorage.setItem("noor-dashboard-appearance", next); }
      catch (ignore) { /* Preference remains active for this page. */ }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initDashboardTheme);
  } else {
    initDashboardTheme();
  }
}());
