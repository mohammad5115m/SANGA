(function (root, factory) {
  "use strict";
  var calculator = factory();
  if (typeof module === "object" && module.exports) module.exports = calculator;
  else root.SangaInvoiceCalculator = calculator;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function number(value) {
    var parsed = Number(String(value == null ? "" : value).replace(/,/g, ""));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function rounded(value, digits) {
    var scale = Math.pow(10, digits);
    return Math.round((value + Number.EPSILON) * scale) / scale;
  }

  function discount(base, type, value) {
    if (type === "percent") return rounded(base * number(value) / 100, 2);
    if (type === "amount") return rounded(number(value), 2);
    return 0;
  }

  function calculate(payload) {
    var grossSubtotal = 0;
    var lineDiscountTotal = 0;
    var lines = (payload.lines || []).map(function (line) {
      var quantity = rounded(number(line.quantity), 3);
      var unitPrice = rounded(number(line.unit_price), 2);
      var gross = rounded(quantity * unitPrice, 2);
      var discountAmount = discount(gross, line.discount_type, line.discount_value);
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
      payload.invoice_discount_value
    );
    var totalAmount = rounded(
      netItemsTotal - invoiceDiscount + number(payload.tax_amount) +
      number(payload.shipping_amount) + number(payload.adjustment_amount),
      2
    );
    var amountDue = rounded(
      totalAmount - number(payload.paid_amount) +
      (payload.previous_balance_included ? number(payload.previous_balance_snapshot) : 0),
      2
    );
    return {
      lines: lines,
      gross_subtotal: grossSubtotal,
      line_discount_total: lineDiscountTotal,
      net_items_total: netItemsTotal,
      invoice_discount_amount: invoiceDiscount,
      total_amount: totalAmount,
      amount_due: amountDue
    };
  }

  return {calculate: calculate};
});
