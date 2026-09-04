"""Precision-first quantity extraction: an allowlist, not a guess.

The design constraint everything follows from: a wrong unit is worse than a
missing one. So extraction is number-adjacent-to-*known*-unit only — the
unit table below is a curated allowlist, and a token it does not know is
either recorded as ``pending_llm`` (when it plausibly IS a unit: short,
capitalised or slashed, not a stopword) or ignored entirely (when it reads
as prose — "10 authors" must never become a quantity). Recall is bought
later by the adjudicator; precision is bought here, and the labelled
fixture holds the floor.

``pint`` does the normalisation — one registry, module-level, because
constructing it costs hundreds of milliseconds and the worker calls this
per paper. Kinds are dimension families (length, time, frequency…), not
semantic roles: whether 300 s is an Isp or a timeout is the reader's call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.models.enums import QuantityStatus

#: token as written -> (pint unit, dimension family). Curated; growing it is
#: a one-line change guarded by the fixture.
UNITS: dict[str, tuple[str, str]] = {
    # length
    "nm": ("nanometer", "length"),
    "µm": ("micrometer", "length"),
    "um": ("micrometer", "length"),
    "mm": ("millimeter", "length"),
    "cm": ("centimeter", "length"),
    "m": ("meter", "length"),
    "km": ("kilometer", "length"),
    "AU": ("astronomical_unit", "length"),
    "au": ("astronomical_unit", "length"),
    "pc": ("parsec", "length"),
    "kpc": ("kiloparsec", "length"),
    "Mpc": ("megaparsec", "length"),
    "ly": ("light_year", "length"),
    # mass
    "µg": ("microgram", "mass"),
    "mg": ("milligram", "mass"),
    "g": ("gram", "mass"),
    "kg": ("kilogram", "mass"),
    # time
    "ns": ("nanosecond", "time"),
    "µs": ("microsecond", "time"),
    "us": ("microsecond", "time"),
    "ms": ("millisecond", "time"),
    "s": ("second", "time"),
    "min": ("minute", "time"),
    "h": ("hour", "time"),
    "hr": ("hour", "time"),
    "days": ("day", "time"),
    "yr": ("year", "time"),
    "Myr": ("megayear", "time"),
    "Gyr": ("gigayear", "time"),
    # frequency — mHz added from the aerospace corpus, where ionospheric
    # gravity waves are reported in millihertz; the adjudicator had already
    # been confirming it as frequency on the biology corpus.
    "mHz": ("millihertz", "frequency"),
    "Hz": ("hertz", "frequency"),
    "kHz": ("kilohertz", "frequency"),
    "MHz": ("megahertz", "frequency"),
    "GHz": ("gigahertz", "frequency"),
    # temperature (affine — converted via Quantity, not by factor)
    "K": ("kelvin", "temperature"),
    "°C": ("degC", "temperature"),
    "°F": ("degF", "temperature"),
    # energy / power
    "eV": ("electron_volt", "energy"),
    "keV": ("kiloelectron_volt", "energy"),
    "MeV": ("megaelectron_volt", "energy"),
    "GeV": ("gigaelectron_volt", "energy"),
    "J": ("joule", "energy"),
    "kJ": ("kilojoule", "energy"),
    "mW": ("milliwatt", "power"),
    "W": ("watt", "power"),
    "kW": ("kilowatt", "power"),
    "MW": ("megawatt", "power"),
    # electromagnetism — bare "A" is deliberately absent: on a real corpus
    # it matched matrix indices and figure-panel labels a thousand times
    # ("-7 A < i,j", "0 A B") and amperes not once. Bare "t" (tonnes)
    # left for the same reason. mA and the compounds carry the load.
    "mV": ("millivolt", "voltage"),
    "V": ("volt", "voltage"),
    "kV": ("kilovolt", "voltage"),
    "mA": ("milliampere", "current"),
    "nT": ("nanotesla", "magnetic_field"),
    "mT": ("millitesla", "magnetic_field"),
    "T": ("tesla", "magnetic_field"),
    # pressure / force
    "Pa": ("pascal", "pressure"),
    "hPa": ("hectopascal", "pressure"),
    "kPa": ("kilopascal", "pressure"),
    "MPa": ("megapascal", "pressure"),
    "bar": ("bar", "pressure"),
    "N": ("newton", "force"),
    "mN": ("millinewton", "force"),
    # velocity
    "µm/s": ("micrometer / second", "velocity"),
    "um/s": ("micrometer / second", "velocity"),
    "mm/s": ("millimeter / second", "velocity"),
    "cm/s": ("centimeter / second", "velocity"),
    "m/s": ("meter / second", "velocity"),
    "km/s": ("kilometer / second", "velocity"),
    "km/h": ("kilometer / hour", "velocity"),
    # wet-lab families, added when the live review queue filled with them:
    # µM doses, mL volumes and mg/L concentrations are what a biology corpus
    # actually measures. Compounds only — bare "L" and "M" are single capital
    # letters, and the bare-"A" lesson above applies to them verbatim; the
    # adjudicator route already covers those.
    "nL": ("nanoliter", "volume"),
    "µL": ("microliter", "volume"),
    "uL": ("microliter", "volume"),
    "mL": ("milliliter", "volume"),
    "nM": ("nanomolar", "molarity"),
    "µM": ("micromolar", "molarity"),
    "uM": ("micromolar", "molarity"),
    "mM": ("millimolar", "molarity"),
    "ng/mL": ("nanogram / milliliter", "mass_concentration"),
    "µg/mL": ("microgram / milliliter", "mass_concentration"),
    "ug/mL": ("microgram / milliliter", "mass_concentration"),
    "mg/mL": ("milligram / milliliter", "mass_concentration"),
    "mg/L": ("milligram / liter", "mass_concentration"),
    "mg/l": ("milligram / liter", "mass_concentration"),
    "g/L": ("gram / liter", "mass_concentration"),
    # angle — the bare degree sign was the aerospace corpus's largest unknown
    # token (115 rows: zenith angles, elevations, latitudes) and an angle in
    # every sample from both corpora. Its one ambiguity is a temperature
    # whose C was set apart ("37 ° C", "100°-175°C"); a sentence carrying a
    # degree-Celsius anywhere sends the bare sign to the adjudicator instead.
    "°": ("degree", "angle"),
    "rad": ("radian", "angle"),
    "mrad": ("milliradian", "angle"),
    "deg": ("degree", "angle"),
    "arcsec": ("arcsecond", "angle"),
    "arcmin": ("arcminute", "angle"),
    # unitless families that stay in their own terms
    "%": ("%", "fraction"),
    "dB": ("dB", "level"),
    "ppm": ("ppm", "fraction"),
}

#: Words that follow numbers constantly and are never units.
_STOPWORDS = frozenset(
    "to of and the in is was for on at or by we a an as with from per out "
    "times more less new all other such each than".split()
)

#: Unit-shaped tokens the adjudicator has rejected every single time — over
#: a thousand "D"s (2 D, 3 D) and the clock suffixes after "12:00". Skipped
#: outright rather than sent for a verdict; case-sensitive, because "d" may
#: yet be a day.
_NEVER_UNITS = frozenset({"D", "UTC", "UT", "LT"})

#: A degree sign in a sentence that also carries a Celsius or Fahrenheit
#: reading is likelier to be a temperature with its letter set apart.
_TEMPERATURE_NEARBY = re.compile(r"°\s?[CF]\b")

#: value: sign, decimals, scientific notation; then optional ± tolerance and
#: an optional range tail — captured whole as value_original, first number
#: as the value.
_NUMBER = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
QUANTITY_RE = re.compile(
    rf"(?<![\w.±-])({_NUMBER})"
    rf"((?:\s*±\s*{_NUMBER})|(?:\s*(?:-|–|\bto\b)\s*{_NUMBER}))?"
    rf"[  ]?([A-Za-zµ°%][A-Za-z/µ°%]{{0,6}})(?![\w/])"
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
MIN_SENTENCE_CHARS = 25
MAX_SENTENCE_CHARS = 500


@dataclass(frozen=True)
class ExtractedQuantity:
    chunk_ord: int
    section: str | None
    quantity_kind: str
    value_si: float | None
    unit_si: str | None
    value_original: str
    unit_original: str
    context_sentence: str
    confidence: float
    status: str


@lru_cache(maxsize=1)
def _registry():
    import pint

    return pint.UnitRegistry()


#: The unit each kind normalises TO. A named unit, not a base-unit
#: decomposition: "hertz" filters and displays; "1 / second" merely exists.
CANONICAL: dict[str, str] = {
    "length": "meter",
    "mass": "kilogram",
    "time": "second",
    "frequency": "hertz",
    "temperature": "kelvin",
    "energy": "joule",
    "power": "watt",
    "voltage": "volt",
    "current": "ampere",
    "magnetic_field": "tesla",
    "pressure": "pascal",
    "force": "newton",
    "velocity": "meter / second",
    "angle": "radian",
    "volume": "liter",
    "molarity": "molar",
    "mass_concentration": "gram / liter",
}


def _normalise(value: float, token: str) -> tuple[float, str, str]:
    """(value_si, unit_si, kind) for an allowlisted token."""
    pint_unit, kind = UNITS[token]
    if pint_unit in ("%", "ppm"):
        factor = 0.01 if pint_unit == "%" else 1e-6
        return value * factor, "dimensionless", kind
    if pint_unit == "dB":
        return value, "dB", kind
    registry = _registry()
    canonical = CANONICAL[kind]
    converted = registry.Quantity(value, pint_unit).to(canonical)
    return float(converted.magnitude), canonical, kind


def _looks_like_a_unit(token: str) -> bool:
    """Unknown but plausibly a unit: short, and marked the way units are —
    capitals, degrees, slashes — rather than the way words are."""
    if token.lower() in _STOPWORDS or len(token) > 4:
        return False
    return any(c.isupper() or c in "°µ/%" for c in token)


#: Markup and table debris that reads as text and is not prose — the same
#: furniture lesson the abstract ladder learned: converted tables and figure
#: text carry numbers that describe layout, not measurements.
_NOT_PROSE = ("<br>", "<!--", "|")


def sentences_of(text: str) -> list[str]:
    return [
        piece.strip()
        for piece in _SENTENCE_SPLIT.split(text)
        if MIN_SENTENCE_CHARS <= len(piece.strip()) <= MAX_SENTENCE_CHARS
        and " " in piece
        and not any(marker in piece for marker in _NOT_PROSE)
    ]


def extract_from_text(
    text: str, chunk_ord: int = 0, section: str | None = None
) -> list[ExtractedQuantity]:
    found: list[ExtractedQuantity] = []
    for sentence in sentences_of(text):
        for match in QUANTITY_RE.finditer(sentence):
            number, tail, token = match.groups()
            value_original = (number + (tail or "")).strip()
            value = float(number)

            if token in _NEVER_UNITS:
                continue
            ambiguous_degree = token == "°" and _TEMPERATURE_NEARBY.search(sentence)
            if token in UNITS and not ambiguous_degree:
                try:
                    value_si, unit_si, kind = _normalise(value, token)
                except Exception:  # noqa: BLE001 - pint surprise -> pending
                    value_si, unit_si, kind = None, None, "unknown"
                found.append(
                    ExtractedQuantity(
                        chunk_ord=chunk_ord,
                        section=section,
                        quantity_kind=kind,
                        value_si=value_si,
                        unit_si=unit_si,
                        value_original=value_original,
                        unit_original=token,
                        context_sentence=sentence,
                        confidence=0.95 if value_si is not None else 0.4,
                        status=QuantityStatus.AUTO
                        if value_si is not None
                        else QuantityStatus.PENDING_LLM,
                    )
                )
            elif ambiguous_degree or _looks_like_a_unit(token):
                found.append(
                    ExtractedQuantity(
                        chunk_ord=chunk_ord,
                        section=section,
                        quantity_kind="unknown",
                        value_si=None,
                        unit_si=None,
                        value_original=value_original,
                        unit_original=token,
                        context_sentence=sentence,
                        confidence=0.4,
                        status=QuantityStatus.PENDING_LLM,
                    )
                )
    return found
