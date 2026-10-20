const { addItem, total, applyDiscount } = require("./cart");

describe("cart", () => {
  test("adds an item", () => {
    expect(addItem([], { price: 5, qty: 1 })).toHaveLength(1);
  });

  test("totals the cart", () => {
    expect(total([{ price: 5, qty: 2 }, { price: 1, qty: 3 }])).toBe(13);
  });

  test("applies a discount", () => {
    expect(applyDiscount(200, 15)).toBe(170);
  });
});
