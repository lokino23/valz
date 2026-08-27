"""Industry lens for sector-aware valuation.

Config: a top-level `industry_lenses` map in config.yaml maps sector
names (matching keys in `sector_map`) to lens blocks:

    industry_lenses:
      bank:
        label: bank_value
        primary: pbv
        supporting:
          roe_min: 0.15
          der_max: 5.0
        verdict_rules:
          undervalued_quality:
            - primary_z: <= -1.0
            - roe:      >= 0.15
            - der:      <= 5.0
          cheap_but_deteriorating:
            - primary_z: <= -1.0
            - roe:      <  0.15
          expensive:
            - primary_z: >= 1.5
          fair: default

Tickers whose sector is not in this map are silently ignored
(industry_lens field is null).
"""
import sqlite3


# Verdict priority order. First matching rule wins. This list must be
# kept in sync with the rule keys the user defines in config.
_VERDICT_PRIORITY = [
    "undervalued_quality",
    "cheap_but_deteriorating",
    "expensive",
    "fair",
]


def _sector_map(cfg):
    """Return cfg['sector_map'] or empty dict if missing."""
    return (cfg or {}).get("sector_map") or {}


def _industry_lenses(cfg):
    """Return cfg['industry_lenses'] or empty dict if missing."""
    return (cfg or {}).get("industry_lenses") or {}


def lens_for(cfg, code):
    """Return the sector name for a code (upper-cased), or None.

    Reads cfg['sector_map'] as a flat code->sector map (matches the
    existing compute.py usage and the config file's "ticker -> group"
    comment). No I/O.
    """
    if not code:
        return None
    return _sector_map(cfg).get(code.upper())


def lens_cfg_for(cfg, sector):
    """Return cfg['industry_lenses'][sector] or None."""
    if not sector:
        return None
    return _industry_lenses(cfg).get(sector)


def list_lens_labels(cfg):
    """Return all lens labels across sectors (preserves config order)."""
    return [lens["label"] for lens in _industry_lenses(cfg).values() if lens.get("label")]


def list_supported_sectors(cfg):
    """Return sectors that have a non-None industry_lenses entry."""
    return list(_industry_lenses(cfg).keys())


# Threshold direction encoded in metric_min / metric_max suffix; verdict
# rule conditions reference the bare metric name (e.g., roe) and the
# operator + value pair.
_THRESHONE_DIR = {
    "min": ">=",
    "max": "<=",
}


def _condition_matches(cond, primary_z, supporting_values):
    """Return True if a single condition (dict of {key: value}) is met.

    A condition is a single-key dict like {"primary_z": "<= -1.0"} or
    {"roe": ">= 0.15"}. We parse the value as "OP threshold".
    """
    if not cond:
        return True
    for metric, op_value in cond.items():
        if metric == "primary_z":
            actual = primary_z
        else:
            actual = supporting_values.get(metric)
        if actual is None:
            return False  # missing data -> rule cannot fire
        # Parse "OP value" string
        op_value = str(op_value).strip()
        for op in ("<=", ">=", "<", ">", "==", "!="):
            if op_value.startswith(op):
                threshold = float(op_value[len(op):].strip())
                if op == "<=" and not (actual <= threshold):
                    return False
                if op == ">=" and not (actual >= threshold):
                    return False
                if op == "<"  and not (actual <  threshold):
                    return False
                if op == ">"  and not (actual >  threshold):
                    return False
                if op == "==" and not (actual == threshold):
                    return False
                if op == "!=" and not (actual != threshold):
                    return False
                break
        else:
            return False  # unparseable condition -> rule cannot fire
    return True


def _all_conditions_match(conditions, primary_z, supporting_values):
    """Return True if all conditions in a list are met (AND-joined).

    A condition-list is a verdict rule's body, e.g.:
        - primary_z: <= -1.0
        - roe:      >= 0.15
    A rule with body "fair: default" is a single string, not a list; treat
    it as a catch-all that always matches.
    """
    if conditions == "default" or conditions == ["default"]:
        return True
    if not isinstance(conditions, list):
        return False
    return all(_condition_matches(c, primary_z, supporting_values) for c in conditions)


def evaluate_verdict(lens_cfg, primary_z, supporting_values):
    """Walk verdict_rules in priority order, return first matching rule's
    name, or 'fair' if none match.

    `supporting_values` is a dict {metric_name: current_value}. Missing
    metrics (None) cause a rule to be skipped silently.

    Returns one of: 'undervalued_quality', 'cheap_but_deteriorating',
    'expensive', 'fair'.
    """
    if not lens_cfg:
        return "fair"
    rules = lens_cfg.get("verdict_rules") or {}
    for verdict in _VERDICT_PRIORITY:
        body = rules.get(verdict)
        if body is None:
            continue
        if _all_conditions_match(body, primary_z, supporting_values):
            return verdict
    return "fair"


