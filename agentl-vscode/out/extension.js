"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.maskNonCode = maskNonCode;
exports.isLowerCaseName = isLowerCaseName;
exports.toSnakeCase = toSnakeCase;
exports.collectStyleIssues = collectStyleIssues;
exports.deactivate = deactivate;
const vscode = require("vscode");
const child_process_1 = require("child_process");
const path = require("path");
let diagnosticCollection;
// --- Liste officielle des mots réservés en MAJUSCULES dans AGENT-L ---
//
// Elle doit rester le miroir exact de `agentl/lexer.py` (KEYWORDS) augmenté de
// l'échelle ordinale de `agentl/core.py` (ORDINAL_SCALE) et des quelques
// constantes que le langage accepte nues. Un mot-clé manquant ici serait
// signalé comme une faute alors qu'il est valide : la liste est donc un
// contrat, pas une commodité.
//
// Le contrat s'était rompu en silence : la liste était restée à v1.5 pendant
// que le langage passait en v1.7, et l'extension signalait comme fautes de
// syntaxe INTERNAL, ATTESTS, DEADLINE et les autres — 27 erreurs sur les
// fichiers du dépôt lui-même. Un miroir tenu à la main dérive ; celui-ci est
// désormais tenu par `TestTheEditorMirrorsTheLexer`
// (tests/test_frontend_aaa.py), qui échoue à la première divergence, dans un
// sens comme dans l'autre.
const RESERVED_KEYWORDS = new Set([
    // Structure & Blocs
    "AGENT", "VERSION", "DESCRIPTION", "GOAL", "MAINTAIN", "ACHIEVE", "TARGET", "WEIGHT",
    "BELIEF", "CONFIDENCE", "SOURCE", "UPDATED", "OBSERVE", "EVERY", "WHEN",
    "MEMORY", "SHORT_TERM", "LONG_TERM", "KNOWLEDGE", "SHARED", "WRITE", "STORE", "INTO",
    "TOOL", "INPUT", "OUTPUT", "SIDE_EFFECT", "RISK",
    "POLICY", "NEVER", "ALLOW", "DENY", "REQUIRE", "APPROVAL", "FOR", "DEFAULT",
    "PLAN", "STEP", "IF", "THEN", "ELSE", "SET", "LOOP", "UNTIL", "MAX", "FOREACH",
    // v1.4 : critères d'acceptation
    "SCENARIO", "GIVEN", "WITHIN",
    "VERIFY", "CONDITION", "ON", "FAIL", "RETRY", "ROLLBACK", "ESCALATE",
    "REASON", "TASK", "USING", "PRODUCE", "ASK", "QUESTION", "TIMEOUT",
    "DELEGATE", "EXPECT", "MESSAGE", "TO", "BROADCAST", "PAYLOAD", "EVENT", "DECIDE", "RULES",
    "UPDATE_BELIEFS", "EVALUATE_GOALS", "SELECT_PLAN", "EXECUTE", "UPDATE_MEMORY", "ACT", "RECEIVE",
    "HYPOTHESIS", "PREDICT", "EVIDENCE", "PRIOR", "LIKELIHOOD", "GIVEN_NOT", "THRESHOLD", "EXPLAINS", "UPDATE_HYPOTHESES",
    "GROUP", "MAX_EVIDENCE", "IN", "FROM", "WHERE",
    "PLANNER", "ENABLE", "MAX_DEPTH", "MAX_NODES", "APPROVAL_COST", "EFFECT", "REQUIRES", "COST", "BIND", "SYNTHESIZE",
    "OUTCOME", "WITH", "UTILITY", "VALUE", "TARGET_CONFIDENCE",
    "AND", "OR", "NOT",
    // v1.5 : la donnée du modèle qui pilote une action porte sa provenance
    "ATTESTS",
    // v1.5 : `NEVER SEND` retient un chemin hors du contexte du modèle
    "SEND",
    // v1.6 : le planificateur arbitre sur le temps
    "DEADLINE", "DURATION", "TIME_WEIGHT",
    // v1.6 : logique trivalente — `ON UNKNOWN DEGRADE`
    "UNKNOWN", "DEGRADE",
    // v1.7 : postcondition qui ne porte pas sur le monde, donc indémentissable
    "INTERNAL",
    // Symboles d'échelle ordinaux et constantes autorisées en majuscules.
    // `UNSET` est le risque non tranché que produit `agentl mcp import` : le
    // fichier fraîchement importé doit s'ouvrir sans une erreur par outil.
    "NONE", "INFO", "LOW", "MODERATE", "MEDIUM", "HIGH", "SEVERE", "CRITICAL", "UNSET",
    "TRUE", "FALSE", "UNDEFINED", "RANGE", "SET"
]);
const AGENTL_DOCS = {
    "AGENT": {
        description: "Déclare une instance d'agent autonome.",
        syntax: "AGENT <Nom> {\n  VERSION \"1.2\"\n  DESCRIPTION \"...\"\n}",
        snippet: "AGENT ${1:Name} {\n\tVERSION \"1.2\"\n\tDESCRIPTION \"${2:Description}\"\n\n\tGOAL ${3:main_goal} { MAINTAIN ${4:condition} }\n\n\t$0\n}"
    },
    "GOAL": {
        description: "Objectif de l'agent (MAINTAIN ou ACHIEVE).",
        syntax: "GOAL <nom> { MAINTAIN <condition> }",
        snippet: "GOAL ${1:name} {\n\tMAINTAIN ${2:condition}\n}"
    },
    "OBSERVE": {
        description: "Déclare les capteurs surveillés par l'hôte.",
        syntax: "OBSERVE {\n  chemin.capteur\n}",
        snippet: "OBSERVE {\n\t${1:sensor_path}\n}"
    },
    "TOOL": {
        description: "Spécifie un outil exécutable par l'hôte.",
        syntax: "TOOL <nom> {\n  INPUT { arg: Symbol }\n  RISK HIGH\n  EFFECT { <effet> }\n}",
        snippet: "TOOL ${1:name} {\n\tDESCRIPTION \"${2:...}\"\n\tINPUT { ${3:arg}: Symbol }\n\tOUTPUT { ${4:status}: Symbol }\n\tRISK ${5|LOW,MEDIUM,HIGH,CRITICAL|}\n\tREQUIRES { ${6:condition} }\n\tEFFECT { ${7:path} = ${8:value} }\n\tCOST ${9:5.0}\n}"
    },
    "POLICY": {
        description: "Moteur de sécurité déterministe hors-LLM.",
        syntax: "POLICY {\n  DEFAULT DENY\n  ALLOW outil IF <cond>\n  NEVER outil WHEN <cond>\n}",
        snippet: "POLICY {\n\tDEFAULT DENY\n\tALLOW ${1:tool} IF ${2:condition}\n\tNEVER ${3:tool} WHEN ${4:condition}\n\tREQUIRE APPROVAL FOR ${5:tool}\n}"
    },
    "HYPOTHESIS": {
        description: "Modèle d'inférence bayésienne.",
        syntax: "HYPOTHESIS <nom> {\n  PRIOR 0.10\n  THRESHOLD 0.60\n}",
        snippet: "HYPOTHESIS ${1:name} {\n\tPRIOR ${2:0.10}\n\tEVIDENCE {\n\t\tGROUP ${3:group_name} {\n\t\t\t${4:sensor} > ${5:0} LIKELIHOOD ${6:0.90} GIVEN_NOT ${7:0.05}\n\t\t}\n\t}\n\tTHRESHOLD ${8:0.60}\n\tEXPLAINS ${9:derived_belief}\n}"
    },
    "PLAN": {
        description: "Séquence d'étapes (STEP) exécutée sous garde WHEN.",
        syntax: "PLAN <nom> WHEN <cond> AND NOT (done == yes) {\n  STEP s1 { ... }\n}",
        snippet: "PLAN ${1:name} WHEN ${2:condition} AND NOT (${3:processed} == yes) {\n\tSTEP ${4:s1} {\n\t\t${0}\n\t}\n}"
    },
    "REASON": {
        description: "Appel au LLM avec sorties typées et bornées par PRODUCE.",
        syntax: "REASON {\n  TASK \"...\"\n  USING { chemin.texte }\n  PRODUCE { champ: Symbol IN [opt1, opt2, unknown] }\n}",
        snippet: "REASON {\n\tTASK \"${1:task_description}\"\n\tUSING { ${2:raw_text_sensor} }\n\tPRODUCE {\n\t\t${3:field}: Symbol IN [${4:option1}, ${5:option2}, unknown]\n\t}\n}"
    },
    "VERIFY": {
        description: "Re-perçoit le monde réel post-action pour valider le résultat.",
        syntax: "VERIFY \"libellé\" {\n  CONDITION <condition>\n}",
        snippet: "VERIFY \"${1:label}\" {\n\tCONDITION ${2:condition}\n}"
    },
    "DELEGATE": {
        description: "Sous-traite de façon synchrone dans un STEP à un sous-agent.",
        syntax: "DELEGATE <sous_agent> {\n  TASK \"...\"\n  EXPECT { resultat }\n}",
        snippet: "DELEGATE ${1:subagent_name} {\n\tTASK \"${2:task_description}\"\n\tINPUT { ${3:input_arg} }\n\tEXPECT { ${4:expected_output} }\n}"
    },
    "MESSAGE": {
        description: "Émet un message asynchrone vers un autre agent.",
        syntax: "MESSAGE <nom> { TO <Agent> PAYLOAD { k = v } }",
        snippet: "MESSAGE ${1:msg_name} {\n\tTO ${2:TargetAgent}\n\tPAYLOAD { ${3:key} = ${4:value} }\n}"
    }
};
function activate(context) {
    diagnosticCollection = vscode.languages.createDiagnosticCollection('agentl');
    context.subscriptions.push(diagnosticCollection);
    vscode.workspace.onDidOpenTextDocument(runDiagnostics, null, context.subscriptions);
    vscode.workspace.onDidSaveTextDocument(runDiagnostics, null, context.subscriptions);
    vscode.workspace.onDidChangeTextDocument((e) => runDiagnostics(e.document), null, context.subscriptions);
    if (vscode.window.activeTextEditor) {
        runDiagnostics(vscode.window.activeTextEditor.document);
    }
    // Autocomplétion
    const completionProvider = vscode.languages.registerCompletionItemProvider('agentl', {
        provideCompletionItems(document, position) {
            const completions = [];
            for (const [kw, doc] of Object.entries(AGENTL_DOCS)) {
                const item = new vscode.CompletionItem(kw, vscode.CompletionItemKind.Keyword);
                item.detail = `Mot-clé réservé AGENT-L: ${kw}`;
                item.documentation = new vscode.MarkdownString(`${doc.description}\n\n\`\`\`agentl\n${doc.syntax}\n\`\`\``);
                if (doc.snippet) {
                    const snippetItem = new vscode.CompletionItem(`${kw} (snippet)`, vscode.CompletionItemKind.Snippet);
                    snippetItem.insertText = new vscode.SnippetString(doc.snippet);
                    snippetItem.detail = `Générer bloc ${kw}`;
                    completions.push(snippetItem);
                }
                completions.push(item);
            }
            return completions;
        }
    });
    context.subscriptions.push(completionProvider);
    // Tooltips au survol
    const hoverProvider = vscode.languages.registerHoverProvider('agentl', {
        provideHover(document, position) {
            const range = document.getWordRangeAtPosition(position);
            if (!range) {
                return undefined;
            }
            const word = document.getText(range);
            // Vérifier si le mot est en majuscules
            if (word === word.toUpperCase() && word.length > 1) {
                const doc = AGENTL_DOCS[word];
                if (doc) {
                    const markdown = new vscode.MarkdownString();
                    markdown.appendMarkdown(`### **${word}** *(Mot-clé AGENT-L)*\n\n`);
                    markdown.appendMarkdown(`${doc.description}\n\n`);
                    markdown.appendCodeblock(doc.syntax, 'agentl');
                    return new vscode.Hover(markdown);
                }
                else if (!RESERVED_KEYWORDS.has(word)) {
                    const markdown = new vscode.MarkdownString();
                    markdown.appendMarkdown(`⚠️ **Avertissement de Synaxe AGENT-L**\n\n`);
                    markdown.appendMarkdown(`Le mot \`${word}\` est en majuscules mais n'est pas un mot-clé réservé du langage.\n\nLes majuscules sont réservées aux mots-clés d'AGENT-L. Pour les variables ou identifiants métier, utilisez des minuscules.`);
                    return new vscode.Hover(markdown);
                }
            }
            return undefined;
        }
    });
    context.subscriptions.push(hoverProvider);
}
function runDiagnostics(document) {
    if (document.languageId !== 'agentl') {
        return;
    }
    const diagnostics = [];
    // 1. Contrôle strict des mots en majuscules (Local VS Code Linter)
    checkReservedKeywords(document, diagnostics);
    // 2. Exécution du backend Python ('agentl check' et 'agentl verify')
    const config = vscode.workspace.getConfiguration('agentl');
    const pythonPath = config.get('pythonPath', 'python3');
    const enableVerify = config.get('enableVerify', true);
    const filePath = document.fileName;
    const cwd = path.dirname(filePath);
    const checkCmd = `${pythonPath} -m agentl check "${filePath}"`;
    (0, child_process_1.exec)(checkCmd, { cwd }, (error, stdout, stderr) => {
        const output = stdout + '\n' + stderr;
        parseDiagnosticsOutput(output, document, diagnostics);
        if (enableVerify && !output.includes('erreur E')) {
            const verifyCmd = `${pythonPath} -m agentl verify "${filePath}"`;
            (0, child_process_1.exec)(verifyCmd, { cwd }, (vErr, vStdout, vStderr) => {
                const vOutput = vStdout + '\n' + vStderr;
                parseDiagnosticsOutput(vOutput, document, diagnostics);
                diagnosticCollection.set(document.uri, diagnostics);
            });
        }
        else {
            diagnosticCollection.set(document.uri, diagnostics);
        }
    });
}
/**
 * Neutralise ce qui n'est pas du code : commentaires de ligne (`//`, `#`),
 * commentaires de bloc, et **contenu des chaînes**. Les caractères sont
 * remplacés par des espaces, pas
 * supprimés : les positions restent exactes, donc les soulignements aussi.
 *
 * Sans cela, `DESCRIPTION "Analyste SOC niveau 1"` faisait signaler `SOC`
 * comme un mot-clé inconnu. Une prose française contient forcément des sigles ;
 * un linter qui les refuse dans les libellés est inutilisable.
 */
