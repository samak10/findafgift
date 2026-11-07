"""Fastsættelse af CO2-udledning til brug for CO2-tillægget (REGAL § 4, stk. 3).

Regler (Den juridiske vejledning I.A.1.4.2):
  * WLTP-CO2 bruges direkte.
  * WLTP-brændstofforbrug: l/100 km × 24 (benzin) / 26,5 (diesel).
  * NEDC-CO2 ganges med 1,21.
  * NEDC-brændstofforbrug: l/100 km × 29 (benzin) / 32 (diesel).
  * Elbiler: 0 g/km.
"""

from __future__ import annotations

from dataclasses import dataclass

from .satser import CO2_PR_L100KM, NEDC_TIL_WLTP_FAKTOR


@dataclass
class Co2Resultat:
    g_km: float | None
    note: str


def normaliser_drivmiddel(drivmiddel: str | None) -> str:
    d = (drivmiddel or "").strip().lower()
    if d in ("el", "electric", "elektrisk", "ev"):
        return "el"
    if d.startswith("diesel"):
        return "diesel"
    if d.startswith("benzin") or d in ("petrol", "gasoline"):
        return "benzin"
    if "hybrid" in d:
        return "hybrid"
    return d


def normaliser_maalemetode(metode: str | None) -> str:
    """Bilinfo bruger 'nedc1', 'nedc2' og 'wltp'. NEDC2 (korreleret NEDC) behandles som NEDC."""
    m = (metode or "").strip().lower()
    if m.startswith("wltp"):
        return "wltp"
    if m.startswith("nedc"):
        return "nedc"
    return "wltp"


def fastsaet_co2(
    drivmiddel: str | None,
    co2_g_km: float | None = None,
    km_pr_liter: float | None = None,
    maalemetode: str | None = "wltp",
) -> Co2Resultat:
    d = normaliser_drivmiddel(drivmiddel)
    metode = normaliser_maalemetode(maalemetode)

    if d == "el":
        return Co2Resultat(0.0, "Elbil: 0 g CO2/km.")

    if co2_g_km is not None and co2_g_km >= 0:
        if metode == "nedc":
            g = round(co2_g_km * NEDC_TIL_WLTP_FAKTOR, 2)
            return Co2Resultat(g, f"NEDC {co2_g_km:g} g/km × {NEDC_TIL_WLTP_FAKTOR} = {g:g} g/km.")
        return Co2Resultat(float(co2_g_km), f"WLTP {co2_g_km:g} g/km.")

    if km_pr_liter and km_pr_liter > 0 and d in ("benzin", "diesel"):
        faktor = CO2_PR_L100KM[metode][d]
        l100 = 100.0 / km_pr_liter
        g = round(l100 * faktor, 2)
        return Co2Resultat(
            g,
            f"Beregnet fra {km_pr_liter:g} km/l ({metode.upper()}): {l100:.2f} l/100 km × {faktor:g} = {g:g} g/km.",
        )

    return Co2Resultat(None, "CO2-udledning kunne ikke fastsættes - angiv CO2 (g/km) eller km/l.")
