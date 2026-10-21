import { describe, it, test, expect } from "vitest";
import { drain, getJob, type Job } from "../src/queue";

const job: Job = { id: "1", state: "queued", payload: null };

describe("queue", () => {
  it("drains a batch", // expect: arbiter/typescript.test-without-assertion
    async function () {
      await drain([job]);
    });

  it("loads a job", // expect: arbiter/typescript.test-without-assertion
    (done) => {
      getJob("1").then(() => done());
    });

  test.skip("retries a failed job", async () => { // expect: arbiter/typescript.skipped-test
    await drain([job]);
    expect(job.state).toBe("done");
  });

  it.skip("respects priority", async () => { // expect: arbiter/assurance.permanently-skipped-test
    await drain([job]);
    expect(job.state).toBe("done");
  });
  it("caches a loaded job", async () => { // expect: arbiter/assurance.test-asserts-nothing
    await getJob("1");
    await getJob("1");
  });
});
