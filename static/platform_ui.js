/* Lightweight navigation helper; no network requests or personal data storage. */
(function () {
  "use strict";
  function normalize(text) {
    return String(text || "").toLocaleLowerCase().trim().normalize("NFKC")
      .replace(/[\u064b-\u065f\u0670]/g, "")
      .replace(/[أإآ]/g, "ا").replace(/ى/g, "ي");
  }
  function initialize(input) {
    var id = input.getAttribute("data-nav-filter");
    var root = document.getElementById(id);
    if (!root) return;
    var empty = document.querySelector('[data-empty-for="' + id + '"]');
    var links = Array.prototype.slice.call(root.querySelectorAll("a"));
    function update() {
      var term = normalize(input.value);
      var matches = 0;
      links.forEach(function (link) {
        var visible = !term || normalize(link.textContent).includes(term);
        link.hidden = !visible;
        if (visible) matches++;
      });
      if (empty) empty.hidden = matches !== 0;
    }
    input.addEventListener("input", update);
    input.addEventListener("search", update);
    update();
  }
  function initializeTableFilter(input) {
    var id = input.getAttribute("data-table-filter");
    var wrapper = document.getElementById(id);
    if (!wrapper) return;
    var rows = Array.prototype.slice.call(wrapper.querySelectorAll("tbody tr"));
    var counter = document.querySelector('[data-table-counter="' + id + '"]');
    function update() {
      var term = normalize(input.value);
      var count = 0;
      rows.forEach(function (row) {
        var visible = !term || normalize(row.textContent).includes(term);
        row.hidden = !visible;
        if (visible) count++;
      });
      if (counter) counter.textContent = "المعروض: " + count + " من " + rows.length;
    }
    input.addEventListener("input", update);
    input.addEventListener("search", update);
    update();
  }
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
  function onReady() {
    initDashboardTheme();
    document.querySelectorAll("[data-nav-filter]").forEach(initialize);
    document.querySelectorAll("[data-table-filter]").forEach(initializeTableFilter);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", onReady);
  } else {
    onReady();
  }
}());