# Map from lens primary metric to the column in the `multiples` table
# where its current value lives. Supports per_ttm, pbv, ev_ebitda, ps_ttm.
_PRIMARY_VAR_COL = {
    "per": "per_ttm",
    "pbv": "pbv",
    "ev_ebitda": "ev_ebitda",
    "ps": "ps_ttm",
}


def _latest_primary(con, code, primary):
    """Read the latest value of `primary` for `code` from the multiples
    table. Returns float or None.
    """
    col = _PRIMARY_VAR_COL.get(primary)
    if col is None:
        return None
    row = con.execute(
        f"SELECT {col} FROM multiples WHERE code=? "
        f"AND {col} IS NOT NULL "
        f"ORDER BY date DESC LIMIT 1", (code,)).fetchone()
    if row is None:
        return None
    return float(row[0])


def _latest_z(con, code):
    """Read the latest mu/sigma for `code` from the stats table.
    Returns (mu, sigma) tuple or (None, None).
    """
    row = con.execute(
        "SELECT mu, sigma FROM stats WHERE code=? AND window='w5y' LIMIT 1",
        (code,)).fetchone()
    if row is None or row["mu"] is None or row["sigma"] is None or row["sigma"] == 0:
        return None, None
    return float(row["mu"]), float(row["sigma"])


def _latest_fundamentals(con, code):
    """Read the latest net_income / equity / total_debt for `code` from
    the fundamentals table. Returns dict with keys net_income, equity,
    total_debt (each float or None).
    """
    row = con.execute(
        "SELECT net_income, equity, total_debt FROM fundamentals "
        "WHERE code=? ORDER BY year DESC, periode DESC LIMIT 1", (code,)
    ).fetchone()
    if row is None:
        return {"net_income": None, "equity": None, "total_debt": None}
    return {
        "net_income": float(row["net_income"]) if row["net_income"] is not None else None,
        "equity":     float(row["equity"])     if row["equity"]     is not None else None,
        "total_debt": float(row["total_debt"]) if row["total_debt"] is not None else None,
    }


def _compute_pctile(value, mu, sigma):
    """Return the pctile of `value` in the (mu, sigma) normal distribution.

    Approximation: linear scale on a 0-1 z-score range capped to [-3, 3].
    """
    if value is None or mu is None or sigma is None or sigma == 0:
        return None
    z = (value - mu) / sigma
    # Map z from [-3, 3] to [0, 1] (clipped)
    if z <= -3: return 0.0
    if z >=  3: return 1.0
    return (z + 3) / 6


def _metric_z_and_pctile(value, mu, sigma):
    """Return (z, pctile) for a value, or (None, None) if data is missing."""
    if value is None or mu is None or sigma is None or sigma == 0:
        return None, None
    z = (value - mu) / sigma
    return z, _compute_pctile(value, mu, sigma)


def lens_metrics_for(cfg, db_path, code):
    """Compute industry_lens for one ticker. Returns None if the code
    is not in a sector with a configured industry_lens, or if the
    primary metric has no data.

    Returns dict {sector, label, primary, available_metrics, verdict}.
    """
    sector = lens_for(cfg, code)
    if not sector:
        return None
    lens_cfg = lens_cfg_for(cfg, sector)
    if not lens_cfg:
        return None

    # Late imports: keeps unit tests import-clean (mirrors peer.py).
    import db

    primary = lens_cfg.get("primary")
    con = db.connect(db_path, readonly=True)
    try:
        # Primary metric: current value + z-score from stats table.
        current_value = _latest_primary(con, code, primary)
        mu, sigma = _latest_z(con, code)
        primary_z, primary_pctile = _metric_z_and_pctile(current_value, mu, sigma)

        if primary_z is None and current_value is None:
            return None  # no primary data; no point in a lens

        # Supporting metrics: derived from fundamentals (ROE, DER).
        fund = _latest_fundamentals(con, code)
        supporting_values = {}
        if fund["net_income"] is not None and fund["equity"] and fund["equity"] > 0:
            supporting_values["roe"] = fund["net_income"] / fund["equity"]
        if fund["total_debt"] is not None and fund["equity"] and fund["equity"] > 0:
            supporting_values["der"] = fund["total_debt"] / fund["equity"]

        # Build available_metrics dict for the response.
        available = {}
        # Primary carries {value, z, pctile}
        if current_value is not None:
            available[primary] = {
                "value": current_value,
                "z": primary_z,
                "pctile": primary_pctile,
            }
        # Supporting metrics carry {value} only (no z-score; threshold-based).
        for metric, value in supporting_values.items():
            available[metric] = {"value": value, "z": None, "pctile": None}

        verdict = evaluate_verdict(lens_cfg, primary_z, supporting_values)

        return {
            "sector": sector,
            "label": lens_cfg.get("label"),
            "primary": primary,
            "available_metrics": available,
            "verdict": verdict,
        }
    finally:
        con.close()
