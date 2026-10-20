"use strict";

function addItem(cart, item) {
  return [...cart, item];
}

function total(cart) {
  return cart.reduce((sum, item) => sum + item.price * item.qty, 0);
}

function applyDiscount(amount, percent) {
  return Math.round(amount * (1 - percent / 100) * 100) / 100;
}

module.exports = { addItem, total, applyDiscount };
