package planted;

import java.math.BigDecimal;
import java.util.Iterator;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/** Charges and refunds through the payment provider. */
public class PaymentService {
    private final Map<String, Receipt> receipts = new ConcurrentHashMap<>();

    public Receipt charge(String account, BigDecimal amount) {
        if (amount.signum() <= 0) {
            throw new IllegalArgumentException("amount must be positive");
        }
        Receipt receipt = new Receipt(UUID.randomUUID().toString(), amount);
        receipts.put(receipt.id(), receipt);
        return receipt;
    }

    public void refund(String receiptId) {
        Receipt receipt = receipts.remove(receiptId);
        if (receipt == null) {
            throw new IllegalStateException("no receipt " + receiptId);
        }
    }

    public Receipt lookup(String receiptId) {
        return receipts.get(receiptId);
    }

    /** Read-only view: the iterator deliberately rejects removal. */
    public Iterator<Receipt> iterator() {
        Iterator<Receipt> inner = receipts.values().iterator();
        return new Iterator<>() {
            @Override
            public boolean hasNext() {
                return inner.hasNext();
            }

            @Override
            public Receipt next() {
                return inner.next();
            }

            @Override
            public void remove() {
                throw new UnsupportedOperationException("read-only view");
            }
        };
    }

    public record Receipt(String id, BigDecimal amount) {}
}
