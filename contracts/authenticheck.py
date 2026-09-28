# v0.2.17
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass
from datetime import datetime, timezone
import json

UserError = gl.vm.UserError

VALID_CATEGORIES = ("Sneakers", "Watch", "Trading Card", "Handbag", "Other")
VALID_VERDICTS = ("AUTHENTIC", "FAKE")
MIN_CONFIDENCE = 60
PAY_SELLER = "PAY_SELLER"
REFUND_BUYER = "REFUND_BUYER"
DISPUTE = "DISPUTE"
RENDER_CHAR_CAP = 2000
MAX_DESCRIPTION = 2000
MAX_SERIAL = 200
MAX_URLS = 6
ZERO_ADDR = Address("0x0000000000000000000000000000000000000000")


def _addr_str(a) -> str:
    if isinstance(a, (bytes, bytearray)):
        return ("0x" + bytes(a).hex()).lower()
    try:
        return str(a.as_hex).lower()
    except Exception:
        pass
    s = str(a).lower().strip()
    if s.startswith("0x"):
        return s
    if len(s) == 40:
        return "0x" + s
    return s


def _to_address(val) -> Address:
    if isinstance(val, Address):
        return val
    if isinstance(val, (bytes, bytearray)):
        return Address("0x" + bytes(val).hex())
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str.startswith("0x"):
            val_str = "0x" + val_str
        return Address(val_str)
    if hasattr(val, "as_hex"):
        return val
    try:
        return Address(val)
    except Exception:
        return Address("0x" + bytes(val).hex())


def _same_addr(a, b) -> bool:
    return _addr_str(a) == _addr_str(b)


def _is_zero(a) -> bool:
    return _same_addr(a, ZERO_ADDR)


def _current_unix_timestamp() -> u256:
    """
    Verified GenVM clock. gl.message.datetime is an ISO-8601 string.
    Parse with datetime.fromisoformat. Do not use gl.block.timestamp.
    """
    raw = None
    try:
        raw = getattr(gl.message, "datetime", None)
    except Exception:
        raw = None
    if raw is None or str(raw).strip() == "":
        try:
            raw = gl.message_raw.get("datetime")
        except Exception:
            raw = None
    if raw is not None and str(raw).strip() != "":
        dt_str = str(raw).strip()
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return u256(int(dt.timestamp()))
        except Exception as err:
            raise UserError("Invalid execution timestamp format from GenVM message: " + str(err))
    return u256(int(datetime.now(timezone.utc).timestamp()))


def _as_str_list(values) -> list:
    out = []
    try:
        n = len(values)
    except Exception:
        return out
    for i in range(n):
        out.append(str(values[i]))
    return out


def _host(url: str) -> str:
    raw = str(url).strip()
    lower = raw.lower()
    if not (lower.startswith("http://") or lower.startswith("https://")):
        raise UserError("URL must start with http:// or https://")
    rest = raw.split("://", 1)[1]
    host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in host:
        host = host.split("@", 1)[1]
    host = host.split(":", 1)[0].lower()
    if host.startswith("www."):
        host = host[4:]
    if len(host) == 0:
        raise UserError("URL host is empty")
    return host


def _clean_urls(urls, kind: str, minimum: int, distinct_hosts: bool) -> list:
    cleaned = []
    hosts = []
    for raw in _as_str_list(urls):
        url = str(raw).strip()
        if len(url) == 0:
            continue
        if len(url) > 500:
            raise UserError(kind + " URL is too long")
        host = _host(url)
        if distinct_hosts:
            for existing in hosts:
                if existing == host:
                    raise UserError(kind + " URLs must be independent (distinct hosts)")
        cleaned.append(url)
        hosts.append(host)
        if len(cleaned) > MAX_URLS:
            raise UserError("At most " + str(MAX_URLS) + " " + kind + " URLs")
    if len(cleaned) < minimum:
        raise UserError("At least " + str(minimum) + " " + kind + " URL(s) required")
    return cleaned


