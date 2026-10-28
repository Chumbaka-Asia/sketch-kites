// IndexedDB-backed kite store for file:// mode (replaces the 5 MB localStorage cap).
// Loaded as a plain script (not a module — modules are blocked over file://).
const KiteStore = (() => {
  const DB_NAME = 'sketch-kites';
  const DB_VERSION = 1;
  const STORE = 'kites';
  const LEGACY_KEY = 'sketch-kites';

  let dbPromise = null;

  function openDB() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: 'id' });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return dbPromise;
  }

  function tx(mode) {
    return openDB().then(db => db.transaction(STORE, mode).objectStore(STORE));
  }

  async function getAll() {
    const store = await tx('readonly');
    return new Promise((resolve, reject) => {
      const req = store.getAll();
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function put(kite) {
    const store = await tx('readwrite');
    return new Promise((resolve, reject) => {
      const req = store.put(kite);
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }

  async function remove(id) {
    const store = await tx('readwrite');
    return new Promise((resolve, reject) => {
      const req = store.delete(id);
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }

  async function clear() {
    const store = await tx('readwrite');
    return new Promise((resolve, reject) => {
      const req = store.clear();
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }

  async function replaceAll(kites) {
    const store = await tx('readwrite');
    return new Promise((resolve, reject) => {
      store.clear();
      for (const kite of kites) store.put(kite);
      store.transaction.oncomplete = () => resolve();
      store.transaction.onerror = () => reject(store.transaction.error);
    });
  }

  async function migrateFromLocalStorage() {
    let raw;
    try {
      raw = localStorage.getItem(LEGACY_KEY);
    } catch (e) {
      return;
    }
    if (!raw) return;
    try {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr) && arr.length) {
        const store = await tx('readwrite');
        await new Promise((resolve, reject) => {
          for (const kite of arr) store.put(kite);
          store.transaction.oncomplete = () => resolve();
          store.transaction.onerror = () => reject(store.transaction.error);
        });
      }
    } catch (e) {
      console.warn('KiteStore: failed to migrate legacy localStorage kites', e);
    }
    localStorage.removeItem(LEGACY_KEY);
  }

  return { getAll, put, remove, clear, replaceAll, migrateFromLocalStorage };
})();
