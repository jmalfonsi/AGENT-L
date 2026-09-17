import { spawn } from 'child_process';
import path from 'path';
import { access } from 'fs/promises';
import { parseRunnerEvent } from './live_events';

const automationBenchPython = '/home/ubuntu/AutomationBench/.venv/bin/python';

// Délai laissé au processus Python entre le SIGTERM et le SIGKILL. Un runner
// bloqué dans time.sleep() garde le verrou /tmp/aitestplatform-gemini-rate.lock
// et gèlerait tous les runs suivants de la machine : il faut donc pouvoir le
// tuer pour de bon, pas seulement le lui demander poliment.
const KILL_GRACE_MS = Number(process.env.AGENT_RUNNER_KILL_GRACE_MS || 5_000);

const MAX_STDOUT_BYTES = 25_000_000;

/** Erreur émise lorsqu'un appelant a explicitement demandé l'arrêt du runner. */
export class RunnerCancelledError extends Error {
  constructor(reason = 'Exécution annulée.') {
    super(reason);
    this.name = 'RunnerCancelled';
  }
}

/** Poignée remise à l'appelant pour interrompre un runner déjà lancé. */
export interface RunnerHandle {
  readonly pid: number | undefined;
  /** Demande l'arrêt : SIGTERM puis SIGKILL après le délai de grâce. */
  cancel(reason?: string): void;
  /** Vrai dès qu'une annulation a été demandée. */
  readonly cancelled: boolean;
}

export interface RunnerOptions {
  timeoutMs?: number;
  onProgress?: (event: Record<string, unknown>) => void;
  extraEnv?: Record<string, string>;
  script?: string;
  stdinText?: string;
  /** Reçoit la poignée dès que le processus est lancé. */
  onChild?: (handle: RunnerHandle) => void;
  killGraceMs?: number;
}

async function pythonExecutable(): Promise<string> {
  if (process.env.AGENT_BENCH_PYTHON) return process.env.AGENT_BENCH_PYTHON;
  await access(automationBenchPython);
  return automationBenchPython;
}

function boundedAppend(current: string, addition: string, maxLength: number): string {
  const next = current + addition;
  return next.length > maxLength ? next.slice(-maxLength) : next;
}

/**
 * Lance benchmark_runner.py (ou un autre script) et lit l'objet JSON de stdout.
 * La promesse n'est résolue ou rejetée qu'après la fin RÉELLE du processus :
 * un appelant qui enchaîne des runs ne peut donc pas en démarrer un nouveau
 * pendant que le précédent agonise encore avec le verrou de quota en main.
 */
