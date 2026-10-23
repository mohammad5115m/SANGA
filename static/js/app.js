// Everything Sang Esf's own pages need at runtime, in one file rather than inline.
// Keeping behavior here allows the Content-Security-Policy to remain
// `script-src 'self'` without an unsafe-inline exception.

(function () {
  "use strict";

  // HTMX boosted navigation can encounter this script again after replacing
  // the body. Document-level listeners survive that swap, so install them once.
  if (window.SANGA_UI_READY) return;
  window.SANGA_UI_READY = true;

  if ("serviceWorker" in navigator) {
    var controllerAtLoad = navigator.serviceWorker.controller;
    var reloadingForWorkerUpdate = false;

    navigator.serviceWorker.addEventListener("controllerchange", function () {
      // An existing PWA tab may initially be controlled by an older worker.
      // Reload once after its replacement claims the page so all requests use
      // the new caching rules. A first-time install does not need a reload.
      if (!controllerAtLoad || reloadingForWorkerUpdate) return;
      reloadingForWorkerUpdate = true;
      window.location.reload();
    });

    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", {updateViaCache: "none"})
        .then(function (registration) { return registration.update(); })
        .catch(function () {});
    });
  }

  function elementsWithin(root, selector) {
    var elements = Array.from(root.querySelectorAll(selector));
    if (root.matches && root.matches(selector)) elements.unshift(root);
    return elements;
  }

  function announce(message) {
    var status = document.getElementById("async-status");
    if (!status) return;
    status.textContent = "";
    window.setTimeout(function () { status.textContent = message; }, 30);
  }

  function dismiss(el) {
    el.classList.add("toast-leaving");
    window.setTimeout(function () { el.remove(); }, 250);
  }

  function initToasts(root) {
    elementsWithin(root, ".toast:not([data-ui-ready])").forEach(function (toast) {
      toast.dataset.uiReady = "true";
      var persistent = toast.classList.contains("toast-error") || toast.classList.contains("toast-warning");
      var timer = persistent ? null : window.setTimeout(function () { dismiss(toast); }, 8000);
      toast.addEventListener("focusin", function () { window.clearTimeout(timer); });
      toast.addEventListener("focusout", function () {
        if (!persistent) timer = window.setTimeout(function () { dismiss(toast); }, 8000);
      });

      toast.addEventListener("mouseenter", function () { window.clearTimeout(timer); });
      toast.addEventListener("mouseleave", function () {
        if (!persistent) timer = window.setTimeout(function () { dismiss(toast); }, 8000);
      });

      var close = toast.querySelector(".toast-close");
      if (close) close.addEventListener("click", function () { dismiss(toast); });
    });
  }

  function initGalleries(root) {
    elementsWithin(root, "[data-gallery]:not([data-ui-ready])").forEach(function (gallery) {
      gallery.dataset.uiReady = "true";
      var slides = Array.from(gallery.querySelectorAll("[data-gallery-slide]"));
      var thumbs = Array.from(gallery.querySelectorAll("[data-gallery-thumb]"));
      if (!slides.length) return;

      function activate(mediaId) {
        slides.forEach(function (slide) { slide.hidden = slide.dataset.mediaId !== mediaId; });
        thumbs.forEach(function (thumb) {
          thumb.setAttribute("aria-pressed", thumb.dataset.mediaId === mediaId ? "true" : "false");
        });
      }

      var initial = slides.find(function (slide) { return slide.dataset.primary === "true"; }) || slides[0];
      activate(initial.dataset.mediaId);
      thumbs.forEach(function (thumb) {
        thumb.addEventListener("click", function () { activate(thumb.dataset.mediaId); });
      });
    });
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var input = document.createElement("textarea");
      input.value = text;
      input.setAttribute("readonly", "");
      input.className = "visually-hidden";
      document.body.appendChild(input);
      input.select();
      try {
        if (!document.execCommand("copy")) throw new Error("copy failed");
        resolve();
      } catch (error) {
        reject(error);
      } finally {
        input.remove();
      }
    });
  }

  function initShareActions(root) {
    elementsWithin(root, "[data-share-actions]:not([data-ui-ready])").forEach(function (actions) {
      actions.dataset.uiReady = "true";
      actions.setAttribute("aria-live", "polite");
      var copy = actions.querySelector("[data-copy-link]");
      var nativeShare = actions.querySelector("[data-native-share]");

      if (copy) {
        copy.addEventListener("click", function () {
          var label = copy.querySelector("[data-share-label]");
          var original = label ? label.textContent : copy.textContent;
          copyText(actions.dataset.shareUrl).then(function () {
            if (label) label.textContent = "لینک کپی شد";
            else copy.textContent = "لینک کپی شد";
            announce("لینک کپی شد.");
            window.setTimeout(function () {
              if (label) label.textContent = original;
              else copy.textContent = original;
            }, 2000);
          }).catch(function () {
            if (label) label.textContent = "کپی انجام نشد";
            else copy.textContent = "کپی انجام نشد";
            announce("کپی لینک انجام نشد.");
            window.setTimeout(function () {
              if (label) label.textContent = original;
              else copy.textContent = original;
            }, 2000);
          });
        });
      }

      if (nativeShare && typeof navigator.share === "function") {
        nativeShare.hidden = false;
        nativeShare.addEventListener("click", function () {
          navigator.share({title: actions.dataset.shareTitle, url: actions.dataset.shareUrl}).catch(function () {});
        });
      }
    });
  }

  function initMediaOrder(root) {
    elementsWithin(root, "[data-media-order]:not([data-ui-ready])").forEach(function (grid) {
      grid.dataset.uiReady = "true";
      grid.addEventListener("click", function (event) {
        var button = event.target.closest("[data-media-move]");
        if (!button) return;
        var tile = button.closest("[data-media-tile]");
        var sibling = button.dataset.mediaMove === "up" ? tile.previousElementSibling : tile.nextElementSibling;
        if (!sibling) return;
        if (button.dataset.mediaMove === "up") grid.insertBefore(tile, sibling);
        else grid.insertBefore(sibling, tile);
      });
    });
  }

  function initPriceTiers(root) {
    elementsWithin(root, "[data-price-tier]:not([data-ui-ready])").forEach(function (tier) {
      tier.dataset.uiReady = "true";
      var toggle = tier.querySelector('input[name$="-is_special"]');
      var fields = tier.querySelector("[data-special-fields]");
      if (!toggle || !fields) return;
      function sync() {
        fields.hidden = !toggle.checked;
        fields.querySelectorAll("input").forEach(function (input) {
          input.disabled = !toggle.checked;
          input.required = toggle.checked;
        });
      }
      toggle.addEventListener("change", sync);
      sync();
    });
  }

  function initMediaUpload(root) {
    elementsWithin(root, "[data-media-upload]:not([data-ui-ready])").forEach(function (form) {
      form.dataset.uiReady = "true";
      var input = form.querySelector('input[type="file"]');
      var preview = form.querySelector("[data-media-preview]");
      var status = form.querySelector("[data-media-upload-status]");
      if (!input || !preview || !status) return;
      var objectUrls = [];
      var extensions = /\.(jpe?g|png|webp|gif|mp4|mov|webm)$/i;
      function renderSelection() {
        objectUrls.forEach(function (url) { URL.revokeObjectURL(url); });
        objectUrls = [];
        preview.replaceChildren();
        var files = Array.from(input.files || []);
        preview.hidden = files.length === 0;
        var error = files.length > 10 ? "در هر بار حداکثر ۱۰ فایل انتخاب کنید." : "";
        files.forEach(function (file) {
          var video = /\.(mp4|mov|webm)$/i.test(file.name);
          var limit = video ? 60 * 1024 * 1024 : 10 * 1024 * 1024;
          var problem = !extensions.test(file.name) ? "قالب پشتیبانی نمی‌شود" : file.size > limit ? "حجم بیش از حد مجاز" : "";
          if (problem && !error) error = "فایل «" + file.name + "»: " + problem + ".";
          var row = document.createElement("div");
          row.className = "media-upload-file" + (problem ? " is-invalid" : "");
          if (!video && !problem) {
            var photo = document.createElement("img");
            photo.alt = "";
            photo.src = URL.createObjectURL(file);
            objectUrls.push(photo.src);
            row.appendChild(photo);
          }
          var name = document.createElement("span");
          name.textContent = file.name;
          row.appendChild(name);
          var size = document.createElement("small");
          size.textContent = problem || (file.size / (1024 * 1024)).toFixed(1) + " مگابایت";
          row.appendChild(size);
          preview.appendChild(row);
        });
        input.setCustomValidity(error);
        status.textContent = error || (files.length ? files.length + " فایل آمادهٔ بارگذاری است." : "");
      }
      input.addEventListener("change", renderSelection);
      form.addEventListener("submit", function () {
        if (input.checkValidity()) status.textContent = "در حال بارگذاری؛ تا پایان انتقال صفحه را نبندید…";
      });
    });
  }

  function initProductPickers(root) {
    elementsWithin(root, "[data-product-picker]:not([data-dynamic-product-picker]):not([data-ui-ready])").forEach(function (picker, pickerIndex) {
      picker.dataset.uiReady = "true";
      var query = picker.querySelector("[data-product-query]");
      var itemId = picker.querySelector('input[type="hidden"]');
      var results = picker.querySelector("[data-product-results]");
      var clear = picker.querySelector("[data-product-clear]");
      var form = picker.closest("form");
      var timer;
      var controller;
      var activeIndex = -1;
      if (!query || !itemId || !results || !form) return;

      if (!results.id) results.id = "product-results-" + Date.now() + "-" + pickerIndex;
      query.setAttribute("role", "combobox");
      query.setAttribute("aria-autocomplete", "list");
      query.setAttribute("aria-controls", results.id);
      query.setAttribute("aria-expanded", "false");
      results.setAttribute("role", "listbox");

      function options() {
        return Array.from(results.querySelectorAll("[role='option']"));
      }

      function setActive(index) {
        var choices = options();
        if (!choices.length) {
          activeIndex = -1;
          query.removeAttribute("aria-activedescendant");
          return;
        }
        activeIndex = Math.max(0, Math.min(index, choices.length - 1));
        choices.forEach(function (option, optionIndex) {
          var isActive = optionIndex === activeIndex;
          option.classList.toggle("is-active", isActive);
          option.setAttribute("aria-selected", isActive ? "true" : "false");
        });
        query.setAttribute("aria-activedescendant", choices[activeIndex].id);
        choices[activeIndex].scrollIntoView({block: "nearest"});
      }

      function hideResults() {
        results.hidden = true;
        results.setAttribute("aria-busy", "false");
        results.replaceChildren();
        query.setAttribute("aria-expanded", "false");
        query.removeAttribute("aria-activedescendant");
        activeIndex = -1;
      }

      function clearSelection() {
        itemId.value = "";
        query.value = "";
        hideResults();
        query.focus();
      }

      function fillRelatedField(suffix, value) {
        if (!value) return;
        var row = picker.closest("fieldset") || form;
        var target = row.querySelector('[name$="-' + suffix + '"]');
        if (target && !target.value.trim()) target.value = value;
      }

      function choose(item) {
        itemId.value = item.id;
        query.value = item.label;
        if (picker.hasAttribute("data-autofill-name")) fillRelatedField("product_name", item.name);
        if (picker.hasAttribute("data-autofill-stone")) fillRelatedField("stone_type", item.stone);
        hideResults();
        query.focus();
        announce("محصول انتخاب شد.");
      }

      function showItems(items) {
        results.replaceChildren();
        results.setAttribute("aria-busy", "false");
        if (!items.length) {
          var empty = document.createElement("p");
          empty.className = "product-picker-empty";
          empty.textContent = "محصولی پیدا نشد.";
          results.appendChild(empty);
        }
        items.forEach(function (item, itemIndex) {
          var option = document.createElement("button");
          option.type = "button";
          option.id = results.id + "-option-" + itemIndex;
          option.className = "product-picker-option";
          option.setAttribute("role", "option");
          option.setAttribute("aria-selected", "false");
          option.textContent = item.label;
          option.addEventListener("click", function () { choose(item); });
          option.addEventListener("mouseenter", function () { setActive(itemIndex); });
          results.appendChild(option);
        });
        results.hidden = false;
        query.setAttribute("aria-expanded", "true");
      }

      function loadItems() {
        if (controller) controller.abort();
        controller = new AbortController();
        var url = new URL(picker.dataset.productUrl, window.location.origin);
        url.searchParams.set("q", query.value.trim());
        if (picker.hasAttribute("data-trade-product-picker")) {
          var direction = form.querySelector('[name="direction"]:checked');
          var counterparty = form.querySelector('[name="counterparty"]');
          if (direction) url.searchParams.set("direction", direction.value);
          if (counterparty) url.searchParams.set("counterparty", counterparty.value);
        }
        results.hidden = false;
        results.setAttribute("aria-busy", "true");
        results.textContent = "در حال جست‌وجو…";
        query.setAttribute("aria-expanded", "true");
        fetch(url.toString(), {headers: {Accept: "application/json"}, signal: controller.signal})
          .then(function (response) {
            if (!response.ok) throw new Error("product search failed");
            return response.json();
          })
          .then(function (payload) { showItems(payload.items || []); })
          .catch(function (error) {
            if (error.name === "AbortError") return;
            results.setAttribute("aria-busy", "false");
            results.textContent = "جست‌وجو انجام نشد؛ دوباره تلاش کنید.";
            announce("جست‌وجوی محصول انجام نشد.");
          });
      }

      query.addEventListener("focus", loadItems);
      query.addEventListener("input", function () {
        itemId.value = "";
        window.clearTimeout(timer);
        timer = window.setTimeout(loadItems, 250);
      });
      query.addEventListener("keydown", function (event) {
        var choices = options();
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          if (results.hidden || !choices.length) {
            loadItems();
            return;
          }
          setActive(event.key === "ArrowDown" ? activeIndex + 1 : (activeIndex < 0 ? choices.length - 1 : activeIndex - 1));
        } else if (event.key === "Enter" && !results.hidden && activeIndex >= 0) {
          event.preventDefault();
          choices[activeIndex].click();
        } else if (event.key === "Escape" && !results.hidden) {
          event.preventDefault();
          hideResults();
        }
      });
      query.addEventListener("blur", function () {
        window.setTimeout(function () {
          if (!picker.contains(document.activeElement)) hideResults();
        }, 120);
      });
      if (clear) clear.addEventListener("click", clearSelection);

      if (picker.hasAttribute("data-trade-product-picker")) {
        form.querySelectorAll('[name="direction"], [name="counterparty"]').forEach(function (field) {
          field.addEventListener("change", clearSelection);
        });
      }
    });
  }

  function initCollectionPickers(root) {
    elementsWithin(root, "[data-collection-picker]:not([data-ui-ready])").forEach(function (picker) {
      picker.dataset.uiReady = "true";
      var query = picker.querySelector("[data-collection-query]");
      var options = Array.from(picker.querySelectorAll("[data-collection-option]"));
      var empty = picker.querySelector("[data-collection-empty]");
      if (!query || !options.length) return;

      function filterOptions() {
        var needle = query.value.trim().toLocaleLowerCase("fa");
        var visible = 0;
        options.forEach(function (option) {
          var matches = !needle || option.textContent.toLocaleLowerCase("fa").includes(needle);
          option.hidden = !matches;
          if (matches) visible += 1;
        });
        if (empty) empty.hidden = visible > 0;
      }

      query.addEventListener("input", filterOptions);
    });
  }

  function initInventorySelection(root) {
    elementsWithin(root, "[data-inventory-selection]:not([data-ui-ready])").forEach(function (form) {
      form.dataset.uiReady = "true";
      var boxes = Array.from(form.querySelectorAll('input[name="lot_ids"]'));
      var all = form.querySelector("[data-select-all]");
      var label = form.querySelector("[data-select-all-label]");
      var count = form.querySelector("[data-selection-count]");
      var submit = form.querySelector("[data-selection-submit]");
      var details = form.querySelector("[data-selection-details]");
      var destination = form.querySelector('select[name="catalog_id"]');
      if (!boxes.length || !count || !submit) return;
      if (label) label.hidden = false;
      function update() {
        var selected = boxes.filter(function (box) { return box.checked; }).length;
        // HTMX restores serialized HTML; reflect checkbox state in that HTML.
        boxes.forEach(function (box) { box.toggleAttribute("checked", box.checked); });
        count.textContent = selected ? selected.toLocaleString("fa-IR") + " محصول در این صفحه انتخاب شده" : "محصولی انتخاب نشده؛ دایرهٔ کنار محصول را بزنید.";
        submit.disabled = selected === 0;
        if (all) { all.checked = selected === boxes.length; all.indeterminate = selected > 0 && selected < boxes.length; }
      }
      boxes.forEach(function (box) {
        box.addEventListener("change", function () { if (box.checked && details) details.open = true; update(); });
      });
      if (all) all.addEventListener("change", function () {
        boxes.forEach(function (box) { box.checked = all.checked; });
        if (all.checked && details) details.open = true;
        update();
      });
      if (destination) destination.addEventListener("change", function () {
        Array.from(destination.options).forEach(function (option) { option.toggleAttribute("selected", option.selected); });
      });
      update();
    });
  }

  function initUnsavedForms(root) {
    elementsWithin(root, "form[data-unsaved-warning]:not([data-ui-ready])").forEach(function (form) {
      form.dataset.uiReady = "true";
      form.dataset.dirty = form.dataset.dirty || "false";
      function markDirty() { form.dataset.dirty = "true"; }
      form.addEventListener("input", markDirty);
      form.addEventListener("change", markDirty);
    });
  }

  function focusFirstError(root) {
    var invalid = root.querySelector && root.querySelector('[aria-invalid="true"]');
    var target = invalid || (root.querySelector && root.querySelector(".form-error-summary"));
    if (target) {
      for (var parent = target.parentElement; parent; parent = parent.parentElement) {
        if (parent.matches("details")) parent.open = true;
      }
      if (target.getBoundingClientRect().width) {
        target.focus();
        return true;
      }
    }
    return false;
  }

  function initFormAccessibility(root) {
    elementsWithin(root, ".field, .form-row").forEach(function (group) {
      var errors = group.querySelector(".field-error, .errorlist");
      if (!errors) return;
      var control = group.querySelector("input:not([type=hidden]), select, textarea");
      if (!control) return;
      if (!errors.id) errors.id = (control.id || "field-" + Date.now()) + "_errors";
      control.setAttribute("aria-invalid", "true");
      var descriptions = new Set((control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
      descriptions.add(errors.id);
      control.setAttribute("aria-describedby", Array.from(descriptions).join(" "));
    });
  }

  function resetForm(form, failed) {
    if (!form || !form.matches("form")) return;
    if (failed && form.dataset.wasDirty === "true") form.dataset.dirty = "true";
    delete form.dataset.submitting;
    delete form.dataset.wasDirty;
    form.querySelectorAll('[aria-busy="true"]').forEach(function (button) {
      button.removeAttribute("aria-busy");
      button.classList.remove("is-disabled");
    });
  }

  function requestError() {
    var main = document.getElementById("main-content");
    if (!main || main.querySelector("[data-request-error]")) return;
    var alert = document.createElement("div");
    alert.className = "alert alert-error";
    alert.dataset.requestError = "true";
    alert.setAttribute("role", "alert");
    alert.textContent = "ارتباط با سرور برقرار نشد. اطلاعات شما در فرم باقی مانده؛ دوباره تلاش کنید.";
    main.prepend(alert);
  }

  function init(root) {
    initFormAccessibility(root);
    initToasts(root);
    initGalleries(root);
    initShareActions(root);
    initMediaOrder(root);
    initPriceTiers(root);
    initMediaUpload(root);
    initProductPickers(root);
    initCollectionPickers(root);
    initInventorySelection(root);
    initUnsavedForms(root);
    focusFirstError(root);
  }

  function hasDirtyForm() {
    return Boolean(document.querySelector('form[data-unsaved-warning][data-dirty="true"]'));
  }

  function isEditableControl(element) {
    return element instanceof Element && element.matches("input:not([type='checkbox']):not([type='radio']):not([type='button']):not([type='submit']), textarea, select");
  }

  function updateMobileInputState(element) {
    if (!window.matchMedia("(pointer: coarse)").matches) return;
    document.body.classList.toggle("has-mobile-input-focus", isEditableControl(element));
  }

  document.addEventListener("focusin", function (event) {
    updateMobileInputState(event.target);
  });
  document.addEventListener("focusout", function () {
    window.setTimeout(function () { updateMobileInputState(document.activeElement); }, 0);
  });

  document.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;
    var errorLink = event.target.closest(".form-error-summary a");
    if (errorLink) {
      var target = document.getElementById(errorLink.hash.slice(1));
      if (target) {
        event.preventDefault();
        for (var parent = target.parentElement; parent; parent = parent.parentElement) {
          if (parent.matches("details")) parent.open = true;
        }
        target.focus();
        target.scrollIntoView({block: "center"});
      }
    }
    var printButton = event.target.closest("[data-print-action]");
    if (printButton) {
      event.preventDefault();
      window.print();
    }

    var link = event.target.closest("a[href]");
    var samePage = link && link.origin === window.location.origin && link.pathname === window.location.pathname && link.search === window.location.search;
    var leavesPage = link && !samePage && !link.target && !link.hasAttribute("download") && /^https?:/.test(link.protocol);
    if (leavesPage && hasDirtyForm() && !window.confirm("تغییرات ذخیره‌نشده دارید. از این صفحه خارج می‌شوید؟")) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  window.addEventListener("beforeunload", function (event) {
    if (!hasDirtyForm()) return;
    event.preventDefault();
    event.returnValue = "";
  });

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var submitter = event.submitter;
    if (submitter && submitter.dataset.confirm && !window.confirm(submitter.dataset.confirm)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    if (!event.defaultPrevented) {
      form.dataset.wasDirty = form.dataset.dirty || "false";
      form.dataset.dirty = "false";
      form.dataset.submitting = "true";
      submitter = submitter || form.querySelector('[type="submit"]');
      if (submitter) {
        submitter.setAttribute("aria-busy", "true");
        submitter.classList.add("is-disabled");
      }
    }
  }, true);

  document.addEventListener("htmx:beforeRequest", function () {
    document.querySelectorAll("[data-request-error]").forEach(function (alert) { alert.remove(); });
    document.body.classList.add("is-loading");
    announce("در حال بارگذاری…");
  });
  ["htmx:afterRequest", "htmx:sendError", "htmx:responseError", "htmx:timeout", "htmx:sendAbort"].forEach(function (eventName) {
    document.addEventListener(eventName, function (event) {
      document.body.classList.remove("is-loading");
      var failed = eventName !== "htmx:afterRequest" || (event.detail && event.detail.successful === false);
      var source = event.detail && event.detail.elt;
      resetForm(source && (source.matches("form") ? source : source.closest("form")), failed);
      if (eventName !== "htmx:afterRequest" || (event.detail && event.detail.successful === false)) {
        announce("در بارگذاری صفحه مشکلی پیش آمد؛ دوباره تلاش کنید.");
        if (eventName !== "htmx:sendAbort") requestError();
      } else {
        announce("صفحه به‌روزرسانی شد.");
      }
    });
  });
  window.addEventListener("pageshow", function () {
    document.querySelectorAll("form[data-submitting]").forEach(function (form) { resetForm(form, true); });
    document.body.classList.remove("is-loading");
  });
  document.addEventListener("htmx:historyRestore", function () {
    document.querySelectorAll("[data-ui-ready]").forEach(function (element) {
      delete element.dataset.uiReady;
    });
    document.querySelectorAll("form[data-submitting]").forEach(function (form) { resetForm(form, true); });
    init(document);
    if (window.SangaInvoiceEditorInit) window.SangaInvoiceEditorInit();
  });
  document.addEventListener("htmx:beforeSwap", function (event) {
    var xhr = event.detail.xhr;
    if (xhr && [400, 422].includes(xhr.status) && xhr.responseText.includes('id="main-content"')) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });
  document.addEventListener("htmx:afterSettle", function (event) {
    if (event.detail.target !== document.body) return;
    if (focusFirstError(document)) return;
    var focus = document.querySelector("#main-content");
    if (focus) focus.focus();
  });
  document.addEventListener("pointerdown", function (event) {
    document.querySelectorAll("[data-product-picker]").forEach(function (picker) {
      if (picker.contains(event.target)) return;
      var results = picker.querySelector("[data-product-results]");
      var query = picker.querySelector("[data-product-query]");
      if (results) results.hidden = true;
      if (query) { query.setAttribute("aria-expanded", "false"); query.removeAttribute("aria-activedescendant"); }
    });
  });
  document.addEventListener("htmx:load", function (event) { init(event.detail.elt || document); });

  init(document);
})();
