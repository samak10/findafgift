"""Satser for registreringsafgift (registreringsafgiftsloven, REGAL).

Kilde: Skatte- og Vækstministeriet, "Gældende satser for registreringsafgiften"
https://svmn.dk/tal-og-metode/satser/satser-og-beloebsgraenser-i-lovgivningen/gaeldende-satser-for-registreringsafgiften
(opdateret 02-09-2026).

2027-satserne for skalaknæk, CO2-tillæg og bundfradrag er de satser, ministeriet
har offentliggjort, men regeringen har foreslået at fastfryse beløbsgrænserne.
De skal derfor betragtes som foreløbige.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PersonbilSatser:
    # Skala: 25 % op til knaek1, 85 % mellem knaek1 og knaek2, 150 % over knaek2.
    knaek1: float
    knaek2: float
    sats1: float
    sats2: float
    sats3: float
    # CO2-tillæg (kr. pr. g/km) og grænser (g/km, WLTP).
    co2_lav: float
    co2_mellem: float
    co2_hoej: float
    co2_graense1: float  # lav sats op til denne grænse
    co2_graense2: float  # mellem sats op til denne grænse, høj sats derover
    bundfradrag: float


@dataclass(frozen=True)
class VarebilSatser:
    graense: float  # 0 % op til grænsen
    sats: float  # 50 % af resten
    co2_sats: float  # kr. pr. g/km
    bundfradrag: float


@dataclass(frozen=True)
class Indfasning:
    """Nedsat afgift for nul- og lavemissionskøretøjer (REGAL § 5 b ff.)."""

    el_pct: float
    plugin_pct: float
    el_bundfradrag_personbil: float
    plugin_bundfradrag_personbil: float
    el_bundfradrag_varebil: float
    plugin_bundfradrag_varebil: float


@dataclass(frozen=True)
class Satser:
    aar: int
    personbil: PersonbilSatser
    varebil: VarebilSatser
    indfasning: Indfasning
    foreloebig: bool = False


SATSER: dict[int, Satser] = {
    2026: Satser(
        aar=2026,
        personbil=PersonbilSatser(
            knaek1=76_400,
            knaek2=237_400,
            sats1=0.25,
            sats2=0.85,
            sats3=1.50,
            co2_lav=294,
            co2_mellem=587,
            co2_hoej=1_115,
            co2_graense1=107,
            co2_graense2=137,
            bundfradrag=25_500,
        ),
        varebil=VarebilSatser(graense=88_100, sats=0.50, co2_sats=294, bundfradrag=35_200),
        indfasning=Indfasning(
            el_pct=0.40,
            plugin_pct=0.68,
            el_bundfradrag_personbil=161_300,
            plugin_bundfradrag_personbil=43_000,
            el_bundfradrag_varebil=76_000,
            plugin_bundfradrag_varebil=43_000,
        ),
    ),
    2027: Satser(
        aar=2027,
        personbil=PersonbilSatser(
            knaek1=78_600,
            knaek2=244_400,
            sats1=0.25,
            sats2=0.85,
            sats3=1.50,
            co2_lav=302,
            co2_mellem=604,
            co2_hoej=1_148,
            co2_graense1=106,
            co2_graense2=136,
            bundfradrag=26_200,
        ),
        varebil=VarebilSatser(graense=90_600, sats=0.50, co2_sats=302, bundfradrag=36_200),
        indfasning=Indfasning(
            el_pct=0.48,
            plugin_pct=0.71,
            el_bundfradrag_personbil=152_200,
            plugin_bundfradrag_personbil=41_000,
            el_bundfradrag_varebil=71_900,
            plugin_bundfradrag_varebil=41_000,
        ),
        foreloebig=True,
    ),
}

# Grænse for lavemissionsbil (typisk plugin-hybrid): under 50 g CO2/km.
LAVEMISSION_GRAENSE_G_KM = 50

# Omregning af CO2/brændstofforbrug (Den juridiske vejledning I.A.1.4.2, REGAL § 4, stk. 3).
NEDC_TIL_WLTP_FAKTOR = 1.21
CO2_PR_L100KM = {
    # målenorm -> drivmiddel -> g CO2/km pr. liter/100 km
    "wltp": {"benzin": 24.0, "diesel": 26.5},
    "nedc": {"benzin": 29.0, "diesel": 32.0},
}


def hent_satser(aar: int) -> Satser:
    try:
        return SATSER[aar]
    except KeyError:
        raise ValueError(
            f"Ingen satser for {aar}. Tilgængelige år: {sorted(SATSER)}"
        ) from None