def _bound_page_text(text) -> str:
    s = str(text or "")
    s = s.replace("<<<", "[").replace(">>>", "]").replace("```", "'''")
    if len(s) > RENDER_CHAR_CAP:
        s = s[:RENDER_CHAR_CAP]
    return s


def _page_unreadable(body: str) -> bool:
    low = str(body or "").lower()
    if len(low.strip()) == 0:
        return True
    if "please login" in low or "please log in" in low or "sign in to continue" in low:
        return True
    if "webpage_load_failed" in low:
        return True
    return False


def _fetch_url(url: str, kind: str) -> str:
    """A blocked or empty page must not revert the transaction. Return a marker instead."""
    try:
        res = gl.nondet.web.render(url)
        raw = res.body if hasattr(res, "body") else res
        body = _bound_page_text(raw).strip()
    except Exception as err:
        return "FETCH_FAILED [" + kind + " " + str(url) + "]: " + str(err)
    if _page_unreadable(body):
        return "FETCH_FAILED [" + kind + " " + str(url) + "]: page was empty or required login"
    return "[" + url + "]: " + body


def _unreadable_pages(contents) -> list:
    failed = []
    for item in contents:
        text = str(item)
        if text.startswith("FETCH_FAILED"):
            failed.append(text)
    return failed


def _dispute_unreadable(failed) -> dict:
    return {
        "verdict": "",
        "confidence": 0,
        "reason": "Could not read a page, so nobody was paid. Replace it with a public text page. " + " | ".join(failed),
        "consequence": DISPUTE,
    }


def _leader_payload(leader_res):
    if hasattr(leader_res, "value") and isinstance(leader_res.value, dict):
        return leader_res.value
    if hasattr(leader_res, "calldata") and isinstance(leader_res.calldata, dict):
        return leader_res.calldata
    if isinstance(leader_res, dict):
        return leader_res
    return None


def _settlement_consequence(verdict: str, confidence: int) -> str:
    """Discrete payout branch. Compared with == . No percentage of the escrow."""
    label = str(verdict or "").strip().upper()
    if label == "AUTHENTIC" and int(confidence) >= MIN_CONFIDENCE:
        return PAY_SELLER
    if label == "FAKE" and int(confidence) >= MIN_CONFIDENCE:
        return REFUND_BUYER
    return DISPUTE


def _parse_verdict(raw) -> dict:
    if isinstance(raw, dict):
        data = raw
    else:
        cleaned = str(raw).strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if len(lines) >= 1 and lines[0].startswith("```"):
                lines = lines[1:]
            if len(lines) >= 1 and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        try:
            data = json.loads(cleaned)
        except Exception as err:
            return {
                "verdict": "",
                "confidence": 0,
                "reason": "Failed to parse LLM response. Error: " + str(err),
                "consequence": DISPUTE,
            }

    if not isinstance(data, dict):
        return {
            "verdict": "",
            "confidence": 0,
            "reason": "AI verdict response must be a JSON object",
            "consequence": DISPUTE,
        }

    verdict = str(data.get("verdict", "")).strip().upper()
    if verdict == "":
        reason = str(data.get("reason", "")).strip()
        if len(reason) == 0:
            reason = "No binary verdict was returned"
        return {
            "verdict": "",
            "confidence": 0,
            "reason": reason,
            "consequence": DISPUTE,
        }
    if verdict not in VALID_VERDICTS:
        return {
            "verdict": "",
            "confidence": 0,
            "reason": "verdict must be AUTHENTIC or FAKE — got: " + verdict,
            "consequence": DISPUTE,
        }

    try:
        conf = int(data.get("confidence", 0))
    except Exception:
        conf = 0
    if conf < 0 or conf > 100:
        conf = 0

    return {
        "verdict": verdict,
        "confidence": conf,
        "reason": str(data.get("reason", "")),
        "consequence": _settlement_consequence(verdict, conf),
    }


