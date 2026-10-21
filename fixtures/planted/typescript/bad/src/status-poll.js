// A server-side poller: a peer that never answers parks the worker forever.
export async function pollStatus(url) {
  const res = await fetch(url); // expect: arbiter/typescript.fetch-no-timeout
  return res.json();
}
