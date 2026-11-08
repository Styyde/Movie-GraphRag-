import base64
import binascii
import hashlib
import hmac
import json
import math
import secrets
import time

from moviegraphrag.schemas.models import QuestionDraft


class Clarifications:
    """Opaque client tokens: signed plans and confirmed choices, scoped to one question."""

    MAX_TOKEN_LENGTH = 65536

    def __init__(self, secret: bytes | None = None, ttl: int = 1800):
        self.secret = secret or secrets.token_bytes(32)
        self.ttl = ttl

    def issue(self, question: str, plan: QuestionDraft, links):
        payload = {"question": hashlib.sha256(question.encode()).hexdigest(),
                   "expires": time.time() + self.ttl, "plan": plan.model_dump(mode="json"),
                   "confirmed": {r.mention.ref: r.canonical_id for r in links
                                 if r.status == "resolved" and r.method == "user_confirmed"},
                   "allowed": {r.mention.ref: [c.id for c in r.alternatives]
                               for r in links if r.status == "ambiguous"
                               and not r.truncated and not r.blocked_by_context}}
        raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        sig = hmac.new(self.secret, raw.encode(), hashlib.sha256).hexdigest()
        token = raw + "." + sig
        if len(token) > self.MAX_TOKEN_LENGTH:
            raise ValueError("Clarification is too large; refine the question")
        return token

    def verify(self, token: str, question: str):
        if not isinstance(token, str) or len(token) > self.MAX_TOKEN_LENGTH:
            raise ValueError("Malformed clarification token")
        try:
            raw, sig = token.rsplit(".", 1)
        except ValueError as exc:
            raise ValueError("Malformed clarification token") from exc
        if not raw or len(sig) != 64 or any(c not in "0123456789abcdef" for c in sig):
            raise ValueError("Malformed clarification token")
        try:
            expected = hmac.new(self.secret, raw.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                raise ValueError("Invalid clarification signature")
        except (TypeError, UnicodeError) as exc:
            raise ValueError("Malformed clarification token") from exc
        try:
            payload = json.loads(base64.b64decode(raw, altchars=b"-_", validate=True))
            if not isinstance(payload, dict) or set(payload) != {
                "question", "expires", "plan", "allowed", "confirmed"
            }:
                raise ValueError("Invalid payload fields")
            expires = payload["expires"]
            if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires):
                raise ValueError("Invalid expiry")
            if not isinstance(payload["question"], str):
                raise ValueError("Invalid question hash")
            plan = QuestionDraft.model_validate(payload["plan"])
            refs = {mention.ref for mention in plan.mentions}
            allowed, confirmed = payload["allowed"], payload["confirmed"]
            if not isinstance(allowed, dict) or not isinstance(confirmed, dict):
                raise ValueError("Invalid selection maps")
            if (set(allowed) | set(confirmed)) - refs or set(allowed) & set(confirmed):
                raise ValueError("Invalid selection references")
            def valid_id(value):
                return isinstance(value, int) and not isinstance(value, bool) and value > 0
            for ids in allowed.values():
                if (not isinstance(ids, list) or not 1 <= len(ids) <= 10
                        or not all(valid_id(value) for value in ids) or len(set(ids)) != len(ids)):
                    raise ValueError("Invalid candidate IDs")
            if not all(valid_id(value) for value in confirmed.values()):
                raise ValueError("Invalid confirmed IDs")
        except (KeyError, TypeError, UnicodeError, ValueError, binascii.Error) as exc:
            raise ValueError("Malformed clarification token") from exc
        if expires <= time.time():
            raise ValueError("Clarification expired; submit the question again")
        if payload["question"] != hashlib.sha256(question.encode()).hexdigest():
            raise ValueError("Clarification belongs to another question")
        return plan, allowed, confirmed
