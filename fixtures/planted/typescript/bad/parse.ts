export function parse(body: string): unknown {
  return eval("(" + body + ")"); // expect: arbiter/typescript.eval-call
}