function maskNonCode(lines) {
    const out = [];
    let inBlockComment = false;
    // Une chaîne AGENT-L peut courir sur plusieurs lignes — le lexeur l'admet,
    // et les `TASK "…"` du dépôt en usent largement. L'état doit donc survivre
    // au saut de ligne, faute de quoi la prose d'une tâche multiligne est
    // relue comme du code (« NÉGATIVE », « RH »… signalés à tort).
    let inString = false;
    for (const raw of lines) {
        const chars = raw.split('');
        let i = 0;
        while (i < chars.length) {
            const c = chars[i];
            const next = chars[i + 1];
            if (inBlockComment) {
                const closing = c === '*' && next === '/';
                chars[i] = ' ';
                if (closing) {
                    chars[i + 1] = ' ';
                    i += 2;
                    inBlockComment = false;
                    continue;
                }
                i += 1;
                continue;
            }
            if (inString) {
                // Une échappée `\"` ne ferme pas la chaîne.
                if (c === '\\' && next !== undefined) {
                    chars[i] = ' ';
                    chars[i + 1] = ' ';
                    i += 2;
                    continue;
                }
                chars[i] = ' ';
                if (c === '"') {
                    inString = false;
                }
                i += 1;
                continue;
            }
            if (c === '"') {
                chars[i] = ' ';
                inString = true;
                i += 1;
                continue;
            }
            if (c === '/' && next === '*') {
                chars[i] = ' ';
                chars[i + 1] = ' ';
                i += 2;
                inBlockComment = true;
                continue;
            }
            if ((c === '/' && next === '/') || c === '#') {
                for (let j = i; j < chars.length; j++) {
                    chars[j] = ' ';
                }
                break;
            }
            i += 1;
        }
        out.push(chars.join(''));
    }
    return out;
}
/** Un nom d'agent contient-il une majuscule ? */
function isLowerCaseName(name) {
    return name === name.toLowerCase();
}
/**
 * Nom conforme suggéré. `SOC_ANALYST` → `soc_analyst`, mais aussi
 * `SecurityInvestigator` → `security_investigator` : proposer
 * `securityinvestigator` serait proposer pire que le problème.
 */
