(function () {
  "use strict";
  if (window.SANGA_CALENDAR_READY) return;
  window.SANGA_CALENDAR_READY = true;
  var months = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"];
  function digits(value) {
    return String(value).replace(/[۰-۹]/g, function (d) { return String("۰۱۲۳۴۵۶۷۸۹".indexOf(d)); })
      .replace(/[٠-٩]/g, function (d) { return String("٠١٢٣٤٥٦٧٨٩".indexOf(d)); });
  }
  function node(tag, text, className) {
    var el = document.createElement(tag);
    if (text) el.textContent = text;
    if (className) el.className = className;
    return el;
  }
  function button(text, label) {
    var el = node("button", text);
    el.type = "button";
    if (label) el.setAttribute("aria-label", label);
    return el;
  }
  function init() {
    document.querySelectorAll("[data-calendar-open]").forEach(function (el) {
      el.hidden = false;
      el.disabled = el.closest("[data-jalali-control]").querySelector("[data-jalali-date]").disabled;
    });
  }
  function openCalendar(trigger) {
    var control = trigger.closest("[data-jalali-control]");
    var input = control.querySelector("[data-jalali-date]");
    var canonical = control.querySelector('input[type="hidden"]');
    var parts = digits(input.value).split("/");
    var year = Number(parts[0]) || Number(control.dataset.year);
    var month = Number(parts[1]) || Number(control.dataset.month);
    var dialog = node("dialog", "", "sanga-calendar");
    dialog.dir = "rtl";
    dialog.setAttribute("aria-labelledby", "sanga-calendar-title");
    var header = node("div", "", "calendar-header");
    var title = node("h2", "انتخاب تاریخ شمسی");
    title.id = "sanga-calendar-title";
    var close = button("بستن", "بستن تقویم");
    header.append(title, close);
    var nav = node("div", "", "calendar-navigation");
    var previous = button("‹", "ماه قبل");
    var next = button("›", "ماه بعد");
    var monthLabel = node("label", "ماه");
    var monthSelect = node("select");
    months.forEach(function (name, index) {
      var option = node("option", name);
      option.value = String(index + 1);
      monthSelect.append(option);
    });
    monthLabel.append(monthSelect);
    var yearLabel = node("label", "سال");
    var yearInput = node("input");
    yearInput.type = "text";
    yearInput.inputMode = "numeric";
    yearInput.dir = "ltr";
    yearInput.maxLength = 4;
    yearLabel.append(yearInput);
    nav.append(previous, monthLabel, yearLabel, next);
    var status = node("p", "", "calendar-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    var grid = node("div", "", "calendar-grid");
    grid.setAttribute("role", "group");
    grid.setAttribute("aria-label", "روزهای ماه؛ کلیدهای جهت برای جابه‌جایی");
    var today = button("امروز");
    var retry = button("تلاش دوباره");
    retry.hidden = true;
    dialog.append(header, nav, status, grid, retry, today);
    document.body.append(dialog);
    var controller;
    var selected = input.value;
    function moveMonth(delta, focusDay) {
      month += delta;
      if (month < 1) { month = 12; year -= 1; }
      if (month > 12) { month = 1; year += 1; }
      load(false, focusDay);
    }
    function choose(day) {
      input.value = day.value;
      canonical.value = day.iso;
      control.dataset.year = String(year);
      control.dataset.month = String(month);
      input.dispatchEvent(new Event("input", {bubbles: true}));
      input.dispatchEvent(new Event("change", {bubbles: true}));
      dialog.close();
    }
    function focusAt(index) {
      var choices = Array.from(grid.querySelectorAll("button"));
      if (index < 0) { moveMonth(-1, -1); return; }
      if (index >= choices.length) { moveMonth(1, 0); return; }
      choices.forEach(function (choice, i) { choice.tabIndex = i === index ? 0 : -1; });
      choices[index].focus();
    }
    async function load(current, focusDay) {
      if (controller) controller.abort();
      controller = new AbortController();
      var request = controller;
      grid.replaceChildren();
      grid.setAttribute("aria-busy", "true");
      status.textContent = "در حال بارگذاری تقویم…";
      retry.hidden = true;
      try {
        var url = "/calendar/month/" + (current ? "" : "?year=" + year + "&month=" + month);
        var response = await fetch(url, {signal: request.signal, headers: {Accept: "application/json"}});
        if (!response.ok) throw new Error("calendar");
        var data = await response.json();
        if (request !== controller || !dialog.open) return;
        year = data.year; month = data.month;
        if (focusDay === "today") {
          var currentDay = data.days.find(function (day) { return day.iso === data.today; });
          if (currentDay) { choose(currentDay); return; }
        }
        yearInput.value = String(year).replace(/[0-9]/g, function (d) { return "۰۱۲۳۴۵۶۷۸۹"[Number(d)]; });
        monthSelect.value = String(month);
        status.textContent = data.label;
        data.weekdays.forEach(function (name) { grid.append(node("span", name, "calendar-weekday")); });
        for (var i = 0; i < data.offset; i++) grid.append(node("span"));
        var active = data.days.findIndex(function (day) { return day.value === selected; });
        if (active < 0) active = data.days.findIndex(function (day) { return day.iso === data.today; });
        if (active < 0) active = 0;
        data.days.forEach(function (day, index) {
          var choice = button(day.label, day.name);
          choice.tabIndex = index === active ? 0 : -1;
          choice.setAttribute("aria-pressed", day.value === selected ? "true" : "false");
          if (day.iso === data.today) choice.setAttribute("aria-current", "date");
          choice.addEventListener("click", function () { choose(day); });
          choice.addEventListener("keydown", function (event) {
            var offset = {ArrowLeft: 1, ArrowRight: -1, ArrowDown: 7, ArrowUp: -7}[event.key];
            if (offset !== undefined) { event.preventDefault(); focusAt(index + offset); }
            if (event.key === "Home") { event.preventDefault(); focusAt(0); }
            if (event.key === "End") { event.preventDefault(); focusAt(data.days.length - 1); }
            if (event.key === "PageUp" || event.key === "PageDown") {
              event.preventDefault(); moveMonth(event.key === "PageUp" ? -1 : 1, 0);
            }
          });
          grid.append(choice);
        });
        if (focusDay !== undefined) focusAt(focusDay === "selected" ? active : focusDay === -1 ? data.days.length - 1 : focusDay);
      } catch (error) {
        if (error.name !== "AbortError" && dialog.open) {
          status.textContent = "تقویم در دسترس نیست؛ دوباره تلاش کنید یا تاریخ را دستی وارد کنید.";
          retry.hidden = false;
        }
      } finally {
        if (request === controller) grid.setAttribute("aria-busy", "false");
      }
    }
    previous.addEventListener("click", function () { moveMonth(-1); });
    next.addEventListener("click", function () { moveMonth(1); });
    monthSelect.addEventListener("change", function () { month = Number(monthSelect.value); load(); });
    yearInput.addEventListener("change", function () { year = Number(digits(yearInput.value)); load(); });
    today.addEventListener("click", function () { load(true, "today"); });
    retry.addEventListener("click", function () { load(); });
    close.addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener("close", function () {
      if (controller) controller.abort();
      dialog.remove();
      if (trigger.isConnected) trigger.focus();
    }, {once: true});
    dialog.showModal();
    load(false, "selected");
  }
  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-calendar-open]");
    if (trigger) openCalendar(trigger);
  });
  document.addEventListener("htmx:load", init);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
