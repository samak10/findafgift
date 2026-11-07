import json
import os
import unittest
from datetime import date

from findafgift.afgift import beregn_brugt
from findafgift.bilinfo import laes_fil, tal
from findafgift.server import App
from findafgift.tilbud import Ekstraudstyr, Tilbud
from findafgift.vaerdifastsaettelse import (
    Parametre,
    alder,
    km_regulering,
    nedskrivningsskala_pct,
    rund100,
    udstyr_procent,
    vaerdifastsaet,
)
from findafgift.vurdering import vurder_tilbud

HER = os.path.dirname(__file__)
FEED = os.path.join(HER, "..", "eksempler", "bilinfo_eksempel.json")
TILBUD = os.path.join(HER, "..", "eksempler", "tilbud.json")
DATO = date(2026, 11, 7)


def tilbud(**over):
    with open(TILBUD, encoding="utf-8") as f:
        d = json.load(f)
    d.update(over)
    return Tilbud.fra_dict(d)


def ids(annoncer):
    return sorted(a.bil.vehicle_id[-2:] for a in annoncer)


class TestBilinfo(unittest.TestCase):
    def test_tal(self):
        self.assertEqual(tal("14,50"), 14.5)
        self.assertEqual(tal("1.234,5"), 1234.5)
        self.assertEqual(tal("329990"), 329990)
        self.assertIsNone(tal(""))

    def test_parse(self):
        biler = laes_fil(FEED)
        b = biler[0]
        self.assertEqual(b.motor, 1.5)
        self.assertEqual(b.pris_inkl_afgift, 219900)
        self.assertEqual(b.foerste_registrering, date(2021, 3, 15))
        self.assertEqual(b.oprettet, date(2026, 10, 15))
        self.assertIsNone(biler[4].pris_inkl_afgift)  # leasing
        self.assertTrue(biler[8].er_plugin)
        self.assertEqual(biler[10].koeretoejstype, "varebil")

    def test_tilbud_fra_bilinfo(self):
        b = laes_fil(FEED)[0]
        t = Tilbud.fra_bilinfo(b, stand="under", tilbudt_pris=100000)
        self.assertEqual((t.maerke, t.model, t.hk, t.nypris, t.co2_g_km), ("VW", "Golf", 150, 329990, 130))
        self.assertEqual(t.stand, "under")


class TestMotorstyrelsensRegler(unittest.TestCase):
    def test_km_regulering_eksempel(self):
        # Den juridiske vejledning: 520.000 × 0,2 × (150.000 - 171.000) / 100.000 = -21.840 -> -21.800
        trin, adv = [], []
        self.assertEqual(km_regulering(520_000, 150_000, 171_000, 2, trin, adv), -21_800)

    def test_km_under_10_pct_reguleres_ikke(self):
        self.assertEqual(km_regulering(200_000, 100_000, 109_000, 3, [], []), 0)

    def test_km_over_10_aar(self):
        self.assertEqual(km_regulering(50_000, 150_000, 190_000, 11, [], []), 0)  # afvigelse 27 %
        self.assertNotEqual(km_regulering(50_000, 150_000, 250_000, 11, [], []), 0)  # afvigelse 67 %

    def test_km_loft_10_pct(self):
        # 100.000 × 0,31 × 100.000 / 100.000 = 31.000 > 10.000 -> 10.000 + 21.000/2 = 20.500
        adv = []
        self.assertEqual(km_regulering(100_000, 120_000, 20_000, 0, [], adv), 20_500)
        self.assertTrue(adv)

    def test_km_satser(self):
        # 15.500 > 10 % loft -> 10.000 + 5.500/2 = 12.750 -> afrundet 12.800
        self.assertEqual(km_regulering(100_000, 100_000, 50_000, 0, [], []), 12_800)
        self.assertEqual(km_regulering(100_000, 100_000, 80_000, 1, [], []), 4_400)
        self.assertEqual(km_regulering(100_000, 100_000, 80_000, 2, [], []), 4_000)
        self.assertEqual(km_regulering(100_000, 100_000, 80_000, 5, [], []), 3_400)

    def test_alder(self):
        self.assertEqual(alder(date(2024, 8, 15), date(2026, 11, 7)), (2, 2))
        self.assertEqual(alder(date(2024, 11, 7), date(2026, 11, 7)), (2, 0))

    def test_udstyr_procent(self):
        self.assertEqual(udstyr_procent(1, 4), 66)  # eksemplet i vejledningen
        self.assertEqual(udstyr_procent(5, 3), 20)
        self.assertEqual(udstyr_procent(9, 0), 0)

    def test_nedskrivningsskala_eksempler(self):
        # Eksempel 1 og 2 i Den juridiske vejledning (nypris 700.000 kr.)
        self.assertEqual(nedskrivningsskala_pct(700_000, date(2021, 2, 1), date(2022, 3, 1)), 20)
        self.assertEqual(nedskrivningsskala_pct(700_000, date(2020, 12, 1), date(2022, 3, 1)), 25)
        self.assertEqual(nedskrivningsskala_pct(300_000, date(2018, 1, 1), date(2022, 3, 1)), 30)
        self.assertEqual(nedskrivningsskala_pct(500_000, date(2021, 6, 1), date(2022, 3, 1)), 0)

    def test_rund100(self):
        self.assertEqual(rund100(-21_840), -21_800)
        self.assertEqual(rund100(150), 200)


