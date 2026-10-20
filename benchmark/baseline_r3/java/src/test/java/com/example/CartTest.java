package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import org.junit.jupiter.api.Test;

class CartTest {
    @Test
    void totalsTheCart() {
        assertEquals(13, Cart.total(List.of(new Cart.Item(5, 2), new Cart.Item(1, 3))));
    }

    @Test
    void appliesADiscount() {
        assertEquals(170, Cart.discount(200, 15));
    }
}
