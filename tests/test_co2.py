import unittest

from findafgift.co2 import fastsaet_co2


class TestCo2(unittest.TestCase):
    def test_el(self):
        self.assertEqual(fastsaet_co2("El").g_km, 0)

    def test_wltp_direkte(self):
        self.assertEqual(fastsaet_co2("Benzin", 120, maalemetode="wltp").g_km, 120)

    def test_nedc_faktor(self):
        self.assertEqual(fastsaet_co2("Benzin", 100, maalemetode="nedc1").g_km, 121)
        self.assertEqual(fastsaet_co2("Diesel", 100, maalemetode="nedc2").g_km, 121)

    def test_fra_forbrug(self):
        # 8,5 l/100 km benzin WLTP -> 204 g/km (Den juridiske vejledning)
        self.assertAlmostEqual(fastsaet_co2("Benzin", km_pr_liter=100 / 8.5, maalemetode="wltp").g_km, 204, places=1)
        self.assertAlmostEqual(fastsaet_co2("Diesel", km_pr_liter=20, maalemetode="nedc").g_km, 160, places=1)

    def test_ukendt(self):
        self.assertIsNone(fastsaet_co2("Benzin").g_km)


if __name__ == "__main__":
    unittest.main()
