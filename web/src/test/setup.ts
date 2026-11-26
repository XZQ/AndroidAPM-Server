import "@testing-library/jest-dom/vitest";

function createMemoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.get(String(key)) ?? null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(String(key));
    },
    setItem(key: string, value: string) {
      store.set(String(key), String(value));
    },
  };
}

// Node 25+ defines globalThis.localStorage as a getter that returns undefined without
// --localstorage-file, shadowing jsdom's Storage in Vitest. Ensure both globalThis and
// window expose working Storage instances across Node versions.
for (const name of ["localStorage", "sessionStorage"] as const) {
  const current = (globalThis as Record<string, unknown>)[name] as Partial<Storage> | undefined;
  if (typeof current?.getItem !== "function" || typeof current?.clear !== "function") {
    const fallback = createMemoryStorage();
    Object.defineProperty(globalThis, name, { value: fallback, configurable: true, writable: true });
    Object.defineProperty(window, name, { value: fallback, configurable: true, writable: true });
  }
}

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
});
