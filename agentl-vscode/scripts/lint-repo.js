#!/usr/bin/env node
// Exécute les contrôles de style de l'extension sur des fichiers .agent, hors
// VS Code. C'est le seul moyen de vérifier qu'ils ne crient pas sur les
// fichiers livrés : un linter qui produit du bruit sur le dépôt de référence
// est un linter que personne ne gardera activé.
//
//     node scripts/lint-repo.js ../examples/*.agent ../bench/tasks/*.agent
//
// Sortie non nulle s'il reste une **erreur** (les avertissements de convention
// ne font pas échouer).
const fs = require('fs');
const path = require('path');
const Module = require('module');

// `extension.js` importe `vscode`, indisponible hors de l'éditeur : on le
// remplace par un objet vide, les fonctions exercées ici n'y touchant pas.
const originalLoad = Module._load;
Module._load = function (request, ...rest) {
    if (request === 'vscode') { return {}; }
    return originalLoad.call(this, request, ...rest);
};

const { collectStyleIssues } = require(path.join(__dirname, '..', 'out', 'extension.js'));

const files = process.argv.slice(2);
if (files.length === 0) {
    console.error('usage : node scripts/lint-repo.js <fichier.agent>...');
    process.exit(2);
}

let errors = 0;
let warnings = 0;
for (const file of files) {
    for (const issue of collectStyleIssues(fs.readFileSync(file, 'utf8'))) {
        const where = `${path.basename(file)}:${issue.line + 1}:${issue.start + 1}`;
        console.log(`${issue.severity === 'error' ? '✗' : '!'} ${where}  ${issue.message}`);
        if (issue.severity === 'error') { errors++; } else { warnings++; }
    }
}

console.log(`\n${files.length} fichier(s) · ${errors} erreur(s) · ${warnings} avertissement(s)`);
process.exit(errors ? 1 : 0);
