# Báo cáo Lab 25 — GPU FinOps Optimization

**Họ và tên:** Đoàn Minh Quang  
**MHV:** 2A202600757  
**Lab:** Day 25 — GPU FinOps Workshop (Track 2 — Infrastructure)  
**Ngày nộp:** 2026-07-01  

---

## Tổng quan kết quả

| Hạng mục | Kết quả |
|---|---|
| `verify.py` | **11/11 checks passed** ✅ |
| `pytest` | **15/15 tests passed** ✅ |
| Extensions thực hiện | **Extension 1** (Enhanced `recommend_tier`) + **Extension 3** (`cache_is_worth_it`) + **Extension 5** (Carbon-aware Scheduling) |
| Baseline spend | $27,133/tháng |
| Optimized spend | $14,626/tháng |
| **Tổng tiết kiệm** | **$12,507/tháng (46%)** |
| $/1M-token baseline | $6.488 |
| $/1M-token optimized | $1.126 |

---

## 1. Baseline vs. Optimized — Chi phí trước và sau

### Tổng quan chi phí

```
Baseline spend  : $27,133/tháng
Optimized spend : $14,626/tháng
Savings         : $12,507/tháng  (46% tiết kiệm)
```

### Đơn vị $/1M-token (metric quan trọng nhất)

| Metric | Baseline | Optimized | % Thay đổi |
|---|---|---|---|
| $/1M-token | $6.488 | $1.126 | **-82.6%** |
| Chi phí/ngày | $48.87 | $8.48 | -82.6% |
| Chi phí/tháng (inference) | $1,466 | $254 | -82.6% |

**Tại sao $/1M-token quan trọng hơn $/GPU-hr?**  
`$/GPU-hr` chỉ nói bạn trả bao nhiêu để thuê GPU — không nói bạn nhận được gì. Hai đội trả cùng giá/GPU-hr nhưng một đội tối ưu cascade + cache + batch có thể phục vụ 5–6× nhiều token hơn. `$/1M-token` đo hiệu quả thực sự.

---

## 2. Phân tích từng đòn bẩy (Lever Breakdown)

### Savings Waterfall

![Savings Dashboard](outputs/screenshot_savings_dashboard.png)

| Lever | Tiết kiệm/tháng | % đóng góp |
|---|---|---|
| **Purchasing (spot/reserved)** | $10,040 | **80.3%** |
| Inference (cascade+cache+batch) | $1,212 | 9.7% |
| Right-size util-lies | $655 | 5.2% |
| Kill idle GPUs | $600 | 4.8% |
| **Tổng** | **$12,507** | **100%** |

### Chi tiết từng lever

#### Lever 1: Inference (cascade + cache + batch) — $1,212/tháng

**Output M2:**
```
requests=2400  tokens=7,533,027
baseline  : $48.87/day   $6.488/1M-token
optimized : $8.48/day    $1.126/1M-token
savings   : 82.6%  (cascade + caching + batch)
discount stack (batch + 100% cache): 0.050 of naive
```

**Ba đòn bẩy stacked:**
- **Cascade** (định tuyến sang model nhỏ): giảm $3.00/1M → $0.20/1M input — lớn nhất vì phần lớn request đủ dùng model nhỏ
- **Prompt Caching** (chiết khấu 90% phần input đã cache): giúp các request lặp lại hệ thống prompt
- **Batch API** (chiết khấu 50%): áp dụng cho các request không yêu cầu real-time

**Phân tích:** Cascade đóng góp lớn nhất vì giá model nhỏ rẻ hơn 15× (0.20 vs 3.00 $/1M input). Discount stack: batch × cache = 50% × 10% = 5% của giá gốc → 95% off khi cả hai áp dụng.

#### Lever 2: Purchasing (spot/reserved) — $10,040/tháng (80% tổng savings)

**Output M3:**
```
break-even utilization @ 45% reserved discount = 55%

job               gpu    tier          on-demand   optimized
job-train-llm     H100   spot       $     12,000 $      7,596
job-train-embed   A100   spot       $      2,148 $      1,393
job-finetune      H100   spot       $        900 $        570
job-infer-chat    A10G   reserved   $      4,320 $      2,592
job-infer-rag     A100   reserved   $      3,866 $      2,160
job-infer-search  L4     reserved   $      1,728 $        972
job-dev-sandbox   A10G   spot       $        480 $        203
job-batch-eval    H100   spot       $        225 $        142

monthly: on-demand $25,667 -> optimized $15,627  (39.1% saved)
```

