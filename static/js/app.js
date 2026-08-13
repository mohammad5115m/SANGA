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
})();
