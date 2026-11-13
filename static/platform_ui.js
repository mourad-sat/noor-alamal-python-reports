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
      // Group labels disappear when no link in the group matches the query.
      root.querySelectorAll("[data-nav-group]").forEach(function (group) {
        var groupLinks = Array.prototype.slice.call(group.querySelectorAll("a"));
        group.hidden = !groupLinks.some(function (link) { return !link.hidden; });
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
  function initializeQuickSearchShortcut() {
    document.addEventListener("keydown", function (event) {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "k") return;
      var isMobile = window.matchMedia && window.matchMedia("(max-width: 980px)").matches;
      var mobile = document.getElementById("pv2-mobile-filter");
      var desktop = document.getElementById("pv2-dashboard-search");
      var fallback = document.getElementById("pv2-nav-filter");
      var input = isMobile ? mobile : (desktop || fallback);
      if (!input) return;
      if (isMobile) {
        var panel = input.closest("details");
        if (panel) panel.open = true;
      }
      event.preventDefault();
      input.focus();
    });
  }
  function initializeTimetablePrinting() {
    document.querySelectorAll("[data-print-timetable]").forEach(function (button) {
      button.addEventListener("click", function () { window.print(); });
    });
  }
  function initializeQuickAttendance() {
    document.querySelectorAll("[data-quick-attendance]").forEach(function (form) {
      var selectors = Array.prototype.slice.call(form.querySelectorAll("[data-quick-status]"));
      var preview = form.querySelector("[data-quick-preview]");
      var confirmation = form.querySelector("[data-quick-confirmation]");
      var save = form.querySelector("[data-quick-save]");
      var summary = form.querySelector("[data-quick-summary]");
      if (!preview || !confirmation || !save || !summary) return;
      function update() {
        var counts = {present: 0, absent: 0, late: 0, unmarked: 0};
        selectors.forEach(function (selector) {
          var status = selector.disabled ?
            form.querySelector('input[type="hidden"][name="' + selector.name + '"]').value :
            selector.value;
          if (Object.prototype.hasOwnProperty.call(counts, status)) counts[status]++;
        });
        summary.textContent = "حاضرون: " + counts.present +
          " · غائبون: " + counts.absent +
          " · متأخرون: " + counts.late +
          " · لم يُسجَّل: " + counts.unmarked;
        confirmation.hidden = true;
        save.disabled = true;
      }
      selectors.forEach(function (selector) {
        selector.addEventListener("change", update);
      });
      preview.addEventListener("click", function () {
        update();
        confirmation.hidden = false;
        save.disabled = false;
        confirmation.scrollIntoView({block: "nearest", behavior: "smooth"});
      });
      update();
    });
  }
  function initializeGradeLearnerPicker() {
    document.querySelectorAll("[data-grade-learner-picker]").forEach(function (picker) {
      var assessment = picker.querySelector("[data-grade-assessment]");
      var learner = picker.querySelector("[data-grade-learner]");
      var hint = picker.querySelector("[data-grade-learner-hint]");
      if (!assessment || !learner) return;
      function update() {
        var selected = assessment.options[assessment.selectedIndex];
        var track = selected ? selected.getAttribute("data-track-id") : "";
        var matches = 0;
        Array.prototype.forEach.call(learner.options, function (option) {
          if (!option.value) return;
          var eligible = !!track && option.getAttribute("data-track-id") === track;
          option.disabled = !eligible;
          option.hidden = !eligible;
          if (eligible) matches++;
        });
        // Never retain a learner from a different assessment's track.
        learner.value = "";
        learner.disabled = !matches;
        if (hint) hint.textContent = matches ?
          "المستفيدون المتاحون لهذا الاختبار: " + matches :
          "لا يوجد مستفيدون نشطون في مسلك هذا الاختبار.";
      }
      assessment.addEventListener("change", update);
      update();
    });
  }
  function onReady() {
    initializeQuickAttendance();
    initializeGradeLearnerPicker();
    initializeTimetablePrinting();
    initializeQuickSearchShortcut();
    document.querySelectorAll("[data-nav-filter]").forEach(initialize);
    document.querySelectorAll("[data-table-filter]").forEach(initializeTableFilter);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", onReady);
  } else {
    onReady();
  }
}());
