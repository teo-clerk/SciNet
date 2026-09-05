"""The demo corpus fetcher, and the naming contract it shares with the evaluator.

The corpus is a benchmark only if two scripts agree on one thing: the
filename the fetcher writes is the filename ``eval_clustering.py`` reads the
domain back from. Everything else here is the fetcher's own judgement —
which records are safe to take, and what a pinned manifest means on disk.
No test touches the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from eval_clustering import domain_of  # noqa: E402
from fetch_demo_corpus import (  # noqa: E402
    MANIFESTS,
    RECIPE,
    RECIPES,
    Entry,
    Hit,
    choose_legacy,
    entry_for,
    filename_for,
    is_legacy,
    page_budget,
    parse_arxiv_feed,
    parse_ntrs_results,
    plan_fetch,
    read_manifest,
    sha256_of,
    write_manifest,
)

ATOM_FEED = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2608.11271v1</id>
    <title>Hardware-Aware Deployment of Joint SAR Compression
      and Despeckling on FPGA</title>
    <published>2026-08-11T07:29:13Z</published>
    <link href="https://arxiv.org/abs/2608.11271v1" rel="alternate" type="text/html"/>
    <link href="https://arxiv.org/pdf/2608.11271v1" rel="related"
          type="application/pdf" title="pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/astro-ph/0601001v2</id>
    <title>An old-style identifier</title>
    <published>2006-01-01T00:00:00Z</published>
  </entry>
</feed>
"""


def ntrs_record(**overrides):
    record = {
        "id": 19680012395,
        "title": "Non-linear  orbit determination\nmethods",
        "distribution": "PUBLIC",
        "disseminated": "DOCUMENT_AND_METADATA",
        "copyright": {"determinationType": "GOV_PUBLIC_USE_PERMITTED"},
        "downloads": [
            {
                "mimetype": "application/pdf",
                "links": {"pdf": "/api/citations/19680012395/downloads/x.pdf"},
            }
        ],
    }
    record.update(overrides)
    return record


# --- the naming contract -------------------------------------------------------


@pytest.mark.parametrize(
    ("domain", "source", "ident", "expected"),
    [
        ("SAR", "arxiv", "2608.11271v1", "SAR_2608.11271v1.pdf"),
        ("Instruments", "arxiv", "astro-ph/0601001v2", "Instruments_0601001v2.pdf"),
        ("Orbits", "ntrs", "19680012395", "Orbits_ntrs-19680012395.pdf"),
    ],
)
def test_filenames_carry_the_domain_the_evaluator_reads_back(
    domain, source, ident, expected
):
    name = filename_for(domain, source, ident)

    assert name == expected
    assert domain_of(f"/library/{name}") == domain


def test_every_recipe_label_survives_the_round_trip():
    """A label with a digit or an underscore-digit would silently truncate."""
    for domain in RECIPE:
        assert domain_of(filename_for(domain.label, "arxiv", "2401.00001v1")) == (
            domain.label
        )
        assert domain_of(filename_for(domain.label, "ntrs", "19650023686")) == (
            domain.label
        )


def test_the_old_corpus_convention_still_parses():
    assert domain_of("Genomics_and_Bio_2401.12345v1.pdf") == "Genomics_and_Bio"
    assert domain_of("Astrophysics_0812.4574v2.pdf") == "Astrophysics"
    assert domain_of("some_random_paper.pdf") is None


def test_every_recipe_keeps_the_naming_contract():
    """A second corpus is a second benchmark only if its labels survive the
    same round trip; each recipe also writes its own manifest, so building one
    never overwrites the other's pinned hashes."""
    assert RECIPES["aerospace"] is RECIPE
    for recipe in RECIPES.values():
        for domain in recipe:
            assert domain_of(filename_for(domain.label, "arxiv", "2401.00001v1")) == (
                domain.label
            )
    assert len({path.name for path in MANIFESTS.values()}) == len(MANIFESTS)
    assert set(MANIFESTS) == set(RECIPES)


# --- reading the sources --------------------------------------------------------


def test_arxiv_feed_yields_identifier_title_and_pdf_link():
    hits = parse_arxiv_feed(ATOM_FEED)

    assert [h.ident for h in hits] == ["2608.11271v1", "astro-ph/0601001v2"]
    first = hits[0]
    assert first.source == "arxiv"
    assert first.title == (
        "Hardware-Aware Deployment of Joint SAR Compression and Despeckling on FPGA"
    )
    assert first.url == "https://arxiv.org/pdf/2608.11271v1"
    assert first.year == 2026


