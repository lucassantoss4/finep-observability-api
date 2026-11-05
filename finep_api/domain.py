from datetime import date
from enum import StrEnum

from pydantic import BaseModel, HttpUrl


class Status(StrEnum):
    OPEN = "aberto"
    CLOSED = "encerrado"
    UNKNOWN = "nao_confirmado"


class Opportunity(BaseModel):
    id: str
    title: str
    source_url: HttpUrl
    deadline: date | None = None
    source_status: str = "nao_confirmado"


def classify(item: Opportunity, today: date) -> tuple[Status, str]:
    if item.source_status == "aberto" and item.deadline and item.deadline < today:
        return Status.UNKNOWN, "Fonte indica aberta, mas o prazo informado passou"
    if item.source_status == "encerrado":
        return Status.CLOSED, "Fonte informa encerramento"
    if item.deadline and item.deadline < today:
        return Status.CLOSED, "Prazo informado terminou"
    if item.source_status == "aberto" and item.deadline and item.deadline > today:
        return Status.OPEN, "Fonte indica aberta e prazo informado está no futuro"
    return Status.UNKNOWN, "Prazo ausente, situação desconhecida ou encerra hoje sem horário"
