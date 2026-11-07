"""Vurdering af bilens stand ud fra Bilinfo-billeder og annoncetekst med Claude (vision).

Resultatet bruges efter Motorstyrelsens regler for vedligeholdelsesstand
(Den juridiske vejledning I.A.1.5.3.1.2 og registreringsafgiftsbekendtgørelsens § 30):

  * Den tilbudte bil: stand under middel giver -2 %, over middel +2 % (maks. 20.000 kr.).
    Den officielle stand fastsættes ved toldsynet - billedvurderingen er et forhåndsskøn.
  * Annoncebiler: Annoncer, der adskiller sig væsentligt fra den bil, der værdifastsættes
    (fx tydelige skader, der kræver reparation), fravælges. Mindre afvigelser i stand
    reguleres med ±2 %, så annonceprisen svarer til middel stand.

Kræver pakken `anthropic` (pip install anthropic) og en API-nøgle i ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .bilinfo import BilinfoBil

MODEL = "claude-opus-5-5"

SKADETYPER = [
    "bule",
    "ridse",
    "stenslag",
    "lakskade",
    "rust",
    "revne_glas",
    "faelgskade",
    "daekslid",
    "lygte",
    "kofanger",
    "interioer_slid",
    "interioer_skade",
    "andet",
]

SYSTEM = """Du er en erfaren dansk bilvurderingsmand, der vurderer brugte bilers stand ud fra \
forhandlerens annoncebilleder og annoncetekst. Vurderingen bruges til værdifastsættelse efter \
Motorstyrelsens regler, hvor en bils vedligeholdelsesstand er enten "middel", "under" (under \
middel) eller "over" (over middel).

Sådan skal standen forstås:
- middel: Normal stand for bilens alder og kilometerstand. Almindeligt brugsslid, små \
stenslag i front, lette overfladeridser og let slid på førersæde og rat er middel.
- under: Synlige skader eller slid ud over det normale for alder og km, som en køber vil \
kræve udbedret eller fradrag for. Fx buler, dybe ridser gennem lakken, rust, revner i ruder, \
skadede fælge eller kofangere, afskallet lak, tydeligt slidt eller skadet interiør, \
mismatchende farvetoner der tyder på dårlig reparation.
- over: Usædvanligt velholdt for alderen - fremstår næsten som ny, uden synligt slid.

Sæt "vaesentlig_afvigelse" til true, hvis bilen har skader af en karakter, der gør den \
væsentligt anderledes end en normalt vedligeholdt bil af samme model (fx tydelig \
kollisionsskade, større buler, omfattende rust, manglende dele). Sådanne biler bør ikke \
bruges som sammenligningsgrundlag.

