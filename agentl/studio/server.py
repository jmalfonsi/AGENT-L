"""Serveur web du Studio AGENT-L (module M2).

Expose l'API HTTP décrite au §4 du contrat d'intégration ainsi que le canal
temps réel `/ws` du §2. Le serveur est volontairement mince : toute la logique
métier (chargement, édition, exécution, HITL) appartient à `StudioSession`
(module M1) ; ici on ne fait que transporter, valider et sécuriser.

Principes de sûreté appliqués ici :

* liaison sur ``127.0.0.1`` par défaut — le studio n'est pas un service public ;
* aucun chemin hors de la racine (``--root``) n'est lisible ni écrivable ;
* ``/api/save`` est refusée (403) lorsque ``--no-save`` est demandé ;
* aucune politique CORS permissive n'est installée ;
* le corps d'une édition est borné (2 Mio) ;
* aucun traceback nu ne remonte au client : tout devient ``{"error": ...}``.
"""
from __future__ import annotations

import asyncio
import json
import math
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ..core import AgentLError

# --------------------------------------------------------------------------
# Constantes de garde
# --------------------------------------------------------------------------
#: Taille maximale acceptée pour un source édité (2 Mio) — au-delà, 413.
MAX_SOURCE_BYTES = 2 * 1024 * 1024
#: Nombre maximal de fichiers listés par `/api/files`.
MAX_LISTED_FILES = 500
#: Répertoires jamais explorés lors de la découverte de modules hôtes.
SKIPPED_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules",
                ".mypy_cache", ".pytest_cache", ".tox", "dist", "build"}
#: Période de drainage de la file d'événements de la session (secondes).
DRAIN_PERIOD = 0.03

STATIC_DIR = Path(__file__).resolve().parent / "static"


