// Primitives déterministes : aucun appel à Math.random ni à l'horloge système dans le moteur.
export const DT = 10;            // pas physique : 10 s simulées
export const SENSE = 6;          // capteurs, contrôleur, oracle : toutes les 60 s simulées
export const MIN = 60, HOUR = 3600, DAY = 86400;
export const SAMPLE_EVERY = 30;  // échantillon persisté toutes les 5 min simulées
export const KPI_EVERY = 360;    // instantané KPI persisté toutes les heures simulées
export const T0 = Date.UTC(2026, 6, 1, 0, 0, 0) / 1000;

export function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
export function fnv(str, seed) {
  let h = (seed === undefined ? 0x811c9dc5 : seed) >>> 0;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return h >>> 0;
}
export function h64(str) {
  return fnv(str).toString(16).padStart(8, '0') + fnv(str, 0x9747b28c).toString(16).padStart(8, '0');
}
export const clamp = (x, a, b) => (x < a ? a : x > b ? b : x);
export function median(a) {
  if (!a.length) return NaN;
  const s = [...a].sort((x, y) => x - y), n = s.length;
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
}
export function gauss(r) {
  let u = 0; while (u === 0) u = r();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * r());
}
export const pick = (r, a) => a[Math.floor(r() * a.length)];
export function wpick(r, items) {
  let s = 0; for (const it of items) s += it[0];
  let x = r() * s;
  for (const it of items) { x -= it[0]; if (x <= 0) return it[1]; }
  return items[items.length - 1][1];
}
export function pct(list, p) {
  if (!list.length) return null;
  const s = [...list].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))];
}
export function dewPoint(T, RH) {
  const a = 17.62, b = 243.12, g = Math.log(Math.max(RH, 1) / 100) + (a * T) / (b + T);
  return (b * g) / (a - g);
}

const pad = (n) => String(n).padStart(2, '0');
export function fmtHM(t) { const d = new Date(t * 1000); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; }
export function fmtDur(s) {
  if (s == null || isNaN(s)) return '—';
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + ' s';
  if (s < 3600) return Math.floor(s / 60) + ' min' + (s % 60 ? ' ' + pad(s % 60) + ' s' : '');
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  if (h < 48) return h + ' h ' + pad(m);
  return (s / DAY).toFixed(1).replace('.', ',') + ' j';
}
export function fmtArgs(a) {
  if (!a) return '';
  return Object.entries(a).map(([k, v]) => `${k}=${typeof v === 'number' ? +v.toFixed(2) : v}`).join(', ');
}
