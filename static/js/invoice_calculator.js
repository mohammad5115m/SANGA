(function (root, factory) {
  "use strict";
  var calculator = factory();
  if (typeof module === "object" && module.exports) module.exports = calculator;
  else root.SangaInvoiceCalculator = calculator;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function normalized(value) {
    var persian = "۰۱۲۳۴۵۶۷۸۹";
    var arabic = "٠١٢٣٤٥٦٧٨٩";
    return String(value == null ? "" : value)
      .replace(/[۰-۹]/g, function (digit) { return String(persian.indexOf(digit)); })
      .replace(/[٠-٩]/g, function (digit) { return String(arabic.indexOf(digit)); })
      .replace(/[٬,]/g, "")
      .replace(/٫/g, ".");
  }

  function number(value, errors, label) {
    var raw = normalized(value).trim();
    if (!raw) return 0;
    var parsed = Number(raw);
    if (!Number.isFinite(parsed)) {
      errors.push(label + " عدد معتبر نیست.");
      return 0;
    }
    return parsed;
  }

  function rounded(value, digits) {
    var scale = Math.pow(10, digits);
    return Math.round((value + Number.EPSILON) * scale) / scale;
  }

  function discount(base, type, value, errors, label) {
    var parsed = number(value, errors, label);
    if (parsed < 0) errors.push(label + " نمی‌تواند منفی باشد.");
    if (type === "percent") {
      if (parsed > 100) errors.push("درصد " + label + " نمی‌تواند بیشتر از ۱۰۰ باشد.");
      return rounded(base * parsed / 100, 2);
    }
    if (type === "amount") {
      if (parsed > base) errors.push(label + " نمی‌تواند از مبلغ مربوط بیشتر باشد.");
      return rounded(parsed, 2);
    }
    return 0;
  }

  function calculate(payload) {
    var errors = [];
    var grossSubtotal = 0;
    var lineDiscountTotal = 0;
    var lines = (payload.lines || []).map(function (line) {
      var quantity = rounded(number(line.quantity, errors, "مقدار"), 3);
      var unitPrice = rounded(number(line.unit_price, errors, "قیمت واحد"), 2);
      if (quantity <= 0) errors.push("مقدار هر ردیف باید بزرگ‌تر از صفر باشد.");
      if (unitPrice <= 0) errors.push("قیمت واحد باید بزرگ‌تر از صفر باشد.");
      var gross = rounded(quantity * unitPrice, 2);
      var discountAmount = discount(
        gross, line.discount_type, line.discount_value, errors, "تخفیف ردیف"
      );
      var total = rounded(Math.max(0, gross - discountAmount), 2);
      grossSubtotal += gross;
      lineDiscountTotal += discountAmount;
      return {gross_total: gross, discount_amount: discountAmount, line_total: total};
    });
    grossSubtotal = rounded(grossSubtotal, 2);
    lineDiscountTotal = rounded(lineDiscountTotal, 2);
    var netItemsTotal = rounded(grossSubtotal - lineDiscountTotal, 2);
    var invoiceDiscount = discount(
      netItemsTotal,
      payload.invoice_discount_type,
      payload.invoice_discount_value,
      errors,
      "تخفیف کلی"
    );
    var tax = number(payload.tax_amount, errors, "مالیات");
    var shipping = number(payload.shipping_amount, errors, "هزینه ارسال");
    var adjustment = number(payload.adjustment_amount, errors, "افزایش مبلغ");
    var paid = number(payload.paid_amount, errors, "مبلغ پرداخت‌شده");
    var totalAmount = rounded(
      netItemsTotal - invoiceDiscount + tax + shipping + adjustment,
      2
    );
    if (paid > totalAmount) errors.push("مبلغ پرداخت‌شده نمی‌تواند از مبلغ نهایی بیشتر باشد.");
    var previous = number(payload.previous_balance_snapshot, errors, "مانده قبلی");
    if (payload.previous_balance_state === "creditor") previous *= -1;
    if (payload.previous_balance_state === "settled") previous = 0;
    var amountDue = rounded(
      totalAmount - paid + (payload.previous_balance_included ? previous : 0),
      2
    );
    amountDue = Math.max(0, amountDue);
    return {
      valid: errors.length === 0,
      errors: errors,
      lines: lines,
      gross_subtotal: grossSubtotal,
      line_discount_total: lineDiscountTotal,
      net_items_total: netItemsTotal,
      invoice_discount_amount: invoiceDiscount,
      total_amount: totalAmount,
      amount_due: amountDue
    };
  }

  return {calculate: calculate, normalizeNumber: normalized};
});