def _validators_agree(leader: dict, mine: dict) -> bool:
    """
    Absolute equality of the binary verdict AND the discrete settlement branch.
    Two validators that both say AUTHENTIC but disagree on whether confidence
    clears 60 would pay different parties — that must not pass consensus.
    There is no percentage tolerance and no arithmetic on the escrow amount.
    """
    try:
        lv = str(leader.get("verdict", "")).strip().upper()
        mv = str(mine.get("verdict", "")).strip().upper()
        lc = str(leader.get("consequence", "")).strip()
        mc = str(mine.get("consequence", "")).strip()
    except Exception:
        return False
    if lv != mv:
        return False
    if lc != mc:
        return False
    if lc not in (PAY_SELLER, REFUND_BUYER, DISPUTE):
        return False
    return True


@allow_storage
@dataclass
class Transaction:
    buyer: Address
    seller: Address
    item_category: str
    item_description: str
    serial_or_identifier: str
    amount: bigint
    proof_deadline: u256
    proof_urls: DynArray[str]
    reference_urls: DynArray[str]
    status: str
    verdict: str
    verdict_reason: str
    confidence: u256
    settled: bool


class Contract(gl.Contract):
    owner: Address
    tx_counter: bigint
    transactions: TreeMap[str, Transaction]

    def __init__(self):
        self.owner = _to_address(gl.message.sender_address)
        self.tx_counter = bigint(0)

    def _tx_dict(self, tx_id: str, t: Transaction) -> dict:
        return {
            "tx_id": str(tx_id),
            "buyer": _addr_str(t.buyer),
            "seller": _addr_str(t.seller),
            "item_category": str(t.item_category),
            "item_description": str(t.item_description),
            "serial_or_identifier": str(t.serial_or_identifier),
            "amount": str(int(t.amount)),
            "proof_deadline": str(int(t.proof_deadline)),
            "proof_urls": _as_str_list(t.proof_urls),
            "reference_urls": _as_str_list(t.reference_urls),
            "status": str(t.status),
            "verdict": str(t.verdict),
            "verdict_reason": str(t.verdict_reason),
            "confidence": int(t.confidence),
            "settled": bool(t.settled),
        }

    @gl.public.write.payable
    def create_transaction(
        self,
        seller: Address,
        item_category: str,
        item_description: str,
        serial_or_identifier: str,
        proof_deadline: u256,
    ) -> str:
        """
        Buyer escrows GEN with the call.
        @gl.public.write.payable is required on Studionet so gl.message.value
        is populated. A plain @gl.public.write leaves value at 0.
        """
        amount = bigint(gl.message.value)
        if amount <= bigint(0):
            raise UserError("Must send GEN as escrow (amount must be > 0)")

        description = str(item_description or "").strip()
        if len(description) == 0:
            raise UserError("Item description cannot be empty")
        if len(description) > MAX_DESCRIPTION:
            raise UserError("Item description is too long")

        category = str(item_category or "").strip()
        if category not in VALID_CATEGORIES:
            raise UserError("Item category must be Sneakers, Watch, Trading Card, Handbag, or Other")

        serial = str(serial_or_identifier or "").strip()
        if len(serial) > MAX_SERIAL:
            raise UserError("Serial or identifier is too long")

        buyer = _to_address(gl.message.sender_address)
        seller_addr = _to_address(seller)
        if _is_zero(seller_addr):
            raise UserError("Seller address cannot be zero")
        if _same_addr(buyer, seller_addr):
            raise UserError("Buyer and seller cannot be the same address")

        tx_id = str(self.tx_counter)
        self.tx_counter = self.tx_counter + bigint(1)

        self.transactions[tx_id] = Transaction(
            buyer=buyer,
            seller=seller_addr,
            item_category=category,
            item_description=description,
            serial_or_identifier=serial,
            amount=amount,
            proof_deadline=u256(proof_deadline),
            proof_urls=[],
            reference_urls=[],
            status="PENDING_PROOF",
            verdict="",
            verdict_reason="",
            confidence=u256(0),
            settled=False,
        )
        return tx_id

    @gl.public.write
    def submit_proof(self, tx_id: str, proof_urls: DynArray[str], reference_urls: DynArray[str]) -> None:
        if tx_id not in self.transactions:
            raise UserError("Transaction does not exist")
        t = self.transactions[tx_id]
        if not _same_addr(gl.message.sender_address, t.seller):
            raise UserError("Only seller can submit proof")
        if t.status not in ("PENDING_PROOF", "SUBMITTED", "DISPUTED"):
            raise UserError("Cannot submit proof in status: " + str(t.status))
        # The deadline gates the first submission. A DISPUTED case must still
        # accept a more detailed resubmission after the original deadline.
        if t.status == "PENDING_PROOF" and _current_unix_timestamp() > t.proof_deadline:
            raise UserError("Proof submission deadline has passed")

        proofs = _clean_urls(proof_urls, "proof", 1, False)
        refs = _clean_urls(reference_urls, "independent authentication reference", 2, True)

        t.proof_urls = proofs
        t.reference_urls = refs
        t.status = "SUBMITTED"
        t.verdict = ""
        t.verdict_reason = ""
        t.confidence = u256(0)
        self.transactions[tx_id] = t

    @gl.public.write
    def claim_expired_refund(self, tx_id: str) -> None:
        """Buyer recovers the full escrow if the seller never submitted proof before the deadline."""
        if tx_id not in self.transactions:
            raise UserError("Transaction does not exist")
        t = self.transactions[tx_id]
        if not _same_addr(gl.message.sender_address, t.buyer):
            raise UserError("Only buyer can claim this refund")
        if t.status != "PENDING_PROOF":
            raise UserError("Can only claim refund if seller never submitted proof")
        if _current_unix_timestamp() <= t.proof_deadline:
            raise UserError("Proof deadline has not passed yet")

        buyer = t.buyer
        amount = t.amount
        t.status = "PAYOUT_FAILED"
        t.settled = False
        t.verdict = ""
        t.verdict_reason = "Seller did not submit proof before proof_deadline"
        self.transactions[tx_id] = t
        try:
            gl.get_contract_at(buyer).emit_transfer(value=u256(amount))
            t.settled = True
            t.status = "EXPIRED_REFUNDED"
        except Exception as err:
            t.settled = False
            t.status = "PAYOUT_FAILED"
            t.verdict_reason = "Expired refund failed: " + str(err)
        self.transactions[tx_id] = t

    @gl.public.write
    def resolve_transaction(self, tx_id: str) -> None:
        if tx_id not in self.transactions:
            raise UserError("Transaction does not exist")
        t = self.transactions[tx_id]
        if t.status != "SUBMITTED":
            raise UserError("Transaction not ready for resolution (status: " + str(t.status) + ")")

        item_category = str(t.item_category)
        item_description = str(t.item_description)
        serial = str(t.serial_or_identifier)
        proof_urls_list = _as_str_list(t.proof_urls)
        reference_urls_list = _as_str_list(t.reference_urls)
        seller = t.seller
        buyer = t.buyer
        amount = t.amount

        def leader_fn() -> dict:
            proof_contents = []
            for url in proof_urls_list:
                proof_contents.append(_fetch_url(url, "proof"))

            reference_contents = []
            for url in reference_urls_list:
                reference_contents.append(_fetch_url(url, "reference"))

            failed_pages = _unreadable_pages(proof_contents + reference_contents)
            if len(failed_pages) > 0:
                return _dispute_unreadable(failed_pages)

            prompt = "You are a neutral authenticity adjudicator for secondhand luxury and collectible goods.\n"
            prompt += "Item category: \"" + item_category + "\"\n"
            prompt += "Item description: \"" + item_description + "\"\n"
            prompt += "Serial/identifier (if any): \"" + serial + "\"\n"
            prompt += "Seller-submitted proof (photos/video descriptions): " + str(proof_contents) + "\n"
            prompt += "Independent authentication sources (official serial lookups, verification or grading services). "
            prompt += "Prioritize these if they contradict the seller proof: " + str(reference_contents) + "\n\n"
            prompt += "Decide strictly one of two outcomes based on objective evidence:\n"
            prompt += "- \"AUTHENTIC\": independent sources confirm this is a genuine item matching the description.\n"
            prompt += "- \"FAKE\": independent sources contradict authenticity, flag it as counterfeit, or evidence is insufficient to confirm genuineness.\n"
            prompt += "If any page text starts with FETCH_FAILED, return {\"verdict\": \"\", \"confidence\": 0, \"reason\": \"page unreadable\"}.\n\n"
            prompt += "Return ONLY raw JSON, no markdown:\n"
            prompt += "{\"verdict\": \"AUTHENTIC\" | \"FAKE\", \"confidence\": <0-100>, \"reason\": \"<short justification>\"}"

            raw = gl.nondet.exec_prompt(prompt)
            return _parse_verdict(raw)

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            try:
                leader_val = _leader_payload(leader_res)
                if not isinstance(leader_val, dict) or "verdict" not in leader_val:
                    return False
                my_res = leader_fn()
            except Exception:
                return False
            return _validators_agree(leader_val, my_res)

        result = gl.vm.run_nondet(leader_fn, validator_fn)
        payload = _leader_payload(result)
        if not isinstance(payload, dict):
            raise UserError("Invalid nondet consensus result")
        parsed = _parse_verdict(payload)

        t.verdict = parsed["verdict"]
        t.confidence = u256(int(parsed["confidence"]))
        t.verdict_reason = parsed["reason"]

        if parsed["consequence"] != PAY_SELLER and parsed["consequence"] != REFUND_BUYER:
            t.status = "DISPUTED"
            t.settled = False
            self.transactions[tx_id] = t
            return

        if parsed["consequence"] == PAY_SELLER:
            recipient = seller
            new_status = "RESOLVED_AUTHENTIC"
        else:
            recipient = buyer
            new_status = "RESOLVED_FAKE"

        t.status = "PAYOUT_FAILED"
        t.settled = False
        self.transactions[tx_id] = t
        try:
            gl.get_contract_at(recipient).emit_transfer(value=u256(amount))
            t.settled = True
            t.status = new_status
        except Exception as err:
            t.settled = False
            t.status = "PAYOUT_FAILED"
            t.verdict_reason = parsed["reason"] + " (Transfer failed: " + str(err) + ")"
        self.transactions[tx_id] = t

    @gl.public.write
    def retry_resolution(self, tx_id: str) -> None:
        """Retry the unpaid transfer. Does not re-run the AI."""
        if tx_id not in self.transactions:
            raise UserError("Transaction does not exist")
        t = self.transactions[tx_id]
        sender = gl.message.sender_address
        if (not _same_addr(sender, t.buyer)) and (not _same_addr(sender, t.seller)):
            raise UserError("Only buyer or seller can retry")
        if t.status != "PAYOUT_FAILED":
            raise UserError("Can only retry PAYOUT_FAILED transactions")

        if t.verdict == "AUTHENTIC":
            recipient = t.seller
            new_status = "RESOLVED_AUTHENTIC"
        elif t.verdict == "FAKE":
            recipient = t.buyer
            new_status = "RESOLVED_FAKE"
        else:
            recipient = t.buyer
            new_status = "EXPIRED_REFUNDED"

        amount = t.amount
        try:
            gl.get_contract_at(recipient).emit_transfer(value=u256(amount))
            t.settled = True
            t.status = new_status
        except Exception as err:
            t.settled = False
            t.status = "PAYOUT_FAILED"
            t.verdict_reason = str(t.verdict_reason) + " (Retry failed again: " + str(err) + ")"
        self.transactions[tx_id] = t

    @gl.public.view
    def get_transaction(self, tx_id: str) -> dict:
        if tx_id not in self.transactions:
            return {}
        return self._tx_dict(tx_id, self.transactions[tx_id])

    @gl.public.view
    def list_transactions(self) -> str:
        results = []
        n = int(self.tx_counter)
        for i in range(n):
            tx_id = str(i)
            if tx_id in self.transactions:
                results.append(self._tx_dict(tx_id, self.transactions[tx_id]))
        return json.dumps(results)

    @gl.public.view
    def get_tx_count(self) -> str:
        return str(self.tx_counter)

    @gl.public.view
    def get_owner(self) -> str:
        return _addr_str(self.owner)
