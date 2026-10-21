package planted;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.DisabledOnOs;
import org.junit.jupiter.api.condition.OS;

class JobQueueTest {
    private final JobQueue queue = new JobQueue(id -> null);

    @Test
    void enqueueStoresJob() throws InterruptedException {
        queue.enqueue(new FakeJob("a"));
        assertEquals("a", queue.next().id());
    }

    @Test
    void duplicateIdsAreIgnored() throws InterruptedException {
        queue.enqueue(new FakeJob("a"));
        queue.enqueue(new FakeJob("a"));
        queue.next();
        assertNull(queue.next());
    }

    @Test
    void submittedJobsRun() {
        JobQueue.Job job = mock(JobQueue.Job.class);
        queue.submitAll(java.util.List.of(job));
        verify(job, org.mockito.Mockito.timeout(1_000)).execute();
    }

    @Test
    void claimGivesUpAfterMaxAttempts() {
        JobQueue busy = new JobQueue(id -> {
            throw new JobQueue.LockBusyException("busy");
        });
        assertThrows(IllegalStateException.class, () -> busy.claim("x"));
    }

    @DisabledOnOs(OS.WINDOWS)
    @Test
    void drainsInOrder() throws InterruptedException {
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
