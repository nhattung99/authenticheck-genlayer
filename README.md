# AuthentiCheck — Escrow xác thực hàng hiệu secondhand trên GenLayer

AuthentiCheck giữ **GEN ký quỹ** trong **một Intelligent Contract**. Buyer tạo giao dịch (loại đồ, mô tả, serial, hạn nộp bằng chứng) và gửi GEN vào contract. Seller nộp ảnh/video cận cảnh cộng **tối thiểu 2 nguồn xác thực độc lập** (khác tên miền). GenLayer AI (`gl.vm.run_nondet`) trả về verdict **nhị phân** `AUTHENTIC` hoặc `FAKE`. Validator so **tuyệt đối** (`==`) cả nhãn lẫn nhánh giải ngân rời rạc (`PAY_SELLER` / `REFUND_BUYER` / `DISPUTE`) — không có dung sai phần trăm, không có phép tính trên số tiền.

> AuthentiCheck chết nếu không có GenLayer: không có smart contract EVM nào đọc hiểu được ảnh/video sản phẩm phi cấu trúc để đối chiếu với nguồn xác thực độc lập, và không có dịch vụ giám định nào đủ rẻ/đủ nhanh cho hàng loạt giao dịch secondhand nhỏ lẻ.

**Mạng:** chỉ **Studionet**. Không chuyển sang testnet hay chain khác.

---

## Contract đã deploy

- **Địa chỉ:** chưa có — deploy tay trên Studio, xác nhận `Result: SUCCESS`, rồi điền vào đây.
- **Explorer:** điền sau khi có địa chỉ.

```env
VITE_CONTRACT_ADDRESS=
```

Khi chưa có địa chỉ, frontend vẫn chạy: banner cảnh báo, form vẫn dùng được, nút ghi bị khóa, trang không trắng.

---

## Kiến trúc: một contract giữ tiền

[`contracts/authenticheck.py`](contracts/authenticheck.py) là contract duy nhất:

1. Buyer gọi `create_transaction` kèm `gl.message.value` (GEN escrow). Decorator là `@gl.public.write.payable` — Studionet chỉ điền `gl.message.value` khi có `.payable`. `@gl.public.write` trần để value = 0 (đã thấy trên các bản deploy Studionet thật, gồm ClaimVerdict).
2. Seller gọi `submit_proof` với ≥ 1 proof URL và ≥ 2 reference URL **khác host**.
3. Ai cũng có thể gọi `resolve_transaction`. `gl.vm.run_nondet` đọc `gl.nondet.web.render` + `gl.nondet.exec_prompt`.
4. `AUTHENTIC` và confidence ≥ 60 → `emit_transfer` toàn bộ cho seller (`RESOLVED_AUTHENTIC`).
5. `FAKE` và confidence ≥ 60 → hoàn toàn bộ cho buyer (`RESOLVED_FAKE`).
6. confidence < 60, JSON hỏng, hoặc nhãn không phải `AUTHENTIC`/`FAKE` → `DISPUTED`. Seller nộp lại bằng chứng (kể cả sau hạn gốc), rồi resolve lại.
7. Seller không nộp bằng chứng trước hạn → buyer gọi `claim_expired_refund` (không chạy AI).
8. Mọi nhánh chuyển tiền thất bại → `PAYOUT_FAILED`. `retry_resolution` trả đúng người nhận, **không** chạy lại AI.

Header Studio (hash đang dùng trên các contract đã deploy trong workspace):

```python
# v0.2.17
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
```

### API đã verify

| Việc | API đúng | Không dùng |
|---|---|---|
| Người gọi | `gl.message.sender_address` | `gl.message.sender` |
| Chuyển GEN | `gl.get_contract_at(addr).emit_transfer(value=u256(amount))` | `gl.transfer(...)` |
| Nhận GEN kèm giao dịch | `@gl.public.write.payable` + `gl.message.value` | `@gl.public.write` trần (Studionet để value = 0) |
| Thời gian | `_current_unix_timestamp()` bọc `gl.message.datetime` | `gl.block.timestamp` |
| Đồng thuận | `gl.vm.run_nondet`, so `==` verdict và nhánh `PAY_SELLER` / `REFUND_BUYER` / `DISPUTE` | dung sai % trên tiền |

Hai validator cùng nói `AUTHENTIC` nhưng một bên confidence 91 (trả seller) và một bên 40 (tranh chấp) **không** được coi là đồng thuận. So sánh là chuỗi rời rạc, không phải khoảng sai số.

---

## Luồng resolve

```
PENDING_PROOF ──submit_proof──► SUBMITTED ──resolve_transaction──►
        │                              │
        │ quá hạn, chưa nộp            ├─ confidence < 60 hoặc JSON hỏng ──► DISPUTED ── nộp lại ──► SUBMITTED
        ▼                              ├─ AUTHENTIC + transfer OK ──► RESOLVED_AUTHENTIC
EXPIRED_REFUNDED                       ├─ FAKE + transfer OK ──► RESOLVED_FAKE
                                       └─ transfer lỗi ──► PAYOUT_FAILED ──retry_resolution──► đúng người nhận
```

Web fetch lỗi thì giao dịch revert, trạng thái giữ `SUBMITTED`.

---

## Bảng đối chiếu xử lý tiền (không float)

Mọi số tiền là **wei nguyên** (`1 GEN = 10^18`). Frontend chỉ dùng `parseGenToWei` / `formatWeiToGen` (cắt chuỗi + `BigInt`).

