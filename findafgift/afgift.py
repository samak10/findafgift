"""Beregning af registreringsafgift for personbiler og varebiler.

Nye køretøjer (REGAL § 4 og § 5):
    afgift = skala(værdi) + CO2-tillæg - bundfradrag            (min. 0)
    elbil/plugin: afgift = afgift * indfasning - særligt bundfradrag   (min. 0)

Brugte køretøjer (REGAL § 4, stk. 6, § 5, stk. 7 og § 10):
    Afgiften beregnes som for en tilsvarende ny bil, men skalaknæk, fradrag og
    tillæg nedsættes med bilens værditab i procent. Værditabet er forholdet
    mellem bilens handelspris inkl. afgift og den oprindelige nypris inkl. afgift
    (Den juridiske vejledning I.A.1.5.3.1.1, SKM2014.427.HR).

    I praksis:
      1. Find den nye bils afgiftspligtige værdi V_ny, så V_ny + afgift(V_ny) = nypris.
      2. restværdi r = handelspris / nypris.
      3. Hvert element (skala, CO2-tillæg, bundfradrag, elbil-fradrag) ganges med r.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .satser import LAVEMISSION_GRAENSE_G_KM, Satser, hent_satser


def kr(x: float) -> str:
    """Dansk tusindtalsformat: 1234567 -> '1.234.567'."""
    return f"{x:,.0f}".replace(",", ".")


def pct(x: float, decimaler: int = 2) -> str:
    return f"{x * 100:.{decimaler}f}".replace(".", ",") + " %"


class Koeretoejstype(str, Enum):
    PERSONBIL = "personbil"
    VAREBIL = "varebil"


class Emissionsklasse(str, Enum):
    KONVENTIONEL = "konventionel"
    LAVEMISSION = "lavemission"  # typisk plugin-hybrid, under 50 g CO2/km
    NULEMISSION = "nulemission"  # typisk elbil, 0 g CO2/km


def emissionsklasse(co2_g_km: float) -> Emissionsklasse:
    if co2_g_km <= 0:
        return Emissionsklasse.NULEMISSION
    if co2_g_km < LAVEMISSION_GRAENSE_G_KM:
        return Emissionsklasse.LAVEMISSION
    return Emissionsklasse.KONVENTIONEL


@dataclass
class Trin:
    tekst: str
    beloeb: float

    def as_dict(self) -> dict:
        return {"tekst": self.tekst, "beloeb": round(self.beloeb, 2)}


@dataclass
class AfgiftsBeregning:
    registreringsafgift: int
    afgiftspligtig_vaerdi: int
    aar: int
    koeretoejstype: Koeretoejstype
    emissionsklasse: Emissionsklasse
    co2_g_km: float
    brugt: bool
    handelspris_inkl_afgift: int | None = None
    nypris_inkl_afgift: int | None = None
    restvaerdi_pct: float | None = None
    trin: list[Trin] = field(default_factory=list)
    advarsler: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "registreringsafgift": self.registreringsafgift,
            "afgiftspligtig_vaerdi": self.afgiftspligtig_vaerdi,
            "aar": self.aar,
            "koeretoejstype": self.koeretoejstype.value,
            "emissionsklasse": self.emissionsklasse.value,
            "co2_g_km": self.co2_g_km,
            "brugt": self.brugt,
            "handelspris_inkl_afgift": self.handelspris_inkl_afgift,
            "nypris_inkl_afgift": self.nypris_inkl_afgift,
            "restvaerdi_pct": None if self.restvaerdi_pct is None else round(self.restvaerdi_pct, 2),
            "trin": [t.as_dict() for t in self.trin],
            "advarsler": list(self.advarsler),
        }


@dataclass
class _Elementer:
    """Afgiftens elementer for en ny bil, før nedsættelse for værditab."""

    skala: float
    co2_tillaeg: float
    bundfradrag: float
    indfasning: float  # 1.0 for konventionelle biler
    saerligt_bundfradrag: float
    skala_trin: list[Trin]
    co2_trin: list[Trin]

    def afgift(self, r: float = 1.0) -> float:
        foer = max(0.0, (self.skala + self.co2_tillaeg - self.bundfradrag) * r)
        return max(0.0, foer * self.indfasning - self.saerligt_bundfradrag * r)


def _elementer(
    vaerdi: float, co2: float, type_: Koeretoejstype, satser: Satser
) -> _Elementer:
    klasse = emissionsklasse(co2)
    ind = satser.indfasning
    skala_trin: list[Trin] = []
    co2_trin: list[Trin] = []

    if type_ is Koeretoejstype.PERSONBIL:
        s = satser.personbil
        dele = [
            (s.sats1, 0.0, s.knaek1),
            (s.sats2, s.knaek1, s.knaek2),
            (s.sats3, s.knaek2, float("inf")),
        ]
        skala = 0.0
        for sats, fra, til in dele:
            del_ = max(0.0, min(vaerdi, til) - fra)
            if del_ > 0:
                skala += del_ * sats
                interval = f"over {kr(fra)}" if til == float("inf") else f"{kr(fra)}-{kr(til)}"
                skala_trin.append(Trin(f"{pct(sats, 0)} af {kr(del_)} kr. (værdi {interval} kr.)", del_ * sats))
        co2_dele = [
            (s.co2_lav, 0.0, s.co2_graense1),
            (s.co2_mellem, s.co2_graense1, s.co2_graense2),
            (s.co2_hoej, s.co2_graense2, float("inf")),
        ]
        co2_tillaeg = 0.0
        for sats, fra, til in co2_dele:
            g = max(0.0, min(co2, til) - fra)
            if g > 0:
                co2_tillaeg += g * sats
                co2_trin.append(Trin(f"CO2-tillæg {g:g} g/km á {sats:g} kr.", g * sats))
        bundfradrag = s.bundfradrag
        el_fradrag, plugin_fradrag = ind.el_bundfradrag_personbil, ind.plugin_bundfradrag_personbil
    else:
        s = satser.varebil
        del_ = max(0.0, vaerdi - s.graense)
        skala = del_ * s.sats
        if del_ > 0:
            skala_trin.append(Trin(f"{pct(s.sats, 0)} af værdi over {kr(s.graense)} kr.", skala))
        co2_tillaeg = max(0.0, co2) * s.co2_sats
        if co2_tillaeg:
            co2_trin.append(Trin(f"CO2-tillæg {co2:g} g/km á {s.co2_sats:g} kr.", co2_tillaeg))
        bundfradrag = s.bundfradrag
        el_fradrag, plugin_fradrag = ind.el_bundfradrag_varebil, ind.plugin_bundfradrag_varebil

    if klasse is Emissionsklasse.NULEMISSION:
        indfasning, saerligt = ind.el_pct, el_fradrag
    elif klasse is Emissionsklasse.LAVEMISSION:
        indfasning, saerligt = ind.plugin_pct, plugin_fradrag
    else:
        indfasning, saerligt = 1.0, 0.0

    return _Elementer(skala, co2_tillaeg, bundfradrag, indfasning, saerligt, skala_trin, co2_trin)


def _trin_liste(el: _Elementer, r: float, klasse: Emissionsklasse) -> list[Trin]:
    trin: list[Trin] = []
    suffix = "" if r == 1.0 else f" × restværdi {pct(r)}"
    for t in el.skala_trin:
        trin.append(Trin(t.tekst + suffix, t.beloeb * r))
    for t in el.co2_trin:
        trin.append(Trin(t.tekst + suffix, t.beloeb * r))
    trin.append(Trin("Bundfradrag" + suffix, -el.bundfradrag * r))
    foer = max(0.0, (el.skala + el.co2_tillaeg - el.bundfradrag) * r)
    trin.append(Trin("Afgift før indfasning", foer))
    if klasse is not Emissionsklasse.KONVENTIONEL:
        trin.append(Trin(f"Indfasning {pct(el.indfasning, 0)}", foer * el.indfasning - foer))
        trin.append(
            Trin(
                ("Særligt bundfradrag (elbil)" if klasse is Emissionsklasse.NULEMISSION else "Særligt bundfradrag (plugin)")
                + suffix,
                -el.saerligt_bundfradrag * r,
            )
        )
    trin.append(Trin("Registreringsafgift", el.afgift(r)))
    return trin


def _satser(aar: int | Satser) -> Satser:
    return aar if isinstance(aar, Satser) else hent_satser(aar)


def beregn_ny(
    afgiftspligtig_vaerdi: float,
    co2_g_km: float,
    koeretoejstype: Koeretoejstype = Koeretoejstype.PERSONBIL,
    aar: int | Satser = 2026,
) -> AfgiftsBeregning:
    """Registreringsafgift for et nyt køretøj ud fra afgiftspligtig værdi (inkl. moms, ekskl. afgift)."""
    satser = _satser(aar)
    el = _elementer(afgiftspligtig_vaerdi, co2_g_km, koeretoejstype, satser)
    klasse = emissionsklasse(co2_g_km)
    res = AfgiftsBeregning(
        registreringsafgift=round(el.afgift()),
        afgiftspligtig_vaerdi=round(afgiftspligtig_vaerdi),
        aar=satser.aar,
        koeretoejstype=koeretoejstype,
        emissionsklasse=klasse,
        co2_g_km=co2_g_km,
        brugt=False,
        trin=_trin_liste(el, 1.0, klasse),
    )
    if satser.foreloebig:
        res.advarsler.append(f"Satserne for {satser.aar} er foreløbige.")
    return res


def ny_vaerdi_fra_nypris(
    nypris_inkl_afgift: float,
    co2_g_km: float,
    koeretoejstype: Koeretoejstype = Koeretoejstype.PERSONBIL,
    aar: int | Satser = 2026,
) -> float:
    """Find afgiftspligtig værdi V, så V + afgift(V) = nypris inkl. afgift (baglæns beregning)."""
    satser = _satser(aar)
    lo, hi = 0.0, float(nypris_inkl_afgift)
    for _ in range(200):
        mid = (lo + hi) / 2
        total = mid + _elementer(mid, co2_g_km, koeretoejstype, satser).afgift()
        if total > nypris_inkl_afgift:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-6:
            break
    return (lo + hi) / 2


def beregn_brugt(
    handelspris_inkl_afgift: float,
    nypris_inkl_afgift: float,
    co2_g_km: float,
    koeretoejstype: Koeretoejstype = Koeretoejstype.PERSONBIL,
    aar: int | Satser = 2026,
    alder_aar: float | None = None,
) -> AfgiftsBeregning:
    """Registreringsafgift for et brugt køretøj (fx import) efter Motorstyrelsens metode.

    handelspris_inkl_afgift: forventet dansk udsalgspris inkl. afgift (fx fra Bilinfo).
    nypris_inkl_afgift: bilens oprindelige danske nypris inkl. afgift.
    alder_aar: bilens alder; for biler på 5 år eller mere kan værdien ikke
        overstige nyprisen (REGAL § 10, stk. 2).
    """
    if nypris_inkl_afgift <= 0 or handelspris_inkl_afgift <= 0:
        raise ValueError("Handelspris og nypris skal være positive.")
    satser = _satser(aar)
    advarsler: list[str] = []
    r = handelspris_inkl_afgift / nypris_inkl_afgift
    if r > 1:
        if alder_aar is not None and alder_aar >= 5:
            advarsler.append(
                "Handelsprisen overstiger nyprisen; for biler på 5 år eller mere begrænses værdien til nyprisen."
            )
            r = 1.0
        else:
            advarsler.append("Handelsprisen overstiger nyprisen - kontrollér priserne.")

    v_ny = ny_vaerdi_fra_nypris(nypris_inkl_afgift, co2_g_km, koeretoejstype, satser)
    el = _elementer(v_ny, co2_g_km, koeretoejstype, satser)
    klasse = emissionsklasse(co2_g_km)
    afgift = el.afgift(r)
    handelspris_brugt = r * nypris_inkl_afgift
    if satser.foreloebig:
        advarsler.append(f"Satserne for {satser.aar} er foreløbige.")

    trin = [
        Trin(f"Nypris inkl. afgift {kr(nypris_inkl_afgift)} kr. -> afgiftspligtig værdi som ny", v_ny),
        Trin(f"Restværdi i procent ({kr(handelspris_brugt)} / {kr(nypris_inkl_afgift)})", r * 100),
    ] + _trin_liste(el, r, klasse)

    return AfgiftsBeregning(
        registreringsafgift=round(afgift),
        afgiftspligtig_vaerdi=round(handelspris_brugt - afgift),
        aar=satser.aar,
        koeretoejstype=koeretoejstype,
        emissionsklasse=klasse,
        co2_g_km=co2_g_km,
        brugt=True,
        handelspris_inkl_afgift=round(handelspris_inkl_afgift),
        nypris_inkl_afgift=round(nypris_inkl_afgift),
        restvaerdi_pct=r * 100,
        trin=trin,
        advarsler=advarsler,
    )


def beregn_brugt_fra_pris_uden_afgift(
    pris_uden_afgift: float,
    nypris_inkl_afgift: float,
    co2_g_km: float,
    koeretoejstype: Koeretoejstype = Koeretoejstype.PERSONBIL,
    aar: int | Satser = 2026,
    alder_aar: float | None = None,
) -> AfgiftsBeregning:
    """Som beregn_brugt, men når kun prisen uden afgift kendes (fx Bilinfo 'RetailPriceWithoutTax').

    Finder handelsprisen H inkl. afgift, så H - afgift(H) = pris uden afgift.
    """
    satser = _satser(aar)
    lo, hi = float(pris_uden_afgift), float(pris_uden_afgift) * 4 + nypris_inkl_afgift
    for _ in range(200):
        mid = (lo + hi) / 2
        res = beregn_brugt(mid, nypris_inkl_afgift, co2_g_km, koeretoejstype, satser, alder_aar)
        if mid - res.registreringsafgift > pris_uden_afgift:
            hi = mid
        else:
            lo = mid
        if hi - lo < 0.5:
            break
    return beregn_brugt((lo + hi) / 2, nypris_inkl_afgift, co2_g_km, koeretoejstype, satser, alder_aar)
