"""Compatibilité entre versions : un journal publié doit se rejouer à l'octet.

Les journaux de `tests/golden/<version>/` ont été enregistrés par la version
qui nomme leur répertoire, **avant** toute modification de la suivante, avec
la source exacte du programme à côté. Ils ne se régénèrent jamais : un journal
doré qui cesse de se rejouer signale qu'une version a changé la décision d'un
programme publié — ce qui peut être voulu, mais doit alors se dire dans le
CHANGELOG et s'accompagner d'un nouveau répertoire, pas d'une réécriture.

C'est le filet de l'extraction du noyau (v1.9) : autorisation, permis et
exécuteur ont quitté `runtime.py` sans qu'une seule trace de ces programmes
ne bouge d'un caractère.

Rejeu hors ligne : aucun hôte d'exemple n'est importé, aucun effet n'a lieu.
"""
from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from agentl.cli import main as agentl_main
from agentl.replay import Journal

GOLDEN = Path(__file__).resolve().parent / "golden"
JOURNALS = sorted(GOLDEN.glob("*/*.json"))

#: Ruptures **voulues**, annoncées dans le CHANGELOG. Le journal ancien ne se
#: rejoue plus, et il doit diverger exactement là où la correction agit — ni
#: avant, ni ailleurs. Le comportement corrigé a son propre journal dans le
#: répertoire de la version qui l'a introduit.
#:
#: 1.12.0 — un VERIFY en échec laissait le runtime muet : en v1.8.2, les
#: messages `isolate_and_clean` des ticks 2 à 4 étaient avalés sans trace. Ils
#: relancent désormais le nettoyage, et l'approbation est redemandée.
INTENDED_BREAKS = {
    "v1.8.2/complex_soc": (
        "v1.12.0",
        "divergence au franchissement #18 : enregistré "
        "read:sandbox.alert_triggered, rejoué "
        "approve:quarantine_folder(folder=suspicious_logs)"),
}


def _replay(journal_path: Path):
    source = journal_path.with_suffix(".agent")
    assert source.is_file(), f"source absente à côté de {journal_path.name}"
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = agentl_main(["replay", str(journal_path), "--source",
                            str(source), "--quiet"])
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("journal_path", JOURNALS,
                         ids=[f"{p.parent.name}/{p.stem}" for p in JOURNALS])
def test_published_journal_replays_identically(journal_path: Path) -> None:
    name = f"{journal_path.parent.name}/{journal_path.stem}"
    code, out, err = _replay(journal_path)
    if name in INTENDED_BREAKS:
        successor, divergence = INTENDED_BREAKS[name]
        assert code != 0, f"{name} se rejoue encore : la rupture annoncée n'a pas lieu"
        assert divergence in err, f"{name} diverge ailleurs qu'annoncé :\n{err[-2000:]}"
        assert (GOLDEN / successor / f"{journal_path.stem}.json").is_file(), \
            f"rupture de {name} sans journal successeur dans {successor}/"
        return
    assert code == 0, f"{name} ne se rejoue plus :\n{err[-2000:]}"
    assert "rejeu conforme" in out


def test_the_golden_set_is_not_empty() -> None:
    # Un filet vide passerait tout : l'absence de journaux est une panne.
    assert len(JOURNALS) >= 10


@pytest.mark.parametrize("journal_path", JOURNALS[:3],
                         ids=[p.stem for p in JOURNALS[:3]])
def test_golden_journals_are_intact(journal_path: Path) -> None:
    # Chargement strict : la chaîne de hachage doit tenir.
    journal = Journal.load(journal_path)
    assert journal.chain_report is not None and journal.chain_report.ok
