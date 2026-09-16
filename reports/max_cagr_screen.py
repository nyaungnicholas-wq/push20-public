"""MAX-CAGR screen — one deterministic grid, sliced for parallel workers.

Grid: top_n x vol_target x (bull,bear) caps x weight scheme x use_3x, pruned:
  - top_n=1 -> scheme irrelevant (single position), keep return_prop only
  - use_3x=1 only when bull cap > 2.0 (3x tier never triggers at scale<=2)

Run:  .venv/bin/python reports/max_cagr_screen.py --slice K --of N
Prints compact JSON: {"slice":K,"count":n,"results":[{label,cfg,full_*,modern_*}...]}
Also writes reports/max_screen_slice_K.json
"""
from __future__ import annotations
import os, sys, json, argparse, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from opt_harness import load_data, simulate, metrics

def build_grid():
    grid = []
    tops    = [1, 2, 3]
    vts     = [0.22, 0.25, 0.30, 0.35, 0.40]
    caps    = [(1.5,1.0),(2.0,1.0),(2.0,1.5),(2.5,1.5),(3.0,1.0),(3.0,1.5),(3.0,3.0)]
    for tn in tops:
        for vt in vts:
            for (cb, cbear) in caps:
                schemes = ["return_prop"] if tn == 1 else ["return_prop", "squared"]
                use3s   = [0, 1] if cb > 2.0 else [0]
                for ws in schemes:
                    for u3 in use3s:
                        cfg = {"top_n": tn, "vol_target": vt, "vol_cap_bull": cb,
                               "vol_cap_bear": cbear, "weight_scheme": ws, "use_3x": u3}
                        lbl = (f"t{tn}_vt{int(vt*100)}_c{cb}b{cbear}_"
                               f"{'sq' if ws=='squared' else 'rp'}{'_3x' if u3 else ''}")
                        grid.append((lbl, cfg))
    return grid

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--slice", type=int, required=True)
    ap.add_argument("--of", type=int, required=True)
    args = ap.parse_args()

    grid = build_grid()
    mine = grid[args.slice::args.of]
    df = load_data()
    results = []
    for lbl, cfg in mine:
        f = metrics(simulate(df, cfg, "2006-01-01", "2024-12-31"))
        m = metrics(simulate(df, cfg, "2015-01-01", "2024-12-31"))
        if not f or not m:
            continue
        results.append({"label": lbl, "cfg": cfg,
                        "full_cagr": f["cagr"], "full_mdd": f["mdd"],
                        "full_calmar": f["calmar"], "full_sharpe": f["sharpe"],
                        "full_win": f["win_rate_monthly"],
                        "modern_cagr": m["cagr"], "modern_mdd": m["mdd"]})
    results.sort(key=lambda r: -r["full_cagr"])
    out = {"slice": args.slice, "count": len(results), "results": results}
    path = os.path.join(os.path.dirname(__file__), f"max_screen_slice_{args.slice}.json")
    with open(path, "w") as fh:
        json.dump(out, fh)
    # stdout: top 12 only (full set is in the file)
    print(json.dumps({"slice": args.slice, "count": len(results),
                      "results": results[:12]}))
