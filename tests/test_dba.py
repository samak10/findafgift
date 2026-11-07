import base64
import json
import os
import unittest
from datetime import date
from urllib.parse import parse_qs, urlparse

from findafgift.dba import DbaKlient, hent_sammenlignelige, parse_specifikationer
from findafgift.server import App
from findafgift.vurdering import vurder_tilbud
from findafgift.tilbud import Tilbud

HER = os.path.dirname(__file__)
TILBUD = os.path.join(HER, "..", "eksempler", "tilbud.json")
DATO = date(2026, 11, 7)

TRAE = [
    {"display_name": "Audi", "value": "0.744", "filter_items": []},
    {
        "display_name": "VW",
        "value": "0.817",
        "filter_items": [
            {
                "display_name": "Golf-Serie",
                "value": "1.817.1433",
                "filter_items": [
                    {"display_name": "Golf VIII", "value": "2.817.1433.1", "filter_items": []},
                    {"display_name": "Golf Variant", "value": "2.817.1433.2", "filter_items": []},
                ],
            },
            {"display_name": "Polo-Serie", "value": "1.817.2000", "filter_items": []},
        ],
    },
]


def doc(i, spec, pris, aar=2021, km=60000, fuel="Benzin", privat=False):
    return {
        "ad_id": i, "id": str(i), "make": "VW", "model": "Golf VIII", "model_specification": spec,
        "year": aar, "mileage": km, "fuel": fuel, "transmission": "Automatisk",
        "price": {"amount": pris}, "dealer_segment": "Privat" if privat else "Forhandler",
        "registration_class": {"id": 1, "value": "Personbil"}, "timestamp": 1793000000000,
        "canonical_url": f"https://www.dba.dk/mobility/item/{i}",
        "image_urls": [f"https://images.dbastatic.dk/dynamic/default/item/{i}/a"],
        "regno": "AB12345", "chassis_number": "WVWZZZ", "coordinates": {"lat": 1, "lon": 2},
    }


DOCS = [
    doc(1001, "1,5 TSi 150 Life DSG 5D", 221900),
    doc(1002, "1,5 TSi 150 Life DSG 5D", 229900, km=50000, privat=True),
    doc(1003, "1,4 eHybrid DSG 5D", 184900, fuel="Hybrid benzin"),
    doc(1004, "1,5 TSi 150 Life DSG 5D", 215900, km=80000),
]

SPEC = {
    1001: [("Effekt", "150 Hk"), ("Slagvolumen", "1.498 L"), ("Karosseri", "Hatchback 5-dørs"), ("CO₂-udledning", "130 g/km"), ("1. registrering", "15.04.2021"), ("Brændstofforbrug (WLTP)", "17,5 km/L")],
    1002: [("Effekt", "150 Hk"), ("Slagvolumen", "1.498 L"), ("1. registrering", "01.06.2021")],
    1003: [("Effekt", "204 Hk"), ("Slagvolumen", "1.395 L"), ("Drivmiddel", "Hybrid benzin"), ("Rækkevidde (WLTP)", "71 km"), ("CO₂-udledning", "31 g/km"), ("1. registrering", "29.10.2020")],
    1004: [("Effekt", "150 Hk"), ("Slagvolumen", "1.498 L"), ("1. registrering", "20.09.2021"), ("Registreringsnummer", "AB12345")],
}


def soegeside(docs, filters=True):
    data = {"docs": docs, "filters": [{"name": "variant", "filter_items": TRAE}] if filters else [],
            "metadata": {"paging": {"current": 1, "last": 1}}}
    state = {"queries": [{"queryKey": [{"scope": "search"}], "state": {"data": data}}]}
    return '<script type="application/json" data-react-query-state>' + base64.b64encode(json.dumps(state).encode()).decode() + "</script>"


def annonceside(i):
    rows = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in [("Mærke", "VW"), ("Model", "Golf VIII"), ("Modelår", "2021")] + SPEC[i])
    return (f'<h1 class="t1">VW Golf VIII</h1><p class="s">1,5 TSi</p><dl>{rows}</dl>'
            f'<h2 class="t3 mb-0">Beskrivelse</h2><div class="whitespace-pre-wrap x">Flot bil<br>1 ejer</div>'
            f'<script type="application/ld+json">{{"price": {DOCS[0]["price"]["amount"]}}}</script>')


