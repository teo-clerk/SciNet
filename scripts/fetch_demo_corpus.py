#!/usr/bin/env python
"""Fetch the aerospace demo corpus — a benchmark anyone can rebuild.

A personal library cannot be shared, so the map it produces cannot be checked
by anyone else. This corpus can: every document is an arXiv preprint or a
public-domain NASA technical report, and every filename carries its domain —
``SAR_2608.11271v1.pdf``, ``Orbits_ntrs-19680012395.pdf`` — so that
``scripts/eval_clustering.py`` can score the map against ground truth. The
clustering number in the README is therefore one anyone can reproduce.

Nothing is redistributed. ``demo/manifest.jsonl`` pins each document's source
URL and SHA-256; this script downloads from the sources and checks the hash.
It is a user-invoked tool in the spirit of ``download_models.py``, not app
egress: the app's enrichment gate is untouched and ``egress_log`` stays empty.

    uv run python ../scripts/fetch_demo_corpus.py             # pinned corpus → library
    uv run python ../scripts/fetch_demo_corpus.py --dest data/demo/library
    uv run python ../scripts/fetch_demo_corpus.py --dry-run   # what would be fetched
    uv run python ../scripts/fetch_demo_corpus.py --build-manifest   # re-query, re-pin
    uv run python ../scripts/fetch_demo_corpus.py --build-manifest \\
        --per-domain 70 --legacy 8                             # the full-size recipe

The legacy NASA reports are there to be *scanned*. NTRS documents from the
1960s–80s carry OCR text layers of very uneven quality, so candidates are
probed with the app's own tier-0 gate and the ones it would escalate are
preferred: the demo exercises OCR on camera rather than on a promise, and the
manifest records which files those are.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

REPO_ROOT = BACKEND.parent
MANIFEST = REPO_ROOT / "demo" / "manifest.jsonl"

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PDF = "https://arxiv.org/pdf/{id}"
NTRS_API = "https://ntrs.nasa.gov/api/citations/search"
NTRS_HOST = "https://ntrs.nasa.gov"
USER_AGENT = "SciNet-demo-corpus/0.1 (+https://github.com/teo-clerk/SciNet)"

#: arXiv asks for three seconds between API calls. The PDF hosts get the same
#: courtesy at a shorter interval; the corpus is small enough that politeness
#: costs minutes, not hours.
ARXIV_QUERY_PAUSE_S = 3.0
DOWNLOAD_PAUSE_S = 1.5
DOWNLOAD_ATTEMPTS = 3

#: NASA reports before this year are scans, which is what they are here for.
LEGACY_BEFORE = 1995
#: A scan longer than this costs more OCR minutes than it teaches — tier 2
#: runs at roughly nine seconds a page. A legacy report whose OCR layer the
#: gate accepts costs seconds, so it is allowed to run long: the first-run
#: manifest topped out at 57 pages and never exercised the 80-page cap.
MAX_SCAN_PAGES = 60
MAX_TEXT_PAGES = 150
#: Legacy candidates probed per accepted one. Scans are found by probing, and
#: a candidate with a clean OCR layer is not what the demo came for.
LEGACY_CANDIDATES_PER_PICK = 3
#: Pages the tier-0 probe reads to judge a candidate.
PROBE_PAGES = 20

ATOM = {"a": "http://www.w3.org/2005/Atom"}


@dataclass(frozen=True)
class Domain:
    """One ground-truth region: a filename prefix and how to find its papers."""

    label: str
    about: str
    arxiv_query: str
    #: A search on NASA's technical report server for the scanned legacy
    #: reports of the same field; None for fields NASA did not write about.
    ntrs_query: str | None = None


#: Ordered: a paper matched by two queries is filed under the first. SAR comes
#: before GNSS on purpose — ionospheric InSAR correction is SAR literature
#: that cites GNSS, not the reverse — and it is the overlap the semantic
#: search demo is built to land in.
RECIPE: tuple[Domain, ...] = (
    Domain(
        "SAR",
        "radar imaging and interferometry (eess.IV, eess.SP)",
        'cat:eess.IV AND (abs:"synthetic aperture radar" OR abs:InSAR)',
        "synthetic aperture radar",
    ),
    Domain(
        "EarthObs",
        "machine learning on satellite imagery (cs.CV)",
        'cat:cs.CV AND abs:"satellite imagery"',
        "Landsat multispectral",
    ),
    Domain(
        "Orbits",
        "trajectory optimisation and orbit determination (math.OC)",
        "cat:math.OC AND abs:trajectory "
        "AND (abs:spacecraft OR abs:orbit OR abs:orbital)",
        "orbit determination",
    ),
    Domain(
        "Instruments",
        "astronomical instrumentation (astro-ph.IM)",
        "cat:astro-ph.IM AND (abs:telescope OR abs:spectrograph OR abs:detector)",
        "telescope",
    ),
    Domain(
        "GNSS",
        "satellite positioning and the ionosphere",
        "(abs:GNSS OR abs:GPS) AND (abs:ionosphere OR abs:ionospheric)",
        "ionosphere",
    ),
)

#: A second recipe, for a library about minds and machines rather than
#: spacecraft: alignment, the ethics of automated decisions, the science of
#: consciousness, the foundations of physics and cognitive architectures. All
#: arXiv, no NASA reports — nothing here was ever scanned — so it exercises the
#: prose end of the pipeline where the aerospace corpus exercised OCR.
AI_MIND_RECIPE: tuple[Domain, ...] = (
    Domain(
        "Alignment",
        "aligning learned systems with human intent (cs.AI)",
        'cat:cs.AI AND (abs:"value alignment" OR abs:"AI alignment" '
        'OR abs:"reward hacking" OR abs:"AI safety")',
    ),
    Domain(
        "MachineEthics",
        "ethics and fairness of automated decisions (cs.CY)",
        'cat:cs.CY AND (abs:ethics OR abs:fairness) AND abs:"artificial intelligence"',
    ),
    Domain(
        "Consciousness",
        "the science of consciousness (q-bio.NC)",
        'cat:q-bio.NC AND (abs:consciousness OR abs:"neural correlates")',
    ),
    Domain(
        "FoundationsPhys",
        "interpretation and foundations of physics (physics.hist-ph)",
        "cat:physics.hist-ph AND (abs:interpretation OR abs:foundations)",
    ),
    Domain(
        "Cognition",
        "cognitive architectures and theory of mind (cs.AI)",
        'cat:cs.AI AND (abs:"cognitive architecture" OR abs:"theory of mind")',
    ),
)

RECIPES: dict[str, tuple[Domain, ...]] = {
    "aerospace": RECIPE,
    "ai-and-mind": AI_MIND_RECIPE,
}
MANIFESTS: dict[str, Path] = {
    "aerospace": MANIFEST,
    "ai-and-mind": REPO_ROOT / "demo" / "ai-and-mind.jsonl",
}


@dataclass(frozen=True)
class Hit:
    """A search result: enough to name, fetch, and attribute a document."""

    source: str
    ident: str
    title: str
    url: str
    year: int | None
    rights: str


@dataclass(frozen=True)
class Entry:
    """One manifest line — the pinned fact of a document."""

    file: str
    domain: str
    source: str
    id: str
    title: str
    url: str
    year: int | None
    rights: str
    sha256: str | None = None
    bytes: int | None = None
    pages: int | None = None
    #: Whether the tier-0 gate accepted the text layer. False is a scan the
    #: pipeline will OCR; None means the file was not probed.
    text_layer: bool | None = None


# --- naming and manifest ------------------------------------------------------


def filename_for(domain: str, source: str, ident: str, ext: str = "pdf") -> str:
    """``Domain_identifier.ext``, the shape ``eval_clustering.py`` reads back.

    An arXiv identifier is used bare; an old-style one carries its category
    (``astro-ph/0601001v1``), which is dropped — a slash is not a filename
    character, and the domain regex wants a digit right after the underscore.
    Other sources are tagged (``ntrs-19680012395``) so the file says where it
    came from; the regex allows a lower-case tag and a hyphen there.
    """
    if source == "arxiv":
        return f"{domain}_{ident.rsplit('/', 1)[-1]}.{ext}"
    return f"{domain}_{source}-{ident}.{ext}"


def entry_for(domain: str, hit: Hit) -> Entry:
    return Entry(
        file=filename_for(domain, hit.source, hit.ident),
        domain=domain,
        source=hit.source,
        id=hit.ident,
        title=hit.title,
        url=hit.url,
        year=hit.year,
        rights=hit.rights,
    )


def read_manifest(path: Path) -> list[Entry]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Entry(**json.loads(line)) for line in lines if line.strip()]


def write_manifest(path: Path, entries: list[Entry]) -> None:
    """One JSON object per line, ordered for stable diffs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(entries, key=lambda e: (e.domain, e.file))
    path.write_text(
        "".join(json.dumps(asdict(e), ensure_ascii=False) + "\n" for e in ordered),
        encoding="utf-8",
    )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- parsing the sources ------------------------------------------------------


