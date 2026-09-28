import json
import pytest

CONTRACT_PATH = "contracts/authenticheck.py"
FAR_FUTURE = 4102444800  # 2100-01-01 UTC
PAST_DEADLINE = 1

PROOF_URL = "https://photos.example.com/sole-stitch.jpg"
PROOF_URL_2 = "https://photos.example.com/serial-macro.jpg"
REF_STOCKX = "https://stockx.com/verify/serial-abc"
REF_GOAT = "https://goat.com/authenticate/serial-abc"
REF_PSA = "https://psacard.com/cert/12345678"

PROOF_BODY = "Macro photo of stitching, hologram, and serial ABC-123 matching the listing."
STOCKX_BODY = "StockX verification: serial ABC-123 is genuine, size matches the listing."
GOAT_BODY = "GOAT authentication: item passed independent inspection, serial ABC-123."
PSA_BODY = "PSA cert 12345678: grade authentic, cert number matches the card."


def _get_vm(direct_vm=None):
    if direct_vm is not None:
        return direct_vm
    try:
        from gltest.direct.loader import _get_active_vm
        return _get_active_vm()
    except Exception:
        return None


def _set_value(vm, amount):
    if vm is None:
        return
    if hasattr(vm, "value"):
        try:
            vm.value = amount
        except Exception:
            pass
    if hasattr(vm, "_value"):
        vm._value = amount
    if hasattr(vm, "_refresh_gl_message"):
        vm._refresh_gl_message()


def _credit_contract(vm, contract, amount):
    if vm is None or amount <= 0:
        return
    if hasattr(vm, "_balances") and hasattr(contract, "address"):
        vm._balances[contract.address] = vm._balances.get(contract.address, 0) + amount


def _as(vm, sender):
    if hasattr(vm, "sender"):
        vm.sender = sender
    return vm.prank(sender) if hasattr(vm, "prank") else _NullCtx()


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _create(contract, vm, buyer, seller, amount, deadline=FAR_FUTURE, category="Sneakers",
            description="Nike Dunk Low Panda, size 42", serial="ABC-123"):
    if hasattr(vm, "sender"):
        vm.sender = buyer
    _set_value(vm, amount)
    _credit_contract(vm, contract, amount)
    with _as(vm, buyer):
        _set_value(vm, amount)
        tx_id = contract.create_transaction(seller, category, description, serial, deadline)
    _set_value(vm, 0)
    return str(tx_id)


def _install_nondet_mocks(vm, web_map, llm_text):
    if hasattr(vm, "clear_mocks"):
        try:
            vm.clear_mocks()
        except Exception:
            pass

    for url, body in web_map.items():
        if hasattr(vm, "mock_web"):
            vm.mock_web(url, body)
    if hasattr(vm, "mock_llm"):
        vm.mock_llm(".*", llm_text)

    payload = {
        "nondet_web_request": {
            url: {"method": "GET", "status": 200, "body": body} for url, body in web_map.items()
        },
        "nondet_exec_prompt": {"*": llm_text},
    }
    for name in ("sim_installMocks", "sim_install_mocks"):
        fn = getattr(vm, name, None)
        if callable(fn):
            fn(payload)
            return
    try:
        from gltest import sim_installMocks
        sim_installMocks(payload)
    except Exception:
        pass


def _standard_web():
    return {PROOF_URL: PROOF_BODY, REF_STOCKX: STOCKX_BODY, REF_GOAT: GOAT_BODY}


def _verdict_json(verdict, confidence, reason):
    return json.dumps({"verdict": verdict, "confidence": confidence, "reason": reason})


def _get_tx(contract, tx_id):
    row = contract.get_transaction(tx_id)
    if isinstance(row, str):
        row = json.loads(row)
    return row


def _list_txs(contract):
    raw = contract.list_transactions()
    if isinstance(raw, str):
        return json.loads(raw)
    return list(raw)


def _submit(contract, vm, seller, tx_id, proofs=None, refs=None):
    with _as(vm, seller):
        contract.submit_proof(
            tx_id,
            proofs if proofs is not None else [PROOF_URL],
            refs if refs is not None else [REF_STOCKX, REF_GOAT],
        )


def _resolve(contract, vm, caller, tx_id, verdict, confidence, reason, web=None):
    _install_nondet_mocks(
        vm,
        web if web is not None else _standard_web(),
        _verdict_json(verdict, confidence, reason),
    )
    with _as(vm, caller):
        contract.resolve_transaction(tx_id)


