"""Conservative person-name validation for decision-maker extraction.

The extractor in ``app.normalize.report`` must never turn a location, an
organization description, a category label, or arbitrary provider text into a
"person" name. Validation is layered so that no single hardcoded list decides
on its own:

1. **Shape** — an extracted candidate is 2-4 whitespace-separated tokens; every
   token starts with a letter and contains only letters, apostrophes, hyphens,
   or periods (no digits, no control characters).
2. **Organization words** — any token that is an organization/legal-entity
   word ("Corporation", "Ltd", "Foundation", ...) disqualifies a candidate.
3. **Role words** — any token that is a role keyword disqualifies an extracted
   candidate: a title is not a name.
4. **Non-person phrase** — a candidate whose tokens are ALL geographic words,
   industry/category words, generic filler, or role words is a location,
   country, sector label, or title fragment — not a person.
5. **Context** — an extracted candidate directly preceded by a location
   preposition or article ("in", "based in", "from", "a", "the", ...) or
   directly followed by "-based" is a location/description, not a person.

Unfamiliar personal names are accepted: the rules reject only candidates that
carry positive evidence of being a place, an organization, or a category.
Documented tradeoff: a rare real name made entirely of place/category tokens
(for example "Chad Austin") is omitted rather than displayed as a person —
conservative omission is preferred to a false positive.

Structured provider fields (for example the MoltSets ``full_name`` field) use
``is_structured_person_name``: the same vocabulary checks without the
extraction-specific shape/context rules, so single-token names or names with
lowercase particles returned by a provider are still accepted.
"""

import re
import unicodedata

# Names longer than the report's ``MAX_NAME_LEN`` cap cannot be people.
MAX_NAME_CHARS = 120

# Organization/legal-entity words: a candidate containing any of these is an
# organization description, not a person. Kept to words that essentially never
# appear inside a personal name.
_ORG_TOKENS = frozenset(
    {
        "corporation",
        "corp",
        "incorporated",
        "inc",
        "llc",
        "llp",
        "plc",
        "ltd",
        "limited",
        "gmbh",
        "company",
        "foundation",
        "trust",
        "fund",
        "partners",
        "partnership",
        "holdings",
        "ventures",
        "group",
        "labs",
        "laboratory",
        "institute",
        "university",
        "college",
        "academy",
        "school",
        "association",
        "organization",
        "nonprofit",
        "charity",
        "enterprises",
        "industries",
        "agency",
        "bureau",
        "department",
        "ministry",
        "council",
        "committee",
        "authority",
        "commission",
        "center",
        "centre",
        "hospital",
        "clinic",
        "federation",
        "alliance",
        "coalition",
        "chamber",
        "society",
        "union",
        "cooperative",
        "media",
        "networks",
        "systems",
        "solutions",
        "technologies",
        "services",
        "consulting",
        "advisors",
        "associates",
        "realty",
        "estates",
        "insurance",
        "securities",
        "motors",
        "airlines",
        "airways",
        "hotels",
        "resorts",
        "pharmacy",
        "robotics",
        "semiconductor",
        "aviation",
        "aerospace",
        "defense",
        "staffing",
        "recruiting",
        "mortgage",
        "lending",
        "payments",
        "fintech",
        "brokerage",
        "investment",
        "pension",
        "endowment",
        "records",
        "publishing",
        "studios",
        "productions",
        "entertainment",
        "communications",
        "telecom",
        "wireless",
        "logistics",
        "shipping",
        "freight",
        "trucking",
        "cement",
        "aluminum",
        "textiles",
        "apparel",
        "furniture",
        "jewelry",
        "cosmetics",
        "restaurant",
        "brewing",
        "bakeries",
        "foods",
        "beverages",
        "wines",
        "petroleum",
        "mining",
        "farms",
        "electric",
    }
)

