(function () {
  "use strict";
  var button = document.querySelector("[data-print-invoice]");
  if (button) button.addEventListener("click", function () { window.print(); });
})();
