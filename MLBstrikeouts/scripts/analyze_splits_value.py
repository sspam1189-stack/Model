#!/usr/bin/env python3
"""Deep analysis of whether SPLITS_BLEND_WEIGHT recent signal adds value."""
import sys, os, io, contextlib, importlib
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import defaults

def run_at(w):
    defaults.SPLITS_BLEND_WEIGHT = w
    import props_engine; importlib.reload(props_engine)
    import props_backfill; importlib.reload(props_backfill)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        results = props_backfill.backfill()
    return results['strikeouts']['picks']

def units(odds, won, target):
    if odds < 0:
        return target if won else -target*abs(odds)/100
    return target*odds/100 if won else -target

def metrics(picks, label):
    actionable = [p for p in picks if p['pick'] in ('OVER','UNDER')]
    watch = [p for p in picks if p['pick']=='PASS']
    all_p = actionable + watch
    n = len(all_p)
    wins = sum(1 for p in actionable if p['won']); losses = len(actionable)-wins
    wr = wins/(wins+losses)*100 if (wins+losses) else 0
    pu = sum(units(p['odds'], p['won'], 2) for p in actionable)
    prisk = sum(2*abs(p['odds'])/100 if p['odds']<0 else 2 for p in actionable)

    ww = sum(1 for p in watch if p['won']); wl = len(watch)-ww
    wpct = ww/(ww+wl)*100 if (ww+wl) else 0
    lu = sum(units(p['odds'], p['won'], 1) for p in watch)
    lrisk = sum(abs(p['odds'])/100 if p['odds']<0 else 1 for p in watch)

    # K rate calibration
    proj_k = [p['proj'] for p in all_p]
    act_k = [p['actual'] for p in all_p if p['actual'] is not None]
    abs_err = [abs(p['proj']-p['actual']) for p in all_p if p['actual'] is not None]
    bias = sum(p['proj']-p['actual'] for p in all_p if p['actual'] is not None) / len(act_k)
    mae = sum(abs_err)/len(abs_err)

    # Per-bucket bias
    def bucket_bias(lo, hi):
        sub = [p for p in all_p if lo <= p['proj'] < hi and p['actual'] is not None]
        if not sub: return 0, 0
        return sum(p['proj']-p['actual'] for p in sub)/len(sub), len(sub)
    b1,n1 = bucket_bias(0,6); b2,n2 = bucket_bias(6,8); b3,n3 = bucket_bias(8,99)

    proi = pu/prisk*100 if prisk else 0
    lroi = lu/lrisk*100 if lrisk else 0
    print(f"  W={label}:  N={n}  Picks {wins}-{losses} ({wr:.1f}%) {pu:+.2f}u (ROI{proi:+.1f}%) | Leans {ww}-{wl} ({wpct:.1f}%) {lu:+.2f}u (ROI{lroi:+.1f}%)")
    print(f"         MAE {mae:.3f}  bias {bias:+.3f}K  | proj<6 bias{b1:+.2f}(N{n1})  6-8 bias{b2:+.2f}(N{n2})  >=8 bias{b3:+.2f}(N{n3})")
    return dict(n=n, picks_wl=(wins,losses), pu=pu, prisk=prisk, ww=ww, wl=wl, lu=lu, lrisk=lrisk, mae=mae, bias=bias)

# Full season sweep
print("\n=========================================================================")
print("  Full 2026 season: cap=0.36 (current) + varying SPLITS_BLEND_WEIGHT")
print("=========================================================================")
results = {}
for w in [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0]:
    picks = run_at(w)
    results[w] = (picks, metrics(picks, w))

# Past 2 weeks
print("\n=========================================================================")
print("  Past 2 weeks (6/30-7/14) only")
print("=========================================================================")
for w in [0.0, 0.1, 0.2, 0.3, 0.5, 1.0]:
    picks = results[w][0]
    recent = [p for p in picks if '2026-06-30' <= p['date'] <= '2026-07-14']
    metrics(recent, w)

# Recent (7/7+)
print("\n=========================================================================")
print("  Recent panel (7/7-7/14)")
print("=========================================================================")
for w in [0.0, 0.1, 0.2, 0.3, 0.5, 1.0]:
    picks = results[w][0]
    recent = [p for p in picks if p['date'] >= '2026-07-07']
    metrics(recent, w)

# How different are the picks themselves between 0.0 and 0.3?
print("\n=========================================================================")
print("  How much does the blend actually change selections?")
print("=========================================================================")
p00 = {(p['date'],p['player']): p for p in results[0.0][0]}
p30 = {(p['date'],p['player']): p for p in results[0.3][0]}
shared = set(p00.keys()) & set(p30.keys())
diffs = []
for k in shared:
    if p00[k].get('proj') is not None and p30[k].get('proj') is not None:
        diffs.append(p30[k]['proj'] - p00[k]['proj'])
if diffs:
    import statistics
    print(f"  Shared projections: {len(diffs)}")
    print(f"  Mean abs diff in projected K (0.3 vs 0.0): {sum(abs(x) for x in diffs)/len(diffs):.3f}")
    print(f"  Max abs diff: {max(abs(x) for x in diffs):.2f}")
    print(f"  Pct with |diff|>0.5: {sum(1 for x in diffs if abs(x)>0.5)/len(diffs):.1%}")
    print(f"  Pct with |diff|>1.0: {sum(1 for x in diffs if abs(x)>1.0)/len(diffs):.1%}")

# Selection differences
picks_00 = set((p['date'],p['player']) for p in results[0.0][0] if p['pick'] in ('OVER','UNDER'))
picks_30 = set((p['date'],p['player']) for p in results[0.3][0] if p['pick'] in ('OVER','UNDER'))
only_00 = picks_00 - picks_30
only_30 = picks_30 - picks_00
shared_picks = picks_00 & picks_30
print(f"\n  Pick selection: {len(shared_picks)} shared, {len(only_00)} only-at-0.0, {len(only_30)} only-at-0.3")

# Restore (shipped value)
defaults.SPLITS_BLEND_WEIGHT = 0.0
