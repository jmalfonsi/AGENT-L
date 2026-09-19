// Graphiques canvas : lignes 2 px, bande de tolérance, réticule + infobulle au survol, repères verticaux.
import { useEffect, useRef, useState } from 'react';
import { cssVar, fRel, fHM, f, DAY, HOUR, T0 } from '../fmt.js';

function niceTicks(lo, hi, n) {
  const span = hi - lo || 1, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0))), e = step0 / mag;
  const step = (e < 1.5 ? 1 : e < 3 ? 2 : e < 7 ? 5 : 10) * mag;
  const t = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) t.push(+v.toFixed(6));
  return t;
}
const resolve = (c) => (c && c.startsWith('var(') ? cssVar(c.slice(4, -1)) : c && c.startsWith('--') ? cssVar(c) : c);

export function LineChart({ x, series, h = 180, band, bandWarn, refLines, markers, unit = '', dec = 2, ymin, ymax, xmin, xmax, axes = true, xFmt = fRel, onPick }) {
  const wrap = useRef(null), cv = useRef(null);
  const [hover, setHover] = useState(null);
  const [W, setW] = useState(0);
  useEffect(() => {
    const ro = new ResizeObserver((es) => setW(Math.floor(es[0].contentRect.width)));
    if (wrap.current) ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);
  // Géométrie horizontale et point survolé calculés au rendu (l'infobulle reste synchrone avec le tracé).
  const Lp = axes ? 42 : 3, Rp = 8, pwR = W - Lp - Rp;
  const hasX = x && x.length;
  const x0R = hasX ? (xmin != null ? xmin : x[0]) : 0, x1R = hasX ? Math.max(xmax != null ? xmax : x[x.length - 1], x0R + 1) : 1;
  let hv = null;
  if (hover != null && hasX && pwR > 0 && hover >= Lp && hover <= W - Rp) {
    const tt = x0R + ((hover - Lp) / pwR) * (x1R - x0R); let bd = Infinity;
    for (let i = 0; i < x.length; i++) { if (x[i] < x0R || x[i] > x1R) continue; const d = Math.abs(x[i] - tt); if (d < bd) { bd = d; hv = i; } }
  }
  const tipLeft = hv != null ? Lp + ((x[hv] - x0R) / (x1R - x0R)) * pwR : 0;
  const cols = series.map((s) => resolve(s.color) || cssVar('--ink2'));
  useEffect(() => {
    const c = cv.current; if (!c || !W || !x) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = W * dpr; c.height = h * dpr; c.style.height = h + 'px';
    const g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, h);
    const L = axes ? 42 : 3, R = 8, T = 6, B = axes ? 20 : 3, pw = W - L - R, ph = h - T - B;
    if (!x.length || pw <= 0) return;
    const x0 = xmin != null ? xmin : x[0], x1 = Math.max(xmax != null ? xmax : x[x.length - 1], x0 + 1);
    let lo = Infinity, hi = -Infinity;
    for (const s of series) for (let i = 0; i < s.v.length; i++) { if (x[i] < x0 || x[i] > x1) continue; const v = s.v[i]; if (v == null || !isFinite(v)) continue; if (v < lo) lo = v; if (v > hi) hi = v; }
    if (band) { lo = Math.min(lo, band[0]); hi = Math.max(hi, band[1]); }
    if (ymin != null) lo = Math.min(lo, ymin); if (ymax != null) hi = Math.max(hi, ymax);
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    const pad = (hi - lo) * .1 || .5; lo -= pad; hi += pad;
    const X = (t) => L + ((t - x0) / (x1 - x0)) * pw, Y = (v) => T + (1 - (v - lo) / (hi - lo)) * ph;
    const grid = cssVar('--grid'), muted = cssVar('--muted'), surface = cssVar('--surface');
    g.font = '11px "IBM Plex Sans", system-ui, sans-serif'; g.lineWidth = 1;
    if (axes) {
      g.strokeStyle = grid; g.fillStyle = muted;
      for (const v of niceTicks(lo, hi, 4)) { const y = Math.round(Y(v)) + .5; g.beginPath(); g.moveTo(L, y); g.lineTo(W - R, y); g.stroke(); g.textAlign = 'right'; g.textBaseline = 'middle'; g.fillText(String(v).replace('.', ','), L - 6, y); }
      const span = x1 - x0, stepT = span > 20 * DAY ? 5 * DAY : span > 4 * DAY ? DAY : span > 36 * HOUR ? 12 * HOUR : span > 18 * HOUR ? 6 * HOUR : span > 6 * HOUR ? 2 * HOUR : span > 2 * HOUR ? 30 * 60 : 10 * 60;
      g.textAlign = 'center'; g.textBaseline = 'top';
      const first = T0 + Math.ceil((x0 - T0) / stepT) * stepT;
      for (let t = first; t <= x1; t += stepT) { const xx = X(t); if (xx < L + 12 || xx > W - R - 12) continue; g.fillText(stepT >= DAY ? 'J+' + Math.round((t - T0) / DAY) : fHM(t), xx, h - B + 5); }
    }
    if (band) { g.fillStyle = cssVar('--band'); const y1 = Y(band[1]), y2 = Y(band[0]); g.fillRect(L, y1, pw, y2 - y1); }
    if (bandWarn) { g.fillStyle = cssVar('--band-warn'); for (const bw of bandWarn) { const y1 = Y(bw[1]), y2 = Y(bw[0]); g.fillRect(L, y1, pw, y2 - y1); } }
    for (const rl of refLines || []) { g.strokeStyle = cssVar('--axis'); g.setLineDash([4, 4]); g.beginPath(); g.moveTo(L, Y(rl.v) + .5); g.lineTo(W - R, Y(rl.v) + .5); g.stroke(); g.setLineDash([]); }
    for (const mk of markers || []) { if (mk.t < x0 || mk.t > x1) continue; g.strokeStyle = resolve(mk.color) || muted; g.globalAlpha = .75; g.beginPath(); g.moveTo(Math.round(X(mk.t)) + .5, T); g.lineTo(Math.round(X(mk.t)) + .5, T + ph); g.stroke(); g.globalAlpha = 1; }
    series.forEach((s, si) => {
      const col = cols[si];
      if (s.area) {
        g.beginPath(); let st = false, lx = 0;
        for (let i = 0; i < s.v.length; i++) { if (x[i] < x0 || x[i] > x1 || s.v[i] == null) continue; const xx = X(x[i]), yy = Y(s.v[i]); if (!st) { g.moveTo(xx, Y(lo)); g.lineTo(xx, yy); st = true; } else g.lineTo(xx, yy); lx = xx; }
        if (st) { g.lineTo(lx, Y(lo)); g.closePath(); g.globalAlpha = .1; g.fillStyle = col; g.fill(); g.globalAlpha = 1; }
      }
      g.beginPath(); g.strokeStyle = col; g.lineWidth = s.w || 2; g.lineJoin = 'round'; g.lineCap = 'round'; if (s.dash) g.setLineDash(s.dash);
      let st = false;
      for (let i = 0; i < s.v.length; i++) { if (x[i] < x0 || x[i] > x1 || s.v[i] == null) { st = false; continue; } const xx = X(x[i]), yy = Y(s.v[i]); if (!st) { g.moveTo(xx, yy); st = true; } else g.lineTo(xx, yy); }
      g.stroke(); g.setLineDash([]);
      if (!s.dash && s.end !== false) {
        let i = s.v.length - 1; while (i >= 0 && (s.v[i] == null || x[i] > x1)) i--;
        if (i >= 0) { g.beginPath(); g.fillStyle = col; g.arc(X(x[i]), Y(s.v[i]), 4, 0, 7); g.fill(); g.strokeStyle = surface; g.lineWidth = 2; g.stroke(); }
      }
    });
    if (hv != null) {
      const bi = hv, xx = X(x[bi]); g.strokeStyle = cssVar('--ink2'); g.lineWidth = 1; g.beginPath(); g.moveTo(xx + .5, T); g.lineTo(xx + .5, T + ph); g.stroke();
      series.forEach((s, si) => { const v = s.v[bi]; if (v == null) return; g.beginPath(); g.fillStyle = cols[si]; g.arc(xx, Y(v), 4.5, 0, 7); g.fill(); g.strokeStyle = surface; g.lineWidth = 2; g.stroke(); });
    }
  });
  return (
    <div className="chart" ref={wrap}>
      <canvas ref={cv} role="img" aria-label={series.map((s) => s.label).join(', ')}
        onMouseMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); setHover(e.clientX - r.left); }}
        onMouseLeave={() => setHover(null)}
        onClick={() => { if (onPick && hv != null) onPick(x[hv], hv); }} style={{ cursor: onPick ? 'pointer' : 'crosshair' }} />
      {hv != null && (
        <div className="tip" style={{ left: tipLeft + 180 > W ? tipLeft - 190 : tipLeft + 12, top: 8 }}>
          <b>{xFmt(x[hv])}</b>
          {series.map((s, si) => (
            <div key={si}><span className="sw" style={{ background: cols[si] }} />{s.label} <b>{s.v[hv] == null ? '—' : f(s.v[hv], s.dec != null ? s.dec : dec)}</b> {s.unit != null ? s.unit : unit}</div>
          ))}
        </div>
      )}
    </div>
  );
}

export function Legend({ items }) {
  return (
    <div className="legend">
      {items.map((it, i) => <span key={i}><i className={it.dash ? 'dash' : ''} style={{ background: it.dash ? undefined : it.color, color: it.color }} />{it.label}</span>)}
    </div>
  );
}

/* Petite courbe sans axes (tuiles). */
export function Spark({ x, v, band, color = 'var(--ink2)', h = 42 }) {
  return <LineChart x={x} series={[{ label: '', v, color, w: 1.6 }]} band={band} h={h} axes={false} />;
}