def _force_transfer_fail(monkeypatch):
    import gltest.direct.loader

    def failing_emit_transfer(self, value=None, **kwargs):
        raise Exception("Simulated native transfer execution failure")

    monkeypatch.setattr(gltest.direct.loader._EOAProxy, "emit_transfer", failing_emit_transfer)


def test_happy_path_authentic_pays_seller(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 1000)
    assert tx_id == "0"
    row = _get_tx(contract, tx_id)
    assert row["status"] == "PENDING_PROOF"
    assert row["amount"] == "1000"
    assert row["settled"] is False
    assert row["item_category"] == "Sneakers"

    _submit(contract, vm, direct_bob, tx_id)
    assert _get_tx(contract, tx_id)["status"] == "SUBMITTED"

    _resolve(contract, vm, direct_alice, tx_id, "AUTHENTIC", 92, "Serial ABC-123 confirmed on StockX and GOAT")
    row = _get_tx(contract, tx_id)
    assert row["status"] == "RESOLVED_AUTHENTIC"
    assert row["verdict"] == "AUTHENTIC"
    assert row["confidence"] == 92
    assert row["settled"] is True
    assert "StockX" in row["verdict_reason"]


def test_happy_path_fake_refunds_buyer(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 800, category="Trading Card",
                    description="Charizard 1st edition", serial="PSA-12345678")
    _submit(contract, vm, direct_bob, tx_id, [PROOF_URL], [REF_PSA, REF_GOAT])
    web = {PROOF_URL: PROOF_BODY, REF_PSA: "PSA cert lookup: no record for this cert.", REF_GOAT: GOAT_BODY}
    _resolve(contract, vm, direct_bob, tx_id, "FAKE", 88, "PSA has no matching cert", web)
    row = _get_tx(contract, tx_id)
    assert row["status"] == "RESOLVED_FAKE"
    assert row["verdict"] == "FAKE"
    assert row["settled"] is True
    assert row["confidence"] == 88


def test_expired_refund_when_seller_never_submits(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 250, deadline=PAST_DEADLINE)
    with _as(vm, direct_alice):
        contract.claim_expired_refund(tx_id)
    row = _get_tx(contract, tx_id)
    assert row["status"] == "EXPIRED_REFUNDED"
    assert row["settled"] is True
    assert row["verdict"] == ""


def test_late_proof_is_blocked(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 100, deadline=PAST_DEADLINE)
    with _as(vm, direct_bob):
        with pytest.raises(Exception):
            contract.submit_proof(tx_id, [PROOF_URL], [REF_STOCKX, REF_GOAT])
    assert _get_tx(contract, tx_id)["status"] == "PENDING_PROOF"


def test_refund_blocked_before_deadline(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 40)
    with _as(vm, direct_alice):
        with pytest.raises(Exception):
            contract.claim_expired_refund(tx_id)
    assert _get_tx(contract, tx_id)["status"] == "PENDING_PROOF"


def test_low_confidence_disputed_then_resubmit_then_resolve(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 500, category="Handbag",
                    description="Chanel classic flap, serial in the pocket")
    _submit(contract, vm, direct_bob, tx_id)
    _resolve(contract, vm, direct_alice, tx_id, "AUTHENTIC", 45, "Photos are too blurry to match the serial")
    row = _get_tx(contract, tx_id)
    assert row["status"] == "DISPUTED"
    assert row["settled"] is False
    assert row["confidence"] == 45
    assert row["verdict"] == "AUTHENTIC"

    _submit(contract, vm, direct_bob, tx_id, [PROOF_URL, PROOF_URL_2], [REF_STOCKX, REF_GOAT])
    row = _get_tx(contract, tx_id)
    assert row["status"] == "SUBMITTED"
    assert PROOF_URL_2 in row["proof_urls"]

    web = dict(_standard_web())
    web[PROOF_URL_2] = "Sharp macro of the serial plaque ABC-123."
    _resolve(contract, vm, direct_alice, tx_id, "AUTHENTIC", 91, "Serial plaque matches both lookups", web)
    row = _get_tx(contract, tx_id)
    assert row["status"] == "RESOLVED_AUTHENTIC"
    assert row["settled"] is True
    assert row["confidence"] == 91