**Logic tier selection:**
- **Spot**: Job có thể bị gián đoạn (interruptible=1) → dùng checkpoint, tận dụng giá rẻ ~37% hơn on-demand
- **Reserved**: Duty cycle ≥ 55% (≥13.2h/ngày) → cam kết 3yr, tiết kiệm 45%
- **On-demand**: Spiky workload, không interruptible, duty cycle thấp

**Đây là lever lớn nhất** vì tổng chi phí GPU phần cứng (training jobs với H100) chiếm phần lớn hóa đơn.

#### Lever 3: Right-size util-lies — $655/tháng

GPU `gpu-h100-4` báo cáo 98.2% GPU-Util nhưng MFU chỉ 0.194 (19.4%). Downgrade sang A100 tiết kiệm $655/tháng.

#### Lever 4: Kill idle GPUs — $600/tháng

GPU `gpu-h100-5` có 8 idle hours/ngày (GPU-Util <10%). Chi phí lãng phí: $20/ngày = $600/tháng.

---

## 3. GPU-Util Lie — Phân tích sâu

### Output M1:

![Verify Results](outputs/screenshot_verify.png)

```
GPU            type   util%    MFU    MBU  idle_h
gpu-h100-4     H100    98.2  0.194  0.207       0   ← LIE!
gpu-a10g-1     A10G    96.9  0.268  0.302       0   ← borderline
gpu-h100-5     H100    61.1  0.261  0.271       8   ← idle waste
...
GPU-Util LIES (util>=90% but MFU<30%): ['gpu-h100-4', 'gpu-a10g-1']
Idle waste (1 day): $20.00  ->  $600/month
```

### Tại sao GPU-Util 98% có thể đi kèm MFU 20%?

`nvidia-smi GPU-Util` đo **% thời gian GPU clock đang hoạt động** (scheduling kernel), không đo **% FLOPs thực tế được thực hiện**. GPU có thể "bận" vì:

1. **Memory stall**: GPU đang chờ data từ HBM (memory-bound workload) — clock đang chạy nhưng ALU đang chờ
2. **Kernel launch overhead**: Nhiều small kernel liên tiếp → GPU-Util cao nhưng compute thực sự thấp
3. **Decode-heavy workload**: LLM decode có arithmetic intensity ~1-2 FLOP/byte, far below H100 ridge point (~295 FLOP/byte) → memory-bound, MFU thấp là bình thường

**Tác động tài chính của gpu-h100-4:**
- Bạn trả $2.50/giờ (H100 on-demand rate) cho H100
- Nhưng chỉ nhận được 0.194 × 100% = 19.4% FLOPs
- Effective cost per actual TFLOP: $2.50 / (0.194 × 989 TFLOPs) = ~$0.013/TFLOP vs $0.0025 nếu MFU=100%
- **Bạn trả ~5× quá đắt cho mỗi FLOP thực sự được tính**

**Giải pháp:** Downgrade sang A100 (peak 312 TFLOPs, cheaper rate) hoặc tối ưu batch size/kernel fusion để tăng arithmetic intensity.

---

## 4. Phần mở rộng "Your Turn"

### Extension 1 — Enhanced `recommend_tier()` (finops/pricing.py)

**Cải tiến so với policy gốc:**

1. **GPU-type-aware interruption rate**: H100 spot ít bị preempt (~2%/h) hơn A10G (~7%/h)
2. **Duration-aware reserved selection**: Job ngắn (<180 ngày) → so sánh với 1yr discount (30%, break-even=70%), job dài → 3yr discount (45%, break-even=55%)

```python
GPU_INTERRUPT_RATE = {
    "H100": 0.02,    # ~2%/h — premium GPU, fewer preemptions  
    "H200": 0.02,
    "A100": 0.04,
    "A10G": 0.07,
    "L4":   0.08,
    "T4":   0.10,
}
RESERVED_DISCOUNT_1YR = 0.30   # break-even = 70% duty cycle
RESERVED_DISCOUNT_3YR = 0.45   # break-even = 55% duty cycle
```

**Kết quả đo lường:**
- Policy gốc: spot validation không xét interruption rate
- Policy mới: với H100 (interrupt_rate=0.02), effective_mult = 1.01 → spot vẫn rẻ hơn 37%  
- Với A10G (interrupt_rate=0.07), effective_mult = 1.035 → spot vẫn có lợi nhưng margin nhỏ hơn
- **Insight**: H100 spot là lựa chọn an toàn hơn vì ít bị preempt — phù hợp với training jobs dài

