/** Moves a localStorage value to a new key, unless the new key is already set. */
export function migrateKey(from: string, to: string) {
  try {
    const old = localStorage.getItem(from);
    if (old === null) return;
    if (localStorage.getItem(to) === null) localStorage.setItem(to, old);
    localStorage.removeItem(from);
  } catch {
    // storage blocked: nothing to move
  }
}
