"""Lille JSON-API til afgiftsberegning (kun Python standardbibliotek).

Start:
    BILINFO_BRUGER=... BILINFO_ADGANGSKODE=... python -m findafgift.server --port 8000

Endpoints:
    GET  /satser?aar=2026
    POST /afgift/ny      {"vaerdi": 300000, "co2": 120, "koeretoejstype": "personbil", "aar": 2026}
    POST /afgift/brugt   {"handelspris": 800000, "nypris": 1500000, "co2": 0, "aar": 2026}
    POST /vurder         {"tilbud": {...}, "aar": 2026, "parametre": {...}, "dealer_id": "..."}
                         eller {"vehicle_id": "<Bilinfo VehicleId>", "tilbud": {"tilbudt_pris": 120000}}
                         "billedvurdering": true vurderer stand/skader fra Bilinfo-billeder med Claude
    POST /billedvurdering {"vehicle_id": "..."} eller {"billeder": ["https://..."], "info": {...}}
    GET  /biler?maerke=VW&model=Golf
"""

from __future__ import annotations

import argparse
import json
import os
import traceback
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .afgift import Koeretoejstype, beregn_brugt, beregn_brugt_fra_pris_uden_afgift, beregn_ny
from .bilinfo import BilinfoBil, BilinfoKlient, laes_fil, parse_feed
from .satser import SATSER, hent_satser
from .tilbud import Tilbud
from .vaerdifastsaettelse import Parametre
from .vurdering import vurder_tilbud


class Fejl(Exception):
    def __init__(self, status: int, besked: str) -> None:
        super().__init__(besked)
        self.status = status