class TestVaerdifastsaettelse(unittest.TestCase):
    def setUp(self):
        self.biler = laes_fil(FEED)

    def test_identiske_annoncer(self):
        v = vaerdifastsaet(tilbud(), self.biler, DATO)
        # nr. 4 har for lang liggetid, nr. 5 er leasing; resten er ikke identiske
        self.assertEqual(ids(v.annoncer), ["01", "02", "03", "13"])
        self.assertTrue(all(a.niveau == 0 for a in v.annoncer))

    def test_handelsprisniveau_og_km(self):
        v = vaerdifastsaet(tilbud(), self.biler, DATO)
        niveau = (219_900 + 229_900 + 214_900 + 189_900) / 4 - 1_180
        self.assertAlmostEqual(v.handelsprisniveau, niveau)
        self.assertEqual(v.gennemsnit_km, 61_250)
        # bilen er 5 år -> 17 øre; 90.000 km er 47 % over gennemsnittet
        reg = niveau * 0.17 * (61_250 - 90_000) / 100_000
        self.assertEqual(v.km_regulering, rund100(reg))
        self.assertEqual(v.handelspris, round(niveau + rund100(reg)))
        self.assertEqual(v.nypris, 329_990)

    def test_udvidelse_naar_for_faa(self):
        p = Parametre(min_annoncer=6)
        v = vaerdifastsaet(tilbud(), self.biler, DATO, p)
        # identiske (4) -> udstyrsniveau udvidet (+nr. 6) -> år ±1 (+nr. 7)
        self.assertEqual(ids(v.annoncer), ["01", "02", "03", "06", "07", "13"])
        a6 = next(a for a in v.annoncer if a.bil.vehicle_id.endswith("06"))
        self.assertAlmostEqual(a6.korrigeret_pris, (244_900 - 1_180) * 329_990 / 359_990)

    def test_stand_og_chauffoerer(self):
        basis = vaerdifastsaet(tilbud(), self.biler, DATO)
        under = vaerdifastsaet(tilbud(stand="under"), self.biler, DATO)
        self.assertEqual(under.stand_regulering, rund100(-0.02 * basis.handelsprisniveau))
        begge = vaerdifastsaet(tilbud(stand="under", skiftende_chauffoerer=True), self.biler, DATO)
        n = basis.handelsprisniveau
        self.assertEqual(begge.stand_regulering + begge.anvendelse_regulering, rund100(-0.02 * n) + rund100(-0.05 * n))

    def test_samlet_fradrag_max_20000(self):
        # Ingen annoncer: 1.000.000 × (1 - 55 %) = 450.000; 2 % + 5 % = 31.500 -> begrænset til 20.000
        t = tilbud(maerke="Audi", model="A8", nypris=1_000_000, stand="under", skiftende_chauffoerer=True)
        v = vaerdifastsaet(t, self.biler, DATO)
        self.assertEqual(v.stand_regulering + v.anvendelse_regulering, -20_000)
        self.assertEqual(v.handelspris, 430_000)

    def test_ekstraudstyr(self):
        t = tilbud(ekstraudstyr=[
            {"navn": "Panoramatag", "nyvaerdi": 20_000},
            {"navn": "Trækkrog", "nyvaerdi": 12_000, "kategori": "traekkrog"},
        ])
        v = vaerdifastsaet(t, self.biler, DATO)
        self.assertEqual(v.udstyr_tillaeg, 4_000)  # 20.000 × 20 % (over 5 år)
        self.assertEqual(v.nypris, 329_990 + 20_000)

    def test_ingen_annoncer_nedskrivningsskala(self):
        t = tilbud(maerke="Skoda", model="Octavia", nypris=300_000)
        v = vaerdifastsaet(t, self.biler, DATO)
        self.assertEqual(v.metode, "nedskrivningsskala")
        self.assertEqual(v.handelspris, 300_000 * 0.65)  # 5 år, under 350.000 kr.: 35 %

    def test_specielle_koeretoejer_lang_liggetid(self):
        t = tilbud(udstyrsniveau="", variant="", aargang=2021, foerste_registrering="2021-02-10")
        biler = [b for b in self.biler if b.vehicle_id[-2:] in ("04",)]
        v = vaerdifastsaet(t, biler, DATO)
        self.assertEqual(ids(v.annoncer), ["04"])
        self.assertTrue(any("liggetid" in a for a in v.advarsler))


