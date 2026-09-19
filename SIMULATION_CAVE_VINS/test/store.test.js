// Persistance SQLite : écriture, relecture, reprise.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Store } from '../server/store.js';
import { Run, normalizeConfig } from '../server/engine/run.js';
import { verifyChain } from '../server/engine/kernel.js';
import { fingerprint } from '../server/engine/world.js';

const tmp = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'cave-')), 'cave.db');

test('SQLite : tout ce que produit le moteur est persisté puis relu', () => {
  const store = new Store(tmp());
  const cfg = normalizeConfig({ seed: 11, scenario: 'CONT', fault: 'high' });
  const id = store.createRun(cfg, { tokens: ['a', 'b', 'c', 'd'] });
  const run = new Run(id, cfg);
  for (let i = 0; i < 8640 * 2; i++) { run.stepOnce(); if (i % 2000 === 0) store.persist(run); }
  run.addChaos('leak', { zone: 'bgn' });
  for (let i = 0; i < 500; i++) run.stepOnce();
  store.persist(run);
  const c = store.counts(id);
  assert.ok(c.samples >= 4 * 576, `échantillons : ${c.samples}`);
  assert.ok(c.kpis >= 4 * 47, `kpis : ${c.kpis}`);
  assert.ok(c.events > 0 && c.actions > 0 && c.exo > 0);
  const w = run.worlds[0];
  assert.equal(store.actionsAll(id, 0).length, w.seq.act);
  assert.equal(verifyChain(store.actionsAll(id, 0)).ok, true);
  const rec = store.recorded(id);
  assert.equal(rec.chaos.length, 1);
  assert.equal(rec.chaos[0].type, 'leak');
  assert.equal(store.samples(id, 0, 0, 9e12, 100).length <= 101, true);
  assert.equal(store.run(id).lanes.length, 4);
});

test('SQLite : un run relu depuis la base et rattrapé reproduit l’état', () => {
  const file = tmp();
  const store = new Store(file);
  const cfg = normalizeConfig({ seed: 12, fault: 'medium' });
  const id = store.createRun(cfg);
  const run = new Run(id, cfg);
  for (let i = 0; i < 5000; i++) run.stepOnce();
  run.addInput('all', 'policy', { level: 'R2', mode: 'APPROVAL' });
  run.addChaos('sensor_offset', { sensor: 'bdx-T1', offset: 4 });
  for (let i = 0; i < 9000; i++) run.stepOnce();
  store.persist(run);
  const store2 = new Store(file);
  const row = store2.run(id);
  const again = new Run(id, row.config, { recorded: store2.recorded(id) });
  again.fastForward(row.step, true);
  while (again.ffTo != null) again.advance(50, 1000);
  assert.equal(again.step, run.step);
  assert.deepEqual(again.worlds.map(fingerprint), run.worlds.map(fingerprint));
});
