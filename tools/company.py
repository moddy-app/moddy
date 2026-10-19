"""
``/company`` core — French company lookup on the open ``recherche-entreprises``
API (data.gouv.fr / INSEE): query classification (SIREN / SIRET with the Luhn
checksum), response parsing and the small INSEE code tables.

Pure except :func:`search_companies`, which receives the ``bot.public_api``
client. The API is free, keyless and limited to ~7 requests per second.

The label tables are vendored on purpose (the API returns codes only): French
legal forms (INSEE "catégories juridiques"), the NAF rev. 2 sections and
divisions, and the headcount brackets. Legal-form and NAF labels are the
official French wording; the card translates everything around them.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

API_URL = "https://recherche-entreprises.api.gouv.fr/search"
DIRECTORY_URL = "https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}"
MIN_TEXT_LENGTH = 3
MAX_QUERY_LENGTH = 100
PER_PAGE = 10
MAX_DIRECTORS = 3

# --------------------------------------------------------------------------- #
# Query classification
# --------------------------------------------------------------------------- #

KIND_TEXT = "text"
KIND_SIREN = "siren"
KIND_SIRET = "siret"

#: The only SIREN whose SIRETs may also be valid with "sum of digits divisible by 5".
LA_POSTE_SIREN = "356000000"


def luhn_valid(digits: str) -> bool:
    """Luhn checksum of a digit string (used by SIREN and SIRET)."""
    if not digits or not digits.isdigit():
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def siren_valid(siren: str) -> bool:
    return len(siren) == 9 and luhn_valid(siren)


def siret_valid(siret: str) -> bool:
    if len(siret) != 14 or not siret.isdigit():
        return False
    if luhn_valid(siret):
        return True
    # La Poste numbers its establishments by another rule (digit sum divisible
    # by 5) on top of the usual checksum; the head office passes the Luhn test.
    return siret.startswith(LA_POSTE_SIREN) and sum(int(c) for c in siret) % 5 == 0


@dataclass(frozen=True)
class Query:
    kind: str            # text / siren / siret
    value: str           # normalised: digits only for a number, trimmed text otherwise
    valid: bool = True   # False when a SIREN / SIRET fails its checksum


_NUMBER_NOISE = re.compile(r"[\s.\-]")


def classify_query(raw: str) -> Query:
    """Tell a SIREN (9 digits) / SIRET (14 digits) from a name, tolerating spaces."""
    text = (raw or "").strip()
    compact = _NUMBER_NOISE.sub("", text)
    if compact.isdigit():
        if len(compact) == 9:
            return Query(KIND_SIREN, compact, siren_valid(compact))
        if len(compact) == 14:
            return Query(KIND_SIRET, compact, siret_valid(compact))
    return Query(KIND_TEXT, re.sub(r"\s+", " ", text))


def format_siren(siren: str) -> str:
    """``552 032 534`` — the way French documents print it."""
    return f"{siren[0:3]} {siren[3:6]} {siren[6:9]}" if len(siren) == 9 else siren


def format_siret(siret: str) -> str:
    return f"{format_siren(siret[:9])} {siret[9:]}" if len(siret) == 14 else siret


# --------------------------------------------------------------------------- #
# Code tables
# --------------------------------------------------------------------------- #

#: Headcount bracket code -> (kind, low, high). ``NN`` = no employee, never an
#: employer; ``00`` = employer with 0 employee at year end.
HEADCOUNT: Dict[str, Tuple[str, int, int]] = {
    "NN": ("none", 0, 0), "00": ("zero", 0, 0),
    "01": ("range", 1, 2), "02": ("range", 3, 5), "03": ("range", 6, 9),
    "11": ("range", 10, 19), "12": ("range", 20, 49), "21": ("range", 50, 99),
    "22": ("range", 100, 199), "31": ("range", 200, 249), "32": ("range", 250, 499),
    "41": ("range", 500, 999), "42": ("range", 1000, 1999), "51": ("range", 2000, 4999),
    "52": ("range", 5000, 9999), "53": ("plus", 10000, 10000),
}


def headcount_bracket(code: Optional[str]) -> Optional[Tuple[str, int, int]]:
    return HEADCOUNT.get(str(code).upper()) if code not in (None, "") else None


#: INSEE "catégorie juridique" (4 digits) for the forms people actually meet.
LEGAL_FORMS: Dict[str, str] = {
    "1000": "Entrepreneur individuel",
    "2110": "Indivision entre personnes physiques",
    "2210": "Société créée de fait entre personnes physiques",
    "2310": "Société en participation entre personnes physiques",
    "2900": "Autre groupement de droit privé non doté de la personnalité morale",
    "3120": "Société commerciale étrangère immatriculée au RCS",
    "4110": "Établissement public national à caractère industriel ou commercial",
    "5202": "Société en nom collectif (SNC)",
    "5306": "Société en commandite simple (SCS)",
    "5308": "Société en commandite par actions (SCA)",
    "5370": "Société de participations financières de profession libérale (SPFPL)",
    "5385": "Société d'exercice libéral en commandite par actions (SELCA)",
    "5410": "SARL nationale",
    "5415": "SARL d'économie mixte",
    "5485": "Société d'exercice libéral à responsabilité limitée (SELARL)",
    "5498": "Entreprise unipersonnelle à responsabilité limitée (EURL)",
    "5499": "Société à responsabilité limitée (SARL)",
    "5505": "SA à participation ouvrière à conseil d'administration",
    "5510": "SA nationale à conseil d'administration",
    "5515": "SA d'économie mixte à conseil d'administration",
    "5599": "Société anonyme à conseil d'administration (SA)",
    "5699": "Société anonyme à directoire (SA)",
    "5710": "Société par actions simplifiée (SAS)",
    "5720": "Société par actions simplifiée unipersonnelle (SASU)",
    "5800": "Société coopérative européenne",
    "6220": "Groupement d'intérêt économique (GIE)",
    "6316": "Coopérative d'utilisation de matériel agricole en commun (CUMA)",
    "6317": "Société coopérative agricole",
    "6521": "Société civile de placement immobilier (SCPI)",
    "6533": "Groupement agricole d'exploitation en commun (GAEC)",
    "6540": "Société civile immobilière (SCI)",
    "6551": "Société civile d'exploitation agricole (SCEA)",
    "6589": "Société civile de moyens",
    "6598": "Exploitation agricole à responsabilité limitée (EARL)",
    "6599": "Autre société civile",
    "7210": "Commune",
    "7220": "Département",
    "7230": "Région",
    "7346": "Communauté de communes",
    "7348": "Communauté d'agglomération",
    "7361": "Centre communal d'action sociale",
    "7410": "Groupement d'intérêt public (GIP)",
    "8210": "Mutuelle",
    "8420": "Syndicat patronal",
    "8450": "Ordre professionnel ou assimilé",
    "9110": "Syndicat de copropriétaires",
    "9220": "Association déclarée",
    "9221": "Association déclarée d'insertion par l'économique",
    "9223": "Groupement d'employeurs",
    "9224": "Association d'avocats à responsabilité professionnelle individuelle",
    "9230": "Association déclarée, reconnue d'utilité publique",
    "9260": "Association de droit local",
    "9300": "Fondation",
    "9900": "Autre personne morale de droit privé",
}

#: Fallback on the first two digits when the exact code is not vendored.
LEGAL_CATEGORIES: Dict[str, str] = {
    "10": "Entrepreneur individuel",
    "21": "Indivision",
    "22": "Société créée de fait",
    "23": "Société en participation",
    "29": "Groupement de droit privé non doté de la personnalité morale",
    "31": "Personne morale de droit étranger",
    "32": "Personne morale de droit étranger",
    "41": "Établissement public ou régie à caractère industriel ou commercial",
    "51": "Société coopérative commerciale particulière",
    "52": "Société en nom collectif",
    "53": "Société en commandite",
    "54": "Société à responsabilité limitée",
    "55": "Société anonyme à conseil d'administration",
    "56": "Société anonyme à directoire",
    "57": "Société par actions simplifiée",
    "58": "Société européenne",
    "61": "Caisse d'épargne et de prévoyance",
    "62": "Groupement d'intérêt économique",
    "63": "Société coopérative agricole",
    "64": "Société d'assurance mutuelle",
    "65": "Société civile",
    "69": "Autre personne morale de droit privé inscrite au RCS",
    "71": "Administration de l'État",
    "72": "Collectivité territoriale",
    "73": "Établissement public administratif",
    "74": "Autre personne morale de droit public administratif",
    "81": "Organisme de protection sociale à adhésion obligatoire",
    "82": "Organisme mutualiste",
    "83": "Comité social et économique",
    "84": "Organisme professionnel",
    "85": "Organisme de retraite à adhésion non obligatoire",
    "91": "Syndicat de propriétaires",
    "92": "Association loi 1901 ou assimilé",
    "93": "Fondation",
    "99": "Autre personne morale de droit privé",
}


def legal_form_label(code: Optional[str]) -> Optional[str]:
    """Official French label of a ``nature_juridique`` code, ``None`` if unknown."""
    code = str(code or "").strip()
    if not code:
        return None
    return LEGAL_FORMS.get(code) or LEGAL_CATEGORIES.get(code[:2])


#: NAF rev. 2 divisions (2 digits) -> French label.
NAF_DIVISIONS: Dict[str, str] = {
    "01": "Culture et production animale, chasse et services annexes",
    "02": "Sylviculture et exploitation forestière",
    "03": "Pêche et aquaculture",
    "05": "Extraction de houille et de lignite",
    "06": "Extraction d'hydrocarbures",
    "07": "Extraction de minerais métalliques",
    "08": "Autres industries extractives",
    "09": "Services de soutien aux industries extractives",
    "10": "Industries alimentaires",
    "11": "Fabrication de boissons",
    "12": "Fabrication de produits à base de tabac",
    "13": "Fabrication de textiles",
    "14": "Industrie de l'habillement",
    "15": "Industrie du cuir et de la chaussure",
    "16": "Travail du bois et fabrication d'articles en bois et en liège",
    "17": "Industrie du papier et du carton",
    "18": "Imprimerie et reproduction d'enregistrements",
    "19": "Cokéfaction et raffinage",
    "20": "Industrie chimique",
    "21": "Industrie pharmaceutique",
    "22": "Fabrication de produits en caoutchouc et en plastique",
    "23": "Fabrication d'autres produits minéraux non métalliques",
    "24": "Métallurgie",
    "25": "Fabrication de produits métalliques",
    "26": "Fabrication de produits informatiques, électroniques et optiques",
    "27": "Fabrication d'équipements électriques",
    "28": "Fabrication de machines et équipements n.c.a.",
    "29": "Industrie automobile",
    "30": "Fabrication d'autres matériels de transport",
    "31": "Fabrication de meubles",
    "32": "Autres industries manufacturières",
    "33": "Réparation et installation de machines et d'équipements",
    "35": "Production et distribution d'électricité, de gaz et d'air conditionné",
    "36": "Captage, traitement et distribution d'eau",
    "37": "Collecte et traitement des eaux usées",
    "38": "Collecte, traitement et élimination des déchets ; récupération",
    "39": "Dépollution et autres services de gestion des déchets",
    "41": "Construction de bâtiments",
    "42": "Génie civil",
    "43": "Travaux de construction spécialisés",
    "45": "Commerce et réparation d'automobiles et de motocycles",
    "46": "Commerce de gros",
    "47": "Commerce de détail",
    "49": "Transports terrestres et transport par conduites",
    "50": "Transports par eau",
    "51": "Transports aériens",
    "52": "Entreposage et services auxiliaires des transports",
    "53": "Activités de poste et de courrier",
    "55": "Hébergement",
    "56": "Restauration",
    "58": "Édition",
    "59": "Production de films, vidéo, programmes de télévision et musique",
    "60": "Programmation et diffusion",
    "61": "Télécommunications",
    "62": "Programmation, conseil et autres activités informatiques",
    "63": "Services d'information",
    "64": "Activités des services financiers, hors assurance et retraite",
    "65": "Assurance",
    "66": "Activités auxiliaires de services financiers et d'assurance",
    "68": "Activités immobilières",
    "69": "Activités juridiques et comptables",
    "70": "Activités des sièges sociaux ; conseil de gestion",
    "71": "Architecture et ingénierie ; contrôle et analyses techniques",
    "72": "Recherche-développement scientifique",
    "73": "Publicité et études de marché",
    "74": "Autres activités spécialisées, scientifiques et techniques",
    "75": "Activités vétérinaires",
    "77": "Activités de location et location-bail",
    "78": "Activités liées à l'emploi",
    "79": "Agences de voyage, voyagistes et services de réservation",
    "80": "Enquêtes et sécurité",
    "81": "Services relatifs aux bâtiments et aménagement paysager",
    "82": "Activités administratives et autres soutiens aux entreprises",
    "84": "Administration publique et défense ; sécurité sociale obligatoire",
    "85": "Enseignement",
    "86": "Activités pour la santé humaine",
    "87": "Hébergement médico-social et social",
    "88": "Action sociale sans hébergement",
    "90": "Activités créatives, artistiques et de spectacle",
    "91": "Bibliothèques, archives, musées et autres activités culturelles",
    "92": "Organisation de jeux de hasard et d'argent",
    "93": "Activités sportives, récréatives et de loisirs",
    "94": "Activités des organisations associatives",
    "95": "Réparation d'ordinateurs et de biens personnels et domestiques",
    "96": "Autres services personnels",
    "97": "Activités des ménages en tant qu'employeurs de personnel domestique",
    "98": "Activités indifférenciées des ménages pour usage propre",
    "99": "Activités des organisations et organismes extraterritoriaux",
}

#: NAF rev. 2 sections (letters A-U) as inclusive division ranges.
_NAF_SECTIONS: Tuple[Tuple[str, int, int], ...] = (
    ("A", 1, 3), ("B", 5, 9), ("C", 10, 33), ("D", 35, 35), ("E", 36, 39), ("F", 41, 43),
    ("G", 45, 47), ("H", 49, 53), ("I", 55, 56), ("J", 58, 63), ("K", 64, 66), ("L", 68, 68),
    ("M", 69, 75), ("N", 77, 82), ("O", 84, 84), ("P", 85, 85), ("Q", 86, 88), ("R", 90, 93),
    ("S", 94, 96), ("T", 97, 98), ("U", 99, 99),
)
NAF_SECTION_LETTERS = tuple(letter for letter, _, _ in _NAF_SECTIONS)


def naf_division(code: Optional[str]) -> Optional[str]:
    """``'62.01Z'`` / ``'6201Z'`` -> ``'62'``."""
    digits = re.sub(r"[^0-9]", "", str(code or ""))[:2]
    return digits if len(digits) == 2 else None


def naf_section(code: Optional[str]) -> Optional[str]:
    """Section letter (``'J'`` for ``62.01Z``), ``None`` when the code is unusable."""
    division = naf_division(code)
    if division is None:
        return None
    number = int(division)
    for letter, low, high in _NAF_SECTIONS:
        if low <= number <= high:
            return letter
    return None


def naf_division_label(code: Optional[str]) -> Optional[str]:
    division = naf_division(code)
    return NAF_DIVISIONS.get(division) if division else None


# --------------------------------------------------------------------------- #
# Parsed response
# --------------------------------------------------------------------------- #


@dataclass
class Establishment:
    siret: str
    address: str
    postal_code: str
    city: str
    active: bool


@dataclass
class Director:
    name: str
    role: str
    is_company: bool = False


@dataclass
class Company:
    siren: str
    name: str
    legal_name: str
    acronym: str
    commercial_names: List[str]
    active: bool
    closed_on: Optional[date]
    created_on: Optional[date]
    legal_form_code: str
    naf_code: str
    size_category: str            # PME / ETI / GE (may be empty)
    headcount_code: str
    headcount_year: Optional[int]
    establishments: int
    open_establishments: int
    head_office: Optional[Establishment]
    matched: Optional[Establishment]     # the establishment a SIRET search hit
    directors: List[Director] = field(default_factory=list)
    directors_total: int = 0
    is_association: bool = False
    is_ess: bool = False


@dataclass
class SearchResult:
    total: int
    companies: List[Company]


def _date(value: Any) -> Optional[date]:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _establishment(raw: Any) -> Optional[Establishment]:
    if not isinstance(raw, dict) or not raw.get("siret"):
        return None
    address = str(raw.get("adresse") or raw.get("geo_adresse") or "").strip()
    return Establishment(
        siret=str(raw["siret"]),
        address=address,
        postal_code=str(raw.get("code_postal") or ""),
        city=str(raw.get("libelle_commune") or ""),
        active=str(raw.get("etat_administratif") or "A").upper() == "A")


def _person_name(raw: dict) -> str:
    """``Prénom Nom`` — the registry shouts in capitals, the card does not."""
    first = str(raw.get("prenoms") or "").split(",")[0].strip()
    last = str(raw.get("nom") or "").strip()
    if first.isupper():
        first = first.title()
    if last.isupper():
        last = last.title()
    return f"{first} {last}".strip()


def _directors(raw: Any) -> Tuple[List[Director], int]:
    """Up to :data:`MAX_DIRECTORS` as published, plus how many exist."""
    items = [d for d in (raw or []) if isinstance(d, dict)]
    out: List[Director] = []
    for item in items:
        if len(out) >= MAX_DIRECTORS:
            break
        role = str(item.get("qualite") or "").strip()
        if item.get("denomination") and not item.get("nom"):
            name = str(item["denomination"]).strip()
            company = True
        else:
            name = _person_name(item)
            company = False
        if not name or "[" in name:     # "[Données non diffusibles]" and the like
            continue
        out.append(Director(name=name, role=role, is_company=company))
    return out, len(items)


def parse_company(raw: dict, *, searched_siret: Optional[str] = None) -> Optional[Company]:
    """One ``results[]`` entry -> :class:`Company` (``None`` without a SIREN)."""
    if not isinstance(raw, dict) or not raw.get("siren"):
        return None
    siege_raw = raw.get("siege") if isinstance(raw.get("siege"), dict) else {}
    head_office = _establishment(siege_raw)

    matched = None
    if searched_siret:
        for candidate in [siege_raw] + list(raw.get("matching_etablissements") or []):
            est = _establishment(candidate)
            if est is not None and est.siret == searched_siret:
                matched = est
                break

    names: List[str] = []
    for value in [siege_raw.get("nom_commercial"), *(siege_raw.get("liste_enseignes") or [])]:
        if isinstance(value, str) and value.strip() and value.strip() not in names:
            names.append(value.strip())

    complements = raw.get("complements") if isinstance(raw.get("complements"), dict) else {}
    directors, directors_total = _directors(raw.get("dirigeants"))
    name = str(raw.get("nom_complet") or raw.get("nom_raison_sociale") or "").strip()
    legal_name = str(raw.get("nom_raison_sociale") or "").strip()
    return Company(
        siren=str(raw["siren"]),
        name=name or str(raw["siren"]),
        legal_name=legal_name,
        acronym=str(raw.get("sigle") or "").strip(),
        commercial_names=names,
        active=str(raw.get("etat_administratif") or "A").upper() == "A",
        closed_on=_date(raw.get("date_fermeture")),
        created_on=_date(raw.get("date_creation")),
        legal_form_code=str(raw.get("nature_juridique") or ""),
        naf_code=str(raw.get("activite_principale") or siege_raw.get("activite_principale") or ""),
        size_category=str(raw.get("categorie_entreprise") or ""),
        headcount_code=str(raw.get("tranche_effectif_salarie") or ""),
        headcount_year=_int(raw.get("annee_tranche_effectif_salarie"), 0) or None,
        establishments=_int(raw.get("nombre_etablissements")),
        open_establishments=_int(raw.get("nombre_etablissements_ouverts")),
        head_office=head_office,
        matched=matched,
        directors=directors,
        directors_total=directors_total,
        is_association=bool(complements.get("est_association")),
        is_ess=bool(complements.get("est_ess")))


def parse_search(payload: Any, *, searched_siret: Optional[str] = None) -> SearchResult:
    if not isinstance(payload, dict):
        return SearchResult(0, [])
    companies = [c for c in (parse_company(r, searched_siret=searched_siret)
                             for r in payload.get("results") or []) if c is not None]
    return SearchResult(total=_int(payload.get("total_results"), len(companies)),
                        companies=companies)


def age_parts(created: date, today: date) -> Tuple[int, int]:
    """Whole ``(years, months)`` between two dates (never negative)."""
    months = (today.year - created.year) * 12 + (today.month - created.month)
    if today.day < created.day:
        months -= 1
    months = max(0, months)
    return months // 12, months % 12


# --------------------------------------------------------------------------- #
# Network
# --------------------------------------------------------------------------- #

CACHE_TTL = 300
_RETRY_DELAY = 0.8


async def search_companies(api, query: Query) -> SearchResult:
    """Run a search; one automatic retry on a transient failure (the API
    answers 503 now and then). Raises ``PublicAPIError`` after that."""
    from services.public_api import PublicAPIError

    params = {"q": query.value, "page": "1", "per_page": str(PER_PAGE)}
    last: Optional[PublicAPIError] = None
    for attempt in range(2):
        try:
            payload = await api.get_json(API_URL, params=params, timeout=10,
                                         cache_ttl=CACHE_TTL, cache_namespace="company")
            return parse_search(payload, searched_siret=query.value if query.kind == KIND_SIRET else None)
        except PublicAPIError as exc:
            last = exc
            transient = exc.code in ("timeout", "network") or (
                exc.code == "http_error" and (exc.status or 0) >= 500)
            if not transient or attempt == 1:
                break
            await asyncio.sleep(_RETRY_DELAY)
    assert last is not None
    raise last
