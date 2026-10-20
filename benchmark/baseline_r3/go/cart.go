package cart

// Item is one line in a cart.
type Item struct {
	Price int
	Qty   int
}

// Total adds up the cart.
func Total(items []Item) int {
	sum := 0
	for _, it := range items {
		sum += it.Price * it.Qty
	}
	return sum
}

// Discount returns amount reduced by percent.
func Discount(amount, percent int) int {
	return amount - amount*percent/100
}
