# Tổng hợp lỗi A0–A3 và hướng dẫn đọc native tool error

## Phạm vi và nguyên tắc đọc

Báo cáo này chỉ review các episode Retail-58 **đã hoàn tất và đã được
serialize**. Một episode có `reward=0` không tự động là lỗi harness: đó là kết
luận cuối của evaluator benchmark. Các episode A2/A3 bị provider exhaustion và
không tạo `SimulationRun` được ghi riêng là lỗi vận hành chưa giải quyết; chúng
không bị đổi thành một reward-zero giả.

| Stage | Episode hoàn tất | `reward=0` | Operationally unresolved (ngoài mẫu số reward) |
|---|---:|---:|---:|
| A0 native | 232 | 46 | Không áp dụng: native run không có provider sidecar có kiểu lỗi |
| A1 validation | 232 | 40 | 0 được ghi nhận trong root completed này |
| A2 retry | 227 | 38 | 5 |
| A3 recovery/quarantine | 221 | 44 | 11 |

Các dòng review chi tiết nằm trong file
`analysis/failure_annotations_reviewed_v1.csv` của từng stage. Mỗi dòng giữ
simulation ID, task, trial, reference evidence, tóm tắt action kỳ vọng/thực tế
và mức độ tin cậy của attribution.

## Cách quyết định nguyên nhân của một episode `reward=0`

Reviewer đọc theo thứ tự:

1. `evaluation_criteria` frozen của task và reward basis.
2. Toàn bộ hội thoại user/assistant trong `results.json`.
3. Tool call của agent và `ToolMessage` tương ứng, gồm cả arguments.
4. Với A1 trở lên: trace và lifecycle theo episode; với A2/A3: thêm provider
   sidecar.
5. So sánh action thực tế với yêu cầu của user nhìn thấy được, thay vì chỉ so
   với một giá trị ngầm mà evaluator cố định.

Nhãn **không** được quyết định chỉ bằng reward, chỉ bằng một tool error, hay chỉ
bằng một trace event.

| Nhãn | Điều kiện gán nhãn | Không có nghĩa là |
|---|---|---|
| `MODEL_REASONING_FAILURE` | Agent gọi tool hợp lệ về cấu trúc nhưng chọn sai item/order/payment/action, bỏ sót action rõ ràng, hoặc transfer khi vẫn có thể thực hiện action evaluator yêu cầu. | Không phải lỗi parser, dispatcher, tool runtime hay provider. |
| `USER_SIMULATOR_DEVIATION` | User simulator đã đổi yêu cầu, không cung cấp, hoặc mâu thuẫn thông tin/goal mà evaluator frozen yêu cầu. | Không chứng minh agent chắc chắn đúng; chỉ nói không thể quy lỗi đó công bằng cho execution harness. |
| `BENCHMARK_AMBIGUITY` | Hội thoại cho phép một lựa chọn hợp lệ theo policy, nhưng evaluator chỉ chấp nhận một lựa chọn cố định khác mà hội thoại không xác định đủ. | Không phải lý do để harness dùng ground truth evaluator ghi đè user/agent. |
| `INSUFFICIENT_EVIDENCE` | Có nhiều nguyên nhân đều hợp lý, nhưng trajectory đã lưu không đủ để quy về một bên. | Đây là quyết định “không đoán”, không phải execution error code. |

## Taxonomy của các completed reward-zero episode

| Stage | Model reasoning | User simulator deviation | Benchmark ambiguity | Insufficient evidence |
|---|---:|---:|---:|---:|
| A0 | 24 | 13 | 8 | 1 |
| A1 | 27 | 11 | 1 | 1 |
| A2 | 24 | 9 | 4 | 1 |
| A3 | 25 | 14 | 5 | 0 |

### A0: task ID và trial lỗi

| Nhãn chính | Task ID và trial lỗi |
|---|---|
| `MODEL_REASONING_FAILURE` | 18 (1,2,3); 20 (0,1); 21 (3); 37 (2); 79 (0,1,2); 91 (0,1,2,3); 93 (0,2); 103 (1,2,3); 104 (3); 107 (0,1,2,3) |
| `USER_SIMULATOR_DEVIATION` | 22 (0,1,2,3); 28 (3); 62 (0,1,3); 71 (0); 76 (3); 110 (0,1,2) |
| `BENCHMARK_AMBIGUITY` | 16 (0); 38 (0,1,2,3); 60 (2); 61 (0,2) |
| `INSUFFICIENT_EVIDENCE` | 86 (1) |

### A1: task ID lỗi

