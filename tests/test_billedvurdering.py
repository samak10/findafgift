import json
import os
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace

from findafgift.billedvurdering import Billedvurderer, _billed_url
from findafgift.bilinfo import laes_fil
from findafgift.tilbud import Tilbud
from findafgift.vaerdifastsaettelse import Parametre, rund100, vaerdifastsaet
from findafgift.vurdering import vurder_tilbud

HER = os.path.dirname(__file__)
FEED = os.path.join(HER, "..", "eksempler", "bilinfo_eksempel.json")
TILBUD = os.path.join(HER, "..", "eksempler", "tilbud.json")
DATO = date(2026, 11, 7)


def svar(stand="middel", vaesentlig=False, sikkerhed="hoej", skader=()):
    return {
        "stand": stand,
        "vaesentlig_afvigelse": vaesentlig,
        "sikkerhed": sikkerhed,
        "skader": [
            {"type": t, "placering": p, "alvor": a, "billede_nr": 1, "beskrivelse": f"{t} på {p}"}
            for t, p, a in skader
        ],
        "manglende_vinkler": [],
        "begrundelse": "test",
    }


class FalskKlient:
    """Efterligner anthropic.Anthropic().beta.messages.create; svar vælges ud fra billed-URL."""

    def __init__(self, regler, stop_reason="end_turn"):
        self.regler = regler  # liste af (tekst i URL, svar-dict)
        self.kald = []
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.kald.append(kw)
        urls = [b["source"]["url"] for b in kw["messages"][0]["content"] if b["type"] == "image"]
        data = svar()
        for noegle, d in self.regler:
            if any(noegle in u for u in urls):
                data = d
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(data))])


def tilbud(**over):
    with open(TILBUD, encoding="utf-8") as f:
        d = json.load(f)
    d.update(over)
    return Tilbud.fra_dict(d)


class TestBilledvurderer(unittest.TestCase):
    def test_request_opbygning(self):
        k = FalskKlient([])
        bv = Billedvurderer(client=k, max_billeder=2, billedstoerrelse="S800X800")
        res = bv.vurder(["https://billeder.bilinfo.net/a.jpg?class=S1600X1600", "https://x/b.jpg", "https://x/c.jpg"], {"km": 1})
        self.assertTrue(res.ok)
        kw = k.kald[0]
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual(kw["fallbacks"], "default")
        self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
        billeder = [b["source"]["url"] for b in kw["messages"][0]["content"] if b["type"] == "image"]
        self.assertEqual(billeder, ["https://billeder.bilinfo.net/a.jpg?class=S800X800", "https://x/b.jpg"])
        self.assertEqual(res.antal_billeder, 2)

    def test_billed_url(self):
        self.assertEqual(_billed_url("https://billeder.bilinfo.net/x.jpg", "S800X800"), "https://billeder.bilinfo.net/x.jpg?class=S800X800")

    def test_afvisning(self):
        bv = Billedvurderer(client=FalskKlient([], stop_reason="refusal"))
        res = bv.vurder(["https://x/a.jpg"])
        self.assertFalse(res.ok)

    def test_ingen_billeder(self):
        k = FalskKlient([])
        res = Billedvurderer(client=k).vurder([])
        self.assertFalse(res.ok)
        self.assertEqual(k.kald, [])

    def test_cache(self):
        with tempfile.TemporaryDirectory() as d:
            fil = os.path.join(d, "cache.json")
            k = FalskKlient([("a.jpg", svar("under", skader=[("bule", "dør", "middel")]))])
            Billedvurderer(client=k, cache_fil=fil).vurder(["https://x/a.jpg"])
            k2 = FalskKlient([])
            res = Billedvurderer(client=k2, cache_fil=fil).vurder(["https://x/a.jpg"])
            self.assertEqual(k2.kald, [])
            self.assertEqual(res.stand, "under")
            self.assertEqual(res.skader[0].type, "bule")


class TestIntegration(unittest.TestCase):
    def setUp(self):
        self.biler = laes_fil(FEED)

    def ids(self, v):
        return sorted(a.bil.vehicle_id[-2:] for a in v.annoncer)

    def test_uden_billedvurdering_tages_skadet_annonce_med(self):
        v = vaerdifastsaet(tilbud(), self.biler, DATO, Parametre(min_annoncer=4))
        self.assertEqual(self.ids(v), ["01", "02", "03", "13"])

    def test_vaesentlig_skade_fravaelges(self):
        k = FalskKlient([("golf13", svar("under", True, skader=[("bule", "venstre fordør", "stor")]))])
        v = vaerdifastsaet(tilbud(), self.biler, DATO, Parametre(min_annoncer=4), Billedvurderer(client=k))
        self.assertNotIn("13", self.ids(v))
        self.assertEqual(v.fravalgte_annoncer[0]["vehicle_id"][-2:], "13")

    def test_annonce_under_middel_reguleres_op(self):
        k = FalskKlient([("golf2-", svar("under", skader=[("ridse", "bagskærm", "middel")]))])
        v = vaerdifastsaet(tilbud(), self.biler, DATO, Parametre(), Billedvurderer(client=k))
        a2 = next(a for a in v.annoncer if a.bil.vehicle_id.endswith("02"))
        self.assertEqual(a2.korrigeret_pris, 229_900 - 1_180 + rund100(0.02 * (229_900 - 1_180)))
        self.assertEqual(a2.billedvurdering.stand, "under")

    def test_lav_sikkerhed_reguleres_ikke(self):
        k = FalskKlient([("golf2-", svar("under", sikkerhed="lav"))])
        v = vaerdifastsaet(tilbud(), self.biler, DATO, Parametre(), Billedvurderer(client=k))
        a2 = next(a for a in v.annoncer if a.bil.vehicle_id.endswith("02"))
        self.assertEqual(a2.korrigeret_pris, 229_900 - 1_180)

    def test_tilbuddets_stand_fra_billeder(self):
        k = FalskKlient([("tilbud", svar("under", skader=[("bule", "motorhjelm", "middel")]))])
        t = tilbud(billeder=["https://x/tilbud1.jpg"])
        v = vaerdifastsaet(t, self.biler, DATO, Parametre(), Billedvurderer(client=k))
        self.assertEqual((v.stand, v.stand_kilde), ("under", "billedvurdering"))
        self.assertEqual(v.stand_regulering, rund100(-0.02 * v.handelsprisniveau))

    def test_angivet_stand_har_forrang(self):
        k = FalskKlient([("tilbud", svar("under"))])
        t = tilbud(billeder=["https://x/tilbud1.jpg"], stand="middel")
        v = vaerdifastsaet(t, self.biler, DATO, Parametre(), Billedvurderer(client=k))
        self.assertEqual((v.stand, v.stand_regulering), ("middel", 0))
        self.assertTrue(any("afviger" in a for a in v.advarsler))

    def test_vurder_tilbud_med_billeder(self):
        k = FalskKlient([])
        v = vurder_tilbud(tilbud(), self.biler, aar=2026, idag=DATO, billedvurderer=Billedvurderer(client=k))
        self.assertIsNotNone(v.afgift)
        self.assertEqual(len(k.kald), 4)  # fire identiske annoncer med billeder
        json.dumps(v.as_dict())


if __name__ == "__main__":
    unittest.main()
