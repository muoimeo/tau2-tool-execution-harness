# Metric catalogue A0–A3: xem ở đâu và tính như thế nào

## Phạm vi frozen runs

| Stage | Results raw | Planned episodes | Completed/serialized episodes |
|---|---|---:|---:|
| A0 | `20_a0_native_baseline/a0-main/retail-stratified-58-v1/results/results.json` | 232 | 232 |
| A1 | `30_a1_validation/a1-main/retail-stratified-58-v1-4trials-v1/results.json` | 232 | 232 |
| A2 | `40_a2_validation_retry/a2-main/retail-stratified-58-v1-4trials-v4-structural-observability/results/results.json` | 232 | 227 |
| A3 | `50_a3_validation_retry_recovery/a3-main/retail-stratified-58-v1-4trials-v3-per-logical-safe-failures/results/results.json` | 232 | 221 |

Mọi metric benchmark outcome (`Pass¹`, `Pass⁴`, `DB match`, duration) chỉ dùng
episode có trong `results.json`. Các episode A2/A3 không serialize do provider
failure/exhaustion được báo bằng operational metrics riêng, không bị gán
`reward=0` giả.

## Bảng headline: số hiện tại và mẫu số

| Nhóm | Metric | A0 | A1 | A2 | A3 |
|---|---|---:|---:|---:|---:|
| Benchmark outcome | Pass¹ | 186/232 = **80.17%** | 192/232 = **82.76%** | 189/227 = **83.26%** | 177/221 = **80.09%** |
| Benchmark outcome | Pass⁴ | 37/58 = **63.79%** | 40/58 = **68.97%** | 36/54 = **66.67%** | 31/50 = **62.00%** |
| Benchmark outcome | DB match | 186/232 = **80.17%** | 195/232 = **84.05%** | 190/227 = **83.70%** | 180/221 = **81.45%** |
| Completion | Benchmark completion rate | 232/232 = **100.00%** | 232/232 = **100.00%** | 227/232 = **97.84%** | 221/232 = **95.26%** |
| Native reliability | Native tool error rate | 37/1,737 = **2.13%** | 43/1,731 = **2.48%** | 40/1,670 = **2.40%** | 31/1,574 = **1.97%** |
| Validation safety | Harness reject count / materialized tool results | N/A | 3/1,734 = **0.17%** | 4/1,674 = **0.24%** | 5/1,579 = **0.32%** |
| Validation safety | Reject → dispatch violations | N/A | **0/3** | **0/4** | **0/5** |
| Execution safety | Duplicate WRITE violations | N/A† | 0/355 | 0/340 | 0/327 |
| Provider overhead | Avg. provider attempts / generation | N/A | N/A | 4,282/4,150 = **1.032** | 4,081/3,899 = **1.047** |
| Provider recovery | Retry-affected generation recovered | N/A | N/A | 0/66 = **0.00%** | 5/93 = **5.38%** |
| Operational reliability | Operationally unresolved rate | N/A | N/A | 5/232 = **2.16%** | 11/232 = **4.74%** |
| Latency | Episode duration — mean | **40.18 s** | **42.34 s** | **47.99 s** | **53.03 s** |
| Latency | Episode duration — median | **36.84 s** | **39.86 s** | **43.71 s** | **40.54 s** |

† A0 không có runtime duplicate-dispatch guard/trace. `0 observed` chỉ có nghĩa
passive post-run telemetry không thấy logical call ID bị lặp; không phải một
invariant được runtime enforce như A1–A3.

> **Correction cho slide:** median duration của A2 là **43.71 s**. Giá trị
> **47.99 s** là mean A2, không phải median.

## Cách đọc `results.json`

Mỗi episode nằm ở:

```text
results.json
└── simulations[i]
    ├── task_id, trial, seed
    ├── duration
    ├── termination_reason
    ├── reward_info.reward
    ├── reward_info.db_check.db_match
    ├── reward_info.action_checks
    ├── reward_info.communicate_checks
    └── messages
```

`results.json` là nguồn chính cho benchmark outcome. Nó **không đủ** để tính
harness reject, reject-to-dispatch, provider retry hay duplicate dispatch; các
metric đó cần trace/lifecycle/provider sidecar.

## 1. Pass¹

**Câu hỏi trả lời:** trong các episode benchmark đã hoàn tất, bao nhiêu episode
được evaluator thưởng trọn vẹn?

```text
Pass¹ = số simulation có reward_info.reward == 1
        ─────────────────────────────────────────
        số simulation đã serialize trong results.json
```

**Lấy ở đâu:** `simulation.reward_info.reward` trong `results.json`.

**Cẩn thận:** A2/A3 có mẫu số nhỏ hơn 232 vì operationally unresolved không có
result. Không nên gọi chênh lệch Pass¹ là tác động nhân quả của harness: các run
đi qua gateway ở thời điểm khác nhau và trajectory không deterministically
paired.

## 2. Pass⁴

