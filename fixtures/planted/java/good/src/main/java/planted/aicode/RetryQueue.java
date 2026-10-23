package planted.aicode;

import java.util.Queue;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;

/** Job retries with exponential backoff and jitter (ARB-083). */
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
            long wait = Math.min(30_000, retryDelayMs << attempts);
            long jittered = wait / 2 + ThreadLocalRandom.current().nextLong(wait / 2 + 1);
            scheduler.schedule(() -> queue.add(job), jittered, TimeUnit.MILLISECONDS);
        }
    }
}
