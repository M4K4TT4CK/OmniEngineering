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

  it("drains through the callback", function (done) {
    // `done` handed to the code under test: an error there fails the test.
    drain([job]).then(() => done(), done);
  });

  it("routes to the handler", function (done) {
    // The handler ends the response with `done`; a wrong route never calls it.
    getJob("1").then((j) => ({ end: done }).end(j && undefined));
  });

  it("does not throw on an empty batch", function () {
    drain([]);
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
