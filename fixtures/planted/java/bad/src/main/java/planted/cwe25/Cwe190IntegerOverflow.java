package planted.cwe25;

/** CWE-190: quantity * unit price in int cents wraps negative past about 21 million dollars. */
public class Cwe190IntegerOverflow {
    public int lineTotalCents(int quantity, int unitPriceCents) {
        if (quantity <= 0 || unitPriceCents < 0) {
            throw new IllegalArgumentException("bad line");
        }
        return quantity * unitPriceCents; // gap: CWE-190 - nothing in this build reports it (docs/cwe-top25.md)
    }
}
