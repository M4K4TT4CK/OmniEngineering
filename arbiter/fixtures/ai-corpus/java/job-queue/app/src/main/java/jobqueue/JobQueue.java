package jobqueue;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.*;

/** In-memory job queue with a fixed worker pool and bounded retries. */
public final class JobQueue implements AutoCloseable {

    public enum Status { QUEUED, RUNNING, SUCCEEDED, FAILED }

    /** Processes one job type. */
    public interface Handler {
        /** Reject a bad payload at submit time. Throw IllegalArgumentException. */
        default void validate(JsonNode payload) {}

        /** Do the work; the returned value is exposed as the job result (must be JSON-serializable). */
        Object handle(JsonNode payload) throws Exception;
    }

    public static final class Job {
        public final String id = UUID.randomUUID().toString();
        public final String type;
        public final JsonNode payload;
        volatile Status status = Status.QUEUED;
        volatile int attempts;
        volatile Object result;
        volatile String error;

        Job(String type, JsonNode payload) {
            this.type = type;
            this.payload = payload;
        }

        public Status status() { return status; }
        public int attempts() { return attempts; }
        public Object result() { return result; }
        public String error() { return error; }
    }

    private final Map<String, Handler> handlers;
    private final int maxRetries;
    private final long retryDelayMs;
    private final Map<String, Job> jobs = new ConcurrentHashMap<>();
    private final BlockingQueue<Job> queue = new LinkedBlockingQueue<>();
    private final ExecutorService workers;
    private final ScheduledExecutorService retryScheduler = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "job-retry-scheduler");
        t.setDaemon(true);
        return t;
    });

    public JobQueue(Map<String, Handler> handlers, int workerCount, int maxRetries, long retryDelayMs) {
        this.handlers = Map.copyOf(handlers);
        this.maxRetries = maxRetries;
        this.retryDelayMs = retryDelayMs;
        this.workers = Executors.newFixedThreadPool(workerCount, new ThreadFactory() {
            private int n;
            @Override public synchronized Thread newThread(Runnable r) {
                Thread t = new Thread(r, "job-worker-" + (++n));
                t.setDaemon(true);
                return t;
            }
        });
        for (int i = 0; i < workerCount; i++) {
            workers.execute(this::workLoop);
        }
    }

    /** @throws IllegalArgumentException for an unknown type or invalid payload */
    public Job submit(String type, JsonNode payload) {
        Handler h = type == null ? null : handlers.get(type);
        if (h == null) {
            throw new IllegalArgumentException("Unknown job type: " + type);
        }
        if (payload == null || !payload.isObject()) {
            throw new IllegalArgumentException("payload must be a JSON object");
        }
        h.validate(payload);
        Job job = new Job(type, payload);
        jobs.put(job.id, job);
        queue.add(job);
        return job;
    }

    public Job get(String id) {
        return jobs.get(id);
    }

    private void workLoop() {
        try {
            while (!Thread.currentThread().isInterrupted()) {
                run(queue.take());
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void run(Job job) {
        job.status = Status.RUNNING;
        job.attempts++;
        try {
            job.result = handlers.get(job.type).handle(job.payload);
            job.error = null;
            job.status = Status.SUCCEEDED;
        } catch (Exception e) {
            job.error = e.getClass().getSimpleName() + ": " + e.getMessage();
            if (job.attempts <= maxRetries) {
                job.status = Status.QUEUED;
                try {
                    retryScheduler.schedule(() -> queue.add(job), retryDelayMs, TimeUnit.MILLISECONDS);
                } catch (RejectedExecutionException shuttingDown) {
                    job.status = Status.FAILED;
                }
            } else {
                job.status = Status.FAILED;
            }
        }
    }

    @Override
    public void close() {
        retryScheduler.shutdownNow();
        workers.shutdownNow();
    }
}
