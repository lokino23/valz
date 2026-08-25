from zstats import winsorize, fit, streak

def test_winsorize_clips_tails():
    vals = [float(i) for i in range(100)] + [1e9]
    w = winsorize(vals, 0.01, 0.99)
    assert max(w) < 1e8 and min(w) >= 0

def test_fit_window():
    mu, sg, n = fit([float(i) for i in range(10)], 5)
    assert n == 5 and abs(mu - 7.0) < 1e-9 and abs(sg - 1.4142135) < 1e-6

def test_streak_counts_trailing():
    ser = [("d1",-1.0),("d2",-2.5),("d3",-3.0),("d4",-0.1)]
    assert streak(ser, 0.0, 1.0, -1.0) == 0          # last obs z=-0.1 fails thr
    ser2 = [("d1",-0.5),("d2",-1.5),("d3",-2.5)]
    assert streak(ser2, 0.0, 1.0, -1.0) == 2
