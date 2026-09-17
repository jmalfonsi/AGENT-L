"""Renégocie le jeton OAuth de `gmail_butler` — à lancer À LA MAIN.

Pourquoi un script séparé : le consentement Google est une action humaine, pas
une perception. Le faire au milieu d'un `agentl run` noie l'URL dans la trace
et bloque un cycle qui, sous cron, n'a personne pour répondre.

Ce script demande DEUX portées :
  · drive.file     — le journal, créé par l'agent lui-même ;
  · drive.readonly — le fichier « Génération de données RH annuelles », qui
                     appartient à l'utilisateur et qu'un jeton `drive.file`
                     ne peut PAS lire.

Serveur sans écran : ouvrez d'abord un tunnel depuis votre poste

    ssh -L 8899:localhost:8899 ubuntu@ns3132030

puis lancez ce script et collez l'URL affichée dans votre navigateur.
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

for _sp in glob.glob(str(Path(__file__).resolve().parent.parent
                         / ".venv-google" / "lib" / "python*" / "site-packages")):
    sys.path.append(_sp)

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

CONFIG = Path.home() / ".config" / "gmail_butler.env"
SCOPES = ["https://www.googleapis.com/auth/drive.file",
          "https://www.googleapis.com/auth/drive.readonly"]


def _cfg() -> dict:
    valeurs = {}
    if CONFIG.exists():
        for ligne in CONFIG.read_text(encoding="utf-8").splitlines():
            if "=" in ligne and not ligne.strip().startswith("#"):
                cle, _, val = ligne.partition("=")
                valeurs[cle.strip()] = val.split(" #")[0].strip()
    return valeurs


def main() -> int:
    cfg = _cfg()
    creds_path = Path(cfg.get("DRIVE_CREDENTIALS", ""))
    token_path = Path(cfg.get("DRIVE_TOKEN", ""))
    port = int(cfg.get("DRIVE_OAUTH_PORT", "8899"))

    if not creds_path.exists():
        print(f"✗ client OAuth introuvable : {creds_path}")
        return 1

    brut = json.loads(creds_path.read_text(encoding="utf-8"))
    genre = next(iter(brut))
    redirect = brut[genre].get("redirect_uris")
    print(f"  client OAuth : type « {genre} », "
          f"redirect_uris = {redirect or 'AUCUNE'}")
    if genre != "installed" and not redirect:
        print("\n  ⚠ Ce client est de type « web » SANS URI de redirection.")
        print("    Google refusera le consentement (redirect_uri_mismatch) et")
        print("    aucune fenêtre ne s'ouvrira. Deux issues, au choix :")
        print("      a) créer un client « Application de bureau » dans la")
        print("         console Google Cloud et remplacer le fichier ;")
        print(f"      b) ajouter http://localhost:{port}/ aux URI de")
        print("         redirection autorisées du client web actuel.\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=port, open_browser=False,
                                  prompt="consent")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"\n✓ jeton écrit : {token_path}")
    print(f"  portées obtenues : {creds.scopes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
