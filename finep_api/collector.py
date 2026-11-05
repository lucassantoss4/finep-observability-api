import argparse
import os
import re
import time
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from opentelemetry import trace

from finep_api.domain import Opportunity
from finep_api.storage import Storage
from finep_api.telemetry import configure, logger, shutdown

SOURCE_URL = "https://legacy.finep.gov.br/en/chamadas-publicas/chamadaspublicas"
tracer = trace.get_tracer(__name__)


class ParseError(ValueError):
    pass


def decode_html(html: str | bytes) -> str:
    if isinstance(html, str):
        return html
    try:
        return html.decode("utf-8")
    except UnicodeDecodeError:
        return html.decode("cp1252", errors="replace")


def parse(html: str | bytes) -> list[Opportunity]:
    soup = BeautifulSoup(decode_html(html), "html.parser")
    result = {}
    for card in soup.select("div.item"):
        anchor = card.select_one("h3 a[href]")
        if anchor is None or not re.search(r"/chamadapublica/\d+", anchor["href"]):
            continue
        url = urljoin(SOURCE_URL, anchor["href"])
        if urlparse(url).hostname != "legacy.finep.gov.br":
            raise ParseError("Unexpected opportunity host")
        section = card.find_previous("h2")
        label = section.get_text(strip=True).casefold() if section else ""
        state = {"abertas": "aberto", "encerradas": "encerrado"}.get(label, "nao_confirmado")
        deadline_node = card.select_one(".prazo span")
        deadline = None
        if deadline_node:
            match = re.search(r"\b\d{2}/\d{2}/\d{4}\b", deadline_node.get_text())
            if match:
                try:
                    deadline = datetime.strptime(match[0], "%d/%m/%Y").date()
                except ValueError as exc:
                    raise ParseError("Invalid deadline") from exc
        title = anchor.get_text(" ", strip=True)
        if not title:
            raise ParseError("Empty opportunity title")
        item = Opportunity(
            id=url.rstrip("/").split("/")[-1],
            title=title,
            source_url=url,
            deadline=deadline,
            source_status=state,
        )
        result[item.id] = item
    if not result:
        raise ParseError("No calls found; source layout may have changed")
    return list(result.values())


def parse_detail(item: Opportunity, html: str | bytes) -> Opportunity:
    soup = BeautifulSoup(decode_html(html), "html.parser")
    title = soup.select_one("h2.tit_pag a")
    if title is None:
        raise ParseError("Detail page missing call title")
    fields = {}
    for group in soup.select("div.group"):
        label, value = group.select_one(".tit"), group.select_one(".text")
        if label and value:
            fields[label.get_text(strip=True).casefold()] = value.get_text(" ", strip=True)
    raw = next((v for k, v in fields.items() if "prazo para envio" in k), "")
    match = re.search(r"\b\d{2}/\d{2}/\d{4}\b", raw)
    try:
        deadline = datetime.strptime(match[0], "%d/%m/%Y").date() if match else None
    except ValueError as exc:
        raise ParseError("Invalid detail deadline") from exc
    state = next((v.casefold() for k, v in fields.items() if k.startswith("situa")), "")
    status = {"aberta": "aberto", "encerrada": "encerrado"}.get(state, "nao_confirmado")
    return item.model_copy(
        update={"title": title.get_text(" ", strip=True), "deadline": deadline, "source_status": status}
    )


def fetch_items(client: httpx.Client) -> list[Opportunity]:
    with tracer.start_as_current_span("finep.fetch_list"):
        response = client.get(SOURCE_URL)
        response.raise_for_status()
        items = parse(response.content)
    enriched = []
    for item in items:
        with tracer.start_as_current_span("finep.fetch_detail") as span:
            span.set_attribute("opportunity.id", item.id)
            response = client.get(str(item.source_url))
            response.raise_for_status()
            enriched.append(parse_detail(item, response.content))
    return enriched


def collect(storage: Storage, client: httpx.Client | None = None):
    attempted_at = datetime.now(UTC).isoformat()
    started = time.monotonic()
    with tracer.start_as_current_span("finep.collect") as span:
        span.set_attribute("source.name", "finep")
        try:
            if client is None:
                with httpx.Client(
                    timeout=20, follow_redirects=True, headers={"User-Agent": "finep-observability-api/0.1"}
                ) as session:
                    items = fetch_items(session)
            else:
                items = fetch_items(client)
            storage.save_success(items, attempted_at, time.monotonic() - started)
            span.set_attribute("collection.records", len(items))
            logger.info("collection_completed records=%s", len(items))
            return len(items)
        except Exception as exc:
            storage.save_failure(attempted_at, time.monotonic() - started, type(exc).__name__)
            logger.exception("collection_failed error_type=%s", type(exc).__name__)
            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0, help="Seconds between runs; 0 runs once")
    args = parser.parse_args()
    if args.interval and args.interval < 60:
        parser.error("Interval must be at least 60 seconds")
    providers = configure("finep-collector")
    storage = Storage(os.getenv("DATABASE_PATH", "data/finep.db"))
    storage.initialize()
    try:
        while True:
            try:
                collect(storage)
            except Exception:
                if not args.interval:
                    raise
            if not args.interval:
                break
            time.sleep(args.interval)
    finally:
        shutdown(providers)


if __name__ == "__main__":
    main()
