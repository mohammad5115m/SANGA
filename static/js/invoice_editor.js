(function () {
  "use strict";

  var editor = document.querySelector("[data-invoice-editor]");
  if (!editor) return;
  editor.classList.add("is-enhanced");
  var form = editor.querySelector("[data-invoice-form]");
  var lines = editor.querySelector("[data-invoice-lines]");
  var totalForms = form.querySelector('[name="lines-TOTAL_FORMS"]');
  var emptyTemplate = editor.querySelector("[data-empty-line]");
  var preview = editor.querySelector("[data-invoice-preview]");
  var previewStatus = editor.querySelector("[data-preview-status]");
  var previewPanel = editor.querySelector("[data-preview-panel]");
  var previewRefresh = editor.querySelector("[data-preview-refresh]");
  var previewExpand = editor.querySelector("[data-preview-expand]");
  var previewToggle = editor.querySelector("[data-preview-toggle]");
  var compactPreview = window.matchMedia("(max-width: 899px)");
  var previewTimer;
  var previewController;
  var customerName = form.elements.customer_name;
  var customerOptions = editor.querySelector("#invoice-customer-options");

  function setRegionActive(region, active) {
    if (!region) return;
    region.hidden = !active;
    region.setAttribute("aria-hidden", active ? "false" : "true");
    region.querySelectorAll("input, select, textarea, button").forEach(function (control) {
      control.disabled = !active;
    });
  }

  function selectedCounterpartyMode() {
    var selected = form.querySelector('[name="counterparty_mode"]:checked');
    return selected ? selected.value : "customer";
  }

  function syncLocalNewFields(mode) {
    var localNew = editor.querySelector("[data-local-new-fields]");
    var localSelect = form.elements.local_counterparty;
    var needsNewLocal = mode === "local" && localSelect && !localSelect.value;
    setRegionActive(localNew, needsNewLocal);
  }

  function syncCounterpartyMode() {
    var mode = selectedCounterpartyMode();
    editor.querySelectorAll("[data-counterparty-section]").forEach(function (section) {
      setRegionActive(section, section.dataset.counterpartySection === mode);
    });
    var partnerSettlement = editor.querySelector("[data-partner-settlement]");
    setRegionActive(partnerSettlement, mode !== "customer");
    setRegionActive(editor.querySelector("[data-customer-paid]"), mode === "customer");
    syncLocalNewFields(mode);
    form.querySelectorAll('[name="counterparty_mode"]').forEach(function (radio) {
      var controls = radio.value === "business" ? "invoice-business-fields invoice-partner-settlement" :
        radio.value === "local" ? "invoice-local-fields invoice-partner-settlement" : "";
      if (controls) radio.setAttribute("aria-controls", controls);
      else radio.removeAttribute("aria-controls");
    });
    syncChequeFields();
  }

  function syncChequeFields() {
    var chequeFields = editor.querySelector("[data-cheque-fields]");
    if (!chequeFields) return;
    var isPartner = selectedCounterpartyMode() !== "customer";
    var amount = Number(window.SangaInvoiceCalculator.normalizeNumber(
      (form.elements.cheque_amount && form.elements.cheque_amount.value) || 0
    ));
    var method = form.elements.settlement_method && form.elements.settlement_method.value;
    var needsCheque = amount > 0 || method === "cheque" || method === "mixed";
    setRegionActive(chequeFields, isPartner && needsCheque);
  }

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
    payload.paid_amount = selectedCounterpartyMode() === "customer"
      ? form.elements.paid_amount.value
      : Number(window.SangaInvoiceCalculator.normalizeNumber(form.elements.cash_amount.value || 0)) +
        Number(window.SangaInvoiceCalculator.normalizeNumber(form.elements.cheque_amount.value || 0));
    var result = window.SangaInvoiceCalculator.calculate(payload);
    if (!result.valid) {
      activeRows.forEach(function (row) {
        var invalidOutput = row.querySelector("[data-line-total]");
        if (invalidOutput) invalidOutput.textContent = "—";
      });
      editor.querySelector("[data-invoice-total]").textContent = "—";
      return;
    }
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

    var activeIndex = -1;
    function setExpanded(value) {
      query.setAttribute("aria-expanded", value ? "true" : "false");
    }
    function hide() {
      activeIndex = -1;
      results.hidden = true;
      results.replaceChildren();
      setExpanded(false);
    }
    function activate(index) {
      var options = results.querySelectorAll('[role="option"]');
      if (!options.length) return;
      activeIndex = Math.max(0, Math.min(options.length - 1, index));
      options.forEach(function (option, optionIndex) {
        option.setAttribute("aria-selected", optionIndex === activeIndex ? "true" : "false");
      });
      options[activeIndex].focus();
    }
    function load() {
      if (controller) controller.abort();
      controller = new AbortController();
      var url = new URL(picker.dataset.productUrl, window.location.origin);
      url.searchParams.set("q", query.value.trim());
      results.hidden = false;
      setExpanded(true);
      results.textContent = "در حال جست‌وجو…";
      fetch(url, {headers: {Accept: "application/json"}, signal: controller.signal})
        .then(function (response) { if (!response.ok) throw new Error(); return response.json(); })
        .then(function (payload) {
          results.replaceChildren();
          (payload.items || []).forEach(function (item) {
            var option = document.createElement("button");
            option.type = "button";
            option.className = "product-picker-option";
            option.setAttribute("role", "option");
            option.setAttribute("aria-selected", "false");
            option.textContent = item.label;
            option.addEventListener("click", function () {
              hidden.value = item.id;
              query.value = item.name || item.label;
              if (!field(row, "stone_type").value.trim()) field(row, "stone_type").value = item.stone || "";
              picker.classList.add("is-selected");
              hide();
              changed();
            });
            results.appendChild(option);
          });
          if (!results.children.length) results.textContent = "محصولی پیدا نشد.";
          results.hidden = false;
          setExpanded(true);
        })
        .catch(function (error) { if (error.name !== "AbortError") results.textContent = "جست‌وجو انجام نشد."; });
    }
    query.addEventListener("focus", load);
    query.addEventListener("input", function () {
      hidden.value = "";
      picker.classList.remove("is-selected");
      clearTimeout(timer);
      timer = setTimeout(load, 250);
    });
    query.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        activate(0);
      } else if (event.key === "Escape") {
        hide();
      }
    });
    results.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        activate(activeIndex + (event.key === "ArrowDown" ? 1 : -1));
      } else if (event.key === "Escape") {
        hide();
        query.focus();
      }
    });
    clear.addEventListener("click", function () {
      hidden.value = "";
      query.value = "";
      picker.classList.remove("is-selected");
      hide();
      query.focus();
      changed();
    });
    document.addEventListener("pointerdown", function (event) {
      if (!picker.contains(event.target)) hide();
    });
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

  function setPreviewLoading(loading) {
    if (!previewPanel) return;
    previewPanel.classList.toggle("is-loading", loading);
    previewPanel.setAttribute("aria-busy", loading ? "true" : "false");
  }

  function requestPreview(immediate) {
    clearTimeout(previewTimer);
    if (compactPreview.matches && !previewPanel.classList.contains("is-preview-open")) {
      previewStatus.textContent = "برای مشاهده، پیش‌نمایش را باز کنید";
      setPreviewLoading(false);
      return;
    }
    previewTimer = setTimeout(function () {
      if (previewController) previewController.abort();
      previewController = new AbortController();
      previewStatus.textContent = "در حال به‌روزرسانی…";
      setPreviewLoading(true);
      var formData = new FormData(form);
      var previewData = new URLSearchParams();
      formData.forEach(function (value, key) {
        if (typeof value === "string") previewData.append(key, value);
      });
      fetch(form.dataset.previewUrl, {
        method: "POST",
        body: previewData,
        headers: {"X-Requested-With": "XMLHttpRequest"},
        signal: previewController.signal
      }).then(function (response) {
        return response.text().then(function (body) { return {ok: response.ok, body: body}; });
      }).then(function (result) {
        if (!result.ok) {
          previewStatus.textContent = result.body;
          previewStatus.classList.add("is-error");
          setPreviewLoading(false);
          return;
        }
        preview.srcdoc = result.body;
        previewStatus.textContent = "به‌روز است";
        previewStatus.classList.remove("is-error");
        setPreviewLoading(false);
      }).catch(function (error) {
        if (error.name !== "AbortError") {
          previewStatus.textContent = "پیش‌نمایش در دسترس نیست";
          previewStatus.classList.add("is-error");
          setPreviewLoading(false);
        }
      });
    }, immediate ? 0 : 350);
  }

  function changed() { liveTotals(); requestPreview(); }

  if (customerName && customerOptions) {
    customerName.addEventListener("change", function () {
      var match = Array.from(customerOptions.options).find(function (option) {
        return option.value === customerName.value;
      });
      if (!match) return;
      if (form.elements.customer_phone && !form.elements.customer_phone.value) {
        form.elements.customer_phone.value = match.dataset.phone || "";
      }
      if (form.elements.buyer_address && !form.elements.buyer_address.value) {
        form.elements.buyer_address.value = match.dataset.address || "";
      }
      changed();
    });
  }

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
  form.addEventListener("change", function () { syncCounterpartyMode(); syncChequeFields(); changed(); });
  if (previewRefresh) {
    previewRefresh.addEventListener("click", function () {
      previewPanel.classList.add("is-preview-open");
      if (previewToggle) {
        previewToggle.setAttribute("aria-expanded", "true");
        previewToggle.textContent = "بستن پیش‌نمایش";
      }
      requestPreview(true);
    });
  }
  if (previewToggle) {
    previewToggle.addEventListener("click", function () {
      var open = previewPanel.classList.toggle("is-preview-open");
      previewToggle.setAttribute("aria-expanded", open ? "true" : "false");
      previewToggle.textContent = open ? "بستن پیش‌نمایش" : "نمایش پیش‌نمایش";
      if (open) requestPreview(true);
    });
  }
  if (previewExpand) {
    previewExpand.addEventListener("click", function () {
      var expanded = editor.classList.toggle("is-preview-expanded");
      if (expanded) previewPanel.classList.add("is-preview-open");
      previewExpand.setAttribute("aria-expanded", expanded ? "true" : "false");
      previewExpand.textContent = expanded ? "بازگشت به فرم" : "نمایش بزرگ";
      if (expanded) previewPanel.scrollIntoView({behavior: "smooth", block: "start"});
    });
  }
  lines.querySelectorAll("[data-invoice-line]").forEach(initRow);
  syncCounterpartyMode();
  renumber();
  changed();
  var errorSummary = editor.querySelector("[data-error-summary]");
  if (errorSummary) errorSummary.focus();
  window.addEventListener("pageshow", syncCounterpartyMode);
  var handlePreviewViewportChange = function () {
    if (!compactPreview.matches) requestPreview(true);
  };
  if (compactPreview.addEventListener) compactPreview.addEventListener("change", handlePreviewViewportChange);
  else compactPreview.addListener(handlePreviewViewportChange);
})();
