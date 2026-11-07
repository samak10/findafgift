"""Hent sammenlignelige bilannoncer fra DBA (dba.dk) til intern prisvurdering.

Bruges med DBA's/Vend Marketplaces' godkendelse. Klienten er bevidst hensynsfuld:
  * identificerer sig ærligt med en User-Agent (kan sættes til jeres kontaktinfo),
  * overholder robots.txt,
  * holder pause mellem forespørgsler (standard 3 sek.) og cacher svar på disk,
  * gemmer ikke persondata (registreringsnummer, stelnummer, koordinater, sælgernavn).

Data hentes fra søgesidens indlejrede JSON (søgeresultater) og fra annoncesidens
specifikationstabel (effekt, slagvolumen, CO2, forbrug, karrosseri, 1. registrering).
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import date, datetime
from typing import Callable

from .bilinfo import BilinfoBil, dato, heltal, tal

BASIS = "https://www.dba.dk"
SOEG = BASIS + "/mobility/search/car"
STANDARD_UA = "findafgift/0.1 (intern prisvurdering)"

REGISTRERINGSKLASSE = {"personbil": "1", "varebil": "2"}  # 2 = varebil inkl. moms (8 = ex. moms bruges ikke)
MAERKE_ALIAS = {"vw": "volkswagen", "volkswagen": "vw", "mercedes": "mercedes-benz", "mercedes-benz": "mercedes"}


class DbaFejl(Exception):
    pass


def _norm(s: str | None) -> str:
    s = (s or "").lower().replace("ë", "e").replace("é", "e")
    s = re.sub(r"-serie$", "", s.strip())
    return re.sub(r"[\s-]+", " ", s).strip()


def _tekst(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _react_query_state(side: str) -> dict:
    m = re.search(r"data-react-query-state[^>]*>(.*?)</script>", side, re.S)
    if not m:
        raise DbaFejl("Søgesiden indeholder ikke de forventede data (DBA kan have ændret siden).")
    indhold = m.group(1).strip()
    try:
        return json.loads(base64.b64decode(indhold))
    except ValueError:
        return json.loads(html.unescape(indhold))


def _soegedata(side: str) -> dict:
    state = _react_query_state(side)
    for q in state.get("queries", []):
        key = (q.get("queryKey") or [{}])[0]
        if isinstance(key, dict) and key.get("scope") == "search":
            return q["state"]["data"]
    raise DbaFejl("Ingen søgeresultater fundet i siden.")


def parse_specifikationer(side: str) -> dict[str, str]:
    """Annoncesidens specifikationstabel (dt/dd) som {label: værdi}."""
    spec: dict[str, str] = {}
    for dt, dd in re.findall(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", side, re.S):
        label = _tekst(dt)
        # Label kan indeholde en forklaringstekst, fx "Brændstofforbrug (NEDC)NEDC var ..."
        m = re.match(r"^(.*?\))", label)
        if m and "(" in label and len(label) > 40:
            label = m.group(1)
        spec[label] = _tekst(dd)
    return spec


def parse_beskrivelse(side: str) -> str:
    m = re.search(r">Beskrivelse</h2>.*?<div[^>]*whitespace-pre-wrap[^>]*>(.*?)</div>", side, re.S)
    return _tekst(m.group(1))[:5000] if m else ""


def _gear(v: str | None) -> str | None:
    v = (v or "").lower()
    return "A" if v.startswith("auto") else "M" if v.startswith("manu") else None


def _drivmiddel(v: str | None) -> tuple[str, str | None]:
    """DBA-drivmiddel -> (Bilinfo-drivmiddel, sekundært drivmiddel)."""
    v = (v or "").strip()
    lv = v.lower()
    if "plug" in lv:
        return ("Diesel" if "diesel" in lv else "Benzin"), "El"
    if lv in ("el", "elektrisk"):
        return "El", None
    if lv.startswith("diesel"):
        return "Diesel", None
    if lv.startswith("benzin") or "hybrid" in lv:
        return "Benzin", None
    return v, None


def bil_fra_soegeresultat(doc: dict) -> BilinfoBil | None:
    """Et DBA-søgeresultat som BilinfoBil (uden persondata)."""
    pris = (doc.get("price") or {}).get("amount")
    if not pris:
        return None
    klasse = str((doc.get("registration_class") or {}).get("id", ""))
    if klasse == "8":
        return None  # varebil ex. moms - prisen er ikke sammenlignelig
    drivmiddel, sekundaert = _drivmiddel(doc.get("fuel"))
    ts = doc.get("timestamp")
    oprettet = datetime.fromtimestamp(ts / 1000).date() if ts else None
    vid = f"dba-{doc.get('ad_id') or doc.get('id')}"
    model = doc.get("model") or ""
    return BilinfoBil(
        vehicle_id=vid,
        vehicle_source_id=vid,
        maerke=doc.get("make") or "",
        model=model,
        variant=doc.get("model_specification") or "",
        aargang=heltal(doc.get("year")),
        foerste_registrering=None,
        km=heltal(doc.get("mileage")),
        motor=None,
        hk=None,
        drivmiddel=drivmiddel,
        sekundaert_drivmiddel=sekundaert,
        type_id="V" if klasse == "2" else "P",
        type="Varevogn" if klasse == "2" else "Personvogn",
        gear=_gear(doc.get("transmission")),
        karrosseri=None,
        udstyr=[],
        pris=int(pris),
        pris_type="RetailPrice",
        pris_inkl_afgift=int(pris),
        pris_uden_afgift=None,
        nypris=None,
        co2_g_km=None,
        km_pr_liter=None,
        maalemetode=None,
        forhandler=None,
        oprettet=oprettet,
        billeder=list(doc.get("image_urls") or []),
        kilde="dba",
        url=doc.get("canonical_url"),
        privat=(doc.get("dealer_segment") or "").lower() == "privat",
        raw={"dba_id": doc.get("ad_id"), "serie": doc.get("series"), "dba_drivmiddel": doc.get("fuel")},
    )


def berig_med_annonceside(bil: BilinfoBil, side: str) -> BilinfoBil:
    """Tilføj oplysninger fra annoncesidens specifikationstabel."""
    spec = parse_specifikationer(side)
    hk = re.search(r"(\d+)\s*hk", spec.get("Effekt", ""), re.I)
    if hk:
        bil.hk = int(hk.group(1))
    if spec.get("Slagvolumen"):
        bil.motor = tal(spec["Slagvolumen"].lower().replace("l", "").strip())
    if spec.get("Karosseri"):
        bil.karrosseri = spec["Karosseri"]
    if spec.get("1. registrering"):
        bil.foerste_registrering = dato(spec["1. registrering"])
    co2 = re.search(r"(\d+(?:[.,]\d+)?)", spec.get("CO₂-udledning", "") or spec.get("CO2-udledning", ""))
    if co2:
        bil.co2_g_km = tal(co2.group(1))
    for label, vaerdi in spec.items():
        if label.lower().startswith("brændstofforbrug"):
            kml = re.search(r"(\d+(?:[.,]\d+)?)\s*km/l", vaerdi, re.I)
            if kml:
                bil.km_pr_liter = tal(kml.group(1))
            bil.maalemetode = "wltp" if "wltp" in label.lower() else "nedc1" if "nedc" in label.lower() else None
    if spec.get("Kilometertal") and bil.km is None:
        bil.km = heltal(re.sub(r"[^\d]", "", spec["Kilometertal"]))
    # Plugin-hybrid: DBA skriver "Hybrid benzin/diesel" for både plugin- og almindelige hybrider.
    # Plugin-hybrider har opladeligt batteri med elektrisk rækkevidde og CO2 under 50 g/km.
    dba_drivmiddel = (spec.get("Drivmiddel") or bil.raw.get("dba_drivmiddel") or "").lower()
    if "hybrid" in dba_drivmiddel or "plug" in dba_drivmiddel:
        har_raekkevidde = any(k.lower().startswith("rækkevidde") for k in spec)
        if "plug" in dba_drivmiddel or har_raekkevidde or (bil.co2_g_km is not None and bil.co2_g_km < 50):
            bil.sekundaert_drivmiddel = "El"
            bil.drivmiddel = "Diesel" if "diesel" in dba_drivmiddel else "Benzin"
    bil.raw.update({k: v for k, v in spec.items() if k not in ("Registreringsnummer", "Stelnummer")})
    bil.kommentar = parse_beskrivelse(side)
    return bil


class DbaKlient:
    """Henter bilannoncer fra DBA med pauser, cache og robots.txt.

    user_agent: sæt gerne jeres firmanavn og kontakt, fx "findafgift (firma.dk; it@firma.dk)".
    forsinkelse: sekunder mellem forespørgsler til dba.dk.
    cache_mappe: mappe til diskcache; cache_timer: hvor længe en side genbruges.
    hent: kun til tests - funktion der returnerer HTML for en URL.
    """

    def __init__(
        self,
        user_agent: str | None = None,
        forsinkelse: float = 3.0,
        cache_mappe: str | None = None,
        cache_timer: float = 12.0,
        timeout: float = 30.0,
        overhold_robots: bool = True,
        hent: Callable[[str], str] | None = None,
    ) -> None:
        self.user_agent = user_agent or os.environ.get("DBA_USER_AGENT") or STANDARD_UA
        self.forsinkelse = forsinkelse
        self.cache_mappe = cache_mappe
        self.cache_timer = cache_timer
        self.timeout = timeout
        self.overhold_robots = overhold_robots
        self._hent_fn = hent
        self._sidst = 0.0
        self._robots: urllib.robotparser.RobotFileParser | None = None
        self._maerketrae: list[dict] | None = None
        if cache_mappe:
            os.makedirs(cache_mappe, exist_ok=True)

    # ------------------------------------------------------------- HTTP
    def _tilladt(self, url: str) -> bool:
        if not self.overhold_robots or self._hent_fn is not None:
            return True
        if self._robots is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                req = urllib.request.Request(BASIS + "/robots.txt", headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    rp.parse(r.read().decode("utf-8", "replace").splitlines())
            except OSError:
                rp.parse([])
            self._robots = rp
        return self._robots.can_fetch(self.user_agent, url)

    def _cache_sti(self, url: str) -> str | None:
        if not self.cache_mappe:
            return None
        return os.path.join(self.cache_mappe, hashlib.sha256(url.encode()).hexdigest() + ".html")

    def hent(self, url: str) -> str:
        sti = self._cache_sti(url)
        if sti and os.path.exists(sti) and time.time() - os.path.getmtime(sti) < self.cache_timer * 3600:
            with open(sti, encoding="utf-8") as f:
                return f.read()
        if not self._tilladt(url):
            raise DbaFejl(f"robots.txt tillader ikke {url}")
        if self._hent_fn is not None:
            side = self._hent_fn(url)
        else:
            vent = self.forsinkelse - (time.time() - self._sidst)
            if vent > 0:
                time.sleep(vent)
            req = urllib.request.Request(
                url, headers={"User-Agent": self.user_agent, "Accept": "text/html", "Accept-Language": "da-DK"}
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    if r.status != 200:
                        raise DbaFejl(f"DBA svarede {r.status} for {url}")
                    side = r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                raise DbaFejl(f"DBA svarede {e.code} for {url}") from e
            finally:
                self._sidst = time.time()
        if sti:
            with open(sti, "w", encoding="utf-8") as f:
                f.write(side)
        return side

    # ------------------------------------------------------------- mærker og modeller
    def maerketrae(self) -> list[dict]:
        if self._maerketrae is None:
            data = _soegedata(self.hent(SOEG + "?registration_class=1"))
            filt = next((f for f in data.get("filters", []) if f.get("name") == "variant"), None)
            if not filt:
                raise DbaFejl("Mærke/model-filteret blev ikke fundet.")
            self._maerketrae = filt.get("filter_items", [])
        return self._maerketrae

    def variant_kode(self, maerke: str, model: str) -> str | None:
        """DBA's filterkode for mærke+model, fx VW Golf -> '1.817.xxxx'."""
        m = _norm(maerke)
        navne = {m, MAERKE_ALIAS.get(m, m)}
        mk = next((x for x in self.maerketrae() if _norm(x.get("display_name")) in navne), None)
        if mk is None:
            return None
        maal = _norm(model)
        bedste: tuple[int, str] | None = None

        def gaa(noder: list[dict], dybde: int) -> None:
            nonlocal bedste
            for n in noder:
                navn = _norm(n.get("display_name"))
                if navn == maal:
                    kandidat = (0, n["value"])  # præcist navn - foretræk serie (bredere) frem for undermodel
                elif navn.startswith(maal + " ") or maal.startswith(navn + " "):
                    kandidat = (1 + dybde, n["value"])
                else:
                    kandidat = None
                if kandidat and (bedste is None or kandidat[0] < bedste[0]):
                    bedste = kandidat
                gaa(n.get("filter_items") or [], dybde + 1)

        gaa(mk.get("filter_items") or [], 0)
        return bedste[1] if bedste else mk["value"]

    # ------------------------------------------------------------- søgning
    def soeg(
        self,
        maerke: str,
        model: str,
        aar_fra: int | None = None,
        aar_til: int | None = None,
        koeretoejstype: str = "personbil",
        max_sider: int = 3,
    ) -> list[BilinfoBil]:
        kode = self.variant_kode(maerke, model)
        if kode is None:
            raise DbaFejl(f"Mærket '{maerke}' findes ikke på DBA.")
        params = {"variant": kode, "registration_class": REGISTRERINGSKLASSE.get(koeretoejstype, "1")}
        if aar_fra:
            params["year_from"] = str(aar_fra)
        if aar_til:
            params["year_to"] = str(aar_til)
        biler: list[BilinfoBil] = []
        for side_nr in range(1, max_sider + 1):
            url = SOEG + "?" + urllib.parse.urlencode({**params, "page": str(side_nr)})
            data = _soegedata(self.hent(url))
            for doc in data.get("docs", []):
                bil = bil_fra_soegeresultat(doc)
                if bil:
                    biler.append(bil)
            paging = (data.get("metadata") or {}).get("paging") or {}
            if side_nr >= int(paging.get("last") or 1):
                break
        return biler

    def berig(self, bil: BilinfoBil) -> BilinfoBil:
        if bil.url:
            berig_med_annonceside(bil, self.hent(bil.url))
        return bil

    def hent_annonce(self, url_eller_id: str) -> BilinfoBil:
        """Hent én DBA-annonce (fx den bil I får tilbudt) ud fra URL eller annonce-id."""
        m_id = re.search(r"/item/(\d+)", url_eller_id) or re.fullmatch(r"\s*(\d+)\s*", url_eller_id)
        if not m_id:
            raise DbaFejl("Ugyldigt DBA-id/URL - forventer fx https://www.dba.dk/mobility/item/12345678")
        ad_id = m_id
        url = f"{BASIS}/mobility/item/{ad_id.group(1)}"
        side = self.hent(url)
        spec = parse_specifikationer(side)
        pris = re.search(r'"price"\s*:\s*"?(\d+)', side)
        drivmiddel, sekundaert = _drivmiddel(spec.get("Drivmiddel"))
        bil = BilinfoBil(
            vehicle_id=f"dba-{ad_id.group(1)}",
            vehicle_source_id=f"dba-{ad_id.group(1)}",
            maerke=spec.get("Mærke", ""),
            model=spec.get("Model", ""),
            variant=_tekst(m.group(1)) if (m := re.search(r"<h1[^>]*>.*?</h1>\s*<p[^>]*>(.*?)</p>", side, re.S)) else "",
            aargang=heltal(spec.get("Modelår")),
            foerste_registrering=None,
            km=None,
            motor=None,
            hk=None,
            drivmiddel=drivmiddel,
            sekundaert_drivmiddel=sekundaert,
            type_id="V" if "vare" in spec.get("Kategori", "").lower() else "P",
            type=spec.get("Kategori", "Personbil"),
            gear=_gear(spec.get("Geartype")),
            karrosseri=None,
            udstyr=[],
            pris=heltal(pris.group(1)) if pris else None,
            pris_type="RetailPrice",
            pris_inkl_afgift=heltal(pris.group(1)) if pris else None,
            pris_uden_afgift=None,
            nypris=None,
            co2_g_km=None,
            km_pr_liter=None,
            maalemetode=None,
            forhandler=None,
            billeder=sorted(set(re.findall(r'"contentUrl"\s*:\s*"([^"]+)"', side)), key=side.find),
            kilde="dba",
            url=url,
            privat='"@type": "Person"' in side or '"@type":"Person"' in side,
        )
        return berig_med_annonceside(bil, side)


