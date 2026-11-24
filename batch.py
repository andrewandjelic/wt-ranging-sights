"""Generate a ranging sight for every ground vehicle in the game.

    python batch.py              # generate into out/, write out/batch_report.csv
    python batch.py --install    # also copy into the game's UserSights folder
    python batch.py --release    # also refresh sights/ and its manifest, the
                                 # ready-made set the installer ships

One sight per vehicle, built for its best shell (see make_sight.best_shell).
To force a different shell for a vehicle, add it to overrides.json:
    {"ussr_t_54_1947": "100mm_br_412b"}
After a game update, delete the cache/ folder and run again.
"""
import argparse
import csv
import json
from concurrent.futures import ThreadPoolExecutor

import make_sight as m

CHAR = ("https://raw.githubusercontent.com/gszabi99/War-Thunder-Datamine/"
        "master/char.vromfs.bin_u/")
GROUND = {"exp_tank", "exp_heavy_tank", "exp_tank_destroyer", "exp_SPAA"}


def one(unit_id, override, install):
    try:
        out, name, shells = m.make(unit_id, override, install)
        sh = shells[name]
        return [unit_id, "ok", name, sh[0], f"{sh[1]:.0f}", f"{sh[5]:.0f}",
                "; ".join(n for n in shells if n != name)]
    except BaseException as e:      # SystemExit is used for "cannot build"
        if isinstance(e, KeyboardInterrupt):
            raise
        return [unit_id, "skipped", "", "", "", "", f"{type(e).__name__}: {e}"[:200]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--release", action="store_true")
    a = ap.parse_args()

    wp = m.fetch("config/wpcost.blkx", root=CHAR)
    units = sorted(k for k, v in wp.items()
                   if isinstance(v, dict) and v.get("unitClass") in GROUND)
    over_file = m.HERE / "overrides.json"
    over = json.loads(over_file.read_text(encoding="utf-8")) if over_file.exists() else {}

    with ThreadPoolExecutor(12) as pool:
        rows = list(pool.map(lambda u: one(u, over.get(u), a.install), units))

    report = m.HERE / "out" / "batch_report.csv"
    report.parent.mkdir(exist_ok=True)
    with report.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["unit", "status", "shell", "type", "speed_ms", "est_pen_mm", "other_shells_or_reason"])
        w.writerows(rows)
    if a.release:
        import shutil
        rel = m.HERE / "sights"
        shutil.rmtree(rel, ignore_errors=True)
        with_label = []
        for r in rows:
            if r[1] != "ok":
                continue
            safe = "".join(c if c.isalnum() or c in "_-." else "_" for c in r[2])
            src = m.HERE / "out" / r[0] / f"ranging_{safe}.blk"
            (rel / r[0]).mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, rel / r[0] / src.name)
            with_label.append([r[0], src.stem, m.shell_label(r[2], r[3], float(r[4]))])
        with (rel / "manifest.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["unit", "sight", "shell"])
            w.writerows(with_label)
        print("release set:", rel, f"({len(with_label)} sights)")
    ok = sum(r[1] == "ok" for r in rows)
    print(f"{ok} sights built, {len(rows) - ok} skipped, of {len(rows)} ground vehicles")
    print("report:", report)


if __name__ == "__main__":
    main()
