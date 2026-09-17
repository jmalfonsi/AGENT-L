import type { AssertionRecord, LiveRunResult, ToolCallRecord } from './types';
import { describeAssertion, formatValue } from './assertions';

type JsonRecord = Record<string, unknown>;

export interface AssertionEvidence {
  /** Observation factuelle tirée de l'état final, sans recalculer le verdict. */
  summary: string;
  /** Extraits courts de l'état qui permettent de comprendre l'observation. */
  samples: string[];
  /** Indice tiré du journal d'actions, formulé avec prudence. */
  actionHint: string | null;
}

export interface RunDiagnosis {
  title: string;
  explanation: string;
  tone: 'success' | 'warning' | 'error' | 'invalid';
  actionCount: number;
  actionFailures: number;
  blockedCount: number | null;
}

const asRecord = (value: unknown): JsonRecord =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : {};

const records = (value: unknown): JsonRecord[] =>
  Array.isArray(value) ? value.filter((item): item is JsonRecord => item !== null && typeof item === 'object' && !Array.isArray(item)) : [];

const normalizedJson = (value: unknown): string => {
  try {
    return JSON.stringify(value).toLocaleLowerCase('fr');
  } catch {
    return String(value).toLocaleLowerCase('fr');
  }
};

const shorten = (value: unknown, limit = 210): string => {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
};

function compactRecord(record: JsonRecord): string {
  const cells = asRecord(record.cells);
  const source = Object.keys(cells).length > 0 ? cells : record;
  const preferred = [
    'reply_to', 'to', 'email', 'employee', 'employee_name', 'subject', 'title',
    'status', 'priority', 'response_draft', 'body_plain', 'text', 'description',
  ];
  const entries = Object.entries(source);
  const ordered = [
    ...preferred.flatMap((key) => entries.filter(([name]) => name === key)),
    ...entries.filter(([key]) => !preferred.includes(key)),
  ];
  const visible = ordered
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .slice(0, 4)
    .map(([key, value]) => `${key.replace(/_/g, ' ')} : ${shorten(typeof value === 'object' ? JSON.stringify(value) : value, 120)}`);
  return shorten(visible.join(' · '));
}

interface StateCollection {
  label: string;
  entries: JsonRecord[];
}

/** Collection métier pertinente pour les familles d'assertions du benchmark. */
function stateCollection(record: AssertionRecord, state: Record<string, unknown>): StateCollection | null {
  const type = String(record.type || '');
  const params = asRecord(record.params);

  if (type.startsWith('google_sheets_')) {
    const rows = records(asRecord(state.google_sheets).rows).filter((row) => {
      if (params.spreadsheet_id != null && String(row.spreadsheet_id) !== String(params.spreadsheet_id)) return false;
      if (params.worksheet_id != null && String(row.worksheet_id) !== String(params.worksheet_id)) return false;
      return true;
    });
    return { label: 'ligne(s) Google Sheets', entries: rows };
  }

  if (type.startsWith('gmail_')) {
    return { label: 'message(s) Gmail', entries: records(asRecord(state.gmail).messages) };
  }

  if (type.startsWith('slack_')) {
    const slack = asRecord(state.slack);
    const channels = records(slack.channels);
    const expectedChannel = params.channel_name ?? params.channel;
    const channelIds = new Set(channels
      .filter((channel) => expectedChannel == null
        || String(channel.name) === String(expectedChannel)
        || String(channel.id) === String(expectedChannel))
      .map((channel) => String(channel.id)));
    const messages = records(slack.messages).filter((message) =>
      expectedChannel == null || channelIds.has(String(message.channel_id)) || String(message.channel) === String(expectedChannel));
    return { label: 'message(s) Slack', entries: messages };
  }

  if (type.startsWith('salesforce_')) {
    const salesforce = asRecord(state.salesforce);
    let key = type.includes('_case_') ? 'cases'
      : type.includes('_task_') ? 'tasks'
        : type.includes('_lead_') ? 'leads'
          : String(params.collection || '');
    if (!(key in salesforce) && key.endsWith('s')) key = key.slice(0, -1);
    return key && Array.isArray(salesforce[key])
      ? { label: `enregistrement(s) Salesforce · ${key}`, entries: records(salesforce[key]) }
      : null;
  }

  if (type.startsWith('reamaze_')) {
    return { label: 'conversation(s) Re:amaze', entries: records(asRecord(state.reamaze).conversations) };
  }

  if (type.startsWith('zendesk_')) {
    return { label: 'ticket(s) Zendesk', entries: records(asRecord(state.zendesk).tickets) };
  }

  if (type.startsWith('bamboohr_')) {
    const actions = asRecord(asRecord(state.bamboohr).actions);
    return { label: 'action(s) BambooHR', entries: Object.values(actions).flatMap((item) => records(item).length ? records(item) : [asRecord(item)]) };
  }

  return null;
}

