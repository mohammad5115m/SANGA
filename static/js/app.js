// Everything SANGA's own pages need at runtime, in one file rather than inline.
//
// Inline in base.html would mean a Content-Security-Policy that permits
// `unsafe-inline`, which is most of what a script-src policy is for. Moving it
// here lets the policy be `script-src 'self'` with nothing to carve out.

(function () {
  "use strict";

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }

  function dismiss(el) {
    el.classList.add("toast-leaving");
    setTimeout(function () {
      el.remove();
    }, 250);
  }

  // Auto-dismiss toasts after 6s; keep them while hovered so a message can be
  // read or copied.
  document.querySelectorAll(".toast").forEach(function (toast) {
    var timer = setTimeout(function () {
      dismiss(toast);
    }, 6000);

    toast.addEventListener("mouseenter", function () {
      clearTimeout(timer);
    });
    toast.addEventListener("mouseleave", function () {
      timer = setTimeout(function () {
        dismiss(toast);
      }, 3000);
    });

    var close = toast.querySelector(".toast-close");
    if (close) {
      close.addEventListener("click", function () {
        dismiss(toast);
      });
    }
  });

  document.querySelectorAll("[data-gallery]").forEach(function (gallery) {
    var slides = Array.from(gallery.querySelectorAll("[data-gallery-slide]"));
    var thumbs = Array.from(gallery.querySelectorAll("[data-gallery-thumb]"));
    if (!slides.length) return;

    function activate(mediaId) {
      slides.forEach(function (slide) {
        slide.hidden = slide.dataset.mediaId !== mediaId;
      });
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

  document.querySelectorAll("[data-share-actions]").forEach(function (actions) {
    var copy = actions.querySelector("[data-copy-link]");
    var nativeShare = actions.querySelector("[data-native-share]");
    if (copy) {
      copy.addEventListener("click", function () {
        navigator.clipboard.writeText(actions.dataset.shareUrl).then(function () {
          copy.textContent = "لینک کپی شد";
          setTimeout(function () { copy.textContent = "کپی لینک"; }, 2000);
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

  document.querySelectorAll("[data-media-order]").forEach(function (grid) {
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

  document.querySelectorAll("[data-product-picker]").forEach(function (picker) {
    var query = picker.querySelector("[data-product-query]");
    var itemId = picker.querySelector('input[type="hidden"]');
    var results = picker.querySelector("[data-product-results]");
    var clear = picker.querySelector("[data-product-clear]");
    var form = picker.closest("form");
    var timer;
    var controller;
    if (!query || !itemId || !results || !form) return;

    function hideResults() {
      results.hidden = true;
      results.replaceChildren();
    }

    function clearSelection() {
      itemId.value = "";
      query.value = "";
      hideResults();
    }

    function fillRelatedField(suffix, value) {
      if (!value) return;
      var row = picker.closest("fieldset") || form;
      var target = row.querySelector('[name$="-' + suffix + '"]');
      if (target && !target.value.trim()) target.value = value;
    }

    function showItems(items) {
      results.replaceChildren();
      if (!items.length) {
        var empty = document.createElement("p");
        empty.className = "product-picker-empty";
        empty.textContent = "محصولی پیدا نشد.";
        results.appendChild(empty);
      }
      items.forEach(function (item) {
        var option = document.createElement("button");
        option.type = "button";
        option.className = "product-picker-option";
        option.setAttribute("role", "option");
        option.textContent = item.label;
        option.addEventListener("click", function () {
          itemId.value = item.id;
          query.value = item.label;
          if (picker.hasAttribute("data-autofill-name")) fillRelatedField("product_name", item.name);
          if (picker.hasAttribute("data-autofill-stone")) fillRelatedField("stone_type", item.stone);
          hideResults();
        });
        results.appendChild(option);
      });
      results.hidden = false;
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
      results.textContent = "در حال جست‌وجو…";
      fetch(url.toString(), {headers: {Accept: "application/json"}, signal: controller.signal})
        .then(function (response) {
          if (!response.ok) throw new Error("product search failed");
          return response.json();
        })
        .then(function (payload) { showItems(payload.items || []); })
        .catch(function (error) {
          if (error.name === "AbortError") return;
          results.textContent = "جست‌وجو انجام نشد؛ دوباره تلاش کنید.";
        });
    }

    query.addEventListener("focus", loadItems);
    query.addEventListener("input", function () {
      itemId.value = "";
      clearTimeout(timer);
      timer = setTimeout(loadItems, 250);
    });
    if (clear) clear.addEventListener("click", clearSelection);

    if (picker.hasAttribute("data-trade-product-picker")) {
      form.querySelectorAll('[name="direction"], [name="counterparty"]').forEach(function (field) {
        field.addEventListener("change", clearSelection);
      });
    }
  });

  document.addEventListener("submit", function (event) {
    var submitter = event.submitter;
    if (submitter && submitter.dataset.confirm && !window.confirm(submitter.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    if (event.target.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    if (submitter && !event.defaultPrevented) {
      event.target.dataset.submitting = "true";
      submitter.setAttribute("aria-busy", "true");
      submitter.classList.add("is-disabled");
    }
  });
})();