def hent_sammenlignelige(
    klient: DbaKlient,
    maerke: str,
    model: str,
    aargang: int,
    drivmiddel: str | None = None,
    koeretoejstype: str = "personbil",
    max_aar: int = 2,
    max_sider: int = 3,
    max_berig: int = 30,
    idag: date | None = None,
    max_liggetid_dage: int | None = None,
) -> list[BilinfoBil]:
    """Søg DBA og berig de mest relevante annoncer med detaljer fra annoncesiden.

    Kun annoncer med samme drivmiddel og modelår inden for ±max_aar beriges (det
    sparer forespørgsler); de mest nærliggende årgange beriges først.
    """
    from .co2 import normaliser_drivmiddel

    biler = klient.soeg(maerke, model, aargang - max_aar, aargang + max_aar, koeretoejstype, max_sider)
    d = None
    if drivmiddel:
        d = "plugin" if drivmiddel.lower() in ("plugin", "plug-in", "phev") else normaliser_drivmiddel(drivmiddel)
        # Grovfilter før berigelse: plugin-hybrider kendes først fra annoncesiden.
        familie = {"plugin": ("benzin", "diesel"), "benzin": ("benzin",), "diesel": ("diesel",), "el": ("el",)}
        biler = [b for b in biler if normaliser_drivmiddel(b.drivmiddel) in familie.get(d, (d,)) or b.er_plugin]
    if max_liggetid_dage is not None:
        idag = idag or date.today()
        biler = [b for b in biler if b.oprettet is None or (idag - b.oprettet).days <= max_liggetid_dage]
    biler.sort(key=lambda b: abs((b.aargang or aargang) - aargang))
    berigede = []
    for b in biler[:max_berig]:
        try:
            berigede.append(klient.berig(b))
        except DbaFejl:
            continue
    if d:
        berigede = [b for b in berigede if ("plugin" if b.er_plugin else normaliser_drivmiddel(b.drivmiddel)) == d]
    return berigede
