package planted;

import java.math.BigDecimal;

/** Charges and refunds through the payment provider. */
public class PaymentService {
    public Receipt charge(String account, BigDecimal amount) {
        throw new UnsupportedOperationException("not implemented yet"); // expect: arbiter/java.stub-method
    }

    public void refund(String receiptId) {
        throw new RuntimeException("TODO: refunds"); // expect: arbiter/java.stub-method
    }

    public Receipt lookup(String receiptId) {
        return null; // TODO: look up the receipt // expect: arbiter/java.stub-method
    }

    public record Receipt(String id, BigDecimal amount) {}
}