**Tests vẫn pass với policy mới:**
- `recommend_tier(2, True) == "spot"` ✓
- `recommend_tier(24, False) == "reserved"` ✓  
- `recommend_tier(4, False) == "on_demand"` ✓

---

### Extension 3 — `cache_is_worth_it()` (finops/pricing.py)

**Hàm mới:**
```python
def cache_is_worth_it(avg_cache_reads, write_cost_per_m, read_discount=0.10) -> bool:
    """Cache chỉ tiết kiệm tiền khi tổng savings từ đọc > chi phí ghi.
    
    Break-even: reads >= 1 / (1 - read_discount)
    Với Anthropic 90% discount: break-even = 1.11 reads
    """
    break_even_reads = 1.0 / (1.0 - read_discount)
    return avg_cache_reads >= break_even_reads
```

**Kết quả đo lường từ dataset:**
```
Requests with cached input: 2400/2400 (100%)
Break-even reads per prefix: 1.11
Estimated avg reads per cached prefix: 1.00
Cache is worth it? False  (not profitable at this read rate)
-> Need 1.11+ reads per prefix to break even.
```

**Phân tích:**
- **Break-even**: Cần ≥1.11 lần đọc lại mỗi prefix mới có lợi
- Dataset hiện tại ước tính 1.00 reads/prefix → **marginally not profitable** theo model
- Thực tế: nếu nhiều request share cùng system prompt → avg_reads tăng lên → cache rất có lợi
- **Recommendation**: Cache có lợi khi có system prompt dài được tái sử dụng (RAG contexts, few-shot examples). Với workload hiện tại, cần xác nhận avg_reads > 1.11 trước khi bật cache.

**Tại sao Anthropic cho phép cache không có lợi với 1 lần đọc?**  
Vì write_cost ≈ normal_cost, nhưng đọc chỉ 10% giá. Với 1 lần đọc: savings = 90%, nhưng write_cost = 100% → net = -10%. Với 2 lần đọc: savings = 180%, write = 100% → net = +80%. 

---

### Extension 5 — Carbon-aware Scheduling (missions/m_ext5_carbon_scheduling.py)

![Carbon Analysis](outputs/screenshot_carbon.png)

**Kết quả cho 5 interruptible jobs (1,789 kWh tổng):**

```
Job                GPU       kWh    CO2@us-east-1  CO2@europe-north1  Saved    %red
job-train-llm      H100    1568.0     595.84 kg       47.04 kg        548.80  92.1%
job-train-embed    A100      80.0      30.40 kg        2.40 kg         28.00  92.1%
job-finetune       H100      25.2       9.58 kg        0.76 kg          8.82  92.1%
job-dev-sandbox    A10G      52.8      20.06 kg        1.58 kg         18.48  92.1%
job-batch-eval     H100      63.0      23.94 kg        1.89 kg         22.05  92.1%
TOTAL                               679.82 kg         53.67 kg       626.15  92.1%
```

**So sánh tất cả regions:**

| Region | gCO2/kWh | $/kWh | Total CO2 (kg) | Elec Cost ($) |
|---|---|---|---|---|
| **europe-north1** | 30 | $0.090 | **53.67** | $161.01 |
| us-east-wa | 90 | $0.055 | 161.01 | **$98.39** |
| us-west-2 | 120 | $0.070 | 214.68 | $125.23 |
| us-east-1 | 380 | $0.120 | 679.82 | $214.68 |
| europe-central2 | 660 | $0.180 | 1,180.74 | $322.02 |

**Insight:** Migrating 5 interruptible training/eval jobs từ us-east-1 → europe-north1:
- **Tiết kiệm 626.15 kg CO2** (giảm 92.1%)  
- Tiết kiệm thêm **$53.67 tiền điện**
- Trade-off: europe-north1 (Na Uy) cách xa US users ~130ms RTT → chỉ phù hợp cho batch/training, không phải real-time inference

**Tại sao europe-north1 vừa rẻ vừa sạch?** Thủy điện chiếm >90% điện lưới Na Uy (30 gCO2/kWh), thấp hơn 22× so với Ba Lan (660). Chi phí nước giả lập $0.09/kWh — rẻ hơn us-east-1.

**Tối ưu theo nhiều tiêu chí:**
- **Rẻ nhất về điện**: us-east-wa ($0.055/kWh)
- **Sạch nhất CO2**: europe-north1 (30 gCO2/kWh)
- **Trade-off tốt nhất**: europe-north1 (thấp cả CO2 lẫn chi phí điện so với us-east-1)

