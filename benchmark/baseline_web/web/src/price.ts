export interface Line {
  sku: string;
  price: number;
  qty: number;
}

export function lineTotal(line: Line): number {
  return line.price * line.qty;
}

export function formatPrice(value: number): string {
  return `$${value.toFixed(2)}`;
}
