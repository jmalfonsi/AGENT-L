// Formatage (fr-FR) et constantes d'affichage.
export const T0 = Date.UTC(2026, 6, 1, 0, 0, 0) / 1000;
export const MIN = 60, HOUR = 3600, DAY = 86400;
const pad = (n) => String(n).padStart(2, '0');
const JOURS = ['dim.', 'lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.'];
export const fHM = (t) => { const d = new Date(t * 1000); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; };
export const fHMS = (t) => { const d = new Date(t * 1000); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`; };
export const fRel = (t) => `J+${Math.floor((t - T0) / DAY)} ${fHM(t)}`;
export const fDate = (t) => { const d = new Date(t * 1000); return `${JOURS[d.getUTCDay()]} ${pad(d.getUTCDate())}/${pad(d.getUTCMonth() + 1)}/${d.getUTCFullYear()}`; };
export function fDur(s) {
  if (s == null || isNaN(s)) return '—';
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + ' s';
  if (s < 3600) return Math.floor(s / 60) + ' min' + (s % 60 ? ' ' + pad(s % 60) : '');
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  if (h < 48) return h + ' h ' + pad(m);
  return (s / DAY).toFixed(1).replace('.', ',') + ' j';
}
export const f = (x, d = 1) => (x == null || isNaN(x) ? '—' : Number(x).toFixed(d).replace('.', ','));
export const fInt = (x) => (x == null || isNaN(x) ? '—' : Math.round(x).toLocaleString('fr-FR'));
export const fEur = (x) => (x == null ? '—' : x >= 1e6 ? (x / 1e6).toFixed(2).replace('.', ',') + ' M€' : Math.round(x).toLocaleString('fr-FR') + ' €');
export const fPct = (x, d = 1) => (x == null || isNaN(x) ? '—' : f(x, d) + ' %');
export const fArgs = (a) => (!a ? '' : Object.entries(a).map(([k, v]) => `${k}=${typeof v === 'number' ? +v.toFixed(2) : v}`).join(', '));
export const laneColor = (slot) => `var(--l${(slot % 5) + 1})`;
export const cssVar = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

export const STATE_CLS = { NORMAL: 'good', WATCH: 'info', DEGRADED: 'warn', CRITICAL: 'crit', EMERGENCY: 'crit', SAFE_MODE: 'serious' };
export const STATE_FR = { NORMAL: 'Normal', WATCH: 'Surveillance', DEGRADED: 'Dégradé', CRITICAL: 'Critique', EMERGENCY: 'Urgence', SAFE_MODE: 'Mode sûr' };
export const DEC_CLS = { ALLOW: 'good', DENY: 'crit', REQUIRE_APPROVAL: 'warn', DUPLICATE: 'info' };
export const DEC_FR = { ALLOW: 'autorisée', DENY: 'refusée', REQUIRE_APPROVAL: 'approbation', DUPLICATE: 'doublon' };
export const UTIL_CLS = { utile: 'good', neutre: '', inutile: 'warn', nuisible: 'crit' };
export const HEALTH_CLS = { NORMAL: 'good', DRIFTING: 'warn', 'MIS-CALIBRATED': 'warn', NOISY: 'warn', INTERMITTENT: 'warn', STUCK: 'crit', OFFLINE: 'crit', COMPROMISED: 'crit' };
export const KIND_FR = { T: 'Température air', Tb: 'Bouteille témoin', RH: 'Humidité', lux: 'Lumière', vib: 'Vibration', leak: 'Fuite', door: 'Porte', pir: 'Présence', co2: 'CO₂', smoke: 'Fumée', Tout: 'T° extérieure', RHout: 'HR extérieure' };
export const HANDLING_CLS = { autonome: 'good', 'escalade correcte': 'good', escaladé: 'info', 'mal géré': 'crit', manqué: 'crit', "résolu sans l'escalade attendue": 'warn', résilience: '' };