---

## 5. Kết quả verify.py và pytest

### verify.py — 11/11 PASS

```
============================================================
  LAB 25 VERIFY
============================================================
  [PASS] M1 flags the GPU-Util lie (gpu-h100-4)  (['gpu-h100-4', 'gpu-a10g-1'])
  [PASS] M1 detects idle waste  ($20.0/day)
  [PASS] M2 $/1M-token drops after optimization  (6.488 -> 1.126)
  [PASS] M2 inference savings in 60-95% band  (82.6%)
  [PASS] M3 recommends a spot tier  ({'spot', 'reserved'})
  [PASS] M3 recommends a reserved tier  ({'spot', 'reserved'})
  [PASS] M3 purchasing saves money  (39.1%)
  [PASS] M4 tag coverage 85-100%  (92%)
  [PASS] M4 chargeback gate is open  (True)
  [PASS] M5 total savings in 40-95% band  (46.1%)
  [PASS] M5 report.md written
------------------------------------------------------------
  11/11 checks passed
============================================================
```

### pytest — 15/15 PASS

```
...............                                                          [100%]
15 passed in 0.84s
```

---

## 6. Phân tích M4 — Cost Allocation & FOCUS

```
== M4 Cost Allocation ==
cost by team ($/day):
  assistant    $    2.59
  search       $    2.49
  eval         $    1.79
  rag          $    1.60
tag coverage: 92%  ->  chargeback ready? True
FOCUS export -> outputs/focus_export.csv (50 rows)
```

**Nhận xét:**
- Team `assistant` tốn nhiều nhất ($2.59/day = ~28%) — likely dùng model lớn nhiều nhất
- Tag coverage 92% ≥ 80% → **sẵn sàng chargeback** (thu tiền thực sự từ team)
- Nếu coverage < 80% → chỉ có thể showback (thông báo, chưa tính phí)

**FOCUS export** cho phép multi-cloud cost tracking với chuẩn thống nhất: dù dùng AWS, GCP hay Azure, format FOCUS giúp compare apples-to-apples.

---

## 7. Sustainability Analysis

```
Energy per query: 0.24 Wh
Carbon per query: 0.091 gCO2e  (tại us-east-1)
Cheapest+cleanest region: europe-north1
```

**So sánh với reasoning queries:**
- Normal query: 0.24 Wh
- Reasoning query: 0.24 × 80 = 19.2 Wh (~80× gấp)  
- **Recommendation**: Chỉ dùng reasoning khi confidence score < threshold (VD: <0.7), giới hạn 10% traffic

---

## 8. Khuyến nghị cho NimbusAI (3 hành động đầu tiên)

### Ưu tiên 1: Chuyển purchasing sang spot/reserved (ROI cao nhất — $10,040/tháng)
- Ngay lập tức: tag tất cả jobs với `interruptible` flag
- Enable checkpoint trên tất cả training jobs → migrate sang spot H100
- Jobs inference steady (duty >55%): ký 3yr reserved

### Ưu tiên 2: Triển khai cascade routing cho inference ($1,212/tháng)
- Set up routing logic: nếu request không cần complex reasoning → route sang model nhỏ
- Monitor accuracy để validate cascade không làm giảm chất lượng
- Kết hợp batch API cho các request non-real-time (analytics, eval)

### Ưu tiên 3: Fix GPU-Util lie → right-size gpu-h100-4 ($655 + cải thiện workload)
- Investigate tại sao gpu-h100-4 có MFU 19%: profile kernel execution, kiểm tra batch size
- Nếu là decode-only workload: xem xét downgrade sang A100 hoặc tăng concurrency
- Set up MFU monitoring dashboard — dừng dùng GPU-Util từ nvidia-smi làm metric hiệu quả

---

## Tóm tắt Files Nộp

| File | Mô tả |
|---|---|
| `outputs/report.md` | Báo cáo baseline vs optimized (generated by M5) |
| `outputs/savings.png` | Waterfall chart (generated by matplotlib) |
| `outputs/focus_export.csv` | FOCUS format cost allocation export |
| `finops/pricing.py` | Extension 1 (enhanced `recommend_tier`) + Extension 3 (`cache_is_worth_it`) |
| `missions/m2_inference_levers.py` | Extension 3 integrated into M2 |
| `missions/m_ext5_carbon_scheduling.py` | Extension 5 (Carbon-aware Scheduling) |
| `outputs/screenshot_savings_dashboard.png` | Dashboard $/1M-token + savings chart |
| `outputs/screenshot_verify.png` | verify.py 11/11 results |
| `outputs/screenshot_carbon.png` | Carbon-aware analysis charts |

