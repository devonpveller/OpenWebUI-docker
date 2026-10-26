"""What an intake answer means for the connector (DESIGN-intake.md sections 2, 3 and 7; the I5
service's HTTP contract, intake_service/web.py and core.py).

    classify(status, doc, retry_after, sent_key, connector) -> Outcome

THE TABLE (`action`: record = done with this item; retry = send the same envelope again later;
stop = end the run, nothing more is sent - retrying cannot help and the next item would fail the
same way):

  HTTP  intake's meaning                                        action  label
  200   a result: clean | suspicious                             record  clean | suspicious
  200   pending (waiting for the second opinion, or an errored
        screen intake re-screens itself) - intake owns it now    record  pending
  200   injected (discarded; a discard record is kept)           record  injected
  200   rejected (content: a parser bomb) - the same bytes are
        rejected again, so never resent                          record  rejected
  200   no label intake documents, or a key / connector that is
        not the one sent                                         stop    -
  400   the body is not a JSON object / a malformed length       stop    -   (a kit bug)
  401   no key presented                                         stop    -
  403   the key is no caller's, or not this connector's          stop    -
  408   the body did not arrive within intake's read deadline    retry   -
  411   no Content-Length / chunked                              stop    -   (a kit bug)
  413   the body is over intake's request cap                    stop    -   (the kit's cap is
                                                                             set above intake's)
  422   stage 1 rejected an envelope the kit accepted            stop    rejected (schema drift)
  503   busy / no worker / store down / no key announced         retry   -   (Retry-After honoured)
  other (404, 405, 500, ...)                                     stop    -
  no answer: connect refused, reset, timeout, a cut-off reply    retry   -

A `pending` or `injected` answer is RECORDED, not retried: intake stored the decision (or the
discard record) under the item's key, and the second opinion / re-screen loops are intake's.
`repeat: true` means intake already had this exact key; it is recorded like any answer.
The text intake returns for clean/suspicious is NOT kept: a connector is a producer, and
consumers read screened text from Open Brain with its labels.
ONE EXCEPTION, opt-in (`keep_clean_text=True`, IntakeClient(return_clean_text=True)): the search
gate (plan step 3) answers its caller synchronously and returns intake's screened text in place
of the page. Then a `clean` answer carries `extra = {"text", "title", "labels"}` - intake's
extracted text and labels exactly as returned, never the raw bytes - and every other label
carries none (suspicious is withheld from the gate's model-facing callers, DESIGN section 6 /
D41). A connector that does not ask gets the default: no text, as above.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

LABELS = ("clean", "suspicious", "pending", "injected", "rejected")
_STATUS_OF = {"clean": "done", "suspicious": "done", "injected": "done", "pending": "pending",
              "rejected": "rejected"}
RETRY_CODES = (408, 503)
STOP_CODES = (400, 401, 403, 411, 413, 422)


@dataclass(frozen=True)
class Outcome:
    action: str                       # record | retry | stop
    http: int | None                  # None: no HTTP answer (network / timeout)
    label: str | None = None
    reason: str = ""
    intake_id: str | None = None
    repeat: bool = False
    revision: int | None = None
    d2_row: str | None = None
    retry_after: float | None = None
    extra: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"action": self.action, "http": self.http, "label": self.label, "reason": self.reason,
                "intake_id": self.intake_id, "repeat": self.repeat, "revision": self.revision,
                "d2_row": self.d2_row}


def _error_text(doc: object) -> str:
    if isinstance(doc, Mapping):
        err = doc.get("error")
        if isinstance(err, str):
            return err[:300]
        if isinstance(err, Mapping) and isinstance(err.get("detail"), str):
            return str(err.get("kind") or "") + ": " + err["detail"][:300]
    return ""


def network(kind: str, detail: str = "") -> Outcome:
    """No HTTP answer (refused, reset, timeout, a reply cut off or over the cap): retry."""
    return Outcome("retry", None, reason=f"{kind}{': ' + detail if detail else ''}"[:300])


def classify(status: int, doc: object, retry_after: float | None, sent_key: Mapping[str, str],
             connector: str, keep_clean_text: bool = False) -> Outcome:
    if status == 200:
        if not isinstance(doc, Mapping):
            return Outcome("stop", 200, reason="protocol: a 200 without a JSON object")
        label = doc.get("label")
        if label not in LABELS:
            return Outcome("stop", 200, reason=f"protocol: unknown label {label!r}")
        if doc.get("status") != _STATUS_OF[label]:
            return Outcome("stop", 200, reason=f"protocol: status {doc.get('status')!r} for label {label!r}")
        if doc.get("key") != dict(sent_key):
            return Outcome("stop", 200, reason="protocol: intake answered for another key")
        labels = doc.get("labels")
        if not isinstance(labels, Mapping) or labels.get("connector") != connector:
            return Outcome("stop", 200, reason="protocol: intake answered for another connector")
        if label in ("injected", "pending", "rejected") and doc.get("text") is not None:
            return Outcome("stop", 200, reason=f"protocol: text returned for a {label} item")
        rev = doc.get("revision")
        extra: dict[str, object] = {}
        if keep_clean_text and label == "clean" and isinstance(doc.get("text"), str):
            title = doc.get("title")
            extra = {"text": doc["text"], "title": title if isinstance(title, str) else None,
                     "labels": dict(labels)}
        return Outcome("record", 200, label=label, reason="", intake_id=str(doc.get("intake_id") or "") or None,
                       repeat=bool(doc.get("repeat")), revision=rev if isinstance(rev, int) else None,
                       d2_row=doc.get("d2_row") if isinstance(doc.get("d2_row"), str) else None, extra=extra)
    err = _error_text(doc)
    if status in RETRY_CODES:
        return Outcome("retry", status, reason=f"http {status}: {err}", retry_after=retry_after)
    if status == 422:
        return Outcome("stop", 422, label="rejected",
                       reason=f"schema drift: intake's stage 1 rejected an envelope the kit accepted ({err})")
    meaning = {400: "the request was malformed (a kit bug)", 401: "no key was presented",
               403: "the key is not valid (or not for this connector)",
               411: "no Content-Length (a kit bug)",
               413: "the body is over intake's request cap (set the client cap at or below intake's)"}
    return Outcome("stop", status, reason=f"http {status}: {meaning.get(status, 'unexpected answer')}"
                   + (f" ({err})" if err else ""))