# Generic industry/category words: a candidate made ONLY of these (plus
# geography/generic filler) is a sector label such as "Market Research".
_INDUSTRY_TOKENS = frozenset(
    {
        "market",
        "research",
        "software",
        "hardware",
        "healthcare",
        "health",
        "medical",
        "pharma",
        "pharmaceutical",
        "biotech",
        "biotechnology",
        "genomics",
        "diagnostics",
        "financial",
        "finance",
        "banking",
        "education",
        "retail",
        "manufacturing",
        "construction",
        "transportation",
        "hospitality",
        "tourism",
        "energy",
        "utilities",
        "agriculture",
        "farming",
        "telecommunications",
        "automotive",
        "chemical",
        "chemicals",
        "food",
        "beverage",
        "real",
        "estate",
        "information",
        "data",
        "analytics",
        "marketing",
        "advertising",
        "sales",
        "engineering",
        "design",
        "human",
        "resources",
        "cybersecurity",
        "security",
        "cloud",
        "computing",
        "artificial",
        "intelligence",
        "machine",
        "learning",
        "blockchain",
        "crypto",
        "ecommerce",
        "professional",
        "business",
        "consumer",
        "industrial",
        "technology",
        "digital",
        "legal",
        "accounting",
        "silicon",
        "oil",
        "gas",
        "renewable",
        "solar",
        "waste",
        "environmental",
        "sustainability",
        "forestry",
        "fishing",
        "leisure",
        "gaming",
        "gambling",
        "sports",
        "fitness",
        "wellness",
        "credit",
        "venture",
        "equity",
        "hedge",
        "mutual",
        "trading",
        "textile",
        "beauty",
        "fashion",
        "training",
        "certification",
        "government",
        "public",
        "administration",
        "development",
        "management",
        "strategy",
        "operations",
        "customer",
        "success",
        "support",
    }
)

