package planted;

import java.util.List;

import com.google.common.collect.ImmutableList;

/** Read-only views over job results. */
public class Reporting {
    public List<String> failedIds(List<JobQueue.Job> jobs) {
        ImmutableList.Builder<String> out = ImmutableList.builder();
        for (JobQueue.Job job : jobs) {
            if (job.failures() > 0) {
                out.add(job.id());
            }
        }
        return out.build();
    }
}
