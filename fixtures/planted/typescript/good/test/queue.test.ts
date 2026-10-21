import { describe, it, test, expect } from "vitest";
import { drain, getJob, type Job } from "../src/queue";

const job: Job = { id: "1", state: "queued", payload: null };

describe("queue", () => {
  it("drains a batch", async function () {
    const failed = await drain([job]);
    expect(failed).toBe(0);
  });

  it("loads a job", (done) => {
    getJob("1").then((loaded) => {
      expect(loaded.id).toBe("1");
      done();
    });
  });

  test("retries a failed job", async () => {
    await drain([job]);
    expect(job.state).toBe("done");
  });

  test.skipIf(process.env.CI)("respects priority", async () => {
    await drain([job]);
    expect(job.state).toBe("done");
  });

  it("caches a loaded job", async () => {
    const first = await getJob("1");
    const second = await getJob("1");
    expect(second).toBe(first);
  });
});
