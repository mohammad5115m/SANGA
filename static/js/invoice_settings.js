(function () {
  "use strict";

  var form = document.querySelector("[data-invoice-settings]");
  if (!form) return;

  var colorPicker = form.querySelector("[data-invoice-color-picker]");
  if (colorPicker) {
    var paletteInput = form.elements.palette;
    var colorInput = form.elements.primary_color;
    var customColor = colorPicker.querySelector("[data-custom-color]");
    var options = colorPicker.querySelectorAll("[data-color-palette]");

    function selectColor(palette, color) {
      paletteInput.value = palette;
      colorInput.value = color.toLowerCase();
      customColor.value = color;
      options.forEach(function (option) {
        option.setAttribute(
          "aria-pressed",
          option.dataset.colorPalette === palette ? "true" : "false"
        );
      });
      colorPicker.classList.toggle("is-custom", palette === "custom");
    }

    options.forEach(function (option) {
      option.addEventListener("click", function () {
        selectColor(option.dataset.colorPalette, option.dataset.colorValue);
      });
    });
    customColor.addEventListener("input", function () {
      selectColor("custom", customColor.value);
    });
    selectColor(paletteInput.value || "forest", colorInput.value || "#1f513c");
  }

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
