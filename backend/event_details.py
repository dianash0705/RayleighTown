from typing import Any, TypedDict


class EventDetailField(TypedDict, total=False):
    key: str
    label: str
    value: str
    emphasis: bool


class EventDetails(TypedDict):
    title: str
    fields: list[EventDetailField]
    identity: dict[str, str]


def make_event_details(
    title: str,
    fields: list[EventDetailField],
    identity: dict[str, str] | None = None,
) -> EventDetails:
    return {
        "title": title,
        "fields": fields,
        "identity": identity or {},
    }


def field(key: str, label: str, value: Any, emphasis: bool = False) -> EventDetailField | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    return {
        "key": key,
        "label": label,
        "value": text,
        "emphasis": emphasis,
    }


def compact_fields(*items: EventDetailField | None) -> list[EventDetailField]:
    return [item for item in items if item is not None]
