using Cart;
using Xunit;

public class CartTests
{
    [Fact]
    public void TotalsTheCart()
    {
        Assert.Equal(13, Calculator.Total(new[] { new Item(5, 2), new Item(1, 3) }));
    }

    [Fact]
    public void AppliesADiscount()
    {
        Assert.Equal(170, Calculator.Discount(200, 15));
    }
}