export function runRunner(args: string[], options: RunnerOptions = {}): Promise<any> {
  const {
    timeoutMs = 15 * 60_000,
    onProgress,
    extraEnv = {},
    script = 'benchmark_runner.py',
    stdinText,
    onChild,
    killGraceMs = KILL_GRACE_MS,
  } = options;

  return (async () => {
    const python = await pythonExecutable();
    const projectRoot = process.cwd();
    const runnerPath = path.join(projectRoot, script);

    return await new Promise<any>((resolve, reject) => {
      const child = spawn(python, [runnerPath, ...args], {
        cwd: projectRoot,
        env: {
          ...process.env,
          ...extraEnv,
          PYTHONUNBUFFERED: '1',
          CREWAI_DISABLE_TELEMETRY: 'true',
          OTEL_SDK_DISABLED: 'true',
        },
        stdio: [stdinText === undefined ? 'ignore' : 'pipe', 'pipe', 'pipe'],
      });

      let stdout = '';
      let stderr = '';
      let stderrLines = '';
      let stdoutOverflow = false;
      let cancelRequested = false;
      /** Erreur retenue en attendant la mort effective du processus. */
      let pendingFailure: Error | null = null;
      let settled = false;
      let timer: NodeJS.Timeout | undefined;
      let killTimer: NodeJS.Timeout | undefined;

      const clearTimers = () => {
        if (timer) { clearTimeout(timer); timer = undefined; }
        if (killTimer) { clearTimeout(killTimer); killTimer = undefined; }
      };

      const settle = (error: Error | null, value?: any) => {
        if (settled) return;
        settled = true;
        clearTimers();
        if (error) reject(error); else resolve(value);
      };

      // Escalade unique : SIGTERM, puis SIGKILL si le processus ne rend pas la
      // main. La promesse reste en attente jusqu'à l'événement « close ».
      const terminate = (failure: Error) => {
        if (pendingFailure) return;
        pendingFailure = failure;
        if (timer) { clearTimeout(timer); timer = undefined; }
        try { child.kill('SIGTERM'); } catch { /* déjà mort */ }
        killTimer = setTimeout(() => {
          try { child.kill('SIGKILL'); } catch { /* déjà mort */ }
        }, killGraceMs);
        // Ne retient pas la boucle d'événements si le processus est déjà parti.
        killTimer.unref?.();
      };

      if (stdinText !== undefined) {
        // Le cahier des charges n'apparaît jamais dans la ligne de commande.
        child.stdin?.on('error', () => undefined);
        child.stdin?.end(stdinText);
      }

      const handle: RunnerHandle = {
        get pid() { return child.pid; },
        get cancelled() { return cancelRequested; },
        cancel(reason = 'Exécution annulée à la demande.') {
          cancelRequested = true;
          terminate(new RunnerCancelledError(reason));
        },
      };
      onChild?.(handle);

      const consumeStderrLine = (line: string) => {
        const progress = parseRunnerEvent(line);
        if (progress) {
          onProgress?.(progress);
        } else if (line) {
          stderr = boundedAppend(stderr, line + '\n', 2_000_000);
        }
      };

      timer = setTimeout(() => {
        terminate(new Error(`Le runner a dépassé ${Math.round(timeoutMs / 1000)} secondes.`));
      }, timeoutMs);

      child.stdout.on('data', (chunk) => {
        if (stdoutOverflow) return;
        stdout += chunk.toString();
        if (stdout.length > MAX_STDOUT_BYTES) {
          // Au-delà de 25 Mo la sortie n'est de toute façon plus un objet JSON
          // exploitable : on le dit franchement plutôt que de laisser un
          // « réponse non JSON » énigmatique.
          stdoutOverflow = true;
          stdout = stdout.slice(0, MAX_STDOUT_BYTES);
          terminate(new Error(
            `Le runner a émis plus de ${Math.round(MAX_STDOUT_BYTES / 1_000_000)} Mo sur stdout : sortie tronquée, processus interrompu.`,
          ));
        }
      });

      child.stderr.on('data', (chunk) => {
        stderrLines += chunk.toString();
        const lines = stderrLines.split(/\r?\n/);
        stderrLines = lines.pop() || '';
        for (const line of lines) consumeStderrLine(line);
      });

      child.on('error', (error) => {
        // Échec de spawn : aucun processus à attendre.
        settle(pendingFailure ?? error);
      });

      child.on('close', (code) => {
        if (stderrLines) {
          consumeStderrLine(stderrLines);
          stderrLines = '';
        }
        if (pendingFailure) {
          settle(pendingFailure);
          return;
        }
        if (code !== 0) {
          settle(new Error(stderr.trim() || stdout.trim() || `Runner interrompu (code ${code}).`));
          return;
        }
        try {
          settle(null, JSON.parse(stdout));
        } catch {
          settle(new Error(`Réponse non JSON du runner. ${stderr || stdout.slice(-1200)}`));
        }
      });
    });
  })();
}

/**
 * Signature historique conservée pour les appels courts déjà en place.
 * Le paramètre `onChild` permet aux appelants longs de garder la main.
 */
export async function callRunner(
  args: string[],
  timeoutMs = 15 * 60_000,
  onProgress?: (event: Record<string, unknown>) => void,
  extraEnv: Record<string, string> = {},
  script = 'benchmark_runner.py',
  stdinText?: string,
  onChild?: (handle: RunnerHandle) => void,
): Promise<any> {
  return await runRunner(args, { timeoutMs, onProgress, extraEnv, script, stdinText, onChild });
}

export interface ControlledRunner {
  promise: Promise<any>;
  cancel(reason?: string): void;
}

/**
 * Variante explicite : rend la promesse ET le moyen de l'interrompre, sans
 * imposer un rappel. `cancel()` appelé avant le lancement effectif du
 * processus est mémorisé et appliqué dès que celui-ci existe.
 */
export function callRunnerControlled(args: string[], options: RunnerOptions = {}): ControlledRunner {
  let handle: RunnerHandle | null = null;
  let pendingReason: string | null = null;
  const promise = runRunner(args, {
    ...options,
    onChild: (child) => {
      handle = child;
      options.onChild?.(child);
      if (pendingReason !== null) child.cancel(pendingReason);
    },
  });
  return {
    promise,
    cancel(reason = 'Exécution annulée à la demande.') {
      if (handle) handle.cancel(reason);
      else pendingReason = reason;
    },
  };
}

export function isCancellation(error: unknown): boolean {
  return Boolean(error) && (error as any).name === 'RunnerCancelled';
}