def parse_arxiv_feed(xml_text: str) -> list[Hit]:
    """Hits from an arXiv API Atom feed, in the feed's order."""
    root = ET.fromstring(xml_text)
    hits: list[Hit] = []
    for entry in root.findall("a:entry", ATOM):
        raw = (entry.findtext("a:id", default="", namespaces=ATOM)).strip()
        ident = raw.rsplit("/abs/", 1)[-1]
        if not ident:
            continue
        title = " ".join(entry.findtext("a:title", default="", namespaces=ATOM).split())
        published = entry.findtext("a:published", default="", namespaces=ATOM)
        year = int(published[:4]) if published[:4].isdigit() else None
        pdf = next(
            (
                link.get("href")
                for link in entry.findall("a:link", ATOM)
                if link.get("title") == "pdf" and link.get("href")
            ),
            None,
        )
        hits.append(
            Hit(
                source="arxiv",
                ident=ident,
                title=title,
                url=pdf or ARXIV_PDF.format(id=ident),
                year=year,
                rights=f"arXiv licence on the abstract page: https://arxiv.org/abs/{ident}",
            )
        )
    return hits


def parse_ntrs_results(payload: dict[str, Any]) -> list[Hit]:
    """NTRS records that are public, downloadable, and U.S. Government work.

    NTRS holds plenty that is none of those — metadata-only citations,
    contractor reports a publisher still owns — and the API's own filters do
    not narrow it enough, so the record is judged here.
    """
    hits: list[Hit] = []
    for record in payload.get("results", []):
        rights = (record.get("copyright") or {}).get("determinationType")
        if record.get("distribution") != "PUBLIC":
            continue
        if record.get("disseminated") != "DOCUMENT_AND_METADATA":
            continue
        if rights != "GOV_PUBLIC_USE_PERMITTED":
            continue
        pdf = next(
            (
                download["links"]["pdf"]
                for download in record.get("downloads", [])
                if download.get("mimetype") == "application/pdf"
                and (download.get("links") or {}).get("pdf")
            ),
            None,
        )
        if pdf is None:
            continue
        ident = str(record.get("id", ""))
        hits.append(
            Hit(
                source="ntrs",
                ident=ident,
                title=" ".join(str(record.get("title", "")).split()),
                url=NTRS_HOST + pdf,
                year=int(ident[:4]) if ident[:4].isdigit() else None,
                rights="NASA NTRS: U.S. Government work, GOV_PUBLIC_USE_PERMITTED",
            )
        )
    return hits


