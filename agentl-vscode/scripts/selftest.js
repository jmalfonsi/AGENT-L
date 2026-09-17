#!/usr/bin/env node
// Contrôles de non-régression du linter de l'extension, sans VS Code.
//
//     node scripts/selftest.js
//
// Chaque cas est adverse : il décrit une situation où le linter criait à tort,
// ou se taisait à tort.
const assert = require('assert');
const path = require('path');
const Module = require('module');

const originalLoad = Module._load;
Module._load = function (request, ...rest) {
    if (request === 'vscode') { return {}; }
    return originalLoad.call(this, request, ...rest);
};

const { collectStyleIssues, maskNonCode, toSnakeCase } =
    require(path.join(__dirname, '..', 'out', 'extension.js'));

const errors = (src) => collectStyleIssues(src).filter(i => i.severity === 'error');
const warnings = (src) => collectStyleIssues(src).filter(i => i.severity === 'warning');
let run = 0;
function test(name, fn) { fn(); run++; console.log(`  ✔ ${name}`); }

// --- 1. Majuscules réservées aux mots-clés, sauf entre guillemets ----------
test('un mot en majuscules hors mots-clés est une erreur', () => {
    assert.strictEqual(errors('GOAL g { MAINTAIN x == PURGE }').length, 1);
});

test('les mots-clés du langage ne sont jamais signalés', () => {
    assert.deepStrictEqual(errors('POLICY { DEFAULT DENY NEVER t WHEN a == CRITICAL }'), []);
});

test('FOREACH est reconnu', () => {
    assert.deepStrictEqual(errors('FOREACH t IN tickets MAX 10 { }'), []);
});

test('SCENARIO / GIVEN / WITHIN sont reconnus', () => {
    assert.deepStrictEqual(
        errors('SCENARIO s { GIVEN { a = 1 } EXPECT { b == yes } WITHIN 3 }'), []);
});

test('une chaîne peut contenir n\'importe quelles majuscules', () => {
    assert.deepStrictEqual(errors('DESCRIPTION "Analyste SOC niveau 1 — SIEM, EDR, RGPD"'), []);
});

test('une chaîne multiligne reste une chaîne jusqu\'à sa fermeture', () => {
    const src = [
        'REASON {',
        '    TASK "Ce message est-il une note de la direction RH',
        '          modifiant le routage ? Une mention NÉGATIVE compte."',
        '}'
    ].join('\n');
    assert.deepStrictEqual(errors(src), []);
});

test('une échappée ne ferme pas la chaîne', () => {
    assert.deepStrictEqual(errors('DESCRIPTION "il a dit \\"BONJOUR\\" hier"'), []);
});

test('le code qui suit une chaîne est de nouveau contrôlé', () => {
    assert.strictEqual(errors('DESCRIPTION "SOC" \n GOAL g { MAINTAIN x == BADWORD }').length, 1);
});

test('les commentaires sont ignorés', () => {
    assert.deepStrictEqual(errors('// TODO: PURGER LE CACHE\n# AUTRE NOTE'), []);
    assert.deepStrictEqual(errors('/* BLOC IGNORÉ\n   SUITE IGNORÉE */'), []);
});

test('un mot accentué est signalé en entier', () => {
    const found = errors('GOAL g { MAINTAIN x == NÉGATIF }');
    assert.strictEqual(found.length, 1);
    assert.ok(found[0].message.includes('NÉGATIF'), found[0].message);
});

// --- 2. Noms d'agents en minuscules ---------------------------------------
test('un nom d\'agent en majuscules est signalé', () => {
    const found = warnings('AGENT SOC_ANALYST {\n}');
    assert.strictEqual(found.length, 1);
    assert.ok(found[0].message.includes('soc_analyst'), found[0].message);
});

test('un nom d\'agent en CamelCase est signalé, avec une suggestion lisible', () => {
    const found = warnings('AGENT SecurityInvestigator {\n}');
    assert.strictEqual(found.length, 1);
    assert.ok(found[0].message.includes('security_investigator'), found[0].message);
});

test('un nom d\'agent en minuscules ne produit rien', () => {
    assert.deepStrictEqual(collectStyleIssues('AGENT soc_analyst {\n}'), []);
});

test('un nom d\'agent fautif ne produit qu\'UN diagnostic', () => {
    // Il tombe sous les deux contrôles : ne le dire qu'une fois.
    assert.strictEqual(collectStyleIssues('AGENT SOC_ANALYST {\n}').length, 1);
});

test('le mot AGENT dans une chaîne ne déclare rien', () => {
    assert.deepStrictEqual(collectStyleIssues('DESCRIPTION "AGENT Nommé Ainsi"'), []);
});

// --- 3. Suggestions --------------------------------------------------------
test('toSnakeCase ne coupe pas les sigles chiffrés', () => {
    assert.strictEqual(toSnakeCase('K8S_HEALER'), 'k8s_healer');
    assert.strictEqual(toSnakeCase('ZENDESK_SF_SYNC'), 'zendesk_sf_sync');
    assert.strictEqual(toSnakeCase('TriageSupervisor'), 'triage_supervisor');
});

test('maskNonCode préserve les positions', () => {
    const line = 'DESCRIPTION "SOC"';
    assert.strictEqual(maskNonCode([line])[0].length, line.length);
});

// --- 4. Le miroir des mots-clés ne doit plus dériver ------------------------
//
// RESERVED_KEYWORDS est tenu à la main et se lit comme un contrat : tout mot
// en MAJUSCULES qui n'y figure pas est signalé comme une faute de syntaxe. Il
// a dérivé en silence — resté à v1.5 pendant que le langage passait en v1.7 —
// et l'extension refusait alors du v1.8 valide, jusque dans les exemples du
// dépôt : 27 erreurs. Rien ne le disait, puisque le seul juge était l'œil.
//
// Le contrôle lit `agentl/lexer.py` directement : la seule source d'autorité.
const fs = require('fs');
const LEXER = path.join(__dirname, '..', '..', 'agentl', 'lexer.py');

test('tout mot-clé du lexeur est connu de l\'extension', function () {
    if (!fs.existsSync(LEXER)) {
        // L'extension se publie seule ; hors du dépôt, il n'y a rien à comparer.
        console.log('    (lexer.py absent — contrôle hors dépôt, ignoré)');
        return;
    }
    const source = fs.readFileSync(LEXER, 'utf8');
    const block = source.split('KEYWORDS')[1] || '';
    const keywords = new Set(
        (block.split(/\n\s*\n/)[0].match(/"([A-Z][A-Z_0-9]+)"/g) || [])
            .map(w => w.slice(1, -1)));
    assert.ok(keywords.size > 100, `lexeur illisible : ${keywords.size} mots`);

    // Un mot rejeté par le linter alors qu'il est du langage est le défaut.
    const rejected = [...keywords].filter(
        kw => errors(`GOAL g { MAINTAIN x == ${kw} }`).length > 0);
    assert.deepStrictEqual(
        rejected, [],
        `mots-clés du langage signalés comme fautes : ${rejected.join(', ')}`);
});

console.log(`\n${run} contrôles passés`);