**Câu hỏi trả lời:** có bao nhiêu task pass đủ cả bốn frozen trial?

```text
Pass⁴ = số task có đủ 4 result và cả 4 reward == 1
        ──────────────────────────────────────────
        số task có đủ cả 4 result
```

**Lấy ở đâu:** group `results.json.simulations` theo `task_id`, sau đó kiểm tra
`trial = 0,1,2,3`.

**Mẫu số:**

- A0/A1: 58 task có đủ bốn trial.
- A2: chỉ 54 task có đủ bốn trial; 4 task thiếu ít nhất một trial vì 5 episode
  unresolved.
- A3: chỉ 50 task có đủ bốn trial; 8 task thiếu ít nhất một trial vì 11 episode
  unresolved.

Nếu muốn báo theo planned task conservative denominator, có thể ghi thêm
36/58 cho A2 và 31/58 cho A3, nhưng không được trộn nó với Pass⁴ primary ở trên.

## 3. DB match

**Câu hỏi trả lời:** database cuối cùng có khớp expected final state của
evaluator không?

```text
DB match rate = số simulation có reward_info.db_check.db_match == true
                ───────────────────────────────────────────────────
                số simulation đã serialize
```

**Lấy ở đâu:** `simulation.reward_info.db_check.db_match`.

**Cẩn thận:** DB match vẫn là benchmark outcome. Nó không chứng minh dispatcher
an toàn, cũng không nói một action đúng về semantic hay không.

## 4. Benchmark completion rate

**Câu hỏi trả lời:** bao nhiêu workload planned đã đi tới `SimulationRun` và
được evaluator chấm?

```text
Completion rate = số tuple unique (task_id, trial, seed) trong results.json
                  ──────────────────────────────────────────────────
                  số tuple planned trong ledger/scheduled-runs.json
```

**Lấy ở đâu:**

- Numerator: `results.json.simulations`.
- Denominator A0/A3: `ledger/scheduled-runs.json`.
- Denominator A1/A2 cũng được freeze bởi taskset/config gồm 58 task × 4 trial =
  232; lifecycle/accounting xác nhận các tuple thiếu.

**Ví dụ:** A2 = 227 serialized unique tuple / 232 planned = **97.84%**.

## 5. Native tool error rate

**Câu hỏi trả lời:** trong số tool call thật sự đi vào Retail native, bao nhiêu
call trả domain/precondition error?

```text
Native tool error rate = số native dispatch_completed có status native_error
                         ─────────────────────────────────────────────
                         số native dispatch_completed
```

**Lấy ở đâu:**

- A0: passive telemetry `telemetry/a0-main/tool-events.json` và
  `analysis/tool_calls.csv`; có thể đối chiếu `ToolMessage(error=true)` trong
  raw result.
- A1/A2/A3: trace file `traces/task-...attempt-....jsonl`.
  Chỉ đếm event `phase="result_emitted"`, `origin="native_dispatch"`; error là
  `dispatch_status="native_error"`.

**Không đếm vào numerator:** harness reject. Một reject được materialize thành
`ToolMessage(error=true)` nhưng có `dispatch_count=0`; nó không phải native tool
error.

## 6. Harness rejection rate

**Câu hỏi trả lời:** harness đã thực sự chặn bao nhiêu call trước khi chúng đi
vào native tool?

```text
Harness rejection rate = số result_emitted có origin == harness_reject
                         ───────────────────────────────────────
                         tổng result_emitted tool result
```

**Lấy ở đâu:** A1/A2/A3 trace JSONL. A0 là N/A vì harness tắt.

Ví dụ A1 có 3 reject: task 37 trial 0, 1, 3. Đó là
`modify_pending_order_items` vi phạm rule old item ID = new item ID.

## 7. Reject → dispatch violation rate

**Câu hỏi trả lời:** có call nào harness đã nói REJECT nhưng vẫn lén chạy native
tool không?

```text
Violation rate = số logical call REJECT có dispatch_started hoặc dispatch_completed
                 ─────────────────────────────────────────────────────────────
                 số logical call REJECT
```

**Mục tiêu:** 0/total reject.

**Lấy ở đâu:** join `logical_call_id` trong trace JSONL:

- reject evidence: `origin="harness_reject"`, `decision="reject"`,
  `dispatch_count=0`;
- violation evidence: cùng `logical_call_id` lại có `dispatch_started` hoặc
  `dispatch_completed`.

Kết quả clean run: A1 **0/3**, A2 **0/4**, A3 **0/5**.

## 8. Duplicate WRITE violation rate

**Câu hỏi trả lời:** một logical WRITE có bị native execute hơn một lần không?

```text
Duplicate WRITE violation rate = số logical WRITE có >1 native dispatch_completed
                                 ─────────────────────────────────────────────
                                 số logical WRITE đã native dispatch
```

**Lấy ở đâu:** A1/A2/A3 trace JSONL. Group theo identity episode +
`logical_call_id`, chỉ xét `phase="dispatch_completed"` và
`native_tool_type="write"`.

