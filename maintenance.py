from decimal import Decimal
from statistics import median
from typing import Any


def to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def analyze_maintenance_need(
    history: list[dict[str, Any]],
    minimum_recent_days: int = 3,
    minimum_baseline_days: int = 7,
    drop_threshold_percent: Decimal = Decimal("25"),
    minimum_radiation_wh_m2: Decimal = Decimal("3000"),
) -> dict[str, Any]:
    if not history:
        return {"alert": False, "reason": "no_history"}

    ordered = sorted(history, key=lambda item: item["report_date"])

    valid_days = []
    for row in ordered:
        generation = to_decimal(row.get("generation_day_kwh"))
        radiation = to_decimal(row.get("solar_radiation_wh_m2"))
        rainfall = to_decimal(row.get("rainfall_mm"))

        if radiation < minimum_radiation_wh_m2:
            continue

        if rainfall > Decimal("5"):
            continue

        performance_ratio = generation / radiation if radiation > 0 else Decimal("0")

        valid_days.append(
            {
                **row,
                "generation": generation,
                "radiation": radiation,
                "performance_ratio": performance_ratio,
            }
        )

    required_days = minimum_recent_days + minimum_baseline_days
    if len(valid_days) < required_days:
        return {
            "alert": False,
            "reason": "insufficient_favorable_history",
            "available_favorable_days": len(valid_days),
            "required_favorable_days": required_days,
        }

    recent_days = valid_days[-minimum_recent_days:]
    baseline_days = valid_days[:-minimum_recent_days]

    if len(baseline_days) > 30:
        baseline_days = baseline_days[-30:]

    baseline_ratios = [float(day["performance_ratio"]) for day in baseline_days]
    recent_ratios = [float(day["performance_ratio"]) for day in recent_days]

    baseline_median = Decimal(str(median(baseline_ratios)))
    recent_median = Decimal(str(median(recent_ratios)))

    if baseline_median <= 0:
        return {"alert": False, "reason": "invalid_baseline"}

    drop_percentage = (
        (baseline_median - recent_median)
        / baseline_median
        * Decimal("100")
    )

    expected_generation = sum(
        day["radiation"] * baseline_median for day in recent_days
    ) / Decimal(len(recent_days))

    observed_generation = sum(
        day["generation"] for day in recent_days
    ) / Decimal(len(recent_days))

    zero_generation_with_good_sun = all(
        day["generation"] <= Decimal("0.1") for day in recent_days
    )

    if zero_generation_with_good_sun:
        return {
            "alert": True,
            "alert_type": "inverter_offline",
            "severity": "critical",
            "drop_percentage": Decimal("100"),
            "expected_generation_kwh": expected_generation,
            "observed_generation_kwh": observed_generation,
            "favorable_days_count": len(recent_days),
            "reference_start_date": recent_days[0]["report_date"],
            "reference_end_date": recent_days[-1]["report_date"],
            "probable_cause": "Possível falha, desligamento ou problema de comunicação.",
            "details": {
                "baseline_performance_ratio": str(baseline_median),
                "recent_performance_ratio": str(recent_median),
            },
        }

    if drop_percentage < drop_threshold_percent:
        return {
            "alert": False,
            "reason": "drop_below_threshold",
            "drop_percentage": drop_percentage,
        }

    return {
        "alert": True,
        "alert_type": "possible_soiling",
        "severity": "warning",
        "drop_percentage": drop_percentage,
        "expected_generation_kwh": expected_generation,
        "observed_generation_kwh": observed_generation,
        "favorable_days_count": len(recent_days),
        "reference_start_date": recent_days[0]["report_date"],
        "reference_end_date": recent_days[-1]["report_date"],
        "probable_cause": (
            "Possível sujeira, sombreamento ou perda parcial de desempenho. "
            "É necessária inspeção antes de concluir a causa."
        ),
        "details": {
            "baseline_performance_ratio": str(baseline_median),
            "recent_performance_ratio": str(recent_median),
            "minimum_radiation_wh_m2": str(minimum_radiation_wh_m2),
            "drop_threshold_percent": str(drop_threshold_percent),
        },
    }
