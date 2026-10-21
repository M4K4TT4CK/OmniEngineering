import { test } from "node:test";
import { drain } from "../src/queue";

// node:test: the context `t` checks nothing on its own.
test("opens the queue page", // expect: arbiter/typescript.test-without-assertion
  async (t) => {
    await drain([]);
  });