# Geographic words: a candidate made ONLY of these (plus generic filler) is a
# place — city, state, region, or country — never a person.
_GEO_TOKENS = frozenset(
    {
        # countries and country parts
        "united",
        "states",
        "kingdom",
        "america",
        "american",
        "canada",
        "australia",
        "england",
        "scotland",
        "wales",
        "ireland",
        "france",
        "germany",
        "spain",
        "italy",
        "china",
        "japan",
        "india",
        "brazil",
        "mexico",
        "netherlands",
        "belgium",
        "switzerland",
        "austria",
        "sweden",
        "norway",
        "denmark",
        "finland",
        "iceland",
        "portugal",
        "greece",
        "turkey",
        "poland",
        "russia",
        "ukraine",
        "egypt",
        "israel",
        "singapore",
        "malaysia",
        "indonesia",
        "thailand",
        "vietnam",
        "philippines",
        "pakistan",
        "bangladesh",
        "nigeria",
        "kenya",
        "ghana",
        "morocco",
        "chile",
        "argentina",
        "colombia",
        "peru",
        "cuba",
        "jamaica",
        "emirates",
        "arabia",
        "korea",
        "zealand",
        "guinea",
        "leone",
        "faso",
        "salvador",
        "rica",
        "puerto",
        "rico",
        # US states
        "alabama",
        "alaska",
        "arizona",
        "arkansas",
        "california",
        "colorado",
        "connecticut",
        "delaware",
        "florida",
        "georgia",
        "hawaii",
        "idaho",
        "illinois",
        "indiana",
        "iowa",
        "kansas",
        "kentucky",
        "louisiana",
        "maine",
        "maryland",
        "massachusetts",
        "michigan",
        "minnesota",
        "mississippi",
        "missouri",
        "montana",
        "nebraska",
        "nevada",
        "hampshire",
        "jersey",
        "york",
        "carolina",
        "dakota",
        "ohio",
        "oklahoma",
        "oregon",
        "pennsylvania",
        "rhode",
        "island",
        "tennessee",
        "texas",
        "utah",
        "vermont",
        "virginia",
        "washington",
        "virgin",
        "wisconsin",
        "wyoming",
        "columbia",
        "district",
        # major cities and city parts
        "san",
        "francisco",
        "diego",
        "jose",
        "antonio",
        "santa",
        "monica",
        "clara",
        "mateo",
        "los",
        "las",
        "angeles",
        "vegas",
        "orleans",
        "london",
        "paris",
        "berlin",
        "madrid",
        "rome",
        "milan",
        "amsterdam",
        "brussels",
        "vienna",
        "zurich",
        "geneva",
        "stockholm",
        "oslo",
        "copenhagen",
        "helsinki",
        "dublin",
        "manchester",
        "birmingham",
        "glasgow",
        "edinburgh",
        "liverpool",
        "bristol",
        "leeds",
        "cambridge",
        "oxford",
        "boston",
        "seattle",
        "denver",
        "atlanta",
        "chicago",
        "houston",
        "dallas",
        "phoenix",
        "portland",
        "philadelphia",
        "detroit",
        "toronto",
        "vancouver",
        "montreal",
        "ottawa",
        "calgary",
        "melbourne",
        "brisbane",
        "perth",
        "auckland",
        "wellington",
        "tokyo",
        "osaka",
        "kyoto",
        "seoul",
        "beijing",
        "shanghai",
        "shenzhen",
        "taipei",
        "bangkok",
        "jakarta",
        "manila",
        "mumbai",
        "delhi",
        "bangalore",
        "chennai",
        "hyderabad",
        "karachi",
        "lahore",
        "dubai",
        "doha",
        "riyadh",
        "cairo",
        "lagos",
        "nairobi",
        "johannesburg",
        "pretoria",
        "santiago",
        "lima",
        "bogota",
        "caracas",
        "havana",
        "miami",
        "orlando",
        "tampa",
        "cleveland",
        "nashville",
        "memphis",
        "milwaukee",
        "baltimore",
        "richmond",
        "sacramento",
        "oakland",
        "tucson",
        "omaha",
        "wichita",
        "tulsa",
        "honolulu",
        "anchorage",
        "buffalo",
        "raleigh",
        "charlotte",
        "columbus",
        "indianapolis",
        "jacksonville",
        "louisville",
        "minneapolis",
        "newark",
        "pittsburgh",
        "austin",
        "chad",
        "jordan",
        # generic place qualifiers and suffixes
        "north",
        "south",
        "east",
        "west",
        "central",
        "middle",
        "greater",
        "upper",
        "lower",
        "northern",
        "southern",
        "eastern",
        "western",
        "new",
        "old",
        "downtown",
        "uptown",
        "midtown",
        "valley",
        "hills",
        "beach",
        "bay",
        "area",
        "region",
        "county",
        "province",
        "territory",
        "city",
        "town",
        "village",
        "metro",
        "metropolitan",
        "coast",
        "peninsula",
        "islands",
        "harbor",
        "heights",
        "springs",
        "grove",
        "park",
        "plaza",
        "square",
        "station",
        "junction",
        "port",
        "shores",
        "ridge",
        "creek",
        "falls",
        "lake",
        "lakes",
        "river",
        "mountains",
        "desert",
        "forest",
        "prairie",
        "cape",
        "street",
        "wall",
    }
)

# Role keywords: a candidate containing one of these is title text, not a
# name (extraction rejects any token; structured names only when the whole
# value is role words, e.g. ``"Founder"``).
_ROLE_TOKENS = frozenset(
    {
        "ceo",
        "cto",
        "cfo",
        "coo",
        "cio",
        "cmo",
        "ciso",
        "cro",
        "cpo",
        "cdo",
        "founder",
        "founders",
        "cofounder",
        "president",
        "presidents",
        "vice",
        "vp",
        "svp",
        "evp",
        "chief",
        "officer",
        "officers",
        "director",
        "directors",
        "manager",
        "managers",
        "head",
        "lead",
        "leader",
        "leaders",
        "leadership",
        "owner",
        "owners",
        "principal",
        "executive",
        "executives",
        "chairman",
        "chairwoman",
        "chairperson",
        "chair",
        "partner",
        "trustee",
        "treasurer",
        "secretary",
        "controller",
        "counsel",
    }
)

