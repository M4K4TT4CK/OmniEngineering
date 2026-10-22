// The same poll, run in a browser page: one user's tab waits, and the user can
// reload it. No server worker is held.
export async function pollStatus(url) {
  const res = await fetch(url);
  document.getElementById("status").textContent = res.statusText;
  return res.json();
}