const CONTENT_KEYS = [
  'cell_contains', 'cells', 'column', 'value', 'to', 'email', 'subject',
  'subject_contains', 'body_contains', 'body_not_contains', 'text_contains',
  'description_contains', 'tag', 'assignee_email', 'ticket_id', 'conversation_id',
];

function needles(record: AssertionRecord): unknown[] {
  const params = asRecord(record.params);
  return CONTENT_KEYS
    .filter((key) => params[key] !== undefined && params[key] !== null && params[key] !== '')
    .flatMap((key) => {
      const value = params[key];
      if (key === 'cells' && value !== null && typeof value === 'object') return Object.values(asRecord(value));
      return [value];
    });
}

function mentionsEvery(value: unknown, expected: unknown[]): boolean {
  const haystack = normalizedJson(value);
  return expected.every((item) => haystack.includes(String(item).toLocaleLowerCase('fr')));
}

function matchingCalls(calls: ToolCallRecord[], expected: unknown[]): ToolCallRecord[] {
  if (expected.length === 0) return [];
  return calls.filter((call) => call.tool && mentionsEvery(call.args || {}, expected));
}

function countEvidence(record: AssertionRecord, collection: StateCollection): AssertionEvidence {
  const params = asRecord(record.params);
  const expected = params.count;
  return {
    summary: `${collection.entries.length} ${collection.label} observée(s) ; ${formatValue(expected)} attendue(s).`,
    samples: collection.entries.slice(-3).reverse().map(compactRecord).filter(Boolean),
    actionHint: null,
  };
}

/**
 * Explique une assertion avec l'état observé. Le verdict reste toujours celui
 * du scorer officiel : cette fonction ne tente pas d'en fabriquer un second.
 */
export function assertionEvidence(record: AssertionRecord, run: LiveRunResult): AssertionEvidence {
  const readable = describeAssertion(record);
  const collection = stateCollection(record, run.finalState || {});
  const expected = needles(record);
  const calls = matchingCalls(run.toolCalls || [], expected);
  const negative = readable.negative;

  if (collection && String(record.type || '').endsWith('_row_count')) {
    return countEvidence(record, collection);
  }

  if (collection) {
    const matches = expected.length > 0
      ? collection.entries.filter((entry) => mentionsEvery(entry, expected))
      : [];
    const label = expected.length > 0 ? expected.map(formatValue).join(' et ') : 'les critères attendus';
    let summary: string;
    if (record.passed === true) {
      summary = negative
        ? `Aucune correspondance interdite n'a été retenue par le barème dans ${collection.label}.`
        : `Le barème a trouvé le résultat attendu dans ${collection.label}.`;
    } else if (negative) {
      summary = matches.length > 0
        ? `${matches.length} correspondance(s) contenant ${label} sont pourtant présentes dans ${collection.label}.`
        : `Le barème a détecté un résultat interdit dans ${collection.label}.`;
    } else {
      summary = `Aucune des ${collection.entries.length} ${collection.label} finales ne satisfait ${label}.`;
    }
    const sampleSource = matches.length > 0 ? matches : collection.entries.slice(-3).reverse();
    const actionHint = record.passed === true || expected.length === 0 ? null
      : calls.length > 0
        ? `${calls.length} action(s) enregistrée(s) mentionnent pourtant ce critère : leur réponse ou leur contenu est à examiner.`
        : `Aucun argument d'action enregistré ne reprend directement ${label} : l'étape a pu être omise ou produire un contenu incomplet.`;
    return {
      summary,
      samples: sampleSource.slice(0, 3).map(compactRecord).filter(Boolean),
      actionHint,
    };
  }

  return {
    summary: record.passed === true
      ? 'Le scorer officiel a retrouvé ce résultat dans l’état final.'
      : negative
        ? 'Le scorer officiel a retrouvé une action qui devait rester absente.'
        : 'Le scorer officiel n’a pas retrouvé ce résultat dans l’état final.',
    samples: [],
    actionHint: record.passed === true || expected.length === 0 ? null
      : calls.length > 0
        ? `${calls.length} action(s) enregistrée(s) mentionnent ce critère.`
        : 'Aucun argument d’action enregistré ne reprend directement ce critère.',
  };
}

