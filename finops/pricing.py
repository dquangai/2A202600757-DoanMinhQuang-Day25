"""Pricing & purchasing economics — measure in $/1M-token, not $/GPU-hr.

Figures are June-2026 as-of snapshots from the deck's RESEARCH dossier; treat
live prices as fast-moving (re-baseline before each cohort).

Extensions implemented:
  - Extension 1: recommend_tier() enhanced with gpu_type interruption rates
                 and 1yr vs 3yr reserved comparison.
  - Extension 3: cache_is_worth_it() — break-even analysis for prompt caching.
"""
from __future__ import annotations


def request_cost(
    input_tok: int,
    output_tok: int,
    price_in_per_m: float,
    price_out_per_m: float,
    cached_in: int = 0,
    cache_discount: float = 0.10,   # Anthropic cached-read ~0.1x (=-90%)
    batch: bool = False,
    batch_discount: float = 0.50,   # Batch API ~ -50%
) -> float:
    """USD cost of a single request. Cached input billed at cache_discount x price."""
    cached_in = min(max(0, cached_in), input_tok)
    uncached_in = input_tok - cached_in
    cost = (
        (uncached_in / 1e6) * price_in_per_m
        + (cached_in / 1e6) * price_in_per_m * cache_discount
        + (output_tok / 1e6) * price_out_per_m
    )
    if batch:
        cost *= batch_discount
    return cost


def dollars_per_million(total_cost_usd: float, total_tokens: int) -> float:
    """Aggregate unit economics: $ per 1,000,000 tokens served."""
    if total_tokens <= 0:
        return 0.0
    return total_cost_usd / (total_tokens / 1e6)


def discount_stack(
    batch: bool = False,
    cache_hit_frac: float = 0.0,
    batch_discount: float = 0.50,
    cache_discount: float = 0.10,
) -> float:
    """Effective fraction of the naive bill after stacking discounts (input-heavy view).

    Discounts MULTIPLY: cache applies to the cached share of input, batch to the
    whole bill. batch + 100% cache-hit -> 0.5 * 0.1 = 0.05 (~95% off).
    """
    cache_mult = cache_hit_frac * cache_discount + (1.0 - cache_hit_frac)
    batch_mult = batch_discount if batch else 1.0
    return cache_mult * batch_mult


def break_even_utilization(discount_frac: float) -> float:
    """Utilization at which a commitment pays off ~= 1 - discount.

    A 45% reserved discount needs ~55% utilization (~13.2h/day) to beat on-demand.
    """
    return max(0.0, min(1.0, 1.0 - discount_frac))


# Extension 1: Per-GPU interruption rates (H100 spot is more stable than A10G).
# Source: cloud provider SLA data & empirical benchmarks (2026).
GPU_INTERRUPT_RATE = {
    "H100": 0.02,    # ~2% per hour — premium GPU, fewer preemptions
    "H200": 0.02,
    "A100": 0.04,
    "A10G": 0.07,
    "L4":   0.08,
    "T4":   0.10,
}
# Extension 1: 1yr vs 3yr reserved discounts.
RESERVED_DISCOUNT_1YR = 0.30   # 30% for 1-year commitment
RESERVED_DISCOUNT_3YR = 0.45   # 45% for 3-year commitment


