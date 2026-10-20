namespace Cart;

public record Item(int Price, int Qty);

public static class Calculator
{
    public static int Total(IEnumerable<Item> items)
    {
        var sum = 0;
        foreach (var item in items)
        {
            sum += item.Price * item.Qty;
        }
        return sum;
    }

    public static int Discount(int amount, int percent) => amount - amount * percent / 100;
}
