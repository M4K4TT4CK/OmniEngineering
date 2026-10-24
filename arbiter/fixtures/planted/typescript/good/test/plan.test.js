// node:test and AVA style: the test context `t` carries the assertions.
import { test } from "node:test";
import { deepStrictEqual } from "node:assert";
import { tspl } from "@matteo.collina/tspl";
import { drain } from "../src/queue";

test("drains with a plan", async (t) => {
  // undici: a plan-counted assertion object, its methods destructured
  const { strictEqual } = tspl(t, { plan: 1 });
  strictEqual(await drain([]), 0);
});

test("rejects a bad batch", async (t) => {
  // AVA: `t.throwsAsync` is an assertion, not a `throws`
  await t.throwsAsync(drain(null), { message: /batch/ });
});

test("drains in a subtest", async (t) => {
  await t.test("inner", async () => {
    deepStrictEqual(await drain([]), 0);
  });
});

test("drains through a helper", async (t) => {
  await runDrain(t, []);
});

async function runDrain(t, jobs) {
  t.assert.equal(await drain(jobs), 0);
}
