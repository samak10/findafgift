import json
import os
import unittest
from datetime import date

from findafgift.afgift import beregn_brugt
from findafgift.bilinfo import laes_fil, tal
from findafgift.sammenligning import Parametre, Tilbud, find_sammenlignelige
from findafgift.server import App
from findafgift.vurdering import vurder_tilbud

HER = os.path.dirname(__file__)
FEED = os.path.join(HER, "..", "eksempler", "bilinfo_eksempel.json")
TILBUD = os.path.join(HER, "..", "eksempler", "tilbud.json")


def tilbud(**over):
    with open(TILBUD, encoding="utf-8") as f:
        d = json.load(f)
    d.update(over)
    return Tilbud.fra_dict(d)


class TestBilinfo(unittest.TestCase):
    def test_tal(self):
        self.assertEqual(tal("14,50"), 14.5)
        self.assertEqual(tal("1.234,5"), 1234.5)
        self.assertEqual(tal("329990"), 329990)
        self.assertIsNone(tal(""))

    def test_parse(self):
        biler = laes_fil(FEED)
        self.assertEqual(len(biler), 10)
        b = biler[0]
        self.assertEqual(b.motor, 1.5)
        self.assertEqual(b.pris_inkl_afgift, 219900)
        self.assertEqual(b.foerste_registrering, date(2021, 3, 15))
        self.assertIsNone(biler[4].pris_inkl_afgift)  # leasing
        self.assertTrue(biler[6].er_plugin)
        self.assertEqual(biler[8].koeretoejstype, "varebil")


class TestSammenligning(unittest.TestCase):
    def test_kun_relevante_biler(self):
        sml = find_sammenlignelige(tilbud(), laes_fil(FEED))
        ids = {s.bil.vehicle_id[-2:] for s in sml}
        # benzin-Golf personbiler med kontantpris; ikke leasing, diesel, plugin, elbil, varebil eller Polo
        self.assertEqual(ids, {"01", "02", "03", "04"})
        self.assertEqual(sml[0].bil.vehicle_id[-2:], "01")  # mest lig tilbuddet

    def test_km_og_aar_justering(self):
        sml = {s.bil.vehicle_id[-2:]: s for s in find_sammenlignelige(tilbud(), laes_fil(FEED))}
        # nr. 4: 1 år nyere (-6 %) og 25.000 km mindre (-2,5 %)
        self.assertAlmostEqual(sml["04"].justering_pct, -8.5)
        self.assertAlmostEqual(sml["04"].justeret_pris, 249900 * 0.915)

    def test_udstyrsforskelle(self):
        sml = {s.bil.vehicle_id[-2:]: s for s in find_sammenlignelige(tilbud(), laes_fil(FEED))}
        self.assertEqual(sml["01"].forskelle["udstyr_kun_i_bilinfo"], ["AdaptiveCruiseControl"])

    def test_drivmiddel_filter(self):
        sml = find_sammenlignelige(tilbud(drivmiddel="plugin", motor=1.4, hk=204), laes_fil(FEED))
        self.assertEqual([s.bil.vehicle_id[-2:] for s in sml], ["07"])


class TestVurdering(unittest.TestCase):
    def test_vurder(self):
        v = vurder_tilbud(tilbud(), laes_fil(FEED), aar=2026, idag=date(2026, 11, 7))
        self.assertIsNotNone(v.afgift)
        self.assertEqual(v.priser.antal, 4)
        forventet = beregn_brugt(v.priser.handelspris, v.priser.nypris, 130, aar=2026)
        self.assertEqual(v.afgift.registreringsafgift, forventet.registreringsafgift)
        self.assertEqual(v.samlet_pris, 120000 + v.afgift.registreringsafgift)
        self.assertEqual(v.forskel_til_handelspris, v.priser.handelspris - v.samlet_pris)
        json.dumps(v.as_dict())  # skal kunne serialiseres

    def test_angivet_nypris_bruges(self):
        v = vurder_tilbud(tilbud(nypris=340000), laes_fil(FEED), aar=2026)
        self.assertEqual(v.priser.nypris, 340000)
        self.assertEqual(v.priser.nypris_kilde, "angivet")

    def test_annoncefradrag(self):
        biler = laes_fil(FEED)
        uden = vurder_tilbud(tilbud(), biler, aar=2026).priser.handelspris
        med = vurder_tilbud(tilbud(), biler, aar=2026, parametre=Parametre(annoncefradrag_pct=5)).priser.handelspris
        self.assertAlmostEqual(med, uden * 0.95, delta=1)

    def test_ingen_match(self):
        v = vurder_tilbud(tilbud(maerke="Skoda"), laes_fil(FEED), aar=2026)
        self.assertIsNone(v.afgift)
        self.assertIn("Ingen sammenlignelige biler fundet i Bilinfo.", v.advarsler)


class TestServer(unittest.TestCase):
    def setUp(self):
        self.app = App(feed_fil=FEED)

    def test_vurder_endpoint(self):
        with open(TILBUD, encoding="utf-8") as f:
            body = {"tilbud": json.load(f), "aar": 2026}
        res = self.app.haandter("POST", "/vurder", {}, body)
        self.assertGreater(res["afgift"]["registreringsafgift"], 0)

    def test_brugt_endpoint(self):
        res = self.app.haandter("POST", "/afgift/brugt", {}, {"handelspris": 800000, "nypris": 1500000, "co2": 0, "aar": 2026})
        self.assertEqual(res["registreringsafgift"], 216147)

    def test_satser_endpoint(self):
        res = self.app.haandter("GET", "/satser", {"aar": ["2026"]}, None)
        self.assertEqual(res["personbil"]["bundfradrag"], 25500)

    def test_biler_endpoint(self):
        res = self.app.haandter("GET", "/biler", {"model": ["golf"]}, None)
        self.assertEqual(res["antal"], 8)


if __name__ == "__main__":
    unittest.main()