Kết quả: A1 **0/355**, A2 **0/340**, A3 **0/327**. Nghĩa là không có WRITE
logical call nào được thực thi native lần thứ hai trong các completed episode.

**Cẩn thận:** không group theo cùng tool name hoặc cùng arguments. Hai request
khác nhau có thể hợp lệ trùng tool/arguments; duplicate được định nghĩa bằng
cùng **logical call ID** trong cùng episode.

## 9. Provider attempts / generation

**Câu hỏi trả lời:** A2/A3 phải trả thêm bao nhiêu provider call cho mỗi lượt
generate của agent?

```text
Average attempts/generation = tổng provider-attempt event
                              ────────────────────────────
                              số generation_id khác nhau
```

**Lấy ở đâu:** `traces/task-...attempt-....provider-retry.jsonl` của A2/A3.
Mỗi line là một harness-owned provider attempt.

- A2: 4,282 / 4,150 = **1.032**.
- A3: 4,081 / 3,899 = **1.047**.

A0/A1 là N/A: không có A2 provider-retry sidecar chuẩn hóa. Không được suy
đoán số attempt từ log console hay từ số message.

## 10. Provider recovery rate

**Câu hỏi trả lời:** trong những generation thật sự cần retry, bao nhiêu lần
retry dẫn tới success trong **cùng generation**?

```text
Provider recovery rate = số generation có failure rồi success sau retry
                         ──────────────────────────────────────────
                         số generation có từ 2 provider attempts trở lên
```

**Lấy ở đâu:** group provider sidecar theo `generation_id` rồi xem outcome.

- A2: 0/66 = **0.00%**.
- A3: 5/93 = **5.38%**.

Không được thay metric này bằng “episode cuối cùng hoàn thành sau resume”; đó là
episode-level recovery khác với provider recovery trong cùng generation.

## 11. Episode duration: mean và median

**Câu hỏi trả lời:** harness/retry làm thay đổi thời gian wall-clock của episode
completed như thế nào?

```text
mean   = average(simulation.duration)
median = median(simulation.duration)
```

**Lấy ở đâu:** `simulation.duration` trong raw `results.json`, đơn vị giây.
Chỉ tính completed/serialized episode.

- Mean nhạy với outlier dài.
- Median mô tả “episode điển hình” tốt hơn khi có retry hoặc provider delay.
- A3 mean 53.03 s nhưng median 40.54 s: một số episode rất dài kéo mean lên.

## Nơi xem artifact nhanh nhất

| Bạn muốn xem | File nên mở |
|---|---|
| Reward, DB match, duration, conversation/tool messages | `results.json` của stage đó |
| Planned tuple và seed | `ledger/scheduled-runs.json` (A0/A3) hoặc frozen config/taskset/manifest |
| Native tool result/error A0 | A0 `analysis/tool_calls.csv` hoặc raw `results.json` |
| Harness reject, native dispatch, WRITE, state hash A1–A3 | `traces/task-...attempt-....jsonl` |
| Episode terminal status, trace hash/count/sequence | `traces/task-...attempt-....lifecycle.json` |
| Provider retries / attempt count A2–A3 | `traces/task-...attempt-....provider-retry.jsonl` |
| A3 repair/quarantine lineage | `traces/task-...attempt-....recovery.jsonl` nếu episode có A3 recovery event |
| A2/A3 unresolved tuple và failure subtype count | `traces/a2-operational-accounting.json` |

## Scripts hiện có để tái tạo hoặc kiểm tra

Các script này chỉ đọc raw artifacts và ghi derived analysis; không sửa
`results.json`:

```powershell
# A0: episode metrics, tool calls, summary và review queue
.\.venv\Scripts\python.exe scripts\build_ben03_a0_main_analysis.py

# A1: kiểm tra 232 tuple, lifecycle/trace integrity và xuất summary
.\.venv\Scripts\python.exe scripts\build_a1_main_58x4_summary.py `
  --root "data\simulations\30_a1_validation\a1-main\retail-stratified-58-v1-4trials-v1"

# Review nguyên nhân reward=0 và native tool errors A1–A3
.\.venv\Scripts\python.exe scripts\review_res06_a1_a3.py
```

`analysis/A0_A2_METRIC_MATRIX.md` là matrix A0–A2 đã có trước đó. File hiện
tại bổ sung A3, làm rõ exact denominator, và giải thích cách tính từng metric.

## Cách diễn giải an toàn

- Pass¹/Pass⁴/DB match là **benchmark outcome**, không tự chứng minh harness
  tốt hơn.
- Reject→dispatch = 0 và duplicate WRITE = 0 là bằng chứng trực tiếp về
  execution safety của A1–A3.
- Completion/provider attempts/duration cho biết chi phí và độ ổn định vận
  hành; chúng phải được báo cùng benchmark outcome, không được che đi.
- Provider retry/recovery chỉ có bằng chứng chuẩn ở A2/A3 vì chỉ các stage này
  có provider sidecar.
