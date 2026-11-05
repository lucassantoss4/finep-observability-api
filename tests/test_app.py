from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from finep_api.collector import ParseError, collect, decode_html, parse, parse_detail
from finep_api.domain import Opportunity, classify
from finep_api.main import create_app
from finep_api.storage import Storage

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def storage(tmp_path):
    db = Storage(str(tmp_path / "finep.db"))
    db.initialize()
    return db


def opportunity(**changes):
    return Opportunity(
        id="774",
        title="Example",
        source_url="https://legacy.finep.gov.br/en/chamadas-publicas/chamadapublica/774",
        **changes,
    )


@pytest.mark.parametrize(
    ("state", "deadline", "expected"),
    [
        ("aberto", "2026-11-06", "aberto"),
        ("aberto", "2026-11-04", "nao_confirmado"),
        ("aberto", "2026-11-05", "nao_confirmado"),
        ("aberto", None, "nao_confirmado"),
        ("encerrado", "2026-11-06", "encerrado"),
        ("nao_confirmado", "2026-11-04", "encerrado"),
        ("nao_confirmado", "2026-11-06", "nao_confirmado"),
    ],
)
def test_status_rules(state, deadline, expected):
    item = opportunity(source_status=state, deadline=deadline)
    assert classify(item, date(2026, 11, 5))[0] == expected


def test_live_html_fixtures():
    items = parse((FIXTURES / "list.html").read_text())
    assert len(items) == 4
    assert len({item.id for item in items}) == 4
    item = parse_detail(items[0], (FIXTURES / "detail.html").read_text())
    assert item.id == "774"
    assert item.deadline == date(2026, 9, 30)
    assert item.source_status == "aberto"
    assert classify(item, date(2026, 11, 5))[0] == "nao_confirmado"


@pytest.mark.parametrize("html", ["", "<html>Maintenance</html>", '<div class="item">Nothing</div>'])
def test_layout_change_rejected(html):
    with pytest.raises(ParseError):
        parse(html)


def test_invalid_detail_date_rejected():
    html = (FIXTURES / "detail.html").read_text().replace("30/09/2026", "31/02/2026")
    with pytest.raises(ParseError):
        parse_detail(opportunity(), html)


def test_missing_detail_date_is_not_assumed():
    html = '<h2 class="tit_pag"><a>Example</a></h2>'
    assert parse_detail(opportunity(), html).deadline is None


def test_collection_is_idempotent(storage):
    def source(request):
        path = "list.html" if request.url.path.endswith("chamadaspublicas") else "detail.html"
        return httpx.Response(200, text=(FIXTURES / path).read_text())

    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        assert collect(storage, client) == 4
        assert collect(storage, client) == 4
    assert len(storage.snapshot()[0]) == 4
    assert storage.collection_stats()[0] == {"1": 2}


@pytest.mark.parametrize("failure", ["timeout", "http", "parser", "detail"])
def test_failed_collection_preserves_data(storage, failure):
    stamp = datetime.now(UTC).isoformat()
    storage.save_success([opportunity()], stamp, 0.1)

    def source(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("simulated timeout", request=request)
        if failure == "http":
            return httpx.Response(503)
        if failure == "parser" or not request.url.path.endswith("chamadaspublicas"):
            return httpx.Response(200, text="<html>layout changed</html>")
        return httpx.Response(200, text=(FIXTURES / "list.html").read_text())

    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        with pytest.raises((httpx.HTTPError, ParseError)):
            collect(storage, client)
    items, checked = storage.snapshot()
    assert [item.id for item in items] == ["774"]
    assert checked == stamp
    assert storage.collection_stats()[0] == {"0": 1, "1": 1}


def test_api_empty_database(storage):
    with TestClient(create_app(storage.path, telemetry=False)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        data = client.get("/opportunities").json()
        assert data["items"] == []
        assert data["data_stale"] is True
        assert data["last_success_at"] is None
        assert client.get("/opportunities?limit=101").status_code == 422
        assert client.get("/opportunities?offset=-1").status_code == 422


def test_api_freshness_and_metrics(storage):
    stamp = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    storage.save_success([opportunity()], stamp, 0.15)
    with TestClient(create_app(storage.path, telemetry=False)) as client:
        assert client.get("/opportunities").json()["data_stale"] is True
        assert client.get("/opportunities?offset=1").json()["items"] == []
        metrics = client.get("/metrics").text
        families = list(text_string_to_metric_families(metrics))
        assert any(f.name == "finep_http_requests" for f in families)
        assert 'finep_collection_runs{result="success"} 1.0' in metrics
        assert 'finep_opportunities{status="nao_confirmado"} 1.0' in metrics


def test_database_failure_returns_503(storage):
    with TestClient(create_app(storage.path, telemetry=False)) as client:
        Path(storage.path).unlink()
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert client.get("/opportunities").json() == {"detail": "Storage unavailable"}


@pytest.mark.parametrize(
    "payload, expected",
    [
        ("Inovação".encode("utf-8"), "Inovação"),
        ("Inovação".encode("cp1252"), "Inovação"),
        (b"Inova\xe7\xe3o\x8d", "Inovação\ufffd"),
    ],
)
def test_source_encoding(payload, expected):
    assert decode_html(payload) == expected
