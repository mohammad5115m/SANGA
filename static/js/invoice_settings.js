(function () {
  "use strict";

  var form = document.querySelector("[data-invoice-settings]");
  if (!form) return;

  form.querySelectorAll("[data-asset-field]").forEach(function (field) {
    var input = field.querySelector('input[type="file"]');
    var clear = field.querySelector('input[type="checkbox"]');
    var preview = field.querySelector("[data-asset-preview]");
    var objectUrl;
    if (!input || !preview) return;

    input.addEventListener("change", function () {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      var file = input.files && input.files[0];
      if (!file) return;
      objectUrl = URL.createObjectURL(file);
      preview.src = objectUrl;
      preview.hidden = false;
      if (clear) clear.checked = false;
    });

    if (clear) {
      clear.addEventListener("change", function () {
        preview.hidden = clear.checked;
      });
    }
  });
})();