# Generic filler words that can never be part of a person's name.
_GENERIC_TOKENS = frozenset(
    {
        "of",
        "the",
        "and",
        "for",
        "in",
        "at",
        "on",
        "by",
        "with",
        "from",
        "to",
        "or",
        "a",
        "an",
        "its",
        "our",
        "their",
        "this",
        "that",
        "these",
        "those",
        "we",
        "he",
        "she",
        "they",
        "it",
        "as",
        "is",
        "was",
        "are",
        "were",
        "be",
        "been",
        "being",
    }
)

_NON_PERSON_TOKENS = _GEO_TOKENS | _INDUSTRY_TOKENS | _GENERIC_TOKENS

# One name token: starts with a letter, then letters/apostrophes/hyphens/
# periods only (accented letters included via Unicode classes).
_TOKEN_SHAPE_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|['\u2019.\-])*$", re.UNICODE)

# A candidate directly preceded by a location preposition, a location verb
# phrase, or an article is a place/description ("in San Francisco",
# "based in New South Wales", "a Public Benefit Corporation", "the United
# States"), not a person.
_LOCATION_CONTEXT_RE = re.compile(
    r"(?:\b(?:in|from|near|across|throughout|around|within|outside|via|at)"
    r"|\b(?:based|headquartered|located|registered|operating|operates)\s+in"
    r"|\b(?:a|an|the))\s*[,;:]?\s*$",
    re.IGNORECASE,
)

# A candidate directly followed by "-based" is a location ("San
# Francisco-based"), not a person.
_BASED_SUFFIX_RE = re.compile(r"^\s*[\-\u2013\u2014]\s*based\b", re.IGNORECASE)


def _fold(token: str) -> str:
    """Lookup form of one token: casefolded, alphanumerics only."""
    return "".join(ch for ch in token.casefold() if ch.isalnum())


def is_person_name_candidate(candidate: str, *, preceding: str = "", following: str = "") -> bool:
    """Validate one EXTRACTED name candidate (2-4 capitalized tokens).

    ``preceding``/``following`` are the sentence text directly around the
    candidate, used for the location-context rules. Returns ``False`` when
    the candidate is a location, organization, category label, title, or
    otherwise not defensible as an individual's name.
    """
    tokens = candidate.split()
    if not 2 <= len(tokens) <= 4:
        return False
    if not all(_TOKEN_SHAPE_RE.fullmatch(token) for token in tokens):
        return False
    folded = [_fold(token) for token in tokens]
    if not all(folded):
        return False
    if any(token in _ORG_TOKENS for token in folded):
        return False
    if any(token in _ROLE_TOKENS for token in folded):
        return False
    if all(token in _NON_PERSON_TOKENS or token in _ROLE_TOKENS for token in folded):
        return False
    if _LOCATION_CONTEXT_RE.search(preceding):
        return False
    if _BASED_SUFFIX_RE.match(following):
        return False
    return True


def is_structured_person_name(value: str | None) -> bool:
    """Validate a STRUCTURED provider name field (for example MoltSets
    ``full_name``).

    Applies the vocabulary checks only — no extraction shape or context rules
    — so provider-returned names with lowercase particles or a single token
    are still accepted, while clearly non-person values (locations,
    countries, organization names, sector labels, titles, digit strings,
    oversized strings) are rejected.
    """
    if value is None:
        return False
    text = value.strip()
    if not text or len(text) > MAX_NAME_CHARS:
        return False
    if any(unicodedata.category(ch) == "Cc" for ch in text):
        return False
    if any(ch.isdigit() for ch in text):
        return False
    folded = [token for token in (_fold(part) for part in text.split()) if token]
    if not folded:
        return False
    if any(token in _ORG_TOKENS for token in folded):
        return False
    if all(token in _NON_PERSON_TOKENS or token in _ROLE_TOKENS for token in folded):
        return False
    return True
