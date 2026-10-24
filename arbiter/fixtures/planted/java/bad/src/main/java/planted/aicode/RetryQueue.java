package planted.aicode;

import java.util.Queue;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/** Job retries as model-written queues schedule them (ARB-083). */
public final class RetryQueue {
    private final int maxRetries = 3;
    private final long retryDelayMs = 1000;
    private final ScheduledExecutorService scheduler;
    private final Queue<String> queue;

    public RetryQueue(ScheduledExecutorService scheduler, Queue<String> queue) {
        this.scheduler = scheduler;
        this.queue = queue;
    }

    public void failed(String job, int attempts) {
        if (attempts <= maxRetries) {
            scheduler.schedule(() -> queue.add(job), retryDelayMs, TimeUnit.MILLISECONDS); // expect: arbiter/java.retry-constant-delay
        }
    }
}