| Nhãn chính | Task ID |
|---|---|
| `MODEL_REASONING_FAILURE` (27) | 18, 20, 21, 37, 46, 60, 64, 82, 86, 91, 93, 104, 107 |
| `USER_SIMULATOR_DEVIATION` (11) | 22, 39, 76, 110 |
| `BENCHMARK_AMBIGUITY` (1) | 38 |
| `INSUFFICIENT_EVIDENCE` (1) | 86, trial 1 |

### A2: task ID lỗi

| Nhãn chính | Task ID |
|---|---|
| `MODEL_REASONING_FAILURE` (24) | 18, 20, 37, 46, 60, 61, 79, 91, 93, 104, 107 |
| `USER_SIMULATOR_DEVIATION` (9) | 8, 22, 39, 71, 110 |
| `BENCHMARK_AMBIGUITY` (4) | 16, 38 |
| `INSUFFICIENT_EVIDENCE` (1) | 86, trial 1 |

### A3: task ID lỗi

| Nhãn chính | Task ID |
|---|---|
| `MODEL_REASONING_FAILURE` (25) | 18, 21, 28, 37, 46, 64, 74, 79, 91, 93, 101, 103, 104, 107 |
| `USER_SIMULATOR_DEVIATION` (14) | 8, 22, 39, 62, 71, 76, 110 |
| `BENCHMARK_AMBIGUITY` (5) | 16, 38 |
| `INSUFFICIENT_EVIDENCE` | Không có trong 44 completed reward-zero episode |

Trial chính xác của A1–A3 được giữ trong reviewed CSV của từng stage thay vì
chép lại dài dòng trong file này.

## Các ví dụ thật về cách gán nhãn

- **Model reasoning — A1 task 20, trial 0.** User yêu cầu variant đắt nhất của
  mọi item. Agent gọi `modify_pending_order_items` thành công nhưng chọn running
  shoe `4153505238` thay vì item đắt nhất evaluator yêu cầu `4107812777`.
  Tool call hợp lệ và chạy thành công; sai ở lựa chọn có nghĩa nghiệp vụ.
- **User simulator deviation — A1 task 22, trial 0.** Evaluator frozen yêu cầu
  cập nhật địa chỉ profile và địa chỉ đơn pending. Simulator đổi yêu cầu, sau đó
  muốn khôi phục địa chỉ cũ nhưng không cung cấp nó, rồi chấp nhận địa chỉ agent
  suy luận được. Đây không phải lỗi tool-call cấu trúc mà A1/A2/A3 nên “sửa”.
- **Benchmark ambiguity — A1 task 38, trial 2.** Agent hủy đơn với lý do hợp lệ
  theo policy là `ordered by mistake`; evaluator lại đòi `no longer needed`.
  Hội thoại không chứng minh lý do evaluator khóa là lựa chọn duy nhất.
- **Insufficient evidence — A1/A2 task 86, trial 1.** Trajectory có item mutation
  đã hoàn tất, yêu cầu đổi địa chỉ xuất hiện sau đó, và user từ chối cung cấp đủ
  địa chỉ. Artifact không đủ để quy một nguyên nhân duy nhất, nên giữ nhãn
  “không đủ evidence” thay vì gọi là lỗi harness.

## Native tool error: định nghĩa và cách đo

### Đếm cái gì

Biểu diễn native của lỗi tool là một message như sau:

```json
{"role": "tool", "error": true, "content": "Error: ..."}
```

trong `results.json -> simulations[*].messages[*]`.

Metric là **số event**, không phải số episode: một episode có thể có nhiều tool
error. Cách trích xuất là scan mọi message thỏa `role == "tool"` và
`error == true`. Với A1–A3, cùng call đó có thể được đối chiếu thêm trong JSONL
trace: `dispatch_started` → `dispatch_completed` có
`dispatch_status="native_error"` → `result_emitted`.

| Stage | Số native tool-error event | Số message lỗi khác nhau |
|---|---:|---:|
| A0 | 37 | 6 |
| A1 | 46 | 4 |
| A2 | 44 | 5 |
| A3 | 36 | 5 |

### Các native tool error đã quan sát

| Native error hiển thị | Nghĩa | A0 | A1 | A2 | A3 |
|---|---|---:|---:|---:|---:|
| `User not found` | Arguments lookup không xác định được user Retail. | 23 | 27 | 23 | 18 |
| `Order not found` | READ/WRITE gọi order không tồn tại. | 4 | 12 | 12 | 4 |
| `Payment method should be the original payment method` | Return dùng payment method vi phạm precondition hoàn tiền native. | 4 | 4 | 4 | 8 |
| `The new item id should be different from the old item id` | Modify pending order đề xuất item mới trùng item cũ. | 4 | 3 | 4 | 5 |
| `Number of 5996159314 not found.` | Exchange yêu cầu số lượng item nhiều hơn số item trong đơn. | 1 | 0 | 1 | 1 |
| `Payment method not found` | Payment method được tham chiếu không tồn tại. | 1 | 0 | 0 | 0 |