function toSnakeCase(name) {
    // Déjà en SCREAMING_SNAKE : rien à découper. Sans ce cas, `K8S_HEALER`
    // devenait `k8_s_healer` — la frontière chiffre/majuscule n'est une
    // frontière de mot qu'en CamelCase.
    if (name === name.toUpperCase()) {
        return name.toLowerCase();
    }
    return name
        .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
        .replace(/([A-Z]+)([A-Z][a-z])/g, '$1_$2')
        .toLowerCase();
}
/**
 * Contrôles de style, **sans dépendance à l'API VS Code** — ils sont ainsi
 * exécutables sur le dépôt entier par un simple script Node, ce qui est le
 * seul moyen honnête de vérifier qu'ils ne crient pas sur les fichiers
 * livrés.
 */
function collectStyleIssues(source) {
    const issues = [];
    const lines = maskNonCode(source.split('\n'));
    for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
        const line = lines[lineIdx];
        // Les noms d'agents sont repérés d'abord : ils relèvent du contrôle 2
        // et ne doivent pas être signalés deux fois. Un `AGENT SOC_ANALYST`
        // souligné par deux diagnostics concurrents se lit mal.
        const declared = [];
        const declaration = /\bAGENT\s+([A-Za-z_][A-Za-z0-9_]*)/g;
        let decl;
        while ((decl = declaration.exec(line)) !== null) {
            const name = decl[1];
            declared.push([decl.index + decl[0].length - name.length, name.length, name]);
        }
        const isAgentName = (start, length) => declared.some(([s, l]) => s === start && l === length);
        // 1. Majuscules réservées aux mots-clés (hors chaînes et commentaires)
        //    Les lettres accentuées comptent comme des lettres : sans elles,
        //    « NÉGATIF » se lisait « GATIF » et le message désignait un mot
        //    qui n'existe pas dans le fichier.
        const regex = /(?<![A-Za-zÀ-ÖØ-öø-ÿ0-9_])[A-ZÀ-ÖØ-Þ_][A-ZÀ-ÖØ-Þ0-9_]+(?![A-Za-zÀ-ÖØ-öø-ÿ0-9_])/g;
        let match;
        while ((match = regex.exec(line)) !== null) {
            const word = match[0];
            if (RESERVED_KEYWORDS.has(word)) {
                continue;
            }
            if (isAgentName(match.index, word.length)) {
                continue;
            }
            issues.push({
                line: lineIdx, start: match.index, length: word.length,
                severity: 'error',
                message: `[Syntaxe] '${word}' est un mot en majuscules non reconnu. `
                    + `Les majuscules sont réservées aux mots-clés AGENT-L — hors chaînes `
                    + `entre guillemets, où tout est permis. S'il s'agit d'un identifiant, `
                    + `utilisez des minuscules.`
            });
        }
        // 2. Nom d'agent en minuscules
        //    `AGENT SOC_ANALYST` se lit comme un mot-clé au premier coup d'œil
        //    alors que c'en est justement un qui n'existe pas. Avertissement et
        //    non erreur : le parseur l'accepte, c'est la convention qui tranche.
        for (const [start, , name] of declared) {
            if (isLowerCaseName(name)) {
                continue;
            }
            issues.push({
                line: lineIdx, start, length: name.length, severity: 'warning',
                message: `[Convention] nom d'agent '${name}' : les noms d'agents s'écrivent `
                    + `en minuscules (\`${toSnakeCase(name)}\`). Les MAJUSCULES sont `
                    + `réservées aux mots-clés du langage.`
            });
        }
    }
    return issues;
}
// Pont vers VS Code : les contrôles ci-dessus, rendus en diagnostics.
function checkReservedKeywords(document, diagnostics) {
    for (const issue of collectStyleIssues(document.getText())) {
        const range = new vscode.Range(new vscode.Position(issue.line, issue.start), new vscode.Position(issue.line, issue.start + issue.length));
        diagnostics.push(new vscode.Diagnostic(range, issue.message, issue.severity === 'error' ? vscode.DiagnosticSeverity.Error
            : vscode.DiagnosticSeverity.Warning));
    }
}
function parseDiagnosticsOutput(output, document, diagnostics) {
    const lines = output.split('\n');
    for (const line of lines) {
        const checkMatch = line.match(/(erreur|avert\.)\s+([EW]\d{3})\s+ligne\s+(\d+)\s+(.*)/i);
        if (checkMatch) {
            const isError = checkMatch[1].toLowerCase().includes('erreur');
            const code = checkMatch[2];
            const lineNum = Math.max(0, parseInt(checkMatch[3], 10) - 1);
            const message = `[${code}] ${checkMatch[4].trim()}`;
            const severity = isError ? vscode.DiagnosticSeverity.Error : vscode.DiagnosticSeverity.Warning;
            const range = getLineRange(document, lineNum);
            diagnostics.push(new vscode.Diagnostic(range, message, severity));
            continue;
        }
        const verifyMatch = line.match(/\s*([✗!·])\s+(V\d{3})\s+l\.(\d+)\s+(.*)/);
        if (verifyMatch) {
            const symbol = verifyMatch[1];
            const code = verifyMatch[2];
            const lineNum = Math.max(0, parseInt(verifyMatch[3], 10) - 1);
            const message = `[${code}] ${verifyMatch[4].trim()}`;
            let severity = vscode.DiagnosticSeverity.Information;
            if (symbol === '✗') {
                severity = vscode.DiagnosticSeverity.Error;
            }
            else if (symbol === '!') {
                severity = vscode.DiagnosticSeverity.Warning;
            }
            const range = getLineRange(document, lineNum);
            diagnostics.push(new vscode.Diagnostic(range, message, severity));
            continue;
        }
    }
}
function getLineRange(document, lineNum) {
    if (lineNum < document.lineCount) {
        const lineText = document.lineAt(lineNum).text;
        return new vscode.Range(lineNum, 0, lineNum, lineText.length);
    }
    return new vscode.Range(0, 0, 0, 0);
}
function deactivate() { }
//# sourceMappingURL=extension.js.map