def test_web_fetch_failure_reverts(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 400)
    _submit(contract, vm, direct_bob, tx_id)
    if hasattr(vm, "clear_mocks"):
        vm.clear_mocks()
    if hasattr(vm, "mock_llm"):
        vm.mock_llm(".*", _verdict_json("AUTHENTIC", 90, "should not run"))
    with _as(vm, direct_alice):
        with pytest.raises(Exception):
            contract.resolve_transaction(tx_id)
    assert _get_tx(contract, tx_id)["status"] == "SUBMITTED"
    assert _get_tx(contract, tx_id)["settled"] is False


def test_broken_json_goes_disputed(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 300, category="Watch",
                    description="Rolex Submariner, serial on the rehaut")
    _submit(contract, vm, direct_bob, tx_id)
    _install_nondet_mocks(vm, _standard_web(), "this is not json {{{")
    with _as(vm, direct_alice):
        contract.resolve_transaction(tx_id)
    row = _get_tx(contract, tx_id)
    assert row["status"] == "DISPUTED"
    assert row["settled"] is False
    assert row["verdict"] == ""
    assert int(row["confidence"]) == 0


def test_missing_proof_reference_and_create_guards(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)

    _set_value(vm, 100)
    _credit_contract(vm, contract, 100)
    with _as(vm, direct_alice):
        _set_value(vm, 100)
        with pytest.raises(Exception):
            contract.create_transaction(direct_bob, "Sneakers", "   ", "ABC", FAR_FUTURE)
    _set_value(vm, 0)

    with _as(vm, direct_alice):
        _set_value(vm, 0)
        with pytest.raises(Exception):
            contract.create_transaction(direct_bob, "Sneakers", "A real shoe", "ABC", FAR_FUTURE)

    _set_value(vm, 50)
    with _as(vm, direct_alice):
        _set_value(vm, 50)
        with pytest.raises(Exception):
            contract.create_transaction(direct_alice, "Sneakers", "Same person", "ABC", FAR_FUTURE)
    _set_value(vm, 0)

    _set_value(vm, 50)
    with _as(vm, direct_alice):
        _set_value(vm, 50)
        with pytest.raises(Exception):
            contract.create_transaction(direct_bob, "Spaceship", "Not a category", "", FAR_FUTURE)
    _set_value(vm, 0)

    tx_id = _create(contract, vm, direct_alice, direct_bob, 70)
    with _as(vm, direct_bob):
        with pytest.raises(Exception):
            contract.submit_proof(tx_id, [], [REF_STOCKX, REF_GOAT])
        with pytest.raises(Exception):
            contract.submit_proof(tx_id, [PROOF_URL], [REF_STOCKX])
        with pytest.raises(Exception):
            contract.submit_proof(
                tx_id,
                [PROOF_URL],
                ["https://stockx.com/a", "https://www.stockx.com/b"],
            )
        with pytest.raises(Exception):
            contract.submit_proof(tx_id, ["not-a-url"], [REF_STOCKX, REF_GOAT])
    with _as(vm, direct_alice):
        with pytest.raises(Exception):
            contract.submit_proof(tx_id, [PROOF_URL], [REF_STOCKX, REF_GOAT])
    assert _get_tx(contract, tx_id)["status"] == "PENDING_PROOF"


def test_double_submit_and_double_resolve_blocked(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 900)
    _submit(contract, vm, direct_bob, tx_id)
    with _as(vm, direct_bob):
        with pytest.raises(Exception):
            contract.submit_proof(tx_id, [PROOF_URL], [REF_STOCKX, REF_GOAT])

    _resolve(contract, vm, direct_alice, tx_id, "AUTHENTIC", 95, "Both lookups match")
    assert _get_tx(contract, tx_id)["status"] == "RESOLVED_AUTHENTIC"
    with _as(vm, direct_alice):
        with pytest.raises(Exception):
            contract.resolve_transaction(tx_id)
    with _as(vm, direct_bob):
        with pytest.raises(Exception):
            contract.retry_resolution(tx_id)


def test_transfer_fail_authentic_then_retry_pays_seller(
    direct_vm, direct_deploy, direct_alice, direct_bob, monkeypatch
):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 1200)
    _submit(contract, vm, direct_bob, tx_id)
    _force_transfer_fail(monkeypatch)
    _resolve(contract, vm, direct_alice, tx_id, "AUTHENTIC", 91, "Hologram matches StockX")
    row = _get_tx(contract, tx_id)
    assert row["status"] == "PAYOUT_FAILED"
    assert row["verdict"] == "AUTHENTIC"
    assert row["settled"] is False

    monkeypatch.undo()
    with _as(vm, direct_bob):
        contract.retry_resolution(tx_id)
    row = _get_tx(contract, tx_id)
    assert row["status"] == "RESOLVED_AUTHENTIC"
    assert row["settled"] is True


