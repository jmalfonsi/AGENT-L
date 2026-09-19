// Aides partagées entre onglets.
import { DAY } from '../fmt.js';

export const WINDOWS = [[6 * 3600, '6 h'], [DAY, '24 h'], [3 * DAY, '3 j'], [7 * DAY, '7 j']];
/* Fenêtre glissante sur l'historique en mémoire (échantillons à 5 min). */
export function windowed(hist, win) {
  if (!hist || !hist.t.length) return null;
  const t1 = hist.t[hist.t.length - 1], t0 = t1 - win;
  let i = 0; while (i < hist.t.length && hist.t[i] < t0) i++;
  return { i, t: hist.t.slice(i) };
}
export const sl = (arr, w) => (w ? arr.slice(w.i) : []);
/* Pente °C/h sur la dernière heure. */
export function slope(hist, zone) {
  if (!hist || hist.t.length < 13) return 0;
  const T = hist.z[zone].T, n = T.length;
  return (T[n - 1] - T[n - 13]) / ((hist.t[n - 1] - hist.t[n - 13]) / 3600);
}
