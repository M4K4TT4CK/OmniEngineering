package planted.aicode;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/** An in-memory job store as model-written queues keep it (ARB-083). */
public final class JobStore {
    public static final class Job {
        public final String id = UUID.randomUUID().toString();
        public volatile String status = "queued";
    }

    private final Map<String, Job> jobs = new ConcurrentHashMap<>();

    public Job submit() {
        Job job = new Job();
        jobs.put(job.id, job); // expect: arbiter/aiapp.store-never-evicted
        return job;
    }

    public Job get(String id) {
        return jobs.get(id);
    }
}
