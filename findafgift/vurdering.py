"""Samlet vurdering af en tilbudt bil: Bilinfo-sammenligning + registreringsafgift."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .afgift import AfgiftsBeregning, Koeretoejstype, beregn_brugt
from .bilinfo import BilinfoBil
from .co2 import Co2Resultat, fastsaet_co2
from .sammenligning import Parametre, PrisEstimat, Tilbud, estimer_priser


@dataclass
class Vurdering:
    tilbud: Tilbud
    priser: PrisEstimat
    co2: Co2Resultat
    afgift: AfgiftsBeregning | None
    samlet_pris: int | None = None  # tilbudt pris + registreringsafgift
    forskel_til_handelspris: int | None = None  # positiv = billigere end i DK
    advarsler: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        t = self.tilbud
        return {
            "tilbud": {
                k: (v.isoformat() if isinstance(v, date) else v) for k, v in t.__dict__.items()
            },
            "co2": {"g_km": self.co2.g_km, "note": self.co2.note},
            "priser": self.priser.as_dict(),
            "afgift": self.afgift.as_dict() if self.afgift else None,
            "samlet_pris": self.samlet_pris,
            "forskel_til_handelspris": self.forskel_til_handelspris,
            "advarsler": self.advarsler,
        }


def _alder(t: Tilbud, idag: date) -> float | None:
    if t.foerste_registrering:
        return (idag - t.foerste_registrering).days / 365.25
    if t.aargang:
        return float(idag.year - t.aargang)
    return None


def vurder_tilbud(
    tilbud: Tilbud,
    biler: list[BilinfoBil],
    aar: int | None = None,
    parametre: Parametre | None = None,
    idag: date | None = None,
) -> Vurdering:
    """Sammenlign tilbuddet med Bilinfo og beregn registreringsafgiften.

    aar: afgiftsår (satser). Standard er indeværende år.
    """
    idag = idag or date.today()
    aar = aar or idag.year
    advarsler: list[str] = []

    priser = estimer_priser(tilbud, biler, parametre)
    advarsler += priser.advarsler

    if tilbud.er_plugin:
        co2 = fastsaet_co2("benzin", tilbud.co2_g_km, None, tilbud.maalemetode)
        if co2.g_km is None:
            co2 = Co2Resultat(None, "Plugin-hybrid: angiv CO2 (g/km) - kan ikke beregnes fra km/l.")
    else:
        co2 = fastsaet_co2(tilbud.drivmiddel, tilbud.co2_g_km, tilbud.km_pr_liter, tilbud.maalemetode)
    if co2.g_km is None:
        advarsler.append(co2.note)

    afgift = None
    if tilbud.koeretoejstype not in ("personbil", "varebil"):
        advarsler.append(f"Køretøjstypen '{tilbud.koeretoejstype}' understøttes ikke.")
    elif priser.handelspris and priser.nypris and co2.g_km is not None:
        afgift = beregn_brugt(
            priser.handelspris,
            priser.nypris,
            co2.g_km,
            Koeretoejstype(tilbud.koeretoejstype),
            aar,
            alder_aar=_alder(tilbud, idag),
        )
        advarsler += afgift.advarsler

    samlet = forskel = None
    if afgift and tilbud.tilbudt_pris is not None:
        samlet = tilbud.tilbudt_pris + afgift.registreringsafgift
        forskel = priser.handelspris - samlet

    return Vurdering(tilbud, priser, co2, afgift, samlet, forskel, advarsler)
