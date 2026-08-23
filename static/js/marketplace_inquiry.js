(() => {
  const form = document.querySelector("[data-partner-inquiry-selection]");
  if (!form) return;

  const choices = [...form.querySelectorAll("[data-inquiry-lot]")];
  const count = form.querySelector("[data-inquiry-count]");
  const submit = form.querySelector("[data-inquiry-submit]");
  const persianDigits = new Intl.NumberFormat("fa-IR");

  const refresh = () => {
    let selected = 0;
    choices.forEach((choice) => {
      const card = choice.closest("article");
      const quantity = card?.querySelector("[data-inquiry-quantity]");
      if (quantity) quantity.disabled = !choice.checked;
      if (choice.checked) selected += 1;
    });
    count.textContent = persianDigits.format(selected);
    submit.disabled = selected === 0;
  };

  choices.forEach((choice) => choice.addEventListener("change", refresh));
  refresh();
})();