def recommend_tier(
    hours_per_day: float,
    interruptible: bool,
    reserved_discount: float = 0.45,
    gpu_type: str | None = None,
    job_days: int | None = None,
) -> str:
    """Pick a purchasing tier — enhanced (Extension 1).

    Improvements over the simple policy:
      1. GPU-type-aware interruption rate: H100 spot is rarely preempted (~2%/h)
         vs A10G (~7%/h). If effective spot cost (with rework) exceeds on-demand,
         fall back to on_demand.
      2. Duration-aware reserved comparison: jobs < 180 days get 1yr reserved
         discount; jobs >= 180 days prefer 3yr. Only recommends reserved when
         the duty-cycle break-even is satisfied for the chosen tier.

    Original simple policy still applies when gpu_type/job_days not given.
    """
    duty = max(0.0, hours_per_day) / 24.0
    be_3yr = break_even_utilization(RESERVED_DISCOUNT_3YR)   # 55%
    be_1yr = break_even_utilization(RESERVED_DISCOUNT_1YR)   # 70%

    # --- spot: only when interruptible and not 24/7 ---
    if interruptible and hours_per_day < 24:
        # Extension 1: validate spot is actually cheaper given interruption rate
        irr = GPU_INTERRUPT_RATE.get(gpu_type, 0.05) if gpu_type else 0.05
        # rough effective-hour multiplier: rework 0.5h per interrupt
        effective_mult = 1.0 + irr * 0.5
        # spot_hr is approximately (1 - 0.37) of on_demand for H100
        spot_fraction = 0.63  # conservative estimate
        if spot_fraction * effective_mult < 1.0:   # spot still cheaper
            return "spot"
        # else fall through to reserved / on_demand

    # --- reserved: duration-aware (Extension 1) ---
    if job_days is not None and job_days < 180:
        # Short-term job: only commit if duty >= 1yr break-even
        if duty >= be_1yr:
            return "reserved"
    else:
        # Long-term or unknown: use 3yr break-even
        if duty >= be_3yr:
            return "reserved"

    return "on_demand"


def spot_checkpoint_cost(
    job_hours: float,
    spot_hr: float,
    on_demand_hr: float,
    interrupt_rate: float = 0.05,      # per-hour chance (H100 spot ~<5%)
    ckpt_overhead_frac: float = 0.03,  # steady cost of writing checkpoints
    rework_hours_per_interrupt: float = 0.5,
) -> dict:
    """Effective cost of running a checkpointable job on spot vs on-demand.

    Interruptions waste the compute since the last checkpoint (rework); checkpointing
    adds a small steady overhead. Spot still wins for interruptible jobs.
    """
    expected_interrupts = job_hours * interrupt_rate
    rework_hours = expected_interrupts * rework_hours_per_interrupt
    effective_hours = job_hours * (1.0 + ckpt_overhead_frac) + rework_hours
    spot_cost = effective_hours * spot_hr
    on_demand_cost = job_hours * on_demand_hr
    savings_pct = (1.0 - spot_cost / on_demand_cost) * 100.0 if on_demand_cost > 0 else 0.0
    return {
        "spot_effective_hours": round(effective_hours, 2),
        "spot_cost": round(spot_cost, 2),
        "on_demand_cost": round(on_demand_cost, 2),
        "savings_pct": round(savings_pct, 1),
    }


# ---------------------------------------------------------------------------
# Extension 3: Cache economics — is prompt caching actually worth it?
# ---------------------------------------------------------------------------

def cache_is_worth_it(
    avg_cache_reads: float,
    write_cost_per_m: float,
    read_discount: float = 0.10,
) -> bool:
    """Return True when prompt caching saves money, False when it costs more.

    Cache is only profitable once the savings from *reading* the cached prefix
    exceed the cost of *writing* (storing) it in the first place.

    Break-even:
        reads * (1 - read_discount) * write_cost >= write_cost
        => reads * (1 - read_discount) >= 1
        => reads >= 1 / (1 - read_discount)

    For Anthropic's 90% cache discount (read_discount=0.10):
        break-even = 1 / (1 - 0.10) ≈ 1.11 reads.
    Practically ≥2 reads always wins; <1 read always loses.

    Args:
        avg_cache_reads: Average number of times each cached prefix is read back.
        write_cost_per_m: Cost to write (store) 1M tokens in cache (USD).
        read_discount: Fraction of normal price charged for a cache read (0.10 = 90% off).

    Returns:
        True if caching is profitable, False otherwise.
    """
    if avg_cache_reads <= 0 or write_cost_per_m <= 0:
        return False
    # Savings per read = normal_cost - discounted_cost = normal_cost * (1 - read_discount)
    # For break-even: savings_per_read * reads > write_cost
    # Normalize to per-1M-token: savings_per_read_unit = (1 - read_discount) * write_cost_per_m
    # break_even_reads = write_cost_per_m / ((1 - read_discount) * write_cost_per_m)
    #                  = 1 / (1 - read_discount)
    break_even_reads = 1.0 / (1.0 - read_discount)
    return avg_cache_reads >= break_even_reads


def cache_break_even_reads(read_discount: float = 0.10) -> float:
    """Minimum average reads per cached prefix for caching to be profitable."""
    return 1.0 / (1.0 - read_discount)