def is_legacy(hit: Hit) -> bool:
    return hit.year is not None and hit.year < LEGACY_BEFORE


def page_budget(text_layer: bool | None) -> int:
    """How long a legacy report may be: scans are paid for in OCR minutes."""
    return MAX_SCAN_PAGES if text_layer is False else MAX_TEXT_PAGES


def choose_legacy(candidates: list[Entry], wanted: int) -> list[Entry]:
    """Scans first; then the *longest* readable reports.

    A 111-page report with a usable text layer is the document that
    exercises the 80-page cap and the synopsis ladder's book rungs, and it
    costs seconds. The first build took candidates in search order and every
    pick came in under 80 pages, so the cap was never on camera.
    """
    scans = [e for e in candidates if e.text_layer is False]
    readable = sorted(
        (e for e in candidates if e.text_layer is not False),
        key=lambda e: -(e.pages or 0),
    )
    return (scans + readable)[:wanted]


# --- deciding what to fetch ---------------------------------------------------


@dataclass(frozen=True)
class Plan:
    fetch: tuple[Entry, ...]
    present: tuple[Entry, ...]
    #: On disk under the pinned name but with a different hash — re-fetched,
    #: and reported, because a silently different document is the one thing
    #: a pinned corpus exists to prevent.
    changed: tuple[Entry, ...]


