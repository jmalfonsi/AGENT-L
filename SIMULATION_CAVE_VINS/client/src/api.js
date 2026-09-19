// Appels REST vers le serveur du banc.
async function req(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}
export const api = {
  get: (url) => req('GET', url),
  post: (url, body) => req('POST', url, body || {}),
  control: (action, value) => req('POST', '/api/control', { action, value }),
  chaos: (type, p) => req('POST', '/api/chaos', { type, p }),
  input: (lane, type, p) => req('POST', '/api/input', { lane, type, p }),
};
