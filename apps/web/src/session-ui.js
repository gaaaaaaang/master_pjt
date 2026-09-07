export function restoreWorkspaceUi(raw, conversationIds) {
  const fallback = { activeId: conversationIds[0], drafts: {} };
  try {
    const saved = JSON.parse(raw);
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return fallback;
    const drafts = Object.fromEntries(conversationIds
      .filter(id => typeof saved.drafts?.[id] === 'string')
      .map(id => [id, saved.drafts[id].slice(0, 8000)]));
    return { activeId: conversationIds.includes(saved.activeId) ? saved.activeId : fallback.activeId, drafts };
  } catch { return fallback; }
}
