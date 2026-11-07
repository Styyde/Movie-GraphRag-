import base64
import hashlib
import hmac
import json
import secrets
import time

from moviegraphrag.schemas.models import QuestionDraft


class Clarifications:
    """Stateless signed replay; tokens expire and are scoped to the exact question."""

    def __init__(self, secret: bytes | None = None, ttl: int = 1800):
        self.secret = secret or secrets.token_bytes(32)
        self.ttl = ttl

    def issue(self, question: str, plan: QuestionDraft, links):
        payload = {"question": hashlib.sha256(question.encode()).hexdigest(),
                   "expires": time.time() + self.ttl, "plan": plan.model_dump(mode="json"),
                   "allowed": {r.mention.ref: [c.id for c in r.alternatives]
                               for r in links if r.status == "ambiguous"
                               and not r.truncated and not r.blocked_by_context}}
        raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        sig = hmac.new(self.secret, raw.encode(), hashlib.sha256).hexdigest()
        return raw + "." + sig

    def verify(self, token: str, question: str):
        try:
            raw, sig = token.rsplit(".", 1)
            expected = hmac.new(self.secret, raw.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                raise ValueError("Invalid clarification signature")
            payload = json.loads(base64.urlsafe_b64decode(raw))
            if payload["expires"] < time.time():
                raise ValueError("Clarification expired; submit the question again")
            if payload["question"] != hashlib.sha256(question.encode()).hexdigest():
                raise ValueError("Clarification belongs to another question")
            return QuestionDraft.model_validate(payload["plan"]), payload["allowed"]
        except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Malformed clarification token") from exc
