import assert from "node:assert/strict";
import test from "node:test";
import { bookingBalance } from "../admin/billing.js";

test("issued invoice totals take precedence and every payment contributes to the balance", () => {
  const booking = { quote_paise: 8_000_000, invoice: { total_paise: 7_500_000 },
    payments: [{ amount_paise: 1_000_000 }, { amount_paise: 2_000_050 }] };
  assert.deepEqual(bookingBalance(booking),
    { total: 7_500_000, paid: 3_000_050, balance: 4_499_950, status: "Partially paid", tone: "partial" });
});

test("balances distinguish unpaid, paid, overpaid, and unquoted bookings", () => {
  const balance = (quote, paid) => bookingBalance({ quote_paise: quote, payments: paid ? [{ amount_paise: paid }] : [] });
  assert.equal(balance(5000, 0).status, "Unpaid");
  assert.equal(balance(5000, 5000).status, "Paid");
  assert.equal(balance(5000, 6000).status, "Overpaid");
  assert.equal(balance(5000, 6000).balance, 0);
  assert.equal(balance(null, 8000).status, "Quote pending");
  assert.equal(balance(null, 8000).paid, 8000);
});