| # | Chỗ xử lý | File | Cách xử lý | Đơn vị |
|---|---|---|---|---|
| 1 | Buyer gửi escrow lúc tạo giao dịch | `create_transaction` ← `gl.message.value` | `bigint(gl.message.value)` | wei |
| 2 | Lưu số escrow | `Transaction.amount` | `bigint` | wei |
| 3 | `AUTHENTIC` trả seller | `resolve_transaction` | `emit_transfer(value=u256(amount))` toàn bộ | wei |
| 4 | `FAKE` hoàn buyer | `resolve_transaction` | cùng `emit_transfer`, toàn bộ, không chia % | wei |
| 5 | Hết hạn, seller không nộp | `claim_expired_refund` | hoàn toàn bộ cho buyer | wei |
| 6 | Thử lại sau `PAYOUT_FAILED` | `retry_resolution` | dùng lại `amount` đã lưu, không chạy AI | wei |
| 7 | Đọc ra ngoài | `get_transaction` / `list_transactions` | `amount` là **chuỗi chữ số**, không phải số thực | wei string |
| 8 | Ô nhập GEN | `frontend/src/money.js` `sanitizeGenInput` | chỉ chữ số và một dấu `.`, tối đa 18 số lẻ | chuỗi GEN |
| 9 | GEN → wei trước khi ký | `parseGenToWei` | ghép phần nguyên + 18 chữ số lẻ → `BigInt` | wei |
| 10 | wei → GEN để hiển thị | `formatWeiToGen` | chia/dư `10^18` bằng `BigInt` | chuỗi GEN |
| 11 | `value` gửi qua MetaMask | `toValueBigInt` trong `genlayerClient.js` | `bigint` hoặc chuỗi hex → `BigInt`; **number JS thành 0n** | wei |
| 12 | Field tiền đọc từ contract | `toWeiString` | chỉ nhận chuỗi chữ số hoặc `bigint`; `number` → `"0"` | wei string |
| 13 | Chặn float | `scripts/check-no-float-money.js`, gắn `prebuild` | fail nếu `parseFloat` / `Math.round` / `Math.floor` / `Math.ceil` đứng gần `amount\|escrow\|payout\|balance\|wei\|gen` | — |

Không có `parseFloat`, `Math.round`, `Math.floor`, `Math.ceil` trên đường đi của tiền.

---

## Test

```bash
pip install -r requirements.txt
pytest tests/test_authenticheck.py -v
```

**17 passed** (genlayer-test 0.29.2):

```
tests/test_authenticheck.py .................                               [100%]
17 passed
```

Trước mỗi giao dịch nondet, test gọi `_install_nondet_mocks` (`sim_installMocks` khi có, kèm `mock_web` / `mock_llm`).

Các case bắt buộc:

1. Happy path `AUTHENTIC` → trả seller
2. Happy path `FAKE` → hoàn buyer
3. Seller không nộp đúng hạn → buyer refund
4. Nộp bằng chứng trễ hạn bị chặn
5. Confidence < 60 → `DISPUTED` → nộp lại → resolve lại
6. Web fetch lỗi (revert, giữ `SUBMITTED`) và JSON hỏng (`DISPUTED`, không trả tiền)
7. Thiếu proof/reference, trùng host, mô tả rỗng, buyer = seller, amount = 0
8. Double-submit / double-resolve
9. `emit_transfer` ném lỗi ở cả 3 nhánh: AUTHENTIC, FAKE, hết hạn → `PAYOUT_FAILED` → `retry_resolution` trả đúng người

Tiền trên frontend:

```bash
node frontend/src/__tests__/unit_conversion.test.js
node scripts/check-no-float-money.js
```

---

## Deploy contract trên Studionet

1. Mở [GenLayer Studio → Run & Debug](https://studio.genlayer.com/run-debug).
2. Dán `contracts/authenticheck.py` (giữ 2 dòng header).
3. Deploy. Mở giao dịch và xác nhận **`Result: SUCCESS`** (FINALIZED một mình chưa đủ).
4. Điền địa chỉ vào `frontend/.env` (`VITE_CONTRACT_ADDRESS`) và biến môi trường Vercel.

Chưa tự deploy trong repo này.

---

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Mở `http://localhost:3000`. Ở trên Studionet (`genlayer-js/chains` → `studionet`). Kết nối MetaMask và nạp GEN từ Studio → **Accounts**.

`npm run build` chạy `prebuild` → `check-no-float-money`, rồi Vite.

Kết quả bản này: unit conversion **pass**, `check-no-float-money: PASS (6 files scanned)`, `vite build` thành công. Vite cảnh báo bundle JS > 500 kB vì `genlayer-js` được đóng gói cùng app — build không fail.

**Giới hạn đã biết:** nếu AI trả `DISPUTED` rồi seller biến mất, tiền vẫn nằm trong escrow. Không có hạn thứ hai để buyer rút ở trạng thái đó — đúng với rule “chỉ hoàn khi seller chưa từng nộp bằng chứng”. Nộp lần đầu sau hạn vẫn bị chặn; nộp lại khi đang `DISPUTED` vẫn được, để seller bổ sung ảnh rõ hơn.

Luồng trên UI: buyer chọn category (chip gợi ý nguồn xác thực), mô tả, serial, chip GEN, hạn 24 giờ / 3 ngày / 7 ngày → chia sẻ cho seller → seller dán link bằng chứng + ≥ 2 nguồn → **Yêu cầu AI xác thực** (có trạng thái đang tải) → hiện verdict, lý do, confidence, trạng thái giải ngân. `DISPUTED` có form nộp lại. `PAYOUT_FAILED` có nút **Thử lại**.
