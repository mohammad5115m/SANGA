// Everything SANGA's own pages need at runtime, in one file rather than inline.
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
      var timer = window.setTimeout(function () { dismiss(toast); }, 6000);

      toast.addEventListener("mouseenter", function () { window.clearTimeout(timer); });
      toast.addEventListener("mouseleave", function () {
        timer = window.setTimeout(function () { dismiss(toast); }, 3000);
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

  function initUnsavedForms(root) {
    elementsWithin(root, "form[data-unsaved-warning]:not([data-ui-ready])").forEach(function (form) {
      form.dataset.uiReady = "true";
      form.dataset.dirty = "false";
      function markDirty() { form.dataset.dirty = "true"; }
      form.addEventListener("input", markDirty);
      form.addEventListener("change", markDirty);
    });
  }

  function focusFirstError(root) {
    var invalid = root.querySelector && root.querySelector('[aria-invalid="true"]');
    if (invalid) invalid.focus({preventScroll: true});
  }

  function init(root) {
    initToasts(root);
    initGalleries(root);
    initShareActions(root);
    initMediaOrder(root);
    initProductPickers(root);
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
    var printButton = event.target.closest("[data-print-action]");
    if (printButton) {
      event.preventDefault();
      window.print();
    }

    var link = event.target.closest("a[href]");
    if (link && hasDirtyForm() && !window.confirm("تغییرات ذخیره‌نشده دارید. از این صفحه خارج می‌شوید؟")) {
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
      return;
    }
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    if (!event.defaultPrevented) {
      form.dataset.dirty = "false";
      form.dataset.submitting = "true";
      submitter = submitter || form.querySelector('[type="submit"]');
      if (submitter) {
        submitter.setAttribute("aria-busy", "true");
        submitter.classList.add("is-disabled");
      }
    }
  });

  document.addEventListener("htmx:beforeRequest", function () {
    document.body.classList.add("is-loading");
    announce("در حال بارگذاری…");
  });
  ["htmx:afterRequest", "htmx:sendError", "htmx:responseError"].forEach(function (eventName) {
    document.addEventListener(eventName, function (event) {
      document.body.classList.remove("is-loading");
      if (eventName !== "htmx:afterRequest" || (event.detail && event.detail.successful === false)) {
        announce("در بارگذاری صفحه مشکلی پیش آمد؛ دوباره تلاش کنید.");
      } else {
        announce("صفحه به‌روزرسانی شد.");
      }
    });
  });
  document.addEventListener("htmx:load", function (event) { init(event.detail.elt || document); });

  init(document);
})();