def plan_fetch(entries: list[Entry], dest: Path) -> Plan:
    fetch: list[Entry] = []
    present: list[Entry] = []
    changed: list[Entry] = []
    for entry in entries:
        path = dest / entry.file
        if not path.exists():
            fetch.append(entry)
        elif entry.sha256 and sha256_of(path) != entry.sha256:
            changed.append(entry)
            fetch.append(entry)
        else:
            present.append(entry)
    return Plan(tuple(fetch), tuple(present), tuple(changed))


# --- the network --------------------------------------------------------------


class Fetcher:
    """The one place bytes come from, so pacing and retries live together."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self._last_query = 0.0

    def _pace(self, seconds: float) -> None:
        wait = self._last_query + seconds - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_query = time.monotonic()

    def arxiv(self, query: str, max_results: int) -> list[Hit]:
        params = {"search_query": query, "max_results": max_results}
        for attempt in range(DOWNLOAD_ATTEMPTS):
            self._pace(ARXIV_QUERY_PAUSE_S)
            response = self.client.get(ARXIV_API, params=params)
            response.raise_for_status()
            hits = parse_arxiv_feed(response.text)
            # The API occasionally answers a valid query with an empty feed;
            # one more try after a pause is the documented remedy.
            if hits or attempt == DOWNLOAD_ATTEMPTS - 1:
                return hits
        return []

    def ntrs(self, query: str, page_size: int = 100) -> list[Hit]:
        self._pace(DOWNLOAD_PAUSE_S)
        response = self.client.get(
            NTRS_API,
            params={
                "q": query,
                "page.size": page_size,
                "disseminated": "DOCUMENT_AND_METADATA",
                "distribution": "PUBLIC",
            },
        )
        response.raise_for_status()
        return parse_ntrs_results(response.json())

    def download(self, url: str, path: Path) -> int:
        """Fetch a PDF to ``path``; returns its size. Anything but a PDF raises."""
        last: Exception | None = None
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            self._pace(DOWNLOAD_PAUSE_S)
            try:
                response = self.client.get(url)
                response.raise_for_status()
                body = response.content
                if not body.startswith(b"%PDF"):
                    served = response.headers.get("content-type")
                    raise ValueError(f"not a PDF ({served})")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
                return len(body)
            except Exception as exc:  # noqa: BLE001 - retried, then reported
                last = exc
                time.sleep(2.0 * attempt)
        raise RuntimeError(f"{url}: {last}")


def open_client() -> Any:
    import httpx

    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=httpx.Timeout(120.0, connect=30.0),
    )


# --- probing what was fetched -------------------------------------------------


def probe(path: Path) -> tuple[int, bool]:
    """(pages, text-layer accepted by the tier-0 gate). Raises if unreadable."""
    import pymupdf

    from app.services.parse.quality import assess
    from app.services.parse.tier0_pymupdf import probe_pdf

    with pymupdf.open(path) as document:
        pages = document.page_count
    return pages, assess(probe_pdf(path, limit=PROBE_PAGES)).passed


def measured(entry: Entry, path: Path) -> Entry:
    pages, text_layer = probe(path)
    return replace(
        entry,
        sha256=sha256_of(path),
        bytes=path.stat().st_size,
        pages=pages,
        text_layer=text_layer,
    )


# --- building the manifest ----------------------------------------------------


def _fetch_one(fetcher: Fetcher, domain: str, hit: Hit, dest: Path) -> Entry | None:
    """Download and measure; a file already on disk is measured, not refetched,
    so a rebuild after a recipe change re-pins in seconds rather than minutes."""
    entry = entry_for(domain, hit)
    path = dest / entry.file
    try:
        if not path.exists():
            fetcher.download(hit.url, path)
        return measured(entry, path)
    except Exception as exc:  # noqa: BLE001 - one bad document must not stop a build
        print(f"  [skip]  {entry.file}: {exc}")
        path.unlink(missing_ok=True)
        return None


def _pick_papers(
    fetcher: Fetcher, domain: Domain, dest: Path, wanted: int, claimed: set[str]
) -> list[Entry]:
    entries: list[Entry] = []
    for hit in fetcher.arxiv(domain.arxiv_query, max_results=wanted * 2):
        if len(entries) >= wanted:
            break
        if hit.ident in claimed:
            continue
        entry = _fetch_one(fetcher, domain.label, hit, dest)
        if entry is None:
            continue
        claimed.add(hit.ident)
        entries.append(entry)
        print(f"  [ok]    {entry.file}  {entry.pages}p  {entry.title[:60]}")
    return entries


def _pick_legacy(
    fetcher: Fetcher, domain: Domain, dest: Path, wanted: int, claimed: set[str]
) -> list[Entry]:
    """Scans first, then whatever legacy reports are left, up to ``wanted``."""
    if not domain.ntrs_query or wanted <= 0:
        return []
    candidates: list[Entry] = []
    for hit in fetcher.ntrs(domain.ntrs_query):
        if len(candidates) >= wanted * LEGACY_CANDIDATES_PER_PICK:
            break
        if not is_legacy(hit) or hit.ident in claimed:
            continue
        entry = _fetch_one(fetcher, domain.label, hit, dest)
        if entry is None:
            continue
        if entry.pages is not None and entry.pages > page_budget(entry.text_layer):
            print(f"  [long]  {entry.file}: {entry.pages} pages")
            (dest / entry.file).unlink(missing_ok=True)
            continue
        claimed.add(hit.ident)
        candidates.append(entry)
        kind = "scan" if entry.text_layer is False else "text"
        print(f"  [{kind}]  {entry.file}  {entry.pages}p  {entry.title[:60]}")

    chosen = choose_legacy(candidates, wanted)
    for entry in candidates:
        if entry not in chosen:
            (dest / entry.file).unlink(missing_ok=True)
    return chosen


def build(
    fetcher: Fetcher,
    dest: Path,
    per_domain: int,
    legacy: int,
    recipe: tuple[Domain, ...] = RECIPE,
) -> list[Entry]:
    claimed: set[str] = set()
    entries: list[Entry] = []
    for domain in recipe:
        print(f"\n{domain.label} — {domain.about}")
        entries.extend(_pick_papers(fetcher, domain, dest, per_domain, claimed))
        entries.extend(_pick_legacy(fetcher, domain, dest, legacy, claimed))
    return entries


# --- fetching the pinned corpus -----------------------------------------------


def fetch_pinned(fetcher: Fetcher, entries: list[Entry], dest: Path) -> int:
    plan = plan_fetch(entries, dest)
    print(
        f"{len(entries)} document(s) in the manifest: {len(plan.present)} present, "
        f"{len(plan.changed)} changed on disk, {len(plan.fetch)} to fetch"
    )
    failures = 0
    mismatches = 0
    for index, entry in enumerate(plan.fetch, start=1):
        path = dest / entry.file
        try:
            size = fetcher.download(entry.url, path)
        except RuntimeError as exc:
            failures += 1
            print(f"  [fail]  {entry.file}: {exc}")
            continue
        digest = sha256_of(path)
        if entry.sha256 and digest != entry.sha256:
            mismatches += 1
            print(f"  [hash]  {entry.file}: differs from the pinned document")
        else:
            progress = f"({index}/{len(plan.fetch)})"
            print(f"  [ok]    {entry.file}  {size / 1024:.0f} KB  {progress}")

    print()
    print(
        f"fetched {len(plan.fetch) - failures}, failed {failures}, "
        f"hash mismatches {mismatches}"
    )
    if mismatches:
        print(
            "a mismatch means the source now serves different bytes; the file was "
            "kept, but the benchmark number was measured on the pinned one"
        )
    return 1 if failures else 0


def summarize(entries: list[Entry]) -> str:
    by_domain: dict[str, list[Entry]] = {}
    for entry in entries:
        by_domain.setdefault(entry.domain, []).append(entry)
    lines = []
    for label, rows in sorted(by_domain.items()):
        scans = sum(1 for e in rows if e.text_layer is False)
        legacy = sum(1 for e in rows if e.source == "ntrs")
        lines.append(f"  {label:<12} {len(rows):>4}   legacy {legacy}, scans {scans}")
    lines.append(f"  {'total':<12} {len(entries):>4}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recipe",
        choices=sorted(RECIPES),
        default="aerospace",
        help="which corpus: the aerospace benchmark, or minds and machines",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="manifest to read or write (default: the recipe's own)",
    )
    parser.add_argument(
        "--dest", type=Path, default=None, help="destination (default: the library)"
    )
    parser.add_argument("--dry-run", action="store_true", help="plan, fetch nothing")
    parser.add_argument(
        "--build-manifest",
        action="store_true",
        help="query arXiv and NTRS afresh, download, probe, and rewrite the manifest",
    )
    parser.add_argument(
        "--per-domain", type=int, default=15, help="arXiv papers per domain"
    )
    parser.add_argument(
        "--legacy", type=int, default=2, help="NASA legacy reports per domain"
    )
    args = parser.parse_args()
    if args.manifest is None:
        args.manifest = MANIFESTS[args.recipe]

    if args.dest is None:
        from app.core.config import get_settings

        args.dest = get_settings().library_dir
    dest = args.dest.expanduser().resolve()

    if args.build_manifest:
        if args.dry_run:
            print("--build-manifest cannot plan without fetching; drop --dry-run")
            return 2
        dest.mkdir(parents=True, exist_ok=True)
        with open_client() as client:
            entries = build(
                Fetcher(client),
                dest,
                args.per_domain,
                args.legacy,
                recipe=RECIPES[args.recipe],
            )
        write_manifest(args.manifest, entries)
        print(f"\nwrote {args.manifest} ({len(entries)} documents) into {dest}")
        print(summarize(entries))
        return 0

    if not args.manifest.exists():
        print(f"no manifest at {args.manifest}; build one with --build-manifest")
        return 1
    entries = read_manifest(args.manifest)
    if args.dry_run:
        plan = plan_fetch(entries, dest)
        print(f"into {dest}: {len(plan.present)} present, {len(plan.fetch)} to fetch")
        for entry in plan.fetch:
            print(f"  {entry.file:<40} {entry.url}")
        print(summarize(entries))
        return 0
    with open_client() as client:
        return fetch_pinned(Fetcher(client), entries, dest)


if __name__ == "__main__":
    raise SystemExit(main())