def test_arxiv_entry_without_a_pdf_link_falls_back_to_the_canonical_url():
    hits = parse_arxiv_feed(ATOM_FEED)

    assert hits[1].url == "https://arxiv.org/pdf/astro-ph/0601001v2"


def test_ntrs_keeps_only_public_downloadable_government_work():
    payload = {
        "results": [
            ntrs_record(),
            ntrs_record(id=1, disseminated="METADATA_ONLY"),
            ntrs_record(id=2, distribution="LIMITED"),
            ntrs_record(
                id=3, copyright={"determinationType": "MAY_INCLUDE_COPYRIGHT_MATERIAL"}
            ),
            ntrs_record(id=4, downloads=[]),
        ]
    }

    hits = parse_ntrs_results(payload)

    assert [h.ident for h in hits] == ["19680012395"]
    hit = hits[0]
    assert hit.year == 1968
    assert hit.title == "Non-linear orbit determination methods"
    assert hit.url.startswith("https://ntrs.nasa.gov/api/citations/19680012395/")


def test_legacy_means_before_the_scan_era_ended():
    old = Hit("ntrs", "19680012395", "t", "u", 1968, "r")
    new = Hit("ntrs", "20240008045", "t", "u", 2024, "r")
    undated = Hit("ntrs", "x", "t", "u", None, "r")

    assert is_legacy(old)
    assert not is_legacy(new)
    assert not is_legacy(undated)


def test_a_scan_is_budgeted_in_ocr_minutes_and_a_text_layer_is_not():
    """A long report with a text layer costs seconds and exercises the 80-page
    cap; a long scan costs an hour of tier 2 and teaches nothing new."""
    assert page_budget(False) < 80 < page_budget(True)
    assert page_budget(None) == page_budget(True)


def test_legacy_picks_scans_first_then_the_longest_readable_report():
    def legacy(name, pages, text_layer):
        return Entry(
            name, "SAR", "ntrs", name, "t", "u", 1970, "r",
            pages=pages, text_layer=text_layer,
        )  # fmt: skip

    short = legacy("short", 5, True)
    long = legacy("long", 111, True)
    scan = legacy("scan", 20, False)

    assert choose_legacy([short, long, scan], 2) == [scan, long]
    assert choose_legacy([short, long], 1) == [long]
    assert choose_legacy([], 2) == []


# --- the manifest on disk --------------------------------------------------------


def test_manifest_round_trips_and_is_ordered_for_diffs(tmp_path):
    later = entry_for("SAR", Hit("arxiv", "2608.11271v1", "A", "u", 2026, "r"))
    earlier = entry_for("GNSS", Hit("ntrs", "19940026143", "B", "u", 1994, "r"))
    path = tmp_path / "manifest.jsonl"

    write_manifest(path, [later, earlier])

    assert read_manifest(path) == [earlier, later]
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["file"] == "GNSS_ntrs-19940026143.pdf"


def test_plan_fetches_the_missing_and_the_changed_and_keeps_the_intact(tmp_path):
    dest = tmp_path / "library"
    dest.mkdir()
    (dest / "SAR_1.pdf").write_bytes(b"%PDF intact")
    (dest / "SAR_2.pdf").write_bytes(b"%PDF tampered")
    pinned = lambda name, sha: Entry(  # noqa: E731 - a row factory, not a function
        name, "SAR", "arxiv", name[4], "t", "u", 2026, "r", sha256=sha
    )
    intact = pinned("SAR_1.pdf", sha256_of(dest / "SAR_1.pdf"))
    changed = pinned("SAR_2.pdf", "0" * 64)
    missing = pinned("SAR_3.pdf", "0" * 64)

    plan = plan_fetch([intact, changed, missing], dest)

    assert plan.present == (intact,)
    assert plan.changed == (changed,)
    assert plan.fetch == (changed, missing)


def test_an_unpinned_entry_on_disk_counts_as_present(tmp_path):
    """A manifest built before hashing must not re-download what it has."""
    dest = tmp_path
    (dest / "SAR_1.pdf").write_bytes(b"%PDF")
    entry = Entry("SAR_1.pdf", "SAR", "arxiv", "1", "t", "u", 2026, "r")

    assert plan_fetch([entry], dest).present == (entry,)
