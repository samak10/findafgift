import unittest

from findafgift.afgift import (
    Emissionsklasse,
    Koeretoejstype,
    beregn_brugt,
    beregn_brugt_fra_pris_uden_afgift,
    beregn_ny,
    emissionsklasse,
    ny_vaerdi_fra_nypris,
)


class TestNyBil(unittest.TestCase):
    def test_benzinbil_2026(self):
        # Regneeksempel: værdi 300.000 kr., 120 g/km (danskmotorfinans.dk, 2026-satser)
        # 19.100 + 136.850 + 93.900 + 31.458 + 7.631 - 25.500 = 263.439
        self.assertEqual(beregn_ny(300_000, 120, aar=2026).registreringsafgift, 263_439)

    def test_bundfradrag_kan_ikke_give_negativ_afgift(self):
        self.assertEqual(beregn_ny(20_000, 0.0001, aar=2026).registreringsafgift, 0)

    def test_billig_elbil_er_afgiftsfri(self):
        self.assertEqual(beregn_ny(300_000, 0, aar=2026).registreringsafgift, 0)

    def test_elbil_2026(self):
        v = 500_000
        skala = 0.25 * 76_400 + 0.85 * (237_400 - 76_400) + 1.5 * (v - 237_400)
        forventet = round((skala - 25_500) * 0.40 - 161_300)
        self.assertEqual(beregn_ny(v, 0, aar=2026).registreringsafgift, forventet)

    def test_plugin_2026(self):
        v, co2 = 400_000, 30
        skala = 0.25 * 76_400 + 0.85 * (237_400 - 76_400) + 1.5 * (v - 237_400)
        forventet = round((skala + co2 * 294 - 25_500) * 0.68 - 43_000)
        res = beregn_ny(v, co2, aar=2026)
        self.assertEqual(res.emissionsklasse, Emissionsklasse.LAVEMISSION)
        self.assertEqual(res.registreringsafgift, forventet)

    def test_varebil_2026(self):
        # 50 % af (300.000 - 88.100) + 120 × 294 - 35.200
        self.assertEqual(
            beregn_ny(300_000, 120, Koeretoejstype.VAREBIL, 2026).registreringsafgift,
            round(0.5 * (300_000 - 88_100) + 120 * 294 - 35_200),
        )

    def test_co2_hoej_sats(self):
        res = beregn_ny(100_000, 200, aar=2026)
        co2 = 107 * 294 + 30 * 587 + 63 * 1_115
        skala = 0.25 * 76_400 + 0.85 * (100_000 - 76_400)
        self.assertEqual(res.registreringsafgift, round(skala + co2 - 25_500))

    def test_ukendt_aar(self):
        with self.assertRaises(ValueError):
            beregn_ny(100_000, 100, aar=1999)


class TestBrugtBil(unittest.TestCase):
    def test_elbil_eksempel_2026_og_2027(self):
        # Nypris 1.500.000, handelspris 800.000 (ev-import.dk, verificeret mod Motorregistret)
        self.assertEqual(beregn_brugt(800_000, 1_500_000, 0, aar=2026).registreringsafgift, 216_147)
        self.assertEqual(beregn_brugt(800_000, 1_500_000, 0, aar=2027).registreringsafgift, 253_127)

    def test_ny_vaerdi_fra_nypris(self):
        self.assertAlmostEqual(ny_vaerdi_fra_nypris(1_500_000, 0, aar=2026), 1_094_725, places=2)

    def test_brugt_ved_nypris_svarer_til_ny(self):
        v = 250_000
        ny = beregn_ny(v, 130, aar=2026)
        brugt = beregn_brugt(v + ny.registreringsafgift, v + ny.registreringsafgift, 130, aar=2026)
        self.assertAlmostEqual(brugt.registreringsafgift, ny.registreringsafgift, delta=1)
        self.assertAlmostEqual(brugt.afgiftspligtig_vaerdi, v, delta=1)

    def test_afgift_skalerer_med_restvaerdi(self):
        hel = beregn_brugt(400_000, 400_000, 140, aar=2026).registreringsafgift
        halv = beregn_brugt(200_000, 400_000, 140, aar=2026).registreringsafgift
        self.assertAlmostEqual(halv, hel / 2, delta=1)

    def test_vaerdi_begraenses_til_nypris_for_gamle_biler(self):
        res = beregn_brugt(500_000, 400_000, 140, aar=2026, alder_aar=10)
        self.assertEqual(res.restvaerdi_pct, 100.0)
        self.assertTrue(res.advarsler)

    def test_fra_pris_uden_afgift(self):
        res = beregn_brugt_fra_pris_uden_afgift(150_000, 400_000, 140, aar=2026)
        self.assertAlmostEqual(res.handelspris_inkl_afgift - res.registreringsafgift, 150_000, delta=2)

    def test_emissionsklasse(self):
        self.assertEqual(emissionsklasse(0), Emissionsklasse.NULEMISSION)
        self.assertEqual(emissionsklasse(49), Emissionsklasse.LAVEMISSION)
        self.assertEqual(emissionsklasse(50), Emissionsklasse.KONVENTIONEL)


if __name__ == "__main__":
    unittest.main()
