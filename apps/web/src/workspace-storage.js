// Conversation payloads live in IndexedDB, not a tab-scoped or quota-small store.
export const STORE_KEY = 'fab-workspace-v1';
let database;
function openDatabase() {
  database ??= new Promise((resolve, reject) => {
    const request = indexedDB.open('fab-assistant-workspace', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('workspace');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return database;
}
export function readLegacy(key) {
  for (const name of ['localStorage', 'sessionStorage']) {
    try { const value = globalThis[name]?.getItem(key); if (value !== null && value !== undefined) return value; } catch {}
  }
  return null;
}
export function compactConversations(conversations) {
  return conversations.map(conversation => ({ ...conversation, messages: conversation.messages.map(message => {
    if (!message.result) return message;
    const { conversation_history, reasoning_state, agent_reflections, supervisor_reviews, supervisor_decisions, reflection_decisions, ...result } = message.result;
    return { ...message, result };
  }) }));
}
export function mergeConversations(saved, incoming) {
  const merged = new Map((Array.isArray(saved) ? saved : []).map(item => [item.id, item]));
  for (const item of incoming) {
    const prior = merged.get(item.id);
    if (!prior || (item.updatedAt || 0) >= (prior.updatedAt || 0)) merged.set(item.id, item);
  }
  return [...merged.values()].sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
}
export async function loadConversations() {
  try {
    const db = await openDatabase();
    const value = await new Promise((resolve, reject) => {
      const request = db.transaction('workspace').objectStore('workspace').get(STORE_KEY);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    if (Array.isArray(value) && value.length) return value;
  } catch { /* Private browsing can disable IndexedDB; try existing storage. */ }
  try { return JSON.parse(readLegacy(STORE_KEY)); } catch { return null; }
}
export async function saveConversations(conversations) {
  const value = compactConversations(conversations);
  try {
    const db = await openDatabase();
    await new Promise((resolve, reject) => {
      const transaction = db.transaction('workspace', 'readwrite');
      const store = transaction.objectStore('workspace');
      const request = store.get(STORE_KEY);
      request.onsuccess = () => store.put(mergeConversations(request.result, value), STORE_KEY);
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error || new Error('Conversation save aborted'));
    });
  } catch {
    let saved;
    try { saved = JSON.parse(localStorage.getItem(STORE_KEY)); } catch {}
    localStorage.setItem(STORE_KEY, JSON.stringify(mergeConversations(saved, value)));
  }
}
