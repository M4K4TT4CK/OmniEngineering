// node:test and AVA style: the test context `t` carries the assertions.
import { test } from "node:test";
import { tspl } from "@matteo.collina/tspl";
import { drain } from "../src/queue";

test("drains with a plan", // expect: arbiter/typescript.test-without-assertion
  async (t) => {
    await drain([]);
  });

test("rejects a bad batch", // expect: arbiter/typescript.test-without-assertion
  async (t) => {
    await drain(null).catch(() => {});
  });

test("drains in a subtest", // expect: arbiter/typescript.test-without-assertion
  async (t) => {
    await t.test("inner", async (t) => {
      await drain([]);
    });
  });

export { tspl };