class App:
    def __init__(
        self, klient: BilinfoKlient | None = None, feed_fil: str | None = None, billedvurderer=None
    ) -> None:
        self.klient = klient
        self.feed_fil = feed_fil
        self.billedvurderer = billedvurderer

    def _billedvurderer(self, body: dict):
        if not body.get("billedvurdering"):
            return None
        if self.billedvurderer is None:
            try:
                from .billedvurdering import Billedvurderer

                self.billedvurderer = Billedvurderer(cache_fil=os.environ.get("FINDAFGIFT_BILLEDCACHE"))
            except Exception as e:
                raise Fejl(503, f"Billedvurdering er ikke tilgængelig (pip install anthropic + ANTHROPIC_API_KEY): {e}")
        return self.billedvurderer

    def biler(self, dealer_id: str | None = None) -> list[BilinfoBil]:
        if self.feed_fil:
            return laes_fil(self.feed_fil)
        if self.klient is None:
            raise Fejl(503, "Ingen Bilinfo-forbindelse konfigureret (BILINFO_BRUGER / BILINFO_ADGANGSKODE).")
        return self.klient.hent_biler(dealer_id)

    def haandter(self, metode: str, sti: str, query: dict, body: dict | None) -> dict:
        body = body or {}
        if metode == "GET" and sti == "/satser":
            aar = int(query.get("aar", [max(SATSER)])[0])
            return asdict(hent_satser(aar))

        if metode == "POST" and sti == "/afgift/ny":
            return beregn_ny(
                float(body["vaerdi"]),
                float(body.get("co2", 0)),
                Koeretoejstype(body.get("koeretoejstype", "personbil")),
                int(body.get("aar", max(SATSER))),
            ).as_dict()

        if metode == "POST" and sti == "/afgift/brugt":
            args = dict(
                nypris_inkl_afgift=float(body["nypris"]),
                co2_g_km=float(body.get("co2", 0)),
                koeretoejstype=Koeretoejstype(body.get("koeretoejstype", "personbil")),
                aar=int(body.get("aar", max(SATSER))),
                alder_aar=body.get("alder_aar"),
            )
            if "handelspris" in body:
                return beregn_brugt(float(body["handelspris"]), **args).as_dict()
            if "pris_uden_afgift" in body:
                return beregn_brugt_fra_pris_uden_afgift(float(body["pris_uden_afgift"]), **args).as_dict()
            raise Fejl(400, "Angiv 'handelspris' (inkl. afgift) eller 'pris_uden_afgift'.")

        if metode == "POST" and sti == "/vurder":
            biler = parse_feed(body["biler"]) if "biler" in body else self.biler(body.get("dealer_id"))
            if "vehicle_id" in body:
                # Den tilbudte bil hentes fra Bilinfo; 'tilbud' kan overskrive fx stand og tilbudt_pris.
                bil = next((b for b in biler if body["vehicle_id"] in (b.vehicle_id, b.vehicle_source_id)), None)
                if bil is None:
                    raise Fejl(404, f"Bil med id {body['vehicle_id']} findes ikke i Bilinfo.")
                tilbud = Tilbud.fra_bilinfo(bil, **body.get("tilbud", {}))
                biler = [b for b in biler if b.vehicle_source_id != bil.vehicle_source_id and b.vehicle_id != bil.vehicle_id]
            elif "tilbud" in body:
                tilbud = Tilbud.fra_dict(body["tilbud"])
            else:
                raise Fejl(400, "Angiv 'tilbud' eller 'vehicle_id'.")
            return vurder_tilbud(
                tilbud,
                biler,
                aar=body.get("aar"),
                parametre=Parametre.fra_dict(body.get("parametre")),
                billedvurderer=self._billedvurderer(body),
            ).as_dict()

        if metode == "POST" and sti == "/billedvurdering":
            bv = self._billedvurderer({"billedvurdering": True})
            if "vehicle_id" in body:
                biler = self.biler(body.get("dealer_id"))
                bil = next((b for b in biler if body["vehicle_id"] in (b.vehicle_id, b.vehicle_source_id)), None)
                if bil is None:
                    raise Fejl(404, f"Bil med id {body['vehicle_id']} findes ikke i Bilinfo.")
                return {**bil.kort(), "billedvurdering": bv.vurder_bil(bil).as_dict()}
            if "billeder" in body:
                return bv.vurder(list(body["billeder"]), body.get("info") or {}).as_dict()
            raise Fejl(400, "Angiv 'vehicle_id' eller 'billeder'.")

        if metode == "GET" and sti == "/biler":
            biler = self.biler(query.get("dealer_id", [None])[0])
            maerke = query.get("maerke", [""])[0].lower()
            model = query.get("model", [""])[0].lower()
            valgt = [
                b.kort()
                for b in biler
                if (not maerke or b.maerke.lower() == maerke) and (not model or model in b.model.lower())
            ]
            return {"antal": len(valgt), "biler": valgt}

        raise Fejl(404, f"Ukendt endpoint: {metode} {sti}")


def lav_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _svar(self, status: int, data: dict) -> None:
            payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _koer(self, metode: str) -> None:
            url = urlparse(self.path)
            try:
                body = None
                if metode == "POST":
                    laengde = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(laengde) or b"{}")
                self._svar(200, app.haandter(metode, url.path, parse_qs(url.query), body))
            except Fejl as e:
                self._svar(e.status, {"fejl": str(e)})
            except (KeyError, ValueError, TypeError) as e:
                self._svar(400, {"fejl": f"Ugyldigt input: {e}"})
            except Exception as e:  # pragma: no cover
                traceback.print_exc()
                self._svar(500, {"fejl": str(e)})

        def do_GET(self) -> None:  # noqa: N802
            self._koer("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._koer("POST")

    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="FindAfgift JSON-API")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--feed-fil", help="Brug en gemt Bilinfo JSON-fil i stedet for API'et")
    args = ap.parse_args(argv)

    klient = None
    if not args.feed_fil:
        try:
            klient = BilinfoKlient()
        except ValueError as e:
            print(f"Advarsel: {e} /vurder kræver så 'biler' i request body.")
    server = ThreadingHTTPServer((args.host, args.port), lav_handler(App(klient, args.feed_fil)))
    print(f"FindAfgift API kører på http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
