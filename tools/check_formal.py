"""Vérification formelle du protocole du noyau (TLA+ / TLC).

Exécuter : python3 tools/check_formal.py [--jar tla2tools.jar]

Trois temps, et le troisième est celui qui compte :

1. télécharger TLC 1.8.0 (release officielle `tlaplus`) si besoin, et
   **refuser** un binaire dont l'empreinte SHA-256 ne correspond pas ;
2. vérifier `docs/formal/AgentLKernel.tla` : tous les états atteignables,
   toutes les invariants ;
3. vérifier que le modèle **attrape** des défauts plausibles : chaque mutant
   ci-dessous injecte un bogue réaliste, et TLC doit trouver la violation
   attendue. Un modèle qui ne trouve jamais rien ne prouve rien.

Il faut Java 11 ou plus récent dans le PATH (ou la variable JAVA).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "formal" / "AgentLKernel.tla"
CFG = ROOT / "docs" / "formal" / "AgentLKernel.cfg"
TLC_URL = ("https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/"
           "tla2tools.jar")
TLC_SHA256 = "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"

#: (nom, texte d'origine, texte muté, invariant qui doit tomber)
MUTANTS = [
    ("relance aveugle d'une action indéterminée",
     '''             \\/ /\\ mode = "plain"                      \\* indéterminé
                /\\ wal' = Append(wal, Rec("result", i, FALSE, "in_doubt"))
                /\\ Serve(i) /\\ UNCHANGED <<count, effArgs, lapsedUse>>''',
     '''             \\/ /\\ mode = "plain"
                /\\ Go /\\ UNCHANGED <<wal, count, effArgs, lapsedUse>>''',
     "AtMostOnce"),
    ("hôte qui ne contrôle pas les arguments du permis",
     "       IF sent # permit.args", "       IF FALSE", "OnlyJudgedArgs"),
    ("résultat journalisé traité comme une action en suspens",
     '''       \\/ /\\ ~Has("result", i) /\\ Has("intent", i)    \\* en suspens''',
     '''       \\/ /\\ Has("intent", i)''',
     "OneResult"),
    ("reprise qui ne sait pas servir un résultat journalisé (blocage)",
     '''       \\/ /\\ Has("result", i)                  \\* journalisé : servi
          /\\ Serve(i) /\\ UNCHANGED <<wal, count, effArgs, lapsedUse>>
''', "", "Termination"),
    ("approbation refusée qui émet quand même un permis",
     "               /\\ IF yes THEN Grant(i) ELSE Refuse(i)",
     "               /\\ Grant(i)", "NoUnauthorizedEffect"),
    # 1.15 — le monde change pendant le crash.
    ("intention en suspens relancée sans rejuger le monde d'aujourd'hui",
     "Rejudge(i) == IF facts[i] THEN {FALSE} ELSE IF Asked(i) THEN BOOLEAN "
     "ELSE {TRUE}",
     "Rejudge(i) == IF Asked(i) THEN BOOLEAN ELSE {TRUE}",
     "NoEffectAgainstToday"),
    ("action nouvelle décidée sur une lecture rejouée, exécutée telle quelle",
     "          /\\ IF StaleRead(i) /\\ facts[i]       "
     "\\* décidée sur une lecture rejouée",
     "          /\\ IF FALSE", "NoEffectAgainstToday"),
    ("approbation périmée reprise sans reconsulter l'humain",
     'Asked(i)   == policy[i] = "APPROVE" /\\ lapsed /\\ ~facts[i]',
     "Asked(i)   == FALSE", "NoLapsedApproval"),
    ("approbation de queue réutilisée après le crash",
     '''  /\\ wal' = IF wal # << >> /\\ wal[Len(wal)].type = "approve"
            THEN SubSeq(wal, 1, Len(wal) - 1) ELSE wal''',
     "  /\\ wal' = wal", "NoLapsedApproval"),
    ("révocation d'une action que le réconciliateur dit faite",
     '             \\/ /\\ (mode = "idempotent" \\/ (mode = "reconcile" '
     '/\\ count[i] = 0))',
     '             \\/ /\\ mode \\in {"idempotent", "reconcile"}',
     "RevokedMeansNotExecuted"),
]


def mutant_cfg(invariant: str) -> str:
    """La configuration du modèle, réduite à la propriété que vise le mutant.

    Avec toutes les propriétés, TLC rend le **premier** contre-exemple trouvé
    en largeur — souvent celui d'une autre propriété, plus court (une relance
    aveugle viole `NoEffectAgainstToday` avant de doubler un effet). Le mutant
    serait attrapé, mais pas par la propriété qu'il doit démontrer utile.
    """
    head = CFG.read_text(encoding="utf-8").split("INVARIANTS")[0]
    section = ("PROPERTIES" if invariant == "Termination" else "INVARIANTS")
    return f"{head}{section}\n    {invariant}\nCHECK_DEADLOCK FALSE\n"


def fetch(jar: Path) -> Path:
    if not jar.exists():
        print(f"→ téléchargement de TLC : {TLC_URL}")
        with urllib.request.urlopen(TLC_URL, timeout=120) as resp:
            jar.write_bytes(resp.read())
    digest = hashlib.sha256(jar.read_bytes()).hexdigest()
    if digest != TLC_SHA256:
        raise SystemExit(f"✗ empreinte de {jar} inattendue : {digest} — "
                         f"binaire refusé")
    return jar


def tlc(java: str, jar: Path, spec: Path, cfg: Path, workdir: Path) -> str:
    shutil.copy(spec, workdir / spec.name)
    shutil.copy(cfg, workdir / cfg.name)
    result = subprocess.run(
        [java, "-XX:+UseParallelGC", "-cp", str(jar), "tlc2.TLC",
         "-workers", "auto", "-metadir", str(workdir / "states"),
         "-config", cfg.name, spec.name],
        cwd=workdir, capture_output=True, text=True, timeout=1800)
    return result.stdout + result.stderr


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jar", default=None,
                        help="tla2tools.jar déjà présent (sinon téléchargé)")
    args = parser.parse_args(argv)
    java = os.environ.get("JAVA") or shutil.which("java")
    if not java:
        print("✗ Java introuvable (PATH ou variable JAVA)", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        jar = fetch(Path(args.jar) if args.jar else tmp / "tla2tools.jar")

        out = tlc(java, jar, SPEC, CFG, tmp / "spec")  \
            if (tmp / "spec").mkdir() is None else ""
        states = [l for l in out.splitlines() if "distinct states found" in l]
        if "No error has been found" not in out:
            print(out[-4000:])
            print("✗ le modèle du noyau viole un invariant", file=sys.stderr)
            return 1
        print(f"✓ modèle vérifié (sûreté et vivacité) — "
              f"{states[-1].strip() if states else ''}")

        source = SPEC.read_text(encoding="utf-8")
        failed = 0
        for index, (name, original, mutated, invariant) in enumerate(MUTANTS):
            if original not in source:
                print(f"✗ mutant « {name} » : le texte visé a changé dans la "
                      f"spécification — mettre à jour tools/check_formal.py")
                failed += 1
                continue
            work = tmp / f"m{index}"
            work.mkdir()
            spec = work / "src" / SPEC.name
            spec.parent.mkdir()
            spec.write_text(source.replace(original, mutated), encoding="utf-8")
            cfg = work / "src" / CFG.name
            cfg.write_text(mutant_cfg(invariant), encoding="utf-8")
            out = tlc(java, jar, spec, cfg, work)
            # TLC 1.7.x ne nomme pas la propriété temporelle violée ; le
            # fichier .cfg n'en déclare qu'une (Termination), donc la formule
            # générique lui est attribuable sans ambiguïté.
            if (f"Invariant {invariant} is violated" in out
                    or f"Temporal property {invariant} was violated" in out
                    or (invariant == "Termination"
                        and "Temporal properties were violated" in out)):
                print(f"✓ mutant attrapé — {name} ({invariant})")
            else:
                print(f"✗ mutant NON attrapé — {name} : {invariant} attendu")
                failed += 1
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
