"""Interface en ligne de commande.

    python -m agentl check examples/soc_analyst.agent
    python -m agentl ast   examples/soc_analyst.agent
    python -m agentl run   examples/soc_analyst.agent
    python -m agentl studio examples/soc_analyst.agent --open
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict

from .analyzer import Analyzer
from .core import AgentLError
from .host import Host
from .llm import MockLLM
from .parser import parse_file
from .runtime import Runtime


def _load_host(path: str):
    spec = importlib.util.spec_from_file_location("agentl_host", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Enregistrer le module *avant* de l'exécuter : `@dataclass` remonte à
    # `sys.modules[cls.__module__]` pour résoudre ses annotations, et un hôte
    # qui déclare une dataclass échouait au chargement sur un `AttributeError`
    # obscur venu de la bibliothèque standard.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if hasattr(module, "build"):
        built = module.build()
        if isinstance(built, tuple):
            return (list(built) + [None, None])[:3]
        return built, None, None
    return getattr(module, "host", Host()), getattr(module, "llm", None), None


def host_path_for(agent_file: str) -> Path:
    """Chemin d'hôte **imposé** par la norme de nommage : `X.agent` → `X.py`.

    L'hôte n'est pas un paramètre : c'est le pendant du programme. Le rendre
    choisissable laissait passer un run exécuté avec l'hôte d'un autre agent,
    dont on ne s'aperçoit qu'en lisant des perceptions incohérentes.
    """
    return Path(agent_file).with_suffix(".py")


def _host_for(agent, value, label: str):
    """Extrait d'un dict par agent la valeur qui revient à `agent`.

    `plan` et `infer` inspectent UN agent ; un programme multi-agents fournit
    un hôte (et parfois un LLM) par agent. Une clé absente n'est pas une
    erreur : l'inspection reste possible en monde vide, et on le dit.
    """
    if not isinstance(value, dict):
        return value
    if agent.name in value:
        return value[agent.name]
    print(f"! aucun {label} déclaré pour « {agent.name} » "
          f"({', '.join(sorted(map(str, value))) or 'aucun'}) : "
          f"inspection en monde vide", file=sys.stderr)
    return None


def _resolve_host(args, required: bool = True):
    """(host, llm) depuis l'hôte normatif du programme, sinon défauts.

    Un module hôte introuvable, illisible ou fautif ne doit jamais remonter un
    traceback nu : on le convertit en AgentLError avec un message propre, que
    l'appelant rend en erreur CLI (code de sortie non nul).

    `args.host` reste accepté en interne (le studio l'utilise pour désigner un
    chemin déjà validé) mais n'est plus exposé en ligne de commande.
    """
    target = getattr(args, "host", None)
    if not target:
        target = str(host_path_for(args.file)) if getattr(args, "file", None) else None
    if not target:
        return Host(), MockLLM()
    if not Path(target).is_file():
        if not required:
            # `plan` et `infer` n'agissent pas sur le monde : ils restent
            # utilisables sur un programme dépourvu d'hôte, en simulation.
            print(f"! hôte absent ({target}) : inspection en monde vide",
                  file=sys.stderr)
            return Host(), MockLLM()
        raise AgentLError(
            f"hôte introuvable : {target}. La norme de nommage impose que "
            f"l'hôte porte le nom du programme, à l'extension près "
            f"(X.agent → X.py) — pas d'hôte, pas d'exécution.")
    try:
        host, llm, _ = _load_host(target)
    except AgentLError:
        raise
    except FileNotFoundError:
        raise AgentLError(f"module hôte introuvable : {target}")
    except Exception as exc:                          # exécution du module hôte
        raise AgentLError(f"échec du chargement de l'hôte {target} : {exc}")
    return host, llm or MockLLM()


def _dump(node: Any, indent: int = 0) -> str:
    from dataclasses import fields, is_dataclass

    pad = "  " * indent
    if is_dataclass(node):
        head = f"{pad}{type(node).__name__}"
        lines = [head]
        for f in fields(node):
            if f.name == "line":
                continue
            value = getattr(node, f.name)
            if value in (None, [], {}, "", 0, 0.0):
                continue
            if is_dataclass(value) or isinstance(value, (list, dict)):
                lines.append(f"{pad}  {f.name}:")
                lines.append(_dump(value, indent + 2))
            else:
                lines.append(f"{pad}  {f.name} = {value!r}")
        return "\n".join(l for l in lines if l.strip())
    if isinstance(node, list):
        return "\n".join(_dump(v, indent) for v in node)
    if isinstance(node, dict):
        return "\n".join(f"{pad}{k}: {v!r}" for k, v in node.items())
    return f"{pad}{node!r}"


def _run_society(program, host, llm, args) -> int:
    """`run` sur un programme multi-agents : ordonnance une `Society`.

    Accepte les deux conventions d'hôte en usage dans les exemples :
    `build()` peut rendre un dictionnaire d'hôtes par agent avec un LLM par
    agent, ou avec un unique LLM partagé.
    """
    from .society import Society

    names = [a.name for a in program.agents]
    if isinstance(host, dict):
        hosts = {}
        for name, value in host.items():
            if not all(callable(getattr(value, m, None))
                       for m in ("read", "invoke", "ask", "approve", "drain")):
                raise AgentLError(
                    f"l'hôte de l'agent « {name} » n'est pas un Host "
                    f"(reçu {type(value).__name__})")
            hosts[str(name)] = value
        inconnus = sorted(set(hosts) - set(names))
        if inconnus:
            raise AgentLError("hôte fourni pour des agents absents du "
                              "programme : " + ", ".join(inconnus))
    else:
        hosts = {name: host for name in names}
    llms = ({str(k): v for k, v in llm.items()} if isinstance(llm, dict)
            else {name: (llm or MockLLM()) for name in names})

    try:
        journal = _new_journal(args, program)
    except AgentLError as exc:
        print(f"✗ clé de signature inutilisable : {exc}", file=sys.stderr)
        return 2
    if journal is not None:
        from .replay import RecordingHost, RecordingLLM
        hosts = {n: RecordingHost(h, journal) for n, h in hosts.items()}
        llms = {n: RecordingLLM(l, journal) for n, l in llms.items()}

    try:
        events = _open_events(args)
    except OSError as exc:
        print(f"✗ fichier d'événements inutilisable : {exc}", file=sys.stderr)
        return 2
    society = Society(program.agents, hosts=hosts, llms=llms,
                      echo=not args.quiet)
    try:
        if events is not None:
            events.attach_society(society)
        society.run(max_ticks=args.ticks if args.ticks else 6)
    finally:
        if events is not None:
            events.close()
    if journal is not None:
        _seal_journal(journal, society.render_traces(), args)
    if args.quiet:
        print(society.render_traces())
    print("\nMESSAGES\n" + society.render_messages())
    print("\n" + society.render_shared())
    print("\n" + "  ".join(f"{k}={v}" for k, v in society.metrics.items()))
    return 0


def _refuse_if_malformed(program, command: str):
    """Refuse d'émettre un verdict sur un programme que `check` rejette.

    `agentl verify examples/broken.agent` rendait huit théorèmes **DÉMONTRÉS**
    et un code de retour nul, sur un fichier portant quatre erreurs : outil
    appelé sans être déclaré, plan inexistant, politique sur un outil inconnu,
    outil dupliqué. Les théorèmes étaient vrais *à vide* — un `NEVER` qui vise
    un outil inexistant n'a aucun site d'appel à examiner, donc rien à réfuter.
    T1 annonçait même « aucun appel écrit à la main » pendant qu'un plan
    appelait `wipe_disk()` à la main.

    Rien n'était faux, et tout était trompeur : « DÉMONTRÉ » sur un programme
    mal formé est exactement la fausse assurance que ce projet existe pour
    empêcher. La chaîne documentée met `check` en premier ; elle est désormais
    tenue, et non plus seulement recommandée.

    Le refus vit dans la CLI, pas dans `verify()` : la bibliothèque reste libre
    de vérifier ce qu'elle veut, y compris un agent construit en mémoire.
    Rend `None` si le programme est bien formé, sinon le code de retour.
    """
    from .analyzer import check_program

    errors = [d for agent in program.agents for d in Analyzer(agent).run()
              if d.severity == "error"]
    if len(program.agents) > 1:
        errors += [d for d in check_program(program) if d.severity == "error"]
    if not errors:
        return None
    for diagnostic in errors:
        print("✗ " + diagnostic.render(), file=sys.stderr)
    print(f"`{command}` refusé : le programme ne passe pas l'analyse statique. "
          f"Un verdict porté sur un programme mal formé ne veut rien dire — "
          f"corriger d'abord `agentl check`.", file=sys.stderr)
    return 1


class _EventsFile:
    """La trace en JSON Lines, écrite au fil de l'exécution (`--events`).

    Le texte de la trace est fait pour un terminal. Un outil qui voulait la
    relire devait la découper au glyphe près — `┌─ tick`, `🔧`, `╔═ agent` —
    et le moindre changement de mise en forme le cassait sans bruit. Ce
    fichier porte les mêmes événements sous leur forme d'origine : genre,
    tick, agent. Chaque ligne est vidée aussitôt écrite, pour qu'un lecteur
    puisse suivre l'exécution pendant qu'elle a lieu.

    Il se branche sur le `sink` des traces **sans remplacer** celui qu'un
    hôte a pu poser : deux journaux externes ne s'excluent pas.
    """

    def __init__(self, path: str) -> None:
        self._handle = open(path, "w", encoding="utf-8")
        self._seq = 0

    def attach(self, trace, agent: str) -> None:
        # Un runtime peut journaliser dès sa construction : ce qui précède le
        # branchement est recopié d'abord, pour que le fichier soit complet.
        for event in trace.events:
            self._write(agent, event)
        previous = trace.sink

        def sink(event) -> None:
            if previous is not None:
                try:
                    previous(event)
                except Exception:   # même règle que Trace.log
                    pass
            self._write(agent, event)

        trace.sink = sink

    def attach_society(self, society) -> None:
        for name in society.order:
            self.attach(society.runtimes[name].trace, name)
        self.attach(society.trace, "société")

    def _write(self, agent: str, event) -> None:
        self._seq += 1
        self._handle.write(json.dumps({
            "seq": self._seq, "agent": agent, "tick": event.tick,
            "kind": event.kind, "text": event.text, "detail": event.detail,
        }, ensure_ascii=False, default=str) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


def _open_events(args):
    """`--events` ouvert **avant** le premier tick, sinon `None`.

    Même raison que pour la clé de signature : un chemin inutilisable
    découvert en fin de course arrive après que l'agent a agi.
    """
    path = getattr(args, "events", None)
    return _EventsFile(path) if path else None


def _new_journal(args, program):
    """Journal d'enregistrement si `--record` est demandé, sinon `None`.

    La clé de signature est résolue **ici**, avant le premier tick, et non au
    moment de sceller. Une clé inutilisable découverte en fin de course arrive
    trop tard : l'agent a déjà agi sur le monde, et il ne reste qu'à choisir
    entre un journal non signé et un journal perdu.
    """
    if not getattr(args, "record", None):
        return None
    from .replay import Journal, sha256
    source = Path(args.file).read_text(encoding="utf-8")
    journal = Journal(meta={
        "agent": " · ".join(a.name for a in program.agents),
        "source": str(args.file),
        "source_sha256": sha256(source),
        "ticks": getattr(args, "ticks", None),
    })
    from .seal import load_signing_key
    journal.signing_key = load_signing_key(getattr(args, "sign_key", None))
    return journal


def _seal_journal(journal, trace_text: str, args) -> None:
    key = journal.signing_key
    journal.seal(trace_text, key=key)
    path = journal.save(args.record)
    print(f"\n→ journal de rejeu écrit : {path} "
          f"({len(journal.entries)} franchissements de frontière)")
    print(f"  chaîne : {journal.meta['chain_sha256']}")
    if key is None:
        print("  (non signé — passer --sign-key ou AGENTL_JOURNAL_KEY pour "
              "rattacher ce journal à une clé)")
    else:
        block = journal.meta["signature"]
        print(f"  sceau  : {block['alg']}, clé {block['key_id']}")
    if journal.lossy:
        # Une valeur non sérialisable ne casse pas l'enregistrement, mais elle
        # casse la promesse. On le dit ici plutôt qu'à la première divergence.
        print("! journal lacunaire — ces valeurs n'ont pas su franchir la "
              "sérialisation et ne se rejoueront pas fidèlement :",
              file=sys.stderr)
        for item in journal.lossy:
            print(f"    {item}", file=sys.stderr)


def _report_seal(journal, key_spec, *, require: bool) -> bool:
    """Annonce l'état du scellement. Rend `False` si le rejeu doit s'arrêter.

    Trois états, jamais confondus : chaîne intacte + sceau valide (pièce
    authentique), chaîne intacte sans sceau (pièce non contrefaite mais non
    attribuée), chaîne rompue (pièce corrompue — on s'arrête toujours).
    """
    from .seal import SealError, load_verify_key

    report = journal.chain_report
    if report is not None and not report.ok:
        # Le chargement strict a déjà refusé une altération ; il ne reste ici
        # que les journaux anciens, dont l'intégrité est indéterminée.
        print(f"! {report.render()}", file=sys.stderr)
    elif report is not None:
        print(f"✔ {report.render()}")

    try:
        key = load_verify_key(key_spec)
    except SealError as exc:
        print(f"✗ clé de vérification inutilisable : {exc}", file=sys.stderr)
        return False

    seal_report = journal.verify_seal(key)
    if seal_report.ok:
        print(f"✔ {seal_report.render()}")
        return True

    invalid = journal.signed
    stream = sys.stderr if invalid or require else sys.stdout
    print(f"{'✘' if invalid else '!'} {seal_report.render()}", file=stream)
    # Un sceau *présent et faux* est une falsification : on ne rejoue pas.
    # Un sceau *absent* n'est qu'un manque, qui n'interdit le rejeu que si
    # l'appelant a demandé la garantie.
    return not (invalid or require)


def _mcp_catalog(args):
    """(nom du serveur, catalogue) depuis une config MCP ou un catalogue brut.

    Deux sources parce que deux usages : une configuration pour parler à un
    vrai serveur, un `tools/list` capturé pour travailler hors ligne — et pour
    que l'import soit reproductible sans dépendre d'un processus tiers.
    """
    from .mcp import (MCPError, catalog_from_json, connect, load_config)

    raw = None
    try:
        raw = json.loads(Path(args.source).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MCPError(f"source illisible : {exc}") from exc

    if "mcpServers" in raw or "servers" in raw:
        servers = load_config(args.source)
        if args.server:
            servers = [s for s in servers if s.name == args.server]
            if not servers:
                raise MCPError(f"serveur `{args.server}` absent de {args.source}")
        return [(s.name, connect(s).list_tools()) for s in servers]

    # Catalogue brut : le nom du serveur n'y figure pas, il faut le donner —
    # c'est lui qui préfixe les outils, donc il fait partie du contrat.
    if not args.server:
        raise MCPError("catalogue brut : préciser --server (le nom préfixe "
                       "les outils et fait partie du contrat)")
    return [(args.server, catalog_from_json(raw))]


def run_mcp(args) -> int:
    """`agentl mcp` — inspecter un serveur MCP, ou en importer les contrats."""
    from .mcp import MCPError, translate

    try:
        catalogs = _mcp_catalog(args)
    except MCPError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2

    # Le défaut est de n'importer que les noms. `--strip-descriptions` reste
    # accepté et devient un synonyme du défaut : les scripts existants
    # continuent de marcher, et personne n'est passé en silence du mode sûr
    # au mode risqué.
    keep = bool(getattr(args, "unsafe_import_descriptions", False))
    if keep and args.strip_descriptions:
        print("✗ --strip-descriptions et --unsafe-import-descriptions "
              "s'excluent", file=sys.stderr)
        return 2

    failed = False
    for server, tools in catalogs:
        result = translate(tools, server, keep_descriptions=keep)
        print(f"serveur `{server}` — {len(tools)} outil(s)")
        print(f"  empreinte du catalogue : {result.digest}")

        if args.action == "list":
            for tool in sorted(tools, key=lambda t: t.name):
                marks = "  ⚠ description impérative" \
                    if result.suspicious.get(f"{server}__{tool.name}") else ""
                print(f"  · {tool.name}{marks}")

        # Les descriptions douteuses se disent avant l'écriture : c'est le seul
        # moment où « ré-importer autrement » est encore bon marché.
        for name, marks in sorted(result.suspicious.items()):
            print(f"  ⚠ {name} : description au ton impératif — elle atteindra "
                  f"le contexte du modèle", file=sys.stderr)
            for mark in marks[:3]:
                print(f"      « {mark} »", file=sys.stderr)
        if result.suspicious and keep:
            print("    (ces descriptions sont importées parce que "
                  "--unsafe-import-descriptions a été demandé ; sans lui, "
                  "seuls les noms le seraient)", file=sys.stderr)
        for name, notes in sorted(result.dropped.items()):
            print(f"  ! {name} : contraintes de schéma non vérifiées par "
                  f"INPUT — {', '.join(notes)}", file=sys.stderr)

        if args.action != "import":
            continue

        out = Path(args.out or f"{server}.agent")
        if out.exists() and not args.force:
            # Le fichier généré est celui où l'auteur aura tranché les RISK.
            # L'écraser en silence effacerait le seul travail humain du
            # fichier — précisément ce que E011 a exigé.
            print(f"✗ {out} existe déjà : les RISK que vous y avez tranchés "
                  f"seraient perdus. --force pour écraser.", file=sys.stderr)
            failed = True
            continue
        out.write_text(result.source, encoding="utf-8")
        print(f"→ {out} écrit — {len(tools)} contrat(s) TOOL")
        print(f"  ✗ {len(tools)} diagnostic(s) E011 attendus : le protocole MCP "
              f"ne dit rien du risque.")
        print(f"    Trancher chaque RISK, puis : agentl check {out}")
    return 1 if failed else 0


def run_seal(args) -> int:
    """`agentl seal` — vérifier une pièce, ou fabriquer de quoi en sceller.

    Séparé de `replay` parce que les deux questions le sont : « ce journal
    est-il authentique ? » se répond sans le programme source ni une
    exécution, et se répond souvent en premier.
    """
    from .replay import Journal
    from .seal import SealError, generate_ed25519

    if args.keygen:
        try:
            priv, pub = generate_ed25519(args.keygen, args.name)
        except SealError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"✗ écriture impossible : {exc}", file=sys.stderr)
            return 2
        print(f"→ clé privée  : {priv}  (0600 — ne pas diffuser)")
        print(f"→ clé publique : {pub}  (à remettre au vérificateur)")
        print(f"\n  agentl run A.agent --record j.json --sign-key {priv}")
        print(f"  agentl seal j.json --key {pub}")
        return 0

    if not args.journal:
        print("✗ préciser un journal à vérifier, ou --keygen RÉPERTOIRE",
              file=sys.stderr)
        return 2

    # Non strict : on charge *pour constater* le dommage, pas pour s'en servir.
    try:
        journal = Journal.load(args.journal, strict=False)
    except (OSError, ValueError, AgentLError) as exc:
        print(f"✗ journal illisible : {exc}", file=sys.stderr)
        return 2

    print(f"journal   : {args.journal}")
    print(f"agent     : {journal.meta.get('agent', '?')}")
    print(f"source    : {journal.meta.get('source', '?')}")
    print(f"franchissements : {len(journal.entries)}")

    chain_ok = journal.chain_report is not None and journal.chain_report.ok
    if journal.chain_report is not None:
        print(f"{'✔' if chain_ok else '✘'} {journal.chain_report.render()}")

    from .seal import load_verify_key
    try:
        key = load_verify_key(args.key)
    except SealError as exc:
        print(f"✗ clé de vérification inutilisable : {exc}", file=sys.stderr)
        return 2
    seal_report = journal.verify_seal(key)
    print(f"{'✔' if seal_report.ok else ('✘' if journal.signed else '!')} "
          f"{seal_report.render()}")

    if seal_report.ok and not chain_ok:
        # Combinaison à expliciter : le sceau porte sur la méta, dont la tête de
        # chaîne. Sceau valide + chaîne rompue = les entrées ont été retouchées
        # *après* signature. Sans cette phrase, le « ✔ » ci-dessus rassurerait.
        print("  → les franchissements ont été modifiés après la signature : "
              "le sceau atteste la méta d'origine, pas le contenu actuel")

    if not chain_ok:
        return 1
    if journal.signed and not seal_report.ok:
        return 1
    if not journal.signed:
        # Intègre mais non attribué : ce n'est pas un échec, c'est une nuance,
        # et le code de sortie doit permettre de la distinguer des deux autres.
        return 0
    return 0


def run_autoloop(args) -> int:
    """`agentl autoloop X.agent` — franchir la barrière, puis tenir sur d'autres données.

    Trois codes de sortie, et ils ne disent pas la même chose : `0` l'agent
    tient, `1` il ne tient pas, `3` il tient sur ce que la boucle a vu et cède
    sur le lot retenu. Confondre les deux derniers ferait passer un agent
    appris par cœur pour un agent simplement imparfait — or le premier est le
    plus dangereux des deux, puisqu'il affiche 100 %.
    """
    from .autoloop import autoloop, writer_for

    source = Path(args.file).read_text(encoding="utf-8")

    rewrite = None
    if args.model:
        try:
            rewrite = writer_for(args.model)
        except ValueError as exc:
            raise AgentLError(str(exc))
    else:
        print("! aucun --model : diagnostic seul, rien ne sera réécrit\n",
              file=sys.stderr)

    host = llm = None
    if args.host_pass:
        host, llm = _resolve_host(args)

    def announce(attempt) -> None:
        passed, total = attempt.score
        state = ("grammaire" if attempt.parse_error
                 else "barrière" if not attempt.gates_ok
                 else f"{passed}/{total} cas")
        print(f"  tentative {attempt.index} — {state}", file=sys.stderr)

    report = autoloop(source, filename=args.file, rewrite=rewrite,
                      max_attempts=args.max_attempts, max_cases=args.max_cases,
                      holdout_ratio=args.holdout, seed=args.seed,
                      patience=args.patience, budget_seconds=args.budget,
                      host=host, llm=llm, host_ticks=args.host_ticks,
                      on_attempt=announce)
    print(report.render())

    if args.out and report.source and report.source != source:
        Path(args.out).write_text(report.source, encoding="utf-8")
        print(f"\n→ programme corrigé écrit : {args.out}")
    elif report.source != source:
        # Ne jamais écraser la source sans qu'on l'ait demandé : une boucle
        # d'auto-amélioration qui réécrit le fichier d'entrée fait perdre la
        # seule version que l'auteur avait relue.
        print("\n! le programme a été réécrit mais n'a pas été enregistré "
              "(utilisez -o FICHIER)")

    if report.overfit:
        return 3
    return 0 if report.ok else 1


def run_replay(args) -> int:
    """`agentl replay J.json` — re-dérive la décision, sans le monde réel.

    Le succès n'est pas « ça n'a pas planté » : c'est l'égalité caractère pour
    caractère entre la trace rejouée et la trace scellée dans le journal.
    """
    from .replay import (Journal, ReplayError, ReplayHost, ReplayLLM,
                         sha256, verify_trace)
    from .runtime import Runtime
    from .society import Society

    try:
        journal = Journal.load(args.journal)
    except (OSError, ValueError, AgentLError) as exc:
        print(f"✗ journal illisible : {exc}", file=sys.stderr)
        return 2

    # L'authenticité se tranche **avant** le rejeu : rejouer une pièce
    # corrompue puis annoncer « conforme » serait le pire des résultats.
    if not _report_seal(journal, getattr(args, "key", None),
                        require=getattr(args, "require_seal", False)):
        return 2

    source_path = args.source or journal.meta.get("source")
    if not source_path or not Path(source_path).is_file():
        print(f"✗ programme introuvable : {source_path!r} — "
              f"préciser --source", file=sys.stderr)
        return 2

    source = Path(source_path).read_text(encoding="utf-8")
    if sha256(source) != journal.meta.get("source_sha256"):
        # Rejouer un journal sur un programme modifié ne prouve rien sur
        # l'exécution d'origine — mais reste utile pour montrer *ce qui aurait
        # changé*. On l'autorise, jamais en silence.
        print("! le programme a changé depuis l'enregistrement : le rejeu "
              "montre un écart, il ne reproduit plus l'exécution scellée",
              file=sys.stderr)
        if not args.force:
            print("  (relancer avec --force pour rejouer quand même)",
                  file=sys.stderr)
            return 1

    program = parse_file(source_path)
    ticks = args.ticks if args.ticks is not None else journal.meta.get("ticks")
    try:
        events = _open_events(args)
    except OSError as exc:
        print(f"✗ fichier d'événements inutilisable : {exc}", file=sys.stderr)
        return 2
    try:
        if len(program.agents) > 1:
            names = [a.name for a in program.agents]
            society = Society(program.agents,
                              hosts={n: ReplayHost(journal) for n in names},
                              llms={n: ReplayLLM(journal) for n in names},
                              echo=not args.quiet)
            if events is not None:
                events.attach_society(society)
            society.run(max_ticks=ticks if ticks else 6)
            trace_text = society.render_traces()
            metrics = society.metrics
        else:
            runtime = Runtime(program.agents[0], ReplayHost(journal),
                              ReplayLLM(journal), echo=not args.quiet)
            if events is not None:
                events.attach(runtime.trace, program.agents[0].name)
            runtime.run(max_ticks=ticks)
            trace_text = runtime.trace.render()
            metrics = runtime.metrics
    except ReplayError as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        return 1
    finally:
        if events is not None:
            events.close()

    if args.quiet:
        print(trace_text)
    print()
    identical = verify_trace(journal, trace_text)
    if identical and journal.exhausted:
        print("✔ rejeu conforme — trace identique à l'enregistrement "
              f"(sha256 {journal.meta['trace_sha256'][:12]}…)")
    elif identical:
        print(f"◐ trace identique, mais {journal.remaining} franchissements "
              f"du journal n'ont pas été consommés")
        return 1
    else:
        print("✘ trace différente de l'enregistrement — la décision ne se "
              "re-dérive pas", file=sys.stderr)
        print(f"    scellée : {journal.meta['trace_sha256']}", file=sys.stderr)
        print(f"    rejouée : {sha256(trace_text)}", file=sys.stderr)
        return 1
    print("  " + "  ".join(f"{k}={v}" for k, v in metrics.items()))
    return 0


def _inspect(agent, args) -> int:
    """`plan` et `infer` : figent l'état après N ticks, puis l'examinent.

    Aucun effet sur le monde n'est produit par ces commandes au-delà des
    ticks demandés — elles servent à comprendre *pourquoi* l'agent décide.
    """
    from .bayes import infer as run_infer
    from .planner import Planner
    from .runtime import Runtime

    host, llm = _resolve_host(args, required=False)
    # Programme multi-agents : `build()` rend un Host PAR AGENT, indexé par
    # nom. Passer le dict au runtime faisait échouer le premier capteur sur un
    # `AttributeError: 'dict' object has no attribute 'drain'` — le même piège
    # que `run` traite plus bas, et qui manquait ici. On inspecte un agent à la
    # fois, donc on prend le sien.
    host = _host_for(agent, host, "hôte")
    llm = _host_for(agent, llm, "LLM")
    runtime = Runtime(agent, host, llm or MockLLM())
    if args.ticks <= 0:
        # `--ticks 0` : percevoir sans agir. C'est l'état dans lequel on veut
        # généralement inspecter une inférence ou un plan.
        runtime.state.tick = 1
        runtime.phase_observe()
        runtime.phase_update_beliefs()
        runtime.phase_update_hypotheses()
        runtime.phase_evaluate_goals()
    else:
        runtime.run(max_ticks=args.ticks)

    if args.cmd == "infer":
        if not agent.hypotheses:
            print("Aucune HYPOTHESIS déclarée.")
            return 0
        for hypothesis in agent.hypotheses:
            result = run_infer(hypothesis, runtime.state)
            print(f"\n{result.render()}")
            for outcome in sorted(result.outcomes,
                                  key=lambda o: -abs(o.weight_bits)):
                print(f"    {outcome.render()}")
        return 0

    planner = Planner(agent, agent.planner, runtime.policy)
    if not planner.operators:
        print("Aucun outil ne déclare d'EFFECT : rien à planifier.")
        return 0
    print(f"Opérateurs : " +
          ", ".join(f"{t.name}(coût "
                    f"{t.cost if t.cost is not None else '~risque'})"
                    for t in planner.operators))
    result = planner.synthesize(runtime.state)
    for note in result.pruned_by_policy:
        # `pruned_by_policy` porte aussi, depuis la v1.6, les écarts par
        # échéance : le motif est dans la note, autant ne pas le contredire.
        cause = ("échéance" if "échéance" in note else "la politique")
        print(f"  écarté par {cause} : {note}")
    print(f"\n{result.render()}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="agentl", description="AGENT-L")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("check", "ast", "run", "plan", "infer", "verify", "viz",
                 "boundary", "test"):
        p = sub.add_parser(name)
        p.add_argument("file")
        if name == "boundary":
            p.add_argument("--project-root", metavar="DIR",
                           help="racine des imports Python locaux (détection automatique par défaut)")
            p.add_argument("--external-policy", choices=("report", "error"), default="report",
                           help="lister ou refuser les dépendances externes hors analyse")
        if name == "test":
            p.add_argument("--allow-empty", action="store_true",
                           help="autorise explicitement les agents sans SCENARIO")
            p.add_argument("--trace", action="store_true",
                           help="affiche la trace de chaque scénario")
        if name == "viz":
            p.add_argument("-o", "--out", metavar="FICHIER",
                           help="chemin du HTML produit (défaut : <fichier>.html)")
            p.add_argument("--open", action="store_true",
                           help="ouvre le HTML dans le navigateur")
        if name == "verify":
            from .verifier import DEFAULT_DEPTH
            p.add_argument("--depth", type=int, default=DEFAULT_DEPTH,
                           help="borne de la recherche de route (T2/T5) ; "
                                "la recherche s'arrête normalement bien avant, "
                                "sur son point fixe")
        if name in ("plan", "infer"):
            p.add_argument("--ticks", type=int, default=1,
                           help="ticks avant de figer l'état ; 0 = percevoir "
                                "sans agir")
        if name == "run":
            p.add_argument("--ticks", type=int, default=None)
            p.add_argument("--quiet", action="store_true")
            p.add_argument("--html", metavar="FICHIER",
                           help="écrit un journal visuel HTML autonome")
            p.add_argument("--events", metavar="FICHIER",
                           help="écrit la trace en JSON Lines — un événement "
                                "par ligne, vidé au fil de l'exécution")
            p.add_argument("--record", metavar="FICHIER",
                           help="enregistre un journal de rejeu (JSON) : tout "
                                "ce qui franchit la frontière de l'hôte et "
                                "du modèle")
            p.add_argument("--sign-key", metavar="CLÉ", default=None,
                           help="signe le journal enregistré : chemin d'une clé "
                                "privée Ed25519 (PEM), chemin d'un fichier de "
                                "secret, ou secret littéral. À défaut, la "
                                "variable AGENTL_JOURNAL_KEY est utilisée. Le "
                                "journal est chaîné dans tous les cas.")
            p.add_argument("--durable", metavar="RÉPERTOIRE", default=None,
                           help="exécution durable : intention journalisée et "
                                "synchronisée avant chaque action. Relancer la "
                                "même commande reprend après un crash sans "
                                "doubler un effet déjà journalisé")
            p.add_argument("--run-id", default=None,
                           help="identifiant de l'exécution durable (défaut : "
                                "tiré au hasard, puis relu du journal)")

    # `autoloop` ne suit pas le moule non plus : il doit voir la **source**,
    # pas l'arbre, puisqu'un programme illisible fait partie des états qu'il
    # traverse et corrige.
    auto = sub.add_parser("autoloop",
                          help="porte l'agent jusqu'à ce qu'il tienne, puis "
                               "cherche où il casse")
    auto.add_argument("file")
    auto.add_argument("--model", default=None,
                      help="rédacteur des corrections (gemini-* ou claude-*). "
                           "Sans lui, autoloop diagnostique sans rien réécrire.")
    auto.add_argument("--max-attempts", type=int, default=6,
                      help="plafond de tentatives (défaut : 6)")
    auto.add_argument("--max-cases", type=int, default=200,
                      help="plafond de cas dérivés (défaut : 200)")
    auto.add_argument("--holdout", type=float, default=0.3,
                      help="part des cas retenus, jamais montrés à la boucle "
                           "(défaut : 0,3). 0 la désactive — et avec elle la "
                           "seule mesure du par-cœur.")
    auto.add_argument("--seed", type=int, default=7,
                      help="graine du tirage des cas (défaut : 7)")
    auto.add_argument("--patience", type=int, default=2,
                      help="tentatives sans progrès tolérées (défaut : 2)")
    auto.add_argument("--budget", type=float, default=None,
                      help="budget de temps en secondes")
    auto.add_argument("--host-pass", action="store_true",
                      help="second temps : exécute contre l'hôte réel, en "
                           "lecture seule (aucun outil qui écrit, aucune "
                           "approbation accordée)")
    auto.add_argument("--host-ticks", type=int, default=3)
    auto.add_argument("-o", "--out", metavar="FICHIER", default=None,
                      help="écrit le programme corrigé (défaut : rien n'est "
                           "écrit, la source d'origine est intacte)")

    # `replay` ne prend pas un `.agent` mais un journal : il rejoue une
    # exécution passée sans toucher au monde réel.
    rep = sub.add_parser("replay", help="rejoue une exécution enregistrée")
    rep.add_argument("journal", help="journal produit par `run --record`")
    rep.add_argument("--source", default=None,
                     help="programme .agent (défaut : celui noté au journal)")
    rep.add_argument("--ticks", type=int, default=None)
    rep.add_argument("--quiet", action="store_true")
    rep.add_argument("--events", metavar="FICHIER",
                     help="écrit la trace rejouée en JSON Lines")
    rep.add_argument("--force", action="store_true",
                     help="rejoue même si le programme a changé depuis")
    rep.add_argument("--key", metavar="CLÉ", default=None,
                     help="clé de vérification du sceau (publique Ed25519 ou "
                          "secret HMAC) ; défaut : AGENTL_JOURNAL_KEY")
    rep.add_argument("--require-seal", action="store_true",
                     help="échoue si le journal n'est pas signé ou si son "
                          "sceau ne se vérifie pas")

    # `durable` inspecte ou exporte une exécution durable sans la reprendre.
    dur = sub.add_parser("durable", help="état ou export d'une exécution "
                                         "durable")
    dur.add_argument("action", choices=("status", "export"))
    dur.add_argument("directory", help="répertoire passé à `run --durable`")
    dur.add_argument("-o", "--out", metavar="FICHIER", default=None,
                     help="export : journal de rejeu standard (JSON)")

    # `seal` inspecte un journal sans le rejouer : c'est le geste de
    # l'auditeur qui veut d'abord savoir si la pièce est authentique.
    sl = sub.add_parser("seal", help="vérifie ou produit les clés de scellement")
    sl.add_argument("journal", nargs="?", default=None,
                    help="journal à vérifier")
    sl.add_argument("--key", metavar="CLÉ", default=None,
                    help="clé de vérification ; défaut : AGENTL_JOURNAL_KEY")
    sl.add_argument("--keygen", metavar="RÉPERTOIRE", default=None,
                    help="génère une paire Ed25519 (<nom>.key privée en 0600, "
                         "<nom>.pub publique) au lieu de vérifier")
    sl.add_argument("--name", default="journal",
                    help="nom de base des fichiers de clé (défaut : journal)")

    # `mcp` traduit un catalogue MCP en contrats TOOL. Il ne prend pas de
    # `.agent` : il en produit un.
    mcp = sub.add_parser("mcp", help="importe les outils d'un serveur MCP")
    mcp.add_argument("action", choices=("list", "import"),
                     help="list : inspecte sans rien écrire · "
                          "import : produit les contrats TOOL")
    mcp.add_argument("source",
                     help="configuration MCP (mcpServers), ou catalogue "
                          "tools/list déjà capturé en JSON")
    mcp.add_argument("--server", default=None,
                     help="nom du serveur à importer (défaut : tous ; requis "
                          "si la source est un catalogue brut)")
    mcp.add_argument("-o", "--out", metavar="FICHIER", default=None,
                     help="fichier .agent produit (défaut : <serveur>.agent)")
    # Les descriptions d'outils MCP sont du **texte d'un tiers qui atteint le
    # contexte du modèle** : c'est la surface d'injection indirecte du
    # protocole. `suspicious_description` en attrape les formes grossières et
    # ne prétend pas davantage — une consigne tournée en persona, encodée en
    # homoglyphes ou simplement polie passe au travers. Une heuristique
    # faillible ne peut donc pas être ce qui décide ; seul le défaut le peut.
    # Depuis la v1.9 l'import ne prend que les noms, et importer les
    # descriptions demande de l'écrire.
    mcp.add_argument("--strip-descriptions", action="store_true",
                     help="obsolète : c'est désormais le comportement par "
                          "défaut (conservé pour ne pas casser les scripts)")
    mcp.add_argument("--unsafe-import-descriptions", action="store_true",
                     help="importe les descriptions rédigées par le serveur. "
                          "Elles atteindront le contexte du modèle : ne le "
                          "faire qu'après les avoir lues")
    mcp.add_argument("--force", action="store_true",
                     help="écrase un fichier existant (les RISK déjà tranchés "
                          "y sont perdus)")

    # `studio` ne suit pas le moule des autres sous-commandes : son fichier est
    # facultatif (on peut ouvrir le studio vide puis choisir un agent dedans).
    studio = sub.add_parser("studio", help="interface web d'édition en direct")
    studio.add_argument("file", nargs="?", default=None,
                        help="fichier .agent ouvert au démarrage")
    studio.add_argument("--port", type=int, default=8765)
    studio.add_argument("--host", default="127.0.0.1",
                        help="interface d'écoute (défaut : locale)")
    studio.add_argument("--open", action="store_true",
                        help="ouvre le navigateur")
    studio.add_argument("--no-save", dest="save", action="store_false",
                        help="interdit l'écriture sur disque")
    studio.add_argument("--root", default=".",
                        help="racine des fichiers accessibles")

    args = parser.parse_args(argv)

    if args.cmd == "studio":
        try:
            from .studio import serve
        except ImportError as exc:
            print(f"✗ studio indisponible (fastapi/uvicorn requis) : {exc}",
                  file=sys.stderr)
            return 2
        try:
            return serve(args.file, host=args.host, port=args.port,
                         open_browser=args.open, allow_save=args.save,
                         root=args.root)
        except AgentLError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"✗ écoute impossible sur {args.host}:{args.port} : {exc}",
                  file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 0

    if args.cmd == "autoloop":
        try:
            return run_autoloop(args)
        except AgentLError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"✗ fichier illisible : {exc}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            print("\n! interrompu", file=sys.stderr)
            return 130

    if args.cmd in ("replay", "seal", "mcp", "durable"):
        runner = {"replay": run_replay, "seal": run_seal, "mcp": run_mcp,
                  "durable": run_durable_admin}[args.cmd]
        try:
            return runner(args)
        except AgentLError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 2

    try:
        program = parse_file(args.file)
    except AgentLError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"✗ fichier illisible : {exc}", file=sys.stderr)
        return 2

    agent = program.agents[0]

    if args.cmd == "ast":
        print(_dump(program))
        return 0

    if args.cmd == "viz":
        from .viz import build_program, render_html
        out = args.out or (args.file.rsplit(".", 1)[0] + ".html")
        Path(out).write_text(render_html(build_program(program)), encoding="utf-8")
        print(f"→ visuel écrit : {out}  ({len(program.agents)} agent(s))")
        if getattr(args, "open", False):
            import webbrowser
            webbrowser.open("file://" + str(Path(out).resolve()))
        return 0

    diags = Analyzer(agent).run()
    if args.cmd == "check":
        from .analyzer import check_program

        failed = False
        for candidate in program.agents:
            found = Analyzer(candidate).run()
            print(f"AGENT {candidate.name} v{candidate.version} — "
                  f"{len(candidate.tools)} outils, {len(candidate.plans)} plans, "
                  f"{len(candidate.policies)} règles")
            if not found:
                print("  ✓ aucun diagnostic")
            for d in found:
                print(("  ✗ " if d.severity == "error" else "  ! ") + d.render())
            failed |= any(d.severity == "error" for d in found)
        if len(program.agents) > 1:
            cross = check_program(program)
            print(f"\nPROGRAMME — {len(program.agents)} agents")
            if not cross:
                print("  ✓ contrats de message cohérents")
            for d in cross:
                print(("  ✗ " if d.severity == "error" else "  ! ") + d.render())
            failed |= any(d.severity == "error" for d in cross)
        return 1 if failed else 0

    if args.cmd == "boundary":
        # Contrôle de la frontière hôte/agent : la seule règle du projet dont
        # la violation ne produisait jusqu'ici aucun signal.
        from .boundary import check_pair, render

        report = check_pair(args.file, project_root=args.project_root,
                            external_policy=args.external_policy)
        print(render(report))
        return 0 if report.ok() else 1

    if args.cmd == "verify":
        from .verifier import verify

        refusal = _refuse_if_malformed(program, "verify")
        if refusal is not None:
            return refusal

        failed = False
        for candidate in program.agents:
            report = verify(candidate, depth=args.depth)
            print(report.render())
            failed |= bool(report.refuted)

        if len(program.agents) > 1:
            # T1–T7 se démontrent agent par agent ; T8 est le seul théorème du
            # programme entier. L'omettre laissait « le but reste atteignable »
            # vrai pour chaque agent et faux pour la société.
            from .liveness import verify_liveness

            liveness = verify_liveness(program)
            print(f"\n╔══ vivacité du programme — {len(program.agents)} agents "
                  + "═" * 20)
            print(liveness.render())
            print("╚" + "═" * 60)
            failed |= liveness.refuted
        return 1 if failed else 0

    if args.cmd == "test":
        from .scenario import run_scenarios

        refusal = _refuse_if_malformed(program, "test")
        if refusal is not None:
            return refusal

        failed = False
        empty = False
        for candidate in program.agents:
            report = run_scenarios(candidate, echo=False)
            print(report.render())
            if not report.results:
                empty |= not args.allow_empty
                continue
            if args.trace:
                for result in report.results:
                    print(f"\n── {result.name}\n{result.trace}")
            failed |= not report.passed
        return 1 if failed else (2 if empty else 0)

    if args.cmd in ("plan", "infer"):
        # Comme `check` et `test` : sur une société, on inspecte TOUS les
        # agents. N'en montrer qu'un — le premier, par hasard de l'ordre du
        # fichier — laissait croire que le reste du programme n'avait ni
        # opérateur ni hypothèse.
        try:
            for candidate in program.agents:
                if len(program.agents) > 1:
                    print(f"\n══ {candidate.name} "
                          + "═" * max(0, 56 - len(candidate.name)))
                code = _inspect(candidate, args)
                if code:
                    return code
            return 0
        except AgentLError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 2

    errors = [d for d in diags if d.severity == "error"]
    if errors:
        for d in errors:
            print("✗ " + d.render(), file=sys.stderr)
        print("Exécution refusée : le programme ne passe pas l'analyse statique.",
              file=sys.stderr)
        return 1

    try:
        host, llm = _resolve_host(args)
    except AgentLError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    if getattr(args, "durable", None):
        return _run_durable(program, host, llm, args)
    # Programme multi-agents, ou hôte fournissant un Host par agent : c'est
    # une société. L'exécuter comme un agent isolé donnait un `dict` au
    # runtime, et chaque capteur échouait sur un AttributeError obscur.
    if len(program.agents) > 1 or isinstance(host, dict):
        return _run_society(program, host, llm, args)

    try:
        journal = _new_journal(args, program)
    except AgentLError as exc:
        print(f"✗ clé de signature inutilisable : {exc}", file=sys.stderr)
        return 2
    if journal is not None:
        from .replay import RecordingHost, RecordingLLM
        host = RecordingHost(host, journal)
        llm = RecordingLLM(llm or MockLLM(), journal)

    try:
        events = _open_events(args)
    except OSError as exc:
        print(f"✗ fichier d'événements inutilisable : {exc}", file=sys.stderr)
        return 2
    runtime = Runtime(agent, host, llm or MockLLM(), echo=not args.quiet)
    try:
        if events is not None:
            events.attach(runtime.trace, agent.name)
        runtime.run(max_ticks=args.ticks)
    finally:
        if events is not None:
            events.close()
    if journal is not None:
        _seal_journal(journal, runtime.trace.render(), args)
    if args.quiet:
        print(runtime.trace.render())
    if getattr(args, "html", None):
        from .trace_html import render
        Path(args.html).write_text(render(runtime, agent), encoding="utf-8")
        print(f"→ journal visuel écrit : {args.html}")
    print("\n" + "  ".join(f"{k}={v}" for k, v in runtime.metrics.items()))
    _report_drift(runtime)
    return 0


def _run_durable(program, host, llm, args) -> int:
    """`run --durable` : démarrer, ou reprendre, une exécution durable.

    La même commande fait les deux : c'est le journal du répertoire qui dit si
    l'exécution est neuve, interrompue ou terminée. Une reprise re-dérive
    d'abord tout ce qui fut journalisé — sans effet, sans appel au modèle —
    puis continue en direct.
    """
    from .durable import DurableError, DurableRun, FileStore
    from .replay import ReplayDivergence

    if getattr(args, "record", None):
        print("✗ --durable et --record sont exclusifs : le journal durable "
              "s'exporte (`agentl durable export`)", file=sys.stderr)
        return 2
    store = FileStore(args.durable)
    try:
        source = Path(args.file).read_text(encoding="utf-8")
        run = DurableRun(program.agents, host, llm, store=store,
                         run_id=args.run_id, echo=not args.quiet,
                         source=(str(args.file), source))
    except DurableError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    if run.resumed:
        print(f"↻ reprise de l'exécution durable {run.run_id} : "
              f"{len(run.journal.entries)} entrée(s) journalisée(s), "
              f"re-dérivées sans effet", file=sys.stderr)
        for pending in run.journal.pending():
            print(f"  intention sans résultat : {pending['tool']} "
                  f"({pending.get('action_id')}) — sera tranchée",
                  file=sys.stderr)
    else:
        print(f"→ exécution durable {run.run_id} : {store.wal}",
              file=sys.stderr)
    try:
        result = run.run(max_ticks=args.ticks)
    except (ReplayDivergence, DurableError) as exc:
        print(f"\n✗ reprise impossible : {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    if args.quiet:
        print(run.trace_text())
    for resolution in run.journal.resolutions:
        print(f"  ↻ {resolution['tool']} ({resolution.get('action_id')}) : "
              f"{_RESOLUTION_LABEL.get(resolution['how'], resolution['how'])}")
    metrics = result.metrics
    print("\n" + "  ".join(f"{k}={v}" for k, v in metrics.items()))
    return 0


_RESOLUTION_LABEL = {
    "retry": "relancée avec la même clé d'idempotence",
    "reconciled": "réconciliée : l'effet avait eu lieu",
    "not_executed": "réconciliée : l'effet n'avait pas eu lieu, exécutée",
    "in_doubt": "INDÉTERMINÉE — non relancée, à trancher par un humain",
}


def run_durable_admin(args) -> int:
    """`agentl durable status|export RÉPERTOIRE`."""
    from .durable import DurableError, DurableJournal, FileStore
    from .replay import Journal, decode

    directory = Path(args.directory)
    if not (directory / FileStore.WAL).exists():
        print(f"✗ aucun journal durable dans {directory}", file=sys.stderr)
        return 2
    store = FileStore(directory)
    try:
        journal = DurableJournal.open(store)
    except DurableError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    meta = journal.meta
    if args.action == "status":
        kinds: Dict[str, int] = {}
        for entry in journal.entries:
            kinds[entry.kind] = kinds.get(entry.kind, 0) + 1
        print(f"exécution {meta.get('run_id')} · {meta.get('status')} · "
              f"agents {', '.join(meta.get('agents', []))}")
        print(f"  {len(journal.entries)} entrée(s) : " + ", ".join(
            f"{k}={v}" for k, v in sorted(kinds.items())))
        print(f"  chaîne intacte, tête {journal.entries[-1].hash[:12]}…"
              if journal.entries else "  journal vide")
        for pending in journal.pending():
            print(f"  ⚠ intention sans résultat : {pending['tool']} "
                  f"({pending.get('action_id')}) — la reprise la tranchera")
        for entry in journal.entries:
            if entry.kind == "resolution":
                how = (decode(entry.args) or {}).get("how", "?")
                print(f"  ↻ {entry.key} : {_RESOLUTION_LABEL.get(how, how)}")
        return 0
    if meta.get("status") != "completed":
        print("✗ exécution non terminée : la reprendre avant de l'exporter "
              "(le rejeu exige l'empreinte de la trace finale)", file=sys.stderr)
        return 1
    out = Path(args.out or (directory / "journal.json"))
    exported = Journal(entries=list(journal.entries), meta={
        key: meta[key] for key in ("trace_sha256", "source", "source_sha256",
                                   "run_id") if key in meta})
    exported.meta["agent"] = " · ".join(meta.get("agents", []))
    exported.meta["ticks"] = meta.get("max_ticks") or meta.get("ticks")
    exported.save(out)
    print(f"→ journal de rejeu exporté : {out} ({len(journal.entries)} "
          f"entrées, dont {sum(e.kind in ('intent', 'resolution', 'checkpoint') for e in journal.entries)} "
          f"annotations que le rejeu saute)")
    return 0


def _report_drift(runtime) -> None:
    """Rend le registre de dérive lisible en fin d'exécution.

    Un compteur `effect_drift=3` dit qu'un modèle a été démenti trois fois,
    pas **lequel**. Or c'est le seul chiffre de la sortie qui met en cause la
    spécification elle-même plutôt que l'exécution : il mérite son nom
    d'outil, son chemin et son taux.
    """
    if not runtime.drift:
        return
    lines = []
    for couple, ledger in sorted(runtime.drift.items()):
        total = ledger["confirmé"] + ledger["démenti"]
        if not total:
            continue
        tool, path = couple.split("→", 1)
        rate = (ledger["confirmé"] + 0.5) / (total + 1)
        mark = "✘" if ledger["démenti"] else "✔"
        lines.append(f"  {mark} {tool}() → {path} : {ledger['confirmé']}/{total} "
                     f"confirmé(s), crédibilité {rate:.2f}")
    if lines:
        print("\nmodèle d'effets confronté au réel")
        print("\n".join(lines))


if __name__ == "__main__":  # pragma: no cover
    import contextlib
    with contextlib.suppress(BrokenPipeError):   # tolère `| head`
        raise SystemExit(main())
