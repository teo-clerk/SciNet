"""GET /api/quantities/rows — the measurements themselves, with provenance.

The search endpoint answers the map with paper ids; this one answers a reader
or an agent with the rows, each carrying the sentence it came from. The same
trust rule holds: rejected and unreviewed guesses never appear, however the
question is phrased.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import Base, build_engine, get_db
from app.main import create_app
from app.models import Paper, PaperMeta, Quantity, QuantityStatus
from app.models.enums import PaperStatus
from app.routers.quantities import kind_for_unit


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "library",
        markdown_dir=tmp_path / "markdown",
        vectors_dir=tmp_path / "vectors",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "q.db",
    )
    engine = build_engine(f"sqlite+pysqlite:///{settings.db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        for i in (1, 2):
            session.add(
                Paper(
                    id=i,
                    content_sha256=f"{i:064d}",
                    work_key=f"w{i}",
                    pdf_path=str(tmp_path / f"{i}.pdf"),
                    status=PaperStatus.READY,
                )
            )
            session.add(PaperMeta(paper_id=i, title=f"Paper {i}"))

        def q(paper_id, kind, value_si, unit_si, original, unit, sentence, status):
            return Quantity(
                paper_id=paper_id,
                chunk_ord=0,
                section="Results",
                quantity_kind=kind,
                value_si=value_si,
                unit_si=unit_si,
                value_original=original,
                unit_original=unit,
                context_sentence=sentence,
                confidence=0.95,
                status=status,
            )

        auto, confirmed = QuantityStatus.AUTO, QuantityStatus.CONFIRMED
        session.add_all(
            [
                q(1, "length", 500.0, "meter", "500", "m", "It spans 500 m.", auto),
                q(
                    1,
                    "length",
                    0.05,
                    "meter",
                    "5",
                    "cm",
                    "The gap measured 5 cm.",
                    confirmed,
                ),
                q(
                    1,
                    "length",
                    700.0,
                    "meter",
                    "700",
                    "m",
                    "Pending 700 m.",
                    QuantityStatus.PENDING_REVIEW,
                ),
                q(
                    2,
                    "frequency",
                    40.0,
                    "hertz",
                    "40",
                    "Hz",
                    "Oscillations peaked at 40 Hz during the task.",
                    auto,
                ),
                q(
                    2,
                    "frequency",
                    2000.0,
                    "hertz",
                    "2",
                    "kHz",
                    "Sampling ran at 2 kHz throughout.",
                    auto,
                ),
                q(
                    2,
                    "frequency",
                    10.0,
                    "hertz",
                    "10",
                    "Hz",
                    "A rejected 10 Hz reading.",
                    QuantityStatus.REJECTED,
                ),
            ]
        )
        session.commit()

    app = create_app()

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    engine.dispose()


def rows(client, **params):
    res = client.get("/api/quantities/rows", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def test_kind_and_range_return_the_rows_with_their_sentences(client) -> None:
    body = rows(client, kind="frequency", min_value=1, max_value=100)

    assert body["count"] == 1 and body["papers"] == 1
    [row] = body["rows"]
    assert row["paper_id"] == 2 and row["paper_title"] == "Paper 2"
    assert row["value_si"] == 40.0 and row["unit_si"] == "hertz"
    assert row["context_sentence"] == "Oscillations peaked at 40 Hz during the task."


def test_a_phrase_matches_the_sentence_case_aside(client) -> None:
    body = rows(client, q="OSCILLAT")
    assert [r["value_si"] for r in body["rows"]] == [40.0]


def test_a_unit_token_chooses_the_kind_and_narrows_to_that_token(client) -> None:
    khz = rows(client, unit="kHz")
    assert [r["value_original"] for r in khz["rows"]] == ["2"]

    si_name = rows(client, unit="hertz")
    assert sorted(r["value_si"] for r in si_name["rows"]) == [40.0, 2000.0]


def test_rejected_and_pending_rows_never_appear(client) -> None:
    assert rows(client, kind="frequency")["count"] == 2
    assert rows(client, kind="length")["count"] == 2


def test_no_criterion_is_a_400(client) -> None:
    res = client.get("/api/quantities/rows")
    assert res.status_code == 400
    assert "kind, a unit or a phrase" in res.json()["detail"]


def test_the_limit_pages_while_the_count_stays_whole(client) -> None:
    body = rows(client, kind="length", limit=1)
    assert len(body["rows"]) == 1
    assert body["count"] == 2 and body["papers"] == 1


def test_rows_come_back_smallest_first(client) -> None:
    body = rows(client, kind="length")
    assert [r["value_si"] for r in body["rows"]] == [0.05, 500.0]


def test_kind_for_unit_knows_tokens_and_si_names_and_nothing_else() -> None:
    assert kind_for_unit("Hz") == "frequency"
    assert kind_for_unit("khz") == "frequency"
    assert kind_for_unit("hertz") == "frequency"
    assert kind_for_unit("mm") == "length"
    assert kind_for_unit("furlongs") is None
