# AuthentiCheck — Secondhand authenticity escrow on GenLayer

AuthentiCheck holds a **GEN escrow** in **one Intelligent Contract**. A buyer opens a transaction (category, description, serial, proof deadline) and sends GEN into the contract. The seller submits close-up photos or video plus **at least two independent authentication sources** on different hosts. GenLayer AI (`gl.vm.run_nondet`) returns a **binary** verdict: `AUTHENTIC` or `FAKE`. Validators compare the label and the discrete payout branch (`PAY_SELLER` / `REFUND_BUYER` / `DISPUTE`) with exact equality (`==`). There is no percentage tolerance and no arithmetic on the escrow amount.

> AuthentiCheck dies without GenLayer: no EVM contract can read unstructured product photos and compare them with independent authentication pages, and no appraisal service is cheap or fast enough for small secondhand trades.

**Network:** **Studionet** only.

---

## Live App

**URL:** [https://authenticheck-genlayer.vercel.app](https://authenticheck-genlayer.vercel.app)

Free to use. You only pay GenLayer network gas when you sign a transaction. There is no platform fee.

---

## Deployed Contract

- **Address:** `0x93B67e70466ddB4bCF5d6ebD8BFfedC13371733a`
- **Explorer:** [https://explorer-studio.genlayer.com/address/0x93B67e70466ddB4bCF5d6ebD8BFfedC13371733a](https://explorer-studio.genlayer.com/address/0x93B67e70466ddB4bCF5d6ebD8BFfedC13371733a)

```env
VITE_CONTRACT_ADDRESS=0x93B67e70466ddB4bCF5d6ebD8BFfedC13371733a
```

If the address is missing, the frontend still runs: a warning banner is shown, the form works, write buttons stay locked, and the page does not go blank.

---

## Architecture: one contract holds the funds

[`contracts/authenticheck.py`](contracts/authenticheck.py) is the only contract:

1. The buyer calls `create_transaction` with `gl.message.value` (the GEN escrow). The decorator is `@gl.public.write.payable` so Studionet fills `gl.message.value`. A plain `@gl.public.write` leaves the value at 0.
2. The seller calls `submit_proof` with at least 1 proof URL and at least 2 reference URLs on **different hosts**.
3. Anyone can call `resolve_transaction`. `gl.vm.run_nondet` reads `gl.nondet.web.render` and `gl.nondet.exec_prompt`.
4. `AUTHENTIC` and confidence ≥ 60 pays the full amount to the seller (`RESOLVED_AUTHENTIC`).
5. `FAKE` and confidence ≥ 60 refunds the full amount to the buyer (`RESOLVED_FAKE`).
6. Confidence below 60, broken JSON, or a label other than `AUTHENTIC` / `FAKE` becomes `DISPUTED`. The seller can submit clearer proof, including after the original deadline, then resolve again.
7. If the seller never submits proof before the deadline, the buyer calls `claim_expired_refund`. That path does not run the AI.
8. Any failed transfer becomes `PAYOUT_FAILED`. `retry_resolution` pays the correct party and does **not** re-run the AI.

Studio header:

```python
# v0.2.17
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
```

### Verified APIs

| Task | Correct API | Do not use |
|---|---|---|
| Caller | `gl.message.sender_address` | `gl.message.sender` |
| Send GEN | `gl.get_contract_at(addr).emit_transfer(value=u256(amount))` | `gl.transfer(...)` |
| Receive GEN with the call | `@gl.public.write.payable` + `gl.message.value` | plain `@gl.public.write` (Studionet leaves value at 0) |
| Time | `_current_unix_timestamp()` wrapping `gl.message.datetime` | `gl.block.timestamp` |
| Consensus | `gl.vm.run_nondet`, exact `==` on the verdict and on `PAY_SELLER` / `REFUND_BUYER` / `DISPUTE` | a percentage tolerance on the escrow |

Two validators that both say `AUTHENTIC`, while one confidence is 91 (pay the seller) and the other is 40 (dispute), do **not** agree. The comparison is a discrete string, not an error band.

---

## Resolve flow

```
PENDING_PROOF ──submit_proof──► SUBMITTED ──resolve_transaction──►
        │                              │
        │ deadline passed, no proof    ├─ confidence < 60 or broken JSON ──► DISPUTED ── resubmit ──► SUBMITTED
        ▼                              ├─ AUTHENTIC + transfer OK ──► RESOLVED_AUTHENTIC
EXPIRED_REFUNDED                       ├─ FAKE + transfer OK ──► RESOLVED_FAKE
                                       └─ transfer failed ──► PAYOUT_FAILED ──retry_resolution──► correct recipient
```

If a page is empty, blocked, or shows a login wall, resolve does not revert. The status becomes `DISPUTED`, nobody is paid, and the seller can replace the links. The seller can also replace links while the status is still `SUBMITTED`.

---

## Money handling (no floats)

Every amount is an **integer in wei** (`1 GEN = 10^18`). The frontend uses only `parseGenToWei` / `formatWeiToGen` (string parsing plus `BigInt`).

| # | Place | File | How it is handled | Unit |
|---|---|---|---|---|
| 1 | Buyer sends the escrow when creating a transaction | `create_transaction` ← `gl.message.value` | `bigint(gl.message.value)` | wei |
| 2 | Stored escrow | `Transaction.amount` | `bigint` | wei |
| 3 | `AUTHENTIC` pays the seller | `resolve_transaction` | `emit_transfer(value=u256(amount))` for the full amount | wei |
| 4 | `FAKE` refunds the buyer | `resolve_transaction` | the same `emit_transfer`, full amount, no percentage split | wei |
| 5 | Deadline passed with no proof | `claim_expired_refund` | full refund to the buyer | wei |
| 6 | Retry after `PAYOUT_FAILED` | `retry_resolution` | reuse the stored `amount`; do not run the AI | wei |
| 7 | Values read by the frontend | `get_transaction` / `list_transactions` | `amount` is a **digit string**, not a float | wei string |
| 8 | GEN input | `frontend/src/money.js` `sanitizeGenInput` | digits and one `.`, at most 18 fraction digits | GEN string |
| 9 | GEN → wei before signing | `parseGenToWei` | concatenate the integer part and an 18-digit fraction into a `BigInt` | wei |
| 10 | wei → GEN for display | `formatWeiToGen` | divide and remainder by `10^18` with `BigInt` | GEN string |
| 11 | `value` sent through MetaMask | `toValueBigInt` in `genlayerClient.js` | `bigint` or hex string → `BigInt`; a JS number becomes `0n` | wei |
| 12 | Money fields read from the contract | `toWeiString` | digit strings or `bigint` only; a JS number becomes `"0"` | wei string |
| 13 | Float gate | `scripts/check-no-float-money.js`, wired to `prebuild` | fail if `parseFloat` / `Math.round` / `Math.floor` / `Math.ceil` appear near `amount\|escrow\|payout\|balance\|wei\|gen` | — |

There is no `parseFloat`, `Math.round`, `Math.floor`, or `Math.ceil` on the money path.

---

## Tests

```bash
pip install -r requirements.txt
pytest tests/test_authenticheck.py -v
```

**17 passed** (genlayer-test 0.29.2).

Before every nondet transaction, tests call `_install_nondet_mocks` (`sim_installMocks` when present, plus `mock_web` / `mock_llm`).

Required cases:

1. Happy path `AUTHENTIC` pays the seller
2. Happy path `FAKE` refunds the buyer
3. Seller never submits before the deadline, so the buyer is refunded
4. Late proof is rejected
5. Confidence below 60 becomes `DISPUTED`, then a resubmit, then a second resolve
6. An unreadable page (empty, blocked, or login wall) becomes `DISPUTED` and pays nobody; the seller can replace the links and resolve again. Broken JSON also becomes `DISPUTED`.
7. Missing proof or references, same host, empty description, buyer equals seller, amount 0
8. Double submit and double resolve are rejected
9. `emit_transfer` throws on AUTHENTIC, FAKE, and the expired refund, then `retry_resolution` pays the correct party

Frontend money checks:

```bash
node frontend/src/__tests__/unit_conversion.test.js
node scripts/check-no-float-money.js
```

---

## Deploy the contract on Studionet

1. Open [GenLayer Studio → Run & Debug](https://studio.genlayer.com/run-debug).
2. Paste `contracts/authenticheck.py` and keep the two header lines.
3. Deploy and confirm **`Result: SUCCESS`**.
4. Set `VITE_CONTRACT_ADDRESS` in `frontend/.env` and in the Vercel production environment.

Current deployment: `0x93B67e70466ddB4bCF5d6ebD8BFfedC13371733a`.

---

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. Stay on Studionet (`genlayer-js/chains` → `studionet`). Connect MetaMask and fund GEN from Studio → **Accounts**.

`npm run build` runs `prebuild` → `check-no-float-money`, then Vite.

The buyer picks a category (chips suggest independent sources), a description, a serial, a GEN chip, and a deadline of 24 hours, 3 days, or 7 days, then shares the transaction with the seller. The seller pastes proof links plus at least two sources and clicks **Request AI verification**. The result shows the verdict, reason, confidence, and payout status. `DISPUTED` offers a resubmit form. `PAYOUT_FAILED` offers **Retry payout**.

**Known limit:** if the AI returns `DISPUTED` and the seller disappears, the funds stay in escrow. There is no second deadline that refunds the buyer from that state. The first proof submission is still blocked after the deadline. A resubmit while the status is `DISPUTED` remains allowed so the seller can add clearer evidence.