# --------------------------------------------------------------------------
# Sérialisation défensive
# --------------------------------------------------------------------------
def json_safe(value: Any) -> Any:
    """Rend `value` sérialisable en JSON strict.

    Les flottants non finis (``NaN``, ``±inf``) deviennent ``None``, comme
    l'exige la règle de flux du §2 : le client ne doit jamais recevoir de
    littéral `NaN`, qui n'existe pas en JSON.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    # Les messages de `studio.events` (M1) savent se rendre eux-mêmes : leur
    # `to_dict()` porte le champ `type`, qu'un `asdict()` perdrait.
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return json_safe(to_dict())
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict
        return json_safe(asdict(value))
    return str(value)


def _dump(payload: Any) -> str:
    """Encode un message serveur→client en JSON strict."""
    return json.dumps(json_safe(payload), ensure_ascii=False)


# --------------------------------------------------------------------------
# Diffusion websocket
# --------------------------------------------------------------------------
class Hub:
    """Ensemble des websockets connectés, avec diffusion tolérante aux pannes.

    Un client qui se déconnecte au milieu d'un run ne doit ni interrompre
    l'exécution ni empêcher les autres clients de recevoir la suite : chaque
    envoi est isolé et un socket fautif est simplement retiré.
    """

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def discard(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    @property
    def count(self) -> int:
        return len(self._clients)

    async def broadcast(self, payload: Any) -> None:
        """Envoie `payload` à tous les clients ; retire ceux qui échouent."""
        try:
            text = _dump(payload)
        except (TypeError, ValueError) as exc:      # message indiffusable
            text = _dump({"type": "error",
                          "message": f"événement non sérialisable : {exc}"})
        async with self._lock:
            targets = list(self._clients)
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(text)
            except asyncio.CancelledError:
                raise
            except Exception:                       # socket mort ou exotique
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


# --------------------------------------------------------------------------
# Découverte de modules hôtes
# --------------------------------------------------------------------------
def _iter_files(root: Path, suffix: str) -> Iterator[Path]:
    """Parcourt `root` en évitant les répertoires de service et les liens."""
    stack = [root]
    seen = 0
    while stack and seen < MAX_LISTED_FILES:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in SKIPPED_DIRS:
                    stack.append(entry)
            elif entry.suffix == suffix:
                seen += 1
                yield entry
                if seen >= MAX_LISTED_FILES:
                    return


def discover(root: Path, suffix: str = ".agent") -> list[str]:
    """Chemins relatifs à `root` des fichiers `suffix` découvrables."""
    return sorted(str(p.relative_to(root)) for p in _iter_files(root, suffix))


# --------------------------------------------------------------------------
# Vérification structurée
# --------------------------------------------------------------------------
def verify_source(text: str, depth: int = 4) -> dict[str, Any]:
    """Rend `{report, theorems, refuted}` pour un source AGENT-L.

    `report` est le rendu texte canonique (identique à `agentl verify`) et
    `theorems` sa forme structurée, consommable par l'inspecteur.
    """
    from ..parser import parse_source
    from ..verifier import verify

    program = parse_source(text)
    chunks: list[str] = []
    theorems: list[dict[str, Any]] = []
    refuted: list[str] = []
    for agent in program.agents:
        report = verify(agent, depth=depth)
        chunks.append(report.render())
        for theorem in report.theorems:
            theorems.append({
                "agent": agent.name,
                "key": theorem.key,
                "title": theorem.title,
                "holds": theorem.holds,
                "status": ("proved" if theorem.holds is True else
                           "refuted" if theorem.holds is False else "bounded"),
                "summary": theorem.summary,
                "findings": [{"code": f.code, "severity": f.severity,
                              "title": f.title, "detail": f.detail,
                              "line": f.line} for f in theorem.findings],
            })
        refuted += [f"{agent.name}:{t.key}" for t in report.refuted]
    return {"report": "\n".join(chunks), "theorems": theorems,
            "refuted": refuted}


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------
def create_app(file: str | None = None, root: Path | str = ".",
               allow_save: bool = True) -> FastAPI:
    """Construit l'application FastAPI du studio.

    :param file: fichier `.agent` ouvert au démarrage (facultatif).
    :param root: racine autorisée ; aucun chemin hors de cet arbre n'est servi.
    :param allow_save: si faux, `/api/save` répond 403.
    """
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise AgentLError(f"racine introuvable : {root_path}")

    try:                                      # import paresseux (module M1)
        from .session import StaleRevision, StudioSession
    except ImportError as exc:                # dépendance absente : message net
        raise AgentLError(
            f"moteur de session du studio indisponible : {exc}") from exc

    app = FastAPI(title="AGENT-L Studio", docs_url=None, redoc_url=None)
    hub = Hub()
    session = StudioSession(root=root_path, file=file)
    app.state.session = session
    app.state.hub = hub
    app.state.root = root_path
    app.state.allow_save = bool(allow_save)
    app.state.pump = None

    # ------------------------------------------------------------ utilitaires
    def snapshot() -> dict[str, Any]:
        """Instantané de session enrichi des données propres au serveur."""
        data = dict(session.snapshot())
        # Plus de liste d'hôtes : `hostStatus` dit lequel s'applique
        # et s'il confirme le contrat du programme.
        data.pop("hosts", None)
        data["allowSave"] = app.state.allow_save
        return data

    def resolve(candidate: Any) -> Path:
        """Résout un chemin client sous la racine, ou lève 403.

        Toute tentative de traversée (`../`, chemin absolu extérieur, lien
        symbolique sortant) est refusée — la session applique la même règle,
        mais le serveur doit répondre 403 et non 400.
        """
        if not isinstance(candidate, str) or not candidate.strip():
            raise HTTPException(400, "chemin manquant")
        try:
            return session.resolve_path(candidate)
        except AgentLError as exc:
            raise HTTPException(403, str(exc))
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(400, "chemin invalide")

    async def body(request: Request) -> dict[str, Any]:
        """Corps JSON borné et validé (objet)."""
        raw = await request.body()
        if len(raw) > MAX_SOURCE_BYTES:
            raise HTTPException(413, "charge utile trop volumineuse")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "corps JSON illisible")
        if not isinstance(data, dict):
            raise HTTPException(400, "objet JSON attendu")
        return data

    def check_source(text: Any) -> str:
        """Valide un source reçu du client (type et taille)."""
        if not isinstance(text, str):
            raise HTTPException(400, "champ « text » : chaîne attendue")
        if len(text.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise HTTPException(413, "source trop volumineux (max 2 Mio)")
        return text

    # ------------------------------------------------- gestionnaires d'erreur
    @app.exception_handler(HTTPException)
    async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "erreur"
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(StaleRevision)
    async def _stale(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": str(exc), "rev": session.rev},
                            status_code=409)

    @app.exception_handler(AgentLError)
    async def _agentl_error(_request: Request, exc: AgentLError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, exc: Exception) -> JSONResponse:
        # Filet de dernier recours : le client reçoit un message, jamais la
        # pile d'appels (qui divulguerait des chemins serveur).
        return JSONResponse(
            {"error": f"erreur interne : {type(exc).__name__}: {exc}"},
            status_code=500)

    # ------------------------------------------------------- pont thread→ws
    async def pump() -> None:
        """Draine la file d'événements de la session et la diffuse.

        La session produit ses événements depuis son thread d'exécution ; cette
        tâche asyncio les transfère vers tous les websockets connectés. Elle
        s'abonne une seule fois : la démultiplication vers les clients est le
        rôle du `Hub`, ce qui garantit que tous voient exactement le même flux.
        """
        events = session.subscribe()
        try:
            while True:
                drained = 0
                for event in session.drain(events):
                    drained += 1
                    await hub.broadcast(event)
                await asyncio.sleep(0 if drained else DRAIN_PERIOD)
        except asyncio.CancelledError:
            raise
        finally:
            session.unsubscribe(events)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        """Cycle de vie : la pompe vit exactement le temps de l'application.

        `on_event` ferait le même travail mais est déprécié depuis FastAPI
        0.135 ; le contexte de cycle de vie est la forme pérenne.
        """
        app.state.pump = asyncio.create_task(pump())
        try:
            yield
        finally:
            task = app.state.pump
            app.state.pump = None
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            session.close()

    app.router.lifespan_context = lifespan

    # -------------------------------------------------------------- statiques
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)),
                  name="static")

    @app.get("/")
    async def index() -> Any:
        page = STATIC_DIR / "index.html"
        if not page.is_file():
            return PlainTextResponse(
                "AGENT-L Studio — coque absente (agentl/studio/static/"
                "index.html). L'API reste disponible sur /api/*.",
                status_code=503)
        return FileResponse(str(page), media_type="text/html")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        """Sonde légère (utile aux tests et à la supervision)."""
        return {"ok": True, "clients": hub.count, "running": session.running}

    # ---------------------------------------------------------------- session
    @app.get("/api/session")
    async def get_session() -> Any:
        return json_safe(snapshot())

    @app.get("/api/files")
    async def get_files() -> Any:
        # `hosts` a disparu : l'hôte n'est plus choisi mais déduit du nom.
        return json_safe({"files": session.list_agent_files(MAX_LISTED_FILES)})

    @app.post("/api/open")
    async def post_open(request: Request) -> Any:
        data = await body(request)
        target = resolve(data.get("path"))
        if not target.is_file():
            raise HTTPException(404, "fichier introuvable")
        session.open(target)
        state = snapshot()
        # `open()` n'émet rien : c'est au serveur d'annoncer le nouveau
        # contexte aux clients déjà connectés.
        await hub.broadcast({"type": "source", "text": state["source"],
                             "rev": state["rev"]})
        await hub.broadcast({"type": "diagnostics", "diags": state["diags"]})
        await hub.broadcast({"type": "graph", "graph": state["graph"]})
        return json_safe(state)

    @app.post("/api/source")
    async def post_source(request: Request) -> Any:
        data = await body(request)
        text = check_source(data.get("text", ""))
        rev = data.get("rev")
        if rev is not None and not isinstance(rev, int):
            raise HTTPException(400, "champ « rev » : entier attendu")
        # `set_source` diffuse elle-même `source`/`diagnostics`/`graph` à tous
        # les abonnés : le serveur ne double pas les messages.
        return json_safe(session.set_source(text, rev))

    @app.post("/api/save")
    async def post_save(request: Request) -> Any:
        if not app.state.allow_save:
            raise HTTPException(403, "écriture désactivée (--no-save)")
        data = await body(request)
        path = data.get("path")
        target = resolve(path) if path is not None else None
        if target is not None and target.is_dir():
            raise HTTPException(400, "le chemin désigne un répertoire")
        written = session.save(target)
        return {"saved": session.relpath(written), "path": str(written)}

    @app.post("/api/check")
    async def post_check(request: Request) -> Any:
        data = await body(request)
        text = data.get("text")
        if text is not None:
            text = check_source(text)
        return json_safe({"diags": session.check(text)})

    @app.post("/api/verify")
    async def post_verify(request: Request) -> Any:
        data = await body(request)
        depth = data.get("depth", 4)
        if not isinstance(depth, int) or not 1 <= depth <= 12:
            raise HTTPException(400, "champ « depth » : entier de 1 à 12")
        text = data.get("text")
        text = check_source(text) if text is not None else session.source
        if not text.strip():
            raise HTTPException(400, "aucun source à vérifier")
        return json_safe(verify_source(text, depth=depth))

    # --------------------------------------------------------------- websocket
    def dispatch(message: dict[str, Any]) -> dict[str, Any] | None:
        """Applique un message client ; retourne une réponse directe éventuelle.

        Les effets visibles (source, graphe, run…) transitent par la file
        d'événements de la session, donc par le `Hub` : cette fonction ne
        renvoie que ce qui concerne l'émetteur seul (`pong`, erreurs).
        """
        kind = message.get("type")
        if kind == "ping":
            return {"type": "pong"}
        if kind == "run":
            ticks = message.get("ticks")
            if ticks is not None and (not isinstance(ticks, int) or ticks < 0):
                return {"type": "error", "message": "« ticks » invalide"}
            # L'hôte n'est plus un choix : la norme de nommage le désigne
            # (`X.agent` → `X.py`). Un client qui en propose un se trompe de
            # modèle mental, on le lui dit plutôt que de l'ignorer.
            if message.get("host"):
                return {"type": "error",
                        "message": "l'hôte n'est plus sélectionnable : il est "
                                   "déduit du nom du programme (X.agent → X.py)"}
            pace = message.get("pace", 0)
            if not isinstance(pace, (int, float)) or isinstance(pace, bool):
                return {"type": "error", "message": "« pace » invalide"}
            session.run(ticks=ticks,
                        step_mode=bool(message.get("step", False)),
                        pace=float(pace))
            return None
        if kind == "pace":
            # Mode démonstration réglable en cours de run.
            value = message.get("value")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return {"type": "error", "message": "« value » invalide"}
            return {"type": "pace", "value": session.set_pace(float(value))}
        if kind == "pause":
            session.pause()
            return None
        if kind == "resume":
            session.resume()
            return None
        if kind == "step":
            session.step()
            return None
        if kind == "stop":
            session.stop()
            return None
        if kind == "reply":
            prompt_id = message.get("promptId")
            if not isinstance(prompt_id, str):
                return {"type": "error", "message": "« promptId » invalide"}
            if not session.reply(prompt_id, message.get("value")):
                return {"type": "error",
                        "message": f"question inconnue ou expirée : {prompt_id}"}
            return None
        if kind == "edit":
            text = message.get("text")
            if not isinstance(text, str):
                return {"type": "error", "message": "« text » invalide"}
            if len(text.encode("utf-8")) > MAX_SOURCE_BYTES:
                return {"type": "error", "message": "source trop volumineux"}
            rev = message.get("rev")
            session.set_source(text, rev if isinstance(rev, int) else None)
            return None
        if kind == "save":
            # L'éditeur (M5) propose la sauvegarde par le canal temps réel en
            # plus de `POST /api/save` ; les deux voies appliquent la même
            # règle d'écriture.
            if not app.state.allow_save:
                return {"type": "error",
                        "message": "écriture désactivée (--no-save)"}
            path = message.get("path")
            if path is not None and not isinstance(path, str):
                return {"type": "error", "message": "« path » invalide"}
            try:
                target = resolve(path) if path else None
                if target is not None and target.is_dir():
                    return {"type": "error",
                            "message": "le chemin désigne un répertoire"}
                written = session.save(target)
            except HTTPException as exc:
                return {"type": "error", "message": str(exc.detail)}
            except (AgentLError, OSError) as exc:
                return {"type": "error", "message": str(exc)}
            return {"type": "saved", "path": session.relpath(written)}
        return {"type": "error", "message": f"commande inconnue : {kind!r}"}

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        await hub.add(ws)
        try:
            # Resynchronisation à chaud : un client qui arrive en plein run
            # reçoit immédiatement de quoi reconstruire toute son interface.
            state = snapshot()
            hello = session.hello()
            hello["session"]["allowSave"] = app.state.allow_save
            await ws.send_text(_dump(hello))
            await ws.send_text(_dump({"type": "source", "text": state["source"],
                                      "rev": state["rev"]}))
            await ws.send_text(_dump({"type": "diagnostics",
                                      "diags": state["diags"]}))
            await ws.send_text(_dump({"type": "graph", "graph": state["graph"]}))
            while True:
                raw = await ws.receive_text()
                try:
                    message = json.loads(raw)
                except ValueError:
                    await ws.send_text(_dump(
                        {"type": "error", "message": "message JSON illisible"}))
                    continue
                if not isinstance(message, dict):
                    await ws.send_text(_dump(
                        {"type": "error", "message": "objet JSON attendu"}))
                    continue
                try:
                    reply = dispatch(message)
                except StaleRevision as exc:
                    reply = {"type": "error", "message": str(exc),
                             "rev": session.rev}
                except HTTPException as exc:
                    reply = {"type": "error", "message": str(exc.detail)}
                except AgentLError as exc:
                    reply = {"type": "error", "message": str(exc)}
                except Exception as exc:           # aucune commande ne tue le WS
                    reply = {"type": "error",
                             "message": f"{type(exc).__name__}: {exc}"}
                if reply is not None:
                    await ws.send_text(_dump(reply))
        except WebSocketDisconnect:
            pass
        except (RuntimeError, ConnectionError):
            pass                                    # socket fermé sous nos pieds
        finally:
            await hub.discard(ws)

    return app


# --------------------------------------------------------------------------
# Lancement
# --------------------------------------------------------------------------
def serve(file: str | None = None, host: str = "127.0.0.1", port: int = 8765,
          open_browser: bool = False, allow_save: bool = True,
          root: Path | str = ".") -> int:
    """Démarre le studio et bloque jusqu'à l'arrêt du serveur.

    Retourne 0 en sortie normale. La liaison par défaut est locale : exposer le
    studio sur une interface publique donnerait à quiconque le droit d'exécuter
    des agents et d'écrire des fichiers.
    """
    import uvicorn

    app = create_app(file, root=root, allow_save=allow_save)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"AGENT-L Studio → {url}"
          f"{'' if allow_save else '  (lecture seule)'}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


__all__ = ["create_app", "serve", "Hub", "json_safe", "discover",
           "verify_source", "MAX_SOURCE_BYTES"]
