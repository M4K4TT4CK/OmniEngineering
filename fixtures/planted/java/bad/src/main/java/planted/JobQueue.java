package planted;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.logging.Logger;

/** In-memory job queue drained by a pool of worker threads. */
public class JobQueue implements Runnable {
    private static final Logger LOG = Logger.getLogger(JobQueue.class.getName());

    private final List<Job> pending = new ArrayList<>();
    private final Map<String, Job> byId = new HashMap<>();
    private final ExecutorService pool = Executors.newFixedThreadPool(4);
    private final LockStore store;
    private int processed;
    private int failed;

    public JobQueue(LockStore store) {
        this.store = store;
    }

    public void enqueue(Job job) {
        if (!byId.containsKey(job.id())) { // expect: arbiter/java.check-then-act-unsynchronized
            byId.put(job.id(), job);
            pending.add(job);
        }
    }

    public Job next() {
        if (!pending.isEmpty()) { // expect: arbiter/java.check-then-act-unsynchronized
            return pending.remove(0);
        }
        return null;
    }

    public void forget(String id) {
        if (byId.get(id) != null) byId.remove(id); // expect: arbiter/java.check-then-act-unsynchronized
    }

    @Override
    public void run() {
        while (true) { // expect: arbiter/java.worker-loop-no-stop
            try {
                Job job = next();
                if (job != null) {
                    job.execute();
                    processed++; // expect: arbiter/java.unsynchronized-counter
                }
            } catch (Exception e) { // expect: arbiter/java.swallowed-exception
            }
        }
    }

    public void submitAll(List<Job> jobs) {
        for (Job job : jobs) {
            pool.submit(() -> {
                job.execute();
                failed += job.failures(); // expect: arbiter/java.unsynchronized-counter
            });
        }
    }

    public Job claim(String id) {
        while (true) { // expect: arbiter/java.retry-without-backoff
            try {
                return store.lock(id);
            } catch (LockBusyException e) {
                LOG.warning("lock busy, retrying " + id);
            }
        }
    }

    public int processed() {
        return processed;
    }

    public void start() {
        for (int i = 0; i < 4; i++) {
            pool.execute(this);
        }
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