class TestVurdering(unittest.TestCase):
    def test_vurder(self):
        v = vurder_tilbud(tilbud(), laes_fil(FEED), aar=2026, idag=DATO)
        self.assertIsNotNone(v.afgift)
        self.assertEqual(v.co2.g_km, 130)  # CO2 hentet fra identisk Bilinfo-bil
        forventet = beregn_brugt(v.vaerdi.handelspris, v.vaerdi.nypris, 130, aar=2026, alder_aar=5.6)
        self.assertEqual(v.afgift.registreringsafgift, forventet.registreringsafgift)
        self.assertEqual(v.samlet_pris, 120000 + v.afgift.registreringsafgift)
        self.assertEqual(v.forskel_til_handelspris, v.vaerdi.handelspris - v.samlet_pris)
        json.dumps(v.as_dict())

    def test_vurder_bil_fra_bilinfo(self):
        biler = laes_fil(FEED)
        t = Tilbud.fra_bilinfo(biler[2], udstyrsniveau="Life")
        andre = [b for b in biler if b is not biler[2]]
        v = vurder_tilbud(t, andre, aar=2026, idag=DATO)
        self.assertEqual(v.vaerdi.nypris_kilde, "angivet")
        self.assertIsNotNone(v.afgift)


class TestServer(unittest.TestCase):
    def setUp(self):
        self.app = App(feed_fil=FEED)

    def test_vurder_endpoint(self):
        with open(TILBUD, encoding="utf-8") as f:
            body = {"tilbud": json.load(f), "aar": 2026}
        res = self.app.haandter("POST", "/vurder", {}, body)
        self.assertGreater(res["afgift"]["registreringsafgift"], 0)

    def test_vurder_vehicle_id(self):
        body = {"vehicle_id": "00000000-0000-0000-0000-000000000003", "tilbud": {"udstyrsniveau": "Life", "tilbudt_pris": 100000}}
        res = self.app.haandter("POST", "/vurder", {}, body)
        self.assertEqual(res["tilbud"]["km"], 75000)
        self.assertNotIn("03", [a["vehicle_id"][-2:] for a in res["vaerdifastsaettelse"]["annoncer"]])

    def test_brugt_endpoint(self):
        res = self.app.haandter("POST", "/afgift/brugt", {}, {"handelspris": 800000, "nypris": 1500000, "co2": 0, "aar": 2026})
        self.assertEqual(res["registreringsafgift"], 216147)

    def test_satser_endpoint(self):
        res = self.app.haandter("GET", "/satser", {"aar": ["2026"]}, None)
        self.assertEqual(res["personbil"]["bundfradrag"], 25500)


if __name__ == "__main__":
    unittest.main()


class TestManuelleAnnoncer(unittest.TestCase):
    def setUp(self):
        from findafgift.bilinfo import manuel_annonce

        with open(os.path.join(HER, "..", "eksempler", "manuelle_annoncer.json"), encoding="utf-8") as f:
            self.manuelle = [manuel_annonce(d) for d in json.load(f)]

    def test_parse(self):
        bb, dba = self.manuelle
        self.assertEqual((bb.kilde, bb.pris_inkl_afgift, bb.koeretoejstype), ("bilbasen", 217900, "personbil"))
        self.assertTrue(dba.privat)
        self.assertNotEqual(bb.vehicle_id, dba.vehicle_id)

    def test_kun_manuelle_annoncer(self):
        v = vaerdifastsaet(tilbud(), self.manuelle + [laes_fil(FEED)[1]], DATO)
        self.assertEqual(len(v.annoncer), 3)
        priv = next(a for a in v.annoncer if a.bil.privat)
        # Private annoncer er ikke omfattet af prismærkning -> intet fradrag for nummerplader
        self.assertEqual(priv.korrigeret_pris, 205000)
        bb = next(a for a in v.annoncer if a.bil.kilde == "bilbasen")
        self.assertEqual(bb.korrigeret_pris, 217900 - 1180)

    def test_ukendt_felt(self):
        from findafgift.bilinfo import manuel_annonce

        with self.assertRaises(ValueError):
            manuel_annonce({"maerke": "VW", "model": "Golf", "pris": 1, "farve": "rød"})

    def test_server_manuelle_annoncer(self):
        with open(TILBUD, encoding="utf-8") as f:
            t = json.load(f)
        with open(os.path.join(HER, "..", "eksempler", "manuelle_annoncer.json"), encoding="utf-8") as f:
            m = json.load(f)
        res = App().haandter("POST", "/vurder", {}, {"tilbud": t, "manuelle_annoncer": m, "aar": 2026})
        self.assertEqual(res["vaerdifastsaettelse"]["antal_annoncer"], 2)
