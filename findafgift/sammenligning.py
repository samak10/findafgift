"""Find sammenlignelige biler i Bilinfo og estimér dansk handelspris og nypris.

Motorstyrelsen fastsætter handelsprisen for en brugt bil ud fra annoncerede priser
på tilsvarende biler i Danmark, justeret for kilometerstand, udstyr og stand
(Den juridiske vejledning I.A.1.5.3.1.1). Dette modul gør det samme med biler
fra Bilinfo. Justeringerne for km og årgang er skøn og kan konfigureres.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .bilinfo import BilinfoBil
from .co2 import normaliser_drivmiddel


@dataclass
class Tilbud:
    """Den bil, du har fået tilbudt."""

    maerke: str
    model: str
    aargang: int
    km: int
    variant: str = ""
    foerste_registrering: date | None = None
    motor: float | None = None  # liter
    hk: int | None = None
    drivmiddel: str = "benzin"  # benzin / diesel / el / plugin
    gear: str | None = None  # 'M' / 'A'
    udstyr: list[str] = field(default_factory=list)  # Bilinfo-udstyrsnavne, fx 'Navigation'
    co2_g_km: float | None = None
    km_pr_liter: float | None = None
    maalemetode: str = "wltp"  # 'wltp' eller 'nedc'
    nypris: int | None = None  # oprindelig dansk nypris inkl. afgift, hvis kendt
    koeretoejstype: str = "personbil"
    tilbudt_pris: int | None = None  # prisen du får bilen tilbudt til (uden dansk afgift)

    @classmethod
    def fra_dict(cls, d: dict) -> "Tilbud":
        felter = set(cls.__dataclass_fields__)
        ukendte = set(d) - felter
        if ukendte:
            raise ValueError(f"Ukendte felter i tilbud: {sorted(ukendte)}")
        d = dict(d)
        if isinstance(d.get("foerste_registrering"), str):
            d["foerste_registrering"] = date.fromisoformat(d["foerste_registrering"])
        return cls(**d)

    @property
    def er_plugin(self) -> bool:
        return self.drivmiddel.strip().lower() in ("plugin", "plug-in", "plugin-hybrid", "phev")


@dataclass
class Parametre:
    max_aar_forskel: int = 2
    min_score: float = 40.0
    max_antal: int = 10
    # Prisjustering af sammenlignelige biler til tilbuddets km og årgang (skøn):
    km_justering_pct_pr_10000: float = 1.0  # % pr. 10.000 km forskel
    aar_justering_pct: float = 6.0  # % pr. årgang forskel
    max_justering_pct: float = 25.0
    # Fradrag i annoncepris (fx forhandleravance/klargøring), standard 0 %:
    annoncefradrag_pct: float = 0.0

    @classmethod
    def fra_dict(cls, d: dict | None) -> "Parametre":
        return cls(**(d or {}))


@dataclass
class Sammenlignelig:
    bil: BilinfoBil
    score: float
    justeret_pris: float
    justering_pct: float
    forskelle: dict

    def as_dict(self) -> dict:
        return {
            **self.bil.kort(),
            "score": round(self.score, 1),
            "justering_pct": round(self.justering_pct, 2),
            "justeret_pris": round(self.justeret_pris),
            "forskelle": self.forskelle,
        }


@dataclass
class PrisEstimat:
    handelspris: int | None
    nypris: int | None
    nypris_kilde: str
    antal: int
    min_pris: int | None
    max_pris: int | None
    median_pris: int | None
    sammenlignelige: list[Sammenlignelig]
    advarsler: list[str]

    def as_dict(self) -> dict:
        return {
            "handelspris": self.handelspris,
            "nypris": self.nypris,
            "nypris_kilde": self.nypris_kilde,
            "antal_sammenlignelige": self.antal,
            "min_pris": self.min_pris,
            "max_pris": self.max_pris,
            "median_pris": self.median_pris,
            "sammenlignelige": [s.as_dict() for s in self.sammenlignelige],
            "advarsler": self.advarsler,
        }


def _norm(s: str | None) -> str:
    s = (s or "").lower()
    s = s.replace("ë", "e").replace("é", "e").replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str | None) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9æøå,.]+", _norm(s)) if t}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _model_match(tilbud_model: str, bil_model: str) -> bool:
    a, b = _norm(tilbud_model), _norm(bil_model)
    if not a or not b:
        return False
    return a == b or b.startswith(a + " ") or a.startswith(b + " ") or a.split(" ")[0] == b.split(" ")[0]


def _drivmiddel_klasse_tilbud(t: Tilbud) -> str:
    return "plugin" if t.er_plugin else normaliser_drivmiddel(t.drivmiddel)


def _drivmiddel_klasse_bil(b: BilinfoBil) -> str:
    return "plugin" if b.er_plugin else normaliser_drivmiddel(b.drivmiddel)


def _vaegtet_median(par: list[tuple[float, float]]) -> float | None:
    par = sorted(p for p in par if p[1] > 0)
    if not par:
        return None
    total = sum(w for _, w in par)
    acc = 0.0
    for v, w in par:
        acc += w
        if acc >= total / 2:
            return v
    return par[-1][0]


def score_bil(tilbud: Tilbud, bil: BilinfoBil) -> tuple[float, dict]:
    """Lighedsscore 0-100 og en oversigt over forskelle mellem tilbud og Bilinfo-bil."""
    score = 100.0
    forskelle: dict = {}

    if tilbud.aargang and bil.aargang:
        d = bil.aargang - tilbud.aargang
        score -= 8 * abs(d)
        forskelle["aargang"] = d
    if tilbud.km is not None and bil.km is not None:
        d = bil.km - tilbud.km
        score -= min(20.0, abs(d) / 10_000)
        forskelle["km"] = d
    if tilbud.motor and bil.motor:
        d = round(bil.motor - tilbud.motor, 2)
        score -= min(25.0, abs(d) * 25)
        forskelle["motor_liter"] = d
    if tilbud.hk and bil.hk:
        d = bil.hk - tilbud.hk
        score -= min(15.0, abs(d) * 0.2)
        forskelle["hk"] = d
    if tilbud.gear and bil.gear and tilbud.gear.upper()[0] != bil.gear.upper()[0]:
        score -= 5
        forskelle["gear"] = f"{tilbud.gear} vs {bil.gear}"
    if tilbud.variant:
        j = _jaccard(_tokens(tilbud.variant), _tokens(bil.variant))
        score -= 10 * (1 - j)
        forskelle["variant_lighed"] = round(j, 2)
    if tilbud.udstyr:
        a = {u.lower() for u in tilbud.udstyr}
        b = {u.lower() for u in bil.udstyr}
        j = _jaccard(a, b)
        score -= 10 * (1 - j)
        forskelle["udstyr_lighed"] = round(j, 2)
        forskelle["udstyr_kun_i_tilbud"] = sorted(u for u in tilbud.udstyr if u.lower() not in b)
        forskelle["udstyr_kun_i_bilinfo"] = sorted(u for u in bil.udstyr if u.lower() not in a)
    return max(0.0, score), forskelle


def find_sammenlignelige(
    tilbud: Tilbud, biler: list[BilinfoBil], parametre: Parametre | None = None
) -> list[Sammenlignelig]:
    p = parametre or Parametre()
    klasse = _drivmiddel_klasse_tilbud(tilbud)
    resultat: list[Sammenlignelig] = []
    set_kilder: set[str] = set()

    for bil in biler:
        if _norm(bil.maerke) != _norm(tilbud.maerke) or not _model_match(tilbud.model, bil.model):
            continue
        if bil.pris_inkl_afgift is None or bil.pris_inkl_afgift <= 0:
            continue  # leasing, pris uden afgift eller "ring for pris"
        if bil.koeretoejstype != tilbud.koeretoejstype:
            continue
        if klasse and _drivmiddel_klasse_bil(bil) != klasse:
            continue
        if tilbud.aargang and bil.aargang and abs(bil.aargang - tilbud.aargang) > p.max_aar_forskel:
            continue
        # Samme fysiske bil kan have flere annoncer (fx kontant + leasing).
        kilde = bil.vehicle_source_id or bil.vehicle_id
        if kilde in set_kilder:
            continue

        score, forskelle = score_bil(tilbud, bil)
        if score < p.min_score:
            continue

        justering = 0.0
        if bil.km is not None and tilbud.km is not None:
            justering += p.km_justering_pct_pr_10000 * (bil.km - tilbud.km) / 10_000
        if bil.aargang and tilbud.aargang:
            justering += p.aar_justering_pct * (tilbud.aargang - bil.aargang)
        justering = max(-p.max_justering_pct, min(p.max_justering_pct, justering))
        justeret = bil.pris_inkl_afgift * (1 + justering / 100)

        set_kilder.add(kilde)
        resultat.append(Sammenlignelig(bil, score, justeret, justering, forskelle))

    resultat.sort(key=lambda s: s.score, reverse=True)
    return resultat[: p.max_antal]


def estimer_priser(
    tilbud: Tilbud, biler: list[BilinfoBil], parametre: Parametre | None = None
) -> PrisEstimat:
    p = parametre or Parametre()
    sml = find_sammenlignelige(tilbud, biler, p)
    advarsler: list[str] = []

    if not sml:
        advarsler.append("Ingen sammenlignelige biler fundet i Bilinfo.")
        handelspris = None
        priser: list[float] = []
    else:
        vaegt = sum(s.score for s in sml)
        handelspris_f = sum(s.justeret_pris * s.score for s in sml) / vaegt
        handelspris_f *= 1 - p.annoncefradrag_pct / 100
        handelspris = round(handelspris_f)
        priser = sorted(s.justeret_pris for s in sml)
        if len(sml) < 3:
            advarsler.append(f"Kun {len(sml)} sammenlignelig(e) bil(er) - handelsprisen er usikker.")

    if tilbud.nypris:
        nypris, kilde = tilbud.nypris, "angivet"
    else:
        median = _vaegtet_median([(s.bil.nypris, s.score) for s in sml if s.bil.nypris])
        nypris = None if median is None else round(median)
        kilde = "bilinfo (vægtet median af sammenlignelige)" if nypris else "ukendt"
        if nypris:
            advarsler.append("Nyprisen er estimeret fra Bilinfo-biler - angiv bilens egen nypris for et præcist resultat.")
        else:
            advarsler.append("Nyprisen er ukendt - angiv 'nypris' (dansk nypris inkl. afgift).")

    def mid(xs: list[float]) -> int | None:
        if not xs:
            return None
        n = len(xs)
        return round(xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)

    return PrisEstimat(
        handelspris=handelspris,
        nypris=nypris,
        nypris_kilde=kilde,
        antal=len(sml),
        min_pris=round(priser[0]) if priser else None,
        max_pris=round(priser[-1]) if priser else None,
        median_pris=mid(priser),
        sammenlignelige=sml,
        advarsler=advarsler,
    )