### Vì sao native tool error thường không làm crash episode

Native `Environment.get_response()` bắt exception của tool, đổi nó thành chuỗi
`"Error: {exception}"`, đặt `error=True`, rồi trả về `ToolMessage` bình thường
thay vì ném exception ra ngoài. Orchestrator thêm tool message đó vào trajectory
và model có thêm một lượt để hỏi user thông tin đúng, gọi một tool mới, hoặc vẫn
có thể thất bại sau đó vì lý do khác.

Code evidence:

- `src/tau2/environment/environment.py`, `Environment.get_response()` dòng
  398–415: `try make_tool_call`; `except Exception`; trả
  `ToolMessage(error=True)`.
- `src/tau2/orchestrator/orchestrator.py`, `Orchestrator.step()` dòng 412–425:
  ToolMessage được thêm vào trajectory và quyền điều khiển trở lại phía đã gọi.

Vì vậy `ToolMessage.error=true` **không** đồng nghĩa provider crash, và cũng
không tự động đồng nghĩa episode có `reward=0`.

## Ví dụ cụ thể trong result/trace: lỗi tool rồi phục hồi

### A0 task 68, trial 0: native error rồi conversational recovery, reward = 1

Nguồn: `data/simulations/20_a0_native_baseline/a0-main/retail-stratified-58-v1/results/results.json`.

1. Agent gọi `find_user_id_by_name_zip` cho Noah Ito với zip `98178`.
2. Tool message tương ứng có `error=true`, content `Error: User not found`.
3. User simulator sửa zip thành `98187`.
4. Agent tạo **một tool call mới** với `98187`; tool trả về `noah_ito_3850`.
5. Episode hoàn tất với `reward=1`.

Đây là conversational recovery sau khi có thông tin mới—không phải retry nguyên
request lỗi, và cũng không phải harness retry.

### A1 task 68, trial 0: cùng hành vi nhưng trace chi tiết hơn

Nguồn trace:
`data/simulations/30_a1_validation/a1-main/retail-stratified-58-v1-4trials-v1/traces/task-68__trial-000__seed-626729__attempt-001.jsonl`.

Với logical call `chatcmpl-tool-bb6ca6fbed50b667` ở turn 5:

| Trace sequence | Phase | Evidence chính |
|---:|---|---|
| 2 | `validated` | A1 quyết định `allow`; đây không phải harness reject. |
| 3 | `dispatch_started` | READ native bắt đầu chạy. |
| 4 | `dispatch_completed` | `dispatch_count=1`, `dispatch_status=native_error`, `exception_type=ValueError`, message đã scrub là `User not found`; state hash không đổi. |
| 5 | `result_emitted` | Error result trở thành tool message agent nhìn thấy. |
| 6–11 | logical call kế tiếp | Lookup với zip đã sửa được propose, validate, dispatch đúng một lần và thành công. |

Trace chứng minh đồng thời hai việc: call đầu thực sự đi vào Retail native đúng
một lần; call sửa zip sau đó là logical call khác. Đây không phải duplicate
dispatch.

### Native precondition error trên WRITE: A0 task 37, trial 0

Agent ban đầu gọi `modify_pending_order_items` với một vài `new_item_ids` trùng
`item_ids`. Retail native trả:
`Error: The new item id should be different from the old item id`.

Đây là domain/precondition response. Agent sau đó gửi request modify mới với
item ID khác; episode hoàn tất với `reward=1`. WRITE lỗi ban đầu không được tự
động replay.

## Không nhầm lẫn bốn lớp lỗi

| Dấu hiệu evidence | Nghĩa |
|---|---|
| `results.json` có tool message `error=true` | Native tool/domain/precondition error **sau dispatch**. Agent có thể tiếp tục. |
| A1/A2/A3 trace có `decision=REJECT`, `dispatch_count=0` | Harness chặn call **trước native dispatch**. |
| Provider sidecar A2/A3 báo timeout/503/empty response | Provider-generation operational failure; khi exhaust không được tạo ToolMessage/reward giả. |
| Evaluator trả `reward=0` | Kết quả benchmark cuối; phải review trajectory rồi mới gán nguyên nhân. |

## Ý nghĩa đối với claim của harness

Review này **không** chứng minh “A1–A3 giải quyết reasoning”. Điều nó chứng
minh chính xác hơn là: các benchmark failure completed còn lại chủ yếu nằm ở
semantic/model, user simulator hoặc evaluator; harness giữ structural reject,
native-tool outcome, provider retry và an toàn sau WRITE có thể quan sát và tách
riêng, thay vì âm thầm biến chúng thành benchmark reward failure.
