package cart

import "testing"

func TestTotal(t *testing.T) {
	got := Total([]Item{{5, 2}, {1, 3}})
	if got != 13 {
		t.Fatalf("Total = %d, want 13", got)
	}
}

func TestDiscount(t *testing.T) {
	if got := Discount(200, 15); got != 170 {
		t.Fatalf("Discount = %d, want 170", got)
	}
}
