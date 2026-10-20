package com.example;

import java.util.List;

public class Cart {
    public record Item(int price, int qty) {}

    public static int total(List<Item> items) {
        int sum = 0;
        for (Item item : items) {
            sum += item.price() * item.qty();
        }
        return sum;
    }

    public static int discount(int amount, int percent) {
        return amount - amount * percent / 100;
    }
}
