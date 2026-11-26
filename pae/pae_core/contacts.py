"""Selected-word facts, independent of learning evidence and score state."""
from dataclasses import dataclass

CONTACT_SELECTED = "contact_selected_v1"
SELECTED_TYPES = ("encounter", CONTACT_SELECTED)


@dataclass(frozen=True)
class ContactFact:
    count: int = 0
    last_selected: int | None = None


def project_contact(contacts, event):
    """Project one committed/transaction-staged selection; caller handles dedup."""
    if event["type"] not in SELECTED_TYPES:
        return
    lemma = event.get("lemma_id") or event.get("lemma")
    if not lemma:
        return
    key = (lemma, event.get("sense_id", 0))
    previous = contacts.get(key, ContactFact())
    ts = event["ts"]
    last = ts if previous.last_selected is None else max(previous.last_selected, ts)
    contacts[key] = ContactFact(previous.count + 1, last)
