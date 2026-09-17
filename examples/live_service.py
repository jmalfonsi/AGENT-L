"""Un vrai service HTTP minimal — le « patient » de `service_medic.agent`.

Lancé avec `--crash`, il journalise un démarrage puis meurt d'une vraie
exception (traceback réel dans le journal, code retour 1) — sans rien
allouer : le MemoryError est levé, pas provoqué. Lancé sans, il sert
`GET /health` → 200.
"""
from __future__ import annotations

import argparse
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8763)
    ap.add_argument("--log", required=True)
    ap.add_argument("--crash", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(filename=args.log, level=logging.INFO,
                        format="%(asctime)s %(levelname)s svc-demo %(message)s")
    logging.info("service svc-demo demarre sur le port %s", args.port)

    if args.crash:
        logging.info("chargement de l'index du cache en memoire")
        try:
            raise MemoryError("cannot allocate 8589934592 bytes for cache index")
        except MemoryError:
            logging.exception("echec fatal au demarrage")
            raise SystemExit(1)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                body = b"ok"
                self.send_response(200)
            else:
                body = b"not found"
                self.send_response(404)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *a):
            logging.info("http %s", fmt % a)

    logging.info("pret : /health repond")
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
