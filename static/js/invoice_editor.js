(function () {
  "use strict";

  var editor = document.querySelector("[data-invoice-editor]");
  if (!editor) return;
  var form = editor.querySelector("[data-invoice-form]");
  var lines = editor.querySelector("[data-invoice-lines]");
  var totalForms = form.querySelector('[name="lines-TOTAL_FORMS"]');
  var emptyTemplate = editor.querySelector("[data-empty-line]");
  var preview = editor.querySelector("[data-invoice-preview]");
  var previewStatus = editor.querySelector("[data-preview-status]");
  var previewTimer;
  var previewController;
  var currentStep = 1;

  function field(row, suffix) {
    return row.querySelector('[name$="-' + suffix + '"]');
  }

  function format(value) {
    return new Intl.NumberFormat("fa-IR", {maximumFractionDigits: 2}).format(value || 0);
  }

  function liveTotals() {
    var activeRows = [];
    var payload = {lines: []};
    lines.querySelectorAll("[data-invoice-line]").forEach(function (row) {
      var deletion = field(row, "DELETE");
      if (deletion && deletion.checked) return;
      activeRows.push(row);
      payload.lines.push({
        quantity: field(row, "quantity") && field(row, "quantity").value,
        unit_price: field(row, "unit_price") && field(row, "unit_price").value,
        discount_type: field(row, "discount_type") && field(row, "discount_type").value,
        discount_value: field(row, "discount_value") && field(row, "discount_value").value
      });
    });
    payload.invoice_discount_type = form.elements.invoice_discount_type.value;
    payload.invoice_discount_value = form.elements.invoice_discount_value.value;
    payload.tax_amount = form.elements.tax_amount.value;
    payload.shipping_amount = form.elements.shipping_amount.value;
    payload.adjustment_amount = form.elements.adjustment_amount.value;
    payload.paid_amount = form.elements.paid_amount.value;
    var result = window.SangaInvoiceCalculator.calculate(payload);
    activeRows.forEach(function (row, index) {
      var output = row.querySelector("[data-line-total]");
      if (output) output.textContent = format(result.lines[index].line_total);
    });
    editor.querySelector("[data-invoice-total]").textContent = format(Math.max(0, result.amount_due));
  }

  function renumber() {
    var visible = 0;
    lines.querySelectorAll("[data-invoice-line]").forEach(function (row) {
      var deletion = field(row, "DELETE");
      if (deletion && deletion.checked) return;
      visible += 1;
      var label = row.querySelector("[data-line-number]");
      if (label) label.textContent = "ردیف " + format(visible);
      var order = field(row, "ORDER");
      if (order) order.value = visible - 1;
    });
  }

  function initPicker(picker) {
    if (picker.dataset.ready === "true") return;
    picker.dataset.ready = "true";
    var row = picker.closest("[data-invoice-line]");
    var query = picker.querySelector("[data-product-query]");
    var results = picker.querySelector("[data-product-results]");
    var clear = picker.querySelector("[data-product-clear]");
    var hidden = field(row, "item");
    var timer;
    var controller;

    function hide() { results.hidden = true; results.replaceChildren(); }
    function load() {
      if (controller) controller.abort();
      controller = new AbortController();
      var url = new URL(picker.dataset.productUrl, window.location.origin);
      url.searchParams.set("q", query.value.trim());
      results.hidden = false;
      results.textContent = "در حال جست‌وجو…";
      fetch(url, {headers: {Accept: "application/json"}, signal: controller.signal})
        .then(function (response) { if (!response.ok) throw new Error(); return response.json(); })
        .then(function (payload) {
          results.replaceChildren();
          (payload.items || []).forEach(function (item) {
            var option = document.createElement("button");
            option.type = "button";
            option.className = "product-picker-option";
            option.textContent = item.label;
            option.addEventListener("click", function () {
              hidden.value = item.id;
              query.value = item.label;
              if (!field(row, "product_name").value.trim()) field(row, "product_name").value = item.name || "";
              if (!field(row, "stone_type").value.trim()) field(row, "stone_type").value = item.stone || "";
              hide();
              changed();
            });
            results.appendChild(option);
          });
          if (!results.children.length) results.textContent = "محصولی پیدا نشد.";
          results.hidden = false;
        })
        .catch(function (error) { if (error.name !== "AbortError") results.textContent = "جست‌وجو انجام نشد."; });
    }
    query.addEventListener("focus", load);
    query.addEventListener("input", function () {
      hidden.value = "";
      clearTimeout(timer);
      timer = setTimeout(load, 250);
    });
    clear.addEventListener("click", function () { hidden.value = ""; query.value = ""; hide(); changed(); });
  }

  function initRow(row) {
    row.querySelectorAll("[data-dynamic-product-picker]").forEach(initPicker);
  }

  function addLine() {
    var index = Number(totalForms.value);
    var wrapper = document.createElement("div");
    wrapper.innerHTML = emptyTemplate.innerHTML.replace(/__prefix__/g, String(index));
    var row = wrapper.firstElementChild;
    lines.appendChild(row);
    totalForms.value = index + 1;
    initRow(row);
    renumber();
    field(row, "product_name").focus();
    changed();
  }

  function removeLine(row) {
    var hasData = Array.from(row.querySelectorAll("input, select, textarea")).some(function (input) {
      return !input.name.endsWith("-DELETE") && !input.name.endsWith("-ORDER") && input.value;
    });
    if (hasData && !window.confirm("اطلاعات این ردیف حذف شود؟")) return;
    var deletion = field(row, "DELETE");
    if (deletion) deletion.checked = true;
    row.hidden = true;
    renumber();
    changed();
  }

  function showStep(step) {
    currentStep = Math.max(1, Math.min(3, step));
    editor.querySelectorAll("[data-step-target]").forEach(function (button) {
      button.classList.toggle("is-active", Number(button.dataset.stepTarget) === currentStep);
    });
    editor.querySelectorAll("[data-step]").forEach(function (section) {
      section.classList.toggle("is-current", Number(section.dataset.step) === currentStep);
    });
    if (window.matchMedia("(max-width: 899px)").matches) {
      editor.querySelector('[data-step="' + currentStep + '"]').scrollIntoView({behavior: "smooth", block: "start"});
    }
  }

  function requestPreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(function () {
      if (previewController) previewController.abort();
      previewController = new AbortController();
      previewStatus.textContent = "در حال به‌روزرسانی…";
      fetch(form.dataset.previewUrl, {
        method: "POST",
        body: new FormData(form),
        headers: {"X-Requested-With": "XMLHttpRequest"},
        signal: previewController.signal
      }).then(function (response) {
        return response.text().then(function (body) { return {ok: response.ok, body: body}; });
      }).then(function (result) {
        if (!result.ok) { previewStatus.textContent = result.body; return; }
        preview.srcdoc = result.body;
        previewStatus.textContent = "به‌روز است";
      }).catch(function (error) {
        if (error.name !== "AbortError") previewStatus.textContent = "پیش‌نمایش در دسترس نیست";
      });
    }, 450);
  }

  function changed() { liveTotals(); requestPreview(); }

  editor.querySelector("[data-line-add]").addEventListener("click", addLine);
  lines.addEventListener("click", function (event) {
    var row = event.target.closest("[data-invoice-line]");
    if (!row) return;
    if (event.target.closest("[data-line-remove]")) removeLine(row);
    var move = event.target.closest("[data-line-move]");
    if (move) {
      var sibling = move.dataset.lineMove === "up" ? row.previousElementSibling : row.nextElementSibling;
      if (sibling) {
        if (move.dataset.lineMove === "up") lines.insertBefore(row, sibling);
        else lines.insertBefore(sibling, row);
        renumber(); changed();
      }
    }
  });
  form.addEventListener("input", changed);
  form.addEventListener("change", changed);
  editor.querySelectorAll("[data-step-target]").forEach(function (button) {
    button.addEventListener("click", function () { showStep(Number(button.dataset.stepTarget)); });
  });
  editor.querySelectorAll("[data-step-next]").forEach(function (button) {
    button.addEventListener("click", function () { showStep(currentStep + 1); });
  });
  editor.querySelectorAll("[data-step-prev]").forEach(function (button) {
    button.addEventListener("click", function () { showStep(currentStep - 1); });
  });
  lines.querySelectorAll("[data-invoice-line]").forEach(initRow);
  renumber(); showStep(1); changed();
})();