/** Diagnostic de premier niveau : métier, technique, puis cause la plus sûre. */
export function diagnoseRun(run: LiveRunResult): RunDiagnosis {
  const calls = (run.toolCalls || []).filter((call) => Boolean(call.tool));
  const actionFailures = calls.filter((call) =>
    call.transportStatus === 'exception' || call.ok === false || call.semanticStatus === 'error').length;
  const metrics = asRecord(asRecord(run.runtimeEvidence).runtimeMetrics);
  const blockedRaw = metrics.blocked;
  const blockedCount = typeof blockedRaw === 'number' && Number.isFinite(blockedRaw) ? blockedRaw : null;
  const failed = (run.assertions || []).filter((record) => record.excluded !== true && record.passed !== true).length;

  if (run.status === 'invalid') {
    return {
      title: 'Le résultat n’est pas interprétable',
      explanation: 'L’exécution est techniquement invalide et reste exclue des moyennes. Il faut d’abord corriger l’erreur du framework ou la sortie du modèle.',
      tone: 'invalid', actionCount: calls.length, actionFailures, blockedCount,
    };
  }
  if (run.error) {
    return {
      title: 'Le framework s’est interrompu',
      explanation: 'La note ne reflète pas seulement la qualité de la tâche : une erreur technique a arrêté l’exécution.',
      tone: 'error', actionCount: calls.length, actionFailures, blockedCount,
    };
  }
  if (run.success) {
    return {
      title: 'Tout ce qui comptait a fonctionné',
      explanation: 'Toutes les vérifications évaluées sont satisfaites. Les actions ont produit l’état final attendu par la tâche.',
      tone: 'success', actionCount: calls.length, actionFailures, blockedCount,
    };
  }
  if (calls.length === 0) {
    return {
      title: 'La tâche n’a produit aucune action',
      explanation: `${failed} vérification(s) restent non satisfaites parce qu’aucune modification n’a été tentée dans le système simulé.`,
      tone: 'error', actionCount: 0, actionFailures: 0, blockedCount,
    };
  }
  if (actionFailures > 0) {
    return {
      title: 'Des actions techniques ont échoué',
      explanation: `${actionFailures} action(s) ont été refusées ou ont levé une erreur. Elles peuvent expliquer tout ou partie des ${failed} vérification(s) manquantes.`,
      tone: 'error', actionCount: calls.length, actionFailures, blockedCount,
    };
  }
  return {
    title: 'L’exécution est saine, mais le résultat est incomplet',
    explanation: `Les ${calls.length} action(s) exécutées ont abouti techniquement. La perte de points vient donc d’étapes omises, de mauvaises cibles ou d’un contenu incomplet dans les ${failed} vérification(s) restantes.`,
    tone: 'warning', actionCount: calls.length, actionFailures, blockedCount,
  };
}
