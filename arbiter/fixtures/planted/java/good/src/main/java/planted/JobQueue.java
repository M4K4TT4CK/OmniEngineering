package planted;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.locks.ReentrantLock;
import java.util.logging.Level;
import java.util.logging.Logger;

/** In-memory job queue drained by a pool of worker threads. */
public class JobQueue implements Runnable {
    private static final Logger LOG = Logger.getLogger(JobQueue.class.getName());
    private static final int MAX_ATTEMPTS = 5;

    private final BlockingQueue<Job> pending = new LinkedBlockingQueue<>();
    private final Map<String, Job> byId = new ConcurrentHashMap<>();
    private final List<Job> deadLetters = new ArrayList<>();
    private final Map<String, Integer> attempts = new HashMap<>();
    private final ReentrantLock attemptsLock = new ReentrantLock();
    private final ExecutorService pool = Executors.newFixedThreadPool(4);
    private final LockStore store;
    private final AtomicInteger processed = new AtomicInteger();
    private final AtomicInteger failed = new AtomicInteger();
    private volatile boolean running = true;

    public JobQueue(LockStore store) {
        this.store = store;
    }

    public void enqueue(Job job) {
        if (byId.putIfAbsent(job.id(), job) == null) {
            pending.add(job);
        }
    }

    public Job next() throws InterruptedException {
        return pending.poll(500, TimeUnit.MILLISECONDS);
    }

    public synchronized void bury(Job job) {
        if (!deadLetters.contains(job)) {
            deadLetters.add(job);
        }
    }

    public Job unbury() {
        synchronized (deadLetters) {
            if (!deadLetters.isEmpty()) {
                return deadLetters.remove(0);
            }
        }
        return null;
    }

    public int recordAttempt(String id) {
        attemptsLock.lock();
        try {
            if (!attempts.containsKey(id)) {
                attempts.put(id, 0);
            }
            return attempts.merge(id, 1, Integer::sum);
        } finally {
            attemptsLock.unlock();
        }
    }

    public Map<String, Integer> summary(List<Job> jobs) {
        Map<String, Integer> counts = new HashMap<>();
        for (Job job : jobs) {
            if (!counts.containsKey(job.id())) {
                counts.put(job.id(), 0);
            }
            counts.merge(job.id(), job.failures(), Integer::sum);
        }
        return counts;
    }

    @Override
    public void run() {
        while (running) {
            try {
                Job job = next();
                if (job != null) {
                    job.execute();
                    processed.incrementAndGet();
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            } catch (RuntimeException e) {
                LOG.log(Level.WARNING, "job failed", e);
            }
        }
    }

    public void submitAll(List<Job> jobs) {
        for (Job job : jobs) {
            pool.submit(() -> {
                job.execute();
                failed.addAndGet(job.failures());
            });
        }
    }

    public int countFailures(List<Job> jobs) {
        int total = 0;
        for (Job job : jobs) {
            total += job.failures();
        }
        return total;
    }

    public Job claim(String id) throws InterruptedException {
        long backoffMillis = 50;
        for (int attempt = 1; ; attempt++) {
            try {
                return store.lock(id);
            } catch (LockBusyException e) {
                if (attempt >= MAX_ATTEMPTS) {
                    throw new IllegalStateException("could not lock " + id + " after " + attempt + " attempts", e);
                }
                LOG.warning("lock busy, retrying " + id + " in " + backoffMillis + "ms");
                Thread.sleep(backoffMillis);
                backoffMillis = Math.min(backoffMillis * 2, 2_000);
            }
        }
    }

    public int processed() {
        return processed.get();
    }

    public void start() {
        for (int i = 0; i < 4; i++) {
            pool.execute(this);
        }
    }

    public void stop() {
        running = false;
        pool.shutdown();
    }

    interface Job {
        String id();

        void execute();

        int failures();
    }

    interface LockStore {
        Job lock(String id) throws LockBusyException;
    }

    static final class LockBusyException extends Exception {
        LockBusyException(String message) {
            super(message);
        }
    }
}