---

## 9. Bonus (Tùy chọn — Không tính điểm)

### Bonus 1 — LiteLLM-style Token-Cost Tracker với Budget Cap

**File:** `bonus/litellm_tracker/tracker.py` + `demo.py`

**Mô tả:** Proxy giả lập theo phong cách LiteLLM — track $/request theo từng API key và **hard-stop** khi vượt budget. Không cần API key thực.

**Kết quả chạy:**
```
BLOCKED after 10 chat requests: key=team-chat would spend $0.0507 > cap $0.05

per-key spend: {'team-chat': 0.046, 'team-eval': 0.0003}
requests logged: 15
```

**Phân tích:**
- `team-chat` bị chặn sau **10 requests** (dùng model `large`, prompt dài 30 lần) — chi phí $0.046, request 11 sẽ vượt cap $0.05
- `team-eval` cùng lúc xử lý 5 requests với model `small` + batch → chỉ tốn **$0.0003** — rẻ hơn **153×**
- **Lesson:** Budget cap cứng (hard stop) quan trọng hơn soft alert — nếu chỉ cảnh báo mà không chặn, team có thể tiếp tục overrun

**Insight quan trọng:** Đây là "token tier" của cost observability (§10 deck): gắn cost vào từng API key → dễ dàng chargeback theo team.

---

### Bonus 2 — Real Local Model (tok/s trên CPU)

**File:** `bonus/local_model/run_local.py`

**Kết quả:**
```
[skip] transformers/torch not installed — this bonus is optional.
       pip install torch transformers  (CPU wheels are fine)
```

Script thoát sạch (exit 0) vì `torch` chưa cài. Logic đo: nếu cài torch, script sẽ chạy `sshleifer/tiny-gpt2` trên CPU, đo **tok/s thực tế**, rồi tính `$/1M-token` từ:
```
$/1M = (thời_gian_giây / 3600) × rate_hr / tokens × 1,000,000
```
Với CPU $0.10/hr và ~10 tok/s → `$/1M ≈ $2,778` — đắt hơn H100 inference **~2,470×**, minh họa tại sao GPU quan trọng.

---

### Bonus 3 — Prometheus Exporter (GPU Cost Metrics)

**File:** `bonus/docker/exporter.py` (pure Python stdlib, không cần Docker)

**Kết quả metrics (curl http://localhost:9101/metrics):**
```
# HELP gpu_wasted_cost_usd_per_hr $/hr paid for FLOPs not used (1-mfu)*cost
# TYPE gpu_wasted_cost_usd_per_hr gauge

gpu_util_pct{gpu_id="gpu-h100-4",gpu_type="H100"} 98.16
gpu_mfu{gpu_id="gpu-h100-4",gpu_type="H100"}      0.1943
gpu_hourly_cost_usd{gpu_id="gpu-h100-4",gpu_type="H100"}        2.50
gpu_wasted_cost_usd_per_hr{gpu_id="gpu-h100-4",gpu_type="H100"} 2.0143  ← HIGHEST!

gpu_util_pct{gpu_id="gpu-h100-5",gpu_type="H100"} 61.06
gpu_mfu{gpu_id="gpu-h100-5",gpu_type="H100"}      0.2612
gpu_wasted_cost_usd_per_hr{gpu_id="gpu-h100-5",gpu_type="H100"} 1.8470
```

**Top GPU wasted cost/hr:**

| GPU | GPU-Util% | MFU | Wasted $/hr |
|---|---|---|---|
| **gpu-h100-4** | 98.2% | 0.194 | **$2.014/hr** ← Worst |
| gpu-h100-5 | 61.1% | 0.261 | $1.847/hr |
| gpu-h100-1 | 95.2% | 0.408 | $1.479/hr |
| gpu-h100-2 | 94.3% | 0.401 | $1.497/hr |

**Insight Prometheus:** Metric `gpu_wasted_cost_usd_per_hr = (1 - MFU) × $/hr` là metric quan trọng nhất cần đưa vào Grafana dashboard — nó trực tiếp cho thấy tiền đang bị lãng phí. GPU `gpu-h100-4` tuy util 98% nhưng **lãng phí $2.01/hr** — tệ nhất fleet!

Nếu có Docker: `docker compose up` → mở Grafana `http://localhost:3000` (admin/admin) → dashboard "GPU Cost & Efficiency" tự hiển thị util-lie GPUs nổi bật.

---