Regler for vurderingen:
- Vurdér kun det, du faktisk kan se på billederne eller læse i annonceteksten. Gæt ikke.
- Forhandlerbilleder er ofte taget, så skader skjules. Er der kun få billeder, eller \
mangler vigtige vinkler (front, bag, begge sider, interiør), skal "sikkerhed" være "lav".
- Er der ingen synlige skader og normal stand, er standen "middel".
- Angiv for hver skade hvilket billede (1-indekseret) den ses på.
- Nævner annonceteksten skader eller mangler (fx "lille bule", "stenslag", "skade"), så \
medtag dem med billede_nr 0.
- Skriv begrundelsen kort på dansk."""

SKEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stand": {"type": "string", "enum": ["under", "middel", "over"]},
        "vaesentlig_afvigelse": {"type": "boolean"},
        "sikkerhed": {"type": "string", "enum": ["lav", "middel", "hoej"]},
        "skader": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": SKADETYPER},
                    "placering": {"type": "string"},
                    "alvor": {"type": "string", "enum": ["lille", "middel", "stor"]},
                    "billede_nr": {"type": "integer"},
                    "beskrivelse": {"type": "string"},
                },
                "required": ["type", "placering", "alvor", "billede_nr", "beskrivelse"],
                "additionalProperties": False,
            },
        },
        "manglende_vinkler": {"type": "array", "items": {"type": "string"}},
        "begrundelse": {"type": "string"},
    },
    "required": ["stand", "vaesentlig_afvigelse", "sikkerhed", "skader", "manglende_vinkler", "begrundelse"],
    "additionalProperties": False,
}


@dataclass
class Skade:
    type: str
    placering: str
    alvor: str
    billede_nr: int
    beskrivelse: str


@dataclass
class Billedvurdering:
    stand: str  # 'under' / 'middel' / 'over'
    vaesentlig_afvigelse: bool
    sikkerhed: str  # 'lav' / 'middel' / 'hoej'
    skader: list[Skade] = field(default_factory=list)
    manglende_vinkler: list[str] = field(default_factory=list)
    begrundelse: str = ""
    antal_billeder: int = 0
    fejl: str | None = None  # sat hvis vurderingen ikke kunne gennemføres

    @property
    def ok(self) -> bool:
        return self.fejl is None

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def fra_dict(cls, d: dict) -> "Billedvurdering":
        d = dict(d)
        d["skader"] = [Skade(**s) for s in d.get("skader", [])]
        return cls(**d)

    @classmethod
    def fejlet(cls, fejl: str, antal_billeder: int = 0) -> "Billedvurdering":
        return cls("middel", False, "lav", antal_billeder=antal_billeder, fejl=fejl)


def _billed_url(url: str, stoerrelse: str | None) -> str:
    """Bilinfo-billeder har størrelsen i ?class=S1600X1600 - den kan ændres for at spare tokens."""
    if not stoerrelse or "billeder.bilinfo.net" not in url:
        return url
    if "class=" in url:
        return re.sub(r"class=[^&]+", f"class={stoerrelse}", url)
    return url + ("&" if "?" in url else "?") + f"class={stoerrelse}"


def _info_tekst(info: dict) -> str:
    linjer = [f"- {k}: {v}" for k, v in info.items() if v not in (None, "", [])]
    return "Oplysninger om bilen (fra Bilinfo):\n" + "\n".join(linjer)


class Billedvurderer:
    """Vurderer bilers stand ud fra billeder med Claude.

    client: en `anthropic.Anthropic`-klient (oprettes automatisk hvis None).
    max_billeder: højst så mange billeder sendes pr. bil (styrer omkostningen).
    billedstoerrelse: Bilinfo-størrelse, fx 'S1600X1600' (detaljer) eller 'S800X800' (billigere).
    cache_fil: JSON-fil hvor vurderinger gemmes, så samme bil ikke vurderes to gange.
    """

    def __init__(
        self,
        client: Any = None,
        model: str = MODEL,
        max_billeder: int = 8,
        billedstoerrelse: str | None = "S1600X1600",
        effort: str = "medium",
        cache_fil: str | None = None,
        max_parallelle: int = 4,
    ) -> None:
        if client is None:
            import anthropic  # valgfri afhængighed

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.max_billeder = max_billeder
        self.billedstoerrelse = billedstoerrelse
        self.effort = effort
        self.cache_fil = cache_fil
        self.max_parallelle = max_parallelle
        self._laas = threading.Lock()
        self._cache: dict[str, dict] = {}
        if cache_fil and os.path.exists(cache_fil):
            with open(cache_fil, encoding="utf-8") as f:
                self._cache = json.load(f)

    # ------------------------------------------------------------------ cache
    def _noegle(self, billeder: list[str], info: dict) -> str:
        data = json.dumps({"m": self.model, "b": billeder, "i": info}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(data.encode()).hexdigest()

    def _gem_cache(self) -> None:
        if self.cache_fil:
            tmp = self.cache_fil + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.cache_fil)

    # ------------------------------------------------------------------ API
    def vurder(self, billeder: list[str], info: dict | None = None) -> Billedvurdering:
        """Vurdér standen ud fra billed-URL'er og evt. oplysninger/annoncetekst."""
        info = info or {}
        valgte = [_billed_url(u, self.billedstoerrelse) for u in billeder[: self.max_billeder]]
        if not valgte and not info.get("annoncetekst"):
            return Billedvurdering.fejlet("Ingen billeder eller annoncetekst at vurdere.")

        noegle = self._noegle(valgte, info)
        with self._laas:
            if noegle in self._cache:
                return Billedvurdering.fra_dict(self._cache[noegle])

        indhold: list[dict] = []
        for i, url in enumerate(valgte, 1):
            indhold.append({"type": "text", "text": f"Billede {i}:"})
            indhold.append({"type": "image", "source": {"type": "url", "url": url}})
        indhold.append(
            {
                "type": "text",
                "text": _info_tekst(info)
                + f"\n\nDer er {len(valgte)} billede(r). Vurdér bilens stand og find alle synlige skader.",
            }
        )

        try:
            import anthropic

            fejltyper: tuple = (anthropic.APIError,)
        except ImportError:  # pragma: no cover - kun ved injiceret klient uden SDK
            fejltyper = (Exception,)

        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SKEMA}},
                messages=[{"role": "user", "content": indhold}],
            )
        except fejltyper as e:
            return Billedvurdering.fejlet(f"API-fejl: {e}", len(valgte))

        if resp.stop_reason == "refusal":
            return Billedvurdering.fejlet("Modellen afviste at vurdere billederne.", len(valgte))
        if resp.stop_reason == "max_tokens":
            return Billedvurdering.fejlet("Svaret blev afkortet (max_tokens).", len(valgte))
        tekst = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if not tekst:
            return Billedvurdering.fejlet("Tomt svar fra modellen.", len(valgte))
        try:
            data = json.loads(tekst)
            res = Billedvurdering.fra_dict({**data, "antal_billeder": len(valgte)})
        except (ValueError, TypeError) as e:
            return Billedvurdering.fejlet(f"Kunne ikke læse svaret: {e}", len(valgte))

        with self._laas:
            self._cache[noegle] = res.as_dict()
            self._gem_cache()
        return res

    def vurder_bil(self, bil: BilinfoBil) -> Billedvurdering:
        info = {
            "bil": f"{bil.maerke} {bil.model} {bil.variant}".strip(),
            "årgang": bil.aargang,
            "første registrering": bil.foerste_registrering.isoformat() if bil.foerste_registrering else None,
            "kilometerstand": bil.km,
            "annoncetekst": bil.kommentar[:3000] if bil.kommentar else None,
        }
        return self.vurder(bil.billeder, info)

    def vurder_mange(self, biler: Iterable[BilinfoBil]) -> dict[str, Billedvurdering]:
        """Vurdér flere biler parallelt. Returnerer {vehicle_id: vurdering}."""
        biler = list(biler)
        with ThreadPoolExecutor(max_workers=max(1, self.max_parallelle)) as pool:
            resultater = list(pool.map(self.vurder_bil, biler))
        return {b.vehicle_id: r for b, r in zip(biler, resultater)}