def test_transfer_fail_fake_then_retry_refunds_buyer(
    direct_vm, direct_deploy, direct_alice, direct_bob, monkeypatch
):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 640, category="Watch",
                    description="Omega Speedmaster")
    _submit(contract, vm, direct_bob, tx_id)
    _force_transfer_fail(monkeypatch)
    _resolve(contract, vm, direct_alice, tx_id, "FAKE", 93, "Serial is not in the brand registry")
    row = _get_tx(contract, tx_id)
    assert row["status"] == "PAYOUT_FAILED"
    assert row["verdict"] == "FAKE"
    assert row["settled"] is False

    monkeypatch.undo()
    with _as(vm, direct_alice):
        contract.retry_resolution(tx_id)
    row = _get_tx(contract, tx_id)
    assert row["status"] == "RESOLVED_FAKE"
    assert row["settled"] is True


def test_transfer_fail_expired_refund_then_retry_pays_buyer(
    direct_vm, direct_deploy, direct_alice, direct_bob, monkeypatch
):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 80, deadline=PAST_DEADLINE)
    _force_transfer_fail(monkeypatch)
    with _as(vm, direct_alice):
        contract.claim_expired_refund(tx_id)
    row = _get_tx(contract, tx_id)
    assert row["status"] == "PAYOUT_FAILED"
    assert row["settled"] is False
    assert row["verdict"] == ""

    monkeypatch.undo()
    with _as(vm, direct_bob):
        contract.retry_resolution(tx_id)
    row = _get_tx(contract, tx_id)
    assert row["status"] == "EXPIRED_REFUNDED"
    assert row["settled"] is True


def test_stranger_cannot_refund_or_retry(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 55, deadline=PAST_DEADLINE)
    with _as(vm, direct_charlie):
        with pytest.raises(Exception):
            contract.claim_expired_refund(tx_id)
    with _as(vm, direct_bob):
        with pytest.raises(Exception):
            contract.claim_expired_refund(tx_id)


def test_wei_roundtrip_and_list(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    ten_gen_wei = 10 * 10**18
    tx_id = _create(
        contract, vm, direct_alice, direct_bob, ten_gen_wei,
        category="Other", description="Vintage camera body", serial="",
    )
    row = _get_tx(contract, tx_id)
    assert row["amount"] == str(ten_gen_wei)
    assert row["serial_or_identifier"] == ""
    listed = _list_txs(contract)
    assert len(listed) == 1
    assert listed[0]["tx_id"] == tx_id
    assert listed[0]["amount"] == str(ten_gen_wei)
    assert contract.get_tx_count() == "1"


def test_unknown_verdict_does_not_pay(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT_PATH)
    vm = _get_vm(direct_vm)
    tx_id = _create(contract, vm, direct_alice, direct_bob, 210)
    _submit(contract, vm, direct_bob, tx_id)
    _install_nondet_mocks(vm, _standard_web(), _verdict_json("MAYBE", 99, "not a binary label"))
    with _as(vm, direct_alice):
        contract.resolve_transaction(tx_id)
    row = _get_tx(contract, tx_id)
    assert row["status"] == "DISPUTED"
    assert row["settled"] is False
    assert row["verdict"] == ""


def test_confidence_threshold_is_absolute():
    """Mirror of the contract rule: 59 disputes, 60 settles. No percentage of escrow."""
    def consequence(verdict, confidence):
        if verdict == "AUTHENTIC" and int(confidence) >= 60:
            return "PAY_SELLER"
        if verdict == "FAKE" and int(confidence) >= 60:
            return "REFUND_BUYER"
        return "DISPUTE"

    def agree(leader, mine):
        return leader["verdict"] == mine["verdict"] and consequence(
            leader["verdict"], leader["confidence"]
        ) == consequence(mine["verdict"], mine["confidence"])

    leader = {"verdict": "AUTHENTIC", "confidence": 91}
    validator = {"verdict": "AUTHENTIC", "confidence": 40}
    assert consequence("AUTHENTIC", 59) == "DISPUTE"
    assert consequence("AUTHENTIC", 60) == "PAY_SELLER"
    assert consequence("FAKE", 60) == "REFUND_BUYER"
    assert agree(leader, leader) is True
    assert agree(leader, validator) is False
    assert agree(leader, {"verdict": "FAKE", "confidence": 91}) is False
