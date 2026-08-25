import statistics

def winsorize(vals, lo=0.01, hi=0.99):
    if not vals: return []
    s = sorted(vals); n = len(s)
    lo_v = s[max(0, int(lo*(n-1)))]; hi_v = s[min(n-1, int(hi*(n-1)))]
    return [min(max(v, lo_v), hi_v) for v in vals]

def fit(values, window):
    v = [x for x in values if x is not None][-window:]
    if len(v) < 2: return (None, None, len(v))
    return (statistics.fmean(v), statistics.pstdev(v), len(v))

def streak(ser, mu, sigma, thr):
    if mu is None or not sigma or not ser: return 0
    n = 0
    for _, v in reversed(ser):
        z = (v - mu) / sigma
        if z <= thr: n += 1
        else: break
    return n
