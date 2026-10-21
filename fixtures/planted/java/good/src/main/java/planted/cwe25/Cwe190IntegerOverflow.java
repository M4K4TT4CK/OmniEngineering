package planted.cwe25;

/** CWE-190: the product is computed with an overflow check. */
public class Cwe190IntegerOverflow {
    public int lineTotalCents(int quantity, int unitPriceCents) {
        if (quantity <= 0 || unitPriceCents < 0) {
            throw new IllegalArgumentException("bad line");
        }
        return Math.multiplyExact(quantity, unitPriceCents);
    }
}
