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


@pytest.mark.parametrize("journal_path", JOURNALS,
                         ids=[f"{p.parent.name}/{p.stem}" for p in JOURNALS])
def test_published_journal_replays_identically(journal_path: Path) -> None:
    source = journal_path.with_suffix(".agent")
    assert source.is_file(), f"source absente à côté de {journal_path.name}"
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = agentl_main(["replay", str(journal_path), "--source",
                            str(source), "--quiet"])
    assert code == 0, (f"{journal_path.parent.name}/{journal_path.stem} ne se "
                       f"rejoue plus :\n{err.getvalue()[-2000:]}")
    assert "rejeu conforme" in out.getvalue()


def test_the_golden_set_is_not_empty() -> None:
    # Un filet vide passerait tout : l'absence de journaux est une panne.
    assert len(JOURNALS) >= 10


@pytest.mark.parametrize("journal_path", JOURNALS[:3],
                         ids=[p.stem for p in JOURNALS[:3]])
def test_golden_journals_are_intact(journal_path: Path) -> None:
    # Chargement strict : la chaîne de hachage doit tenir.
    journal = Journal.load(journal_path)
    assert journal.chain_report is not None and journal.chain_report.ok
