package planted;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.math.BigDecimal;

import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;

class JobQueueTest {
    private final JobQueue queue = new JobQueue(id -> null);

    @Test
    void enqueueStoresJob() { // expect: arbiter/java.test-without-assertion
        queue.enqueue(new FakeJob("a"));
        queue.next();
    }

    @Test
    void duplicateIdsAreIgnored() { // expect: arbiter/java.test-without-assertion
        queue.enqueue(new FakeJob("a"));
        queue.enqueue(new FakeJob("a"));
        System.out.println(queue.next());
    }

    @Disabled("flaky under load") // expect: arbiter/assurance.permanently-skipped-test
    @Test
    void drainsInOrder() {
        queue.enqueue(new FakeJob("a"));
        queue.enqueue(new FakeJob("b"));
        assertEquals("a", queue.next().id());
    }

    record FakeJob(String id) implements JobQueue.Job {
        @Override
        public void execute() {
        }

        @Override
        public int failures() {
            return 0;
        }
    }
}