class FalskDba:
    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        u = urlparse(url)
        if u.path.endswith("/search/car"):
            q = parse_qs(u.query)
            return soegeside(DOCS if "variant" in q else [], filters=True)
        return annonceside(int(u.path.rsplit("/", 1)[1]))


class TestDba(unittest.TestCase):
    def setUp(self):
        self.hent = FalskDba()
        self.k = DbaKlient(hent=self.hent)

    def test_variant_kode(self):
        self.assertEqual(self.k.variant_kode("Volkswagen", "Golf"), "1.817.1433")
        self.assertEqual(self.k.variant_kode("VW", "Golf Variant"), "2.817.1433.2")
        self.assertEqual(self.k.variant_kode("VW", "Arteon"), "0.817")  # ukendt model -> hele mærket
        self.assertIsNone(self.k.variant_kode("Skoda", "Octavia"))

    def test_soeg_og_berig(self):
        biler = hent_sammenlignelige(self.k, "VW", "Golf", 2021, "benzin")
        self.assertEqual(sorted(b.vehicle_id for b in biler), ["dba-1001", "dba-1002", "dba-1004"])
        b = next(x for x in biler if x.vehicle_id == "dba-1001")
        self.assertEqual((b.hk, b.motor, b.co2_g_km, b.km_pr_liter, b.maalemetode), (150, 1.498, 130, 17.5, "wltp"))
        self.assertEqual(b.foerste_registrering, date(2021, 4, 15))
        self.assertEqual(b.kommentar, "Flot bil\n1 ejer")
        self.assertTrue(next(x for x in biler if x.vehicle_id == "dba-1002").privat)
        # ingen persondata gemmes
        alt = json.dumps([x.raw for x in biler] + [x.kort() for x in biler], default=str)
        self.assertNotIn("AB12345", alt)
        self.assertNotIn("WVWZZZ", alt)
        q = parse_qs(urlparse(self.hent.urls[1]).query)
        self.assertEqual((q["variant"], q["year_from"], q["year_to"]), (["1.817.1433"], ["2019"], ["2023"]))

    def test_plugin_genkendes(self):
        biler = hent_sammenlignelige(self.k, "VW", "Golf", 2021, "plugin")
        self.assertEqual([b.vehicle_id for b in biler], ["dba-1003"])
        self.assertTrue(biler[0].er_plugin)

    def test_specifikationer_lang_label(self):
        spec = parse_specifikationer("<dt>Brændstofforbrug (NEDC)NEDC var den standardiserede testmetode i EU</dt><dd>12,1 km/L</dd>")
        self.assertEqual(spec, {"Brændstofforbrug (NEDC)": "12,1 km/L"})

    def test_hent_annonce_som_tilbud(self):
        bil = self.k.hent_annonce("https://www.dba.dk/mobility/item/1001")
        t = Tilbud.fra_bilinfo(bil, tilbudt_pris=100000)
        self.assertEqual((t.maerke, t.model, t.hk, t.foerste_registrering), ("VW", "Golf VIII", 150, date(2021, 4, 15)))

    def test_vurder_med_dba(self):
        with open(TILBUD, encoding="utf-8") as f:
            t = Tilbud.fra_dict({**json.load(f), "nypris": 329990, "co2_g_km": 130})
        v = vurder_tilbud(t, [], aar=2026, idag=DATO, dba=self.k)
        self.assertEqual(sorted(a.bil.vehicle_id for a in v.vaerdi.annoncer), ["dba-1001", "dba-1002", "dba-1004"])
        priv = next(a for a in v.vaerdi.annoncer if a.bil.privat)
        self.assertEqual(priv.korrigeret_pris, 229900)  # privat: intet nummerpladefradrag
        self.assertIsNotNone(v.afgift)

    def test_server_dba(self):
        app = App(dba=self.k)
        with open(TILBUD, encoding="utf-8") as f:
            t = {**json.load(f), "nypris": 329990}
        res = app.haandter("POST", "/vurder", {}, {"tilbud": t, "dba": True, "aar": 2026})
        self.assertEqual(res["vaerdifastsaettelse"]["antal_annoncer"], 3)
        res = app.haandter("POST", "/vurder", {}, {"dba_url": "https://www.dba.dk/mobility/item/1001", "dba": True,
                                                     "tilbud": {"udstyrsniveau": "Life", "nypris": 329990}, "aar": 2026})
        self.assertNotIn("dba-1001", [a["vehicle_id"] for a in res["vaerdifastsaettelse"]["annoncer"]])


if __name__ == "__main__":
    unittest.main()
