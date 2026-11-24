"""Generate a War Thunder custom ranging sight for one ground vehicle.

Shell data (type, muzzle velocity) is read from the public datamine of the
game files, so it tracks game updates instead of anyone's memory.

    python make_sight.py ussr_bmp_1 --main 73mm_pg_9
    python make_sight.py ussr_bmp_1 --main 73mm_pg_9 --install

The game computes the drop for every range tick itself; the sight only names
each shell (bulletType + speed) and says which ticks to draw.
"""
import argparse
import json
import pathlib
import ssl
import urllib.request

DATAMINE = ("https://raw.githubusercontent.com/gszabi99/War-Thunder-Datamine/"
            "master/aces.vromfs.bin_u/")
HERE = pathlib.Path(__file__).parent


CACHE = HERE / "cache"
def sight_roots():
    """The UserSights folders the game reads: one per account, under
    Documents/My Games/WarThunder/Saves/<id>/production. (The game folder's
    own UserSights is NOT read: a sight placed only there never showed up.)"""
    import ctypes.wintypes
    buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
    try:    # the real Documents folder, even when it is redirected
        ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)
        docs = pathlib.Path(buf.value)
    except Exception:
        docs = pathlib.Path.home() / "Documents"
    saves = docs / "My Games" / "WarThunder" / "Saves"
    return [p / "UserSights" for p in saves.glob("*/production")]


def fetch(path, root=DATAMINE):
    """Fetch one datamine file (cached on disk under cache/; delete that folder
    after a game update). Paths in game data are mixed-case; the repo is lower-case."""
    path = path.lower()
    if path.endswith(".blk"):
        path += "x"
    cached = CACHE / path.replace("/", "__")
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    import time
    import urllib.error
    for attempt in range(5):
        try:
            with urllib.request.urlopen(root + path, context=ctx, timeout=60) as r:
                raw = r.read().decode("utf-8")
            break
        except urllib.error.HTTPError:
            raise
        except OSError:
            if attempt == 4:
                raise
            time.sleep(1 + attempt)
    CACHE.mkdir(exist_ok=True)
    cached.write_text(raw, encoding="utf-8")
    return json.loads(raw)


def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def shells_of(weapon):
    """Every distinct shell in a weapon file:
    name -> (bulletType, speed, mass, ballistic caliber, Cx); the last three
    may be None when the game data does not list them."""
    out = {}
    for d in walk(weapon):
        if "bulletType" in d and "speed" in d:
            name = d.get("bulletName") or f'{d["bulletType"]}_{d["speed"]:.0f}'
            if isinstance(name, list):
                name = name[0]
            out.setdefault(name, (d["bulletType"], float(d["speed"]), d.get("mass"),
                                  d.get("ballisticCaliber") or d.get("caliber"),
                                  d.get("Cx")))
    return out


# Drag coefficient used when a shell lists none. Fitted from in-game ladders
# (2026-11-24): the M8 HMC's M66 gave 0.35 at 1,000 m and the BMP-1's PG-9
# gave 0.35 at 1,600 m, so the game appears to use about this as its default.
DEFAULT_CX = 0.35

SKIP_TYPES = ("atgm", "rocket", "sam", "aam", "smoke", "shrapnel", "practice",
              "napalm", "flare", "illum")


def _first(belt):
    """First round of a belt, with repeated fields (which the datamine turns
    into lists) reduced to their first value."""
    belt = belt if isinstance(belt, list) else [belt]
    belt = [x for x in belt if isinstance(x, dict)]
    if not belt:
        return None
    return {k: (v[0] if isinstance(v, list) and v and not isinstance(v[0], dict) else v)
            for k, v in belt[0].items()}


def penetration(b):
    """Rough point-blank penetration in mm, for ranking shells only.
    Kinetic rounds use the game's own DeMarre constants (BR-412D comes out at
    239 mm, matching the stat card); HEAT uses its listed value; APFSDS is
    always ranked on top, ordered by a simplified rod-length estimate."""
    t = b.get("bulletType", "")
    cal = (b.get("damageCaliber") or b.get("caliber") or 0) * 1000
    heat = (b.get("cumulativeDamage") or {}).get("armorPower")
    table = ((b.get("armorpower") or {}).get("ArmorPower0m") or [0])[0]
    if table and not heat and "hesh" not in t:
        # Some rounds carry an explicit penetration table: use it as is.
        return (1000 if t.startswith("apds_fs") else 0) + float(table)
    if t.startswith("apds_fs"):
        # Always ranked above other types. Among APFSDS, a simplified
        # Lanz-Odermatt estimate from the game's own rod length and density.
        import math
        lo = ((b.get("damage") or {}).get("kinetic") or {})
        length, dens = lo.get("lanzOdermattWorkingLength"), lo.get("lanzOdermattDensity")
        if length and dens:
            v = b["speed"] / 1000
            return 1000 + 0.7 * length * math.sqrt(dens / 7850) * math.exp(-6607 / (dens * v * v))
        return 1000 + b["speed"] / 10
    if heat:
        return float(heat)
    if "hesh" in t:
        return cal * 1.2
    k = ((b.get("damage") or {}).get("kinetic") or {})
    if k.get("demarrePenetrationK") and cal:
        m = b.get("damageMass") or b.get("mass")
        pen = (k["demarrePenetrationK"]
               * (b["speed"] / 1900) ** k.get("demarreSpeedPow", 1.43)
               * m ** k.get("demarreMassPow", 0.71)
               / (cal / 100) ** k.get("demarreCaliberPow", 1.07)) * 100
        filler = 100 * (b.get("explosiveMass") or 0) / (b.get("mass") or 1)
        pen *= 1.0 if filler < 0.65 else 0.93 if filler < 1.6 else 0.9 if filler < 2             else 0.85 if filler < 3 else 0.75
        if t.startswith("apcr"):
            pen *= 0.75      # poor against slopes and after penetration
        return pen
    return cal * 0.1         # HE and anything unrecognised: last resort


def available_shells(unit, weapon):
    """Shells this vehicle can load: the default one plus every ammo type its
    modifications unlock. Only the FIRST round of each belt, since that is the
    only one the game will draw a scale for.
    name -> (bulletType, speed, mass, ballistic caliber, Cx, penetration)."""
    mods = set(unit.get("modifications") or {})
    belts = [weapon.get("bullet")]
    belts += [v.get("bullet") for k, v in weapon.items()
              if isinstance(v, dict) and "bullet" in v and k in mods]
    out = {}
    for belt in belts:
        b = _first(belt)
        if not b or "bulletType" not in b or "speed" not in b:
            continue
        if any(b["bulletType"].startswith(x) for x in SKIP_TYPES) or b.get("rocket"):
            continue
        name = b.get("bulletName") or f'{b["bulletType"]}_{b["speed"]:.0f}'
        if isinstance(name, list):
            name = name[0]
        out.setdefault(name, (b["bulletType"], float(b["speed"]), b.get("mass"),
                              b.get("ballisticCaliber") or b.get("caliber"),
                              b.get("Cx") or DEFAULT_CX, penetration(b)))
    return out


def best_shell(shells):
    """Highest estimated penetration; ties go to the faster shell."""
    return max(shells, key=lambda n: (shells[n][5], shells[n][1]))


def best_mg(unit):
    """The vehicle's largest-calibre machine gun (ties: fastest round), as
    (round, triggerGroup, calibre in mm) where round is
    (bulletType, speed, mass, caliber, Cx) for the FIRST round of the default
    belt. Naming a later round of the belt (e.g. the tracer) is not drawn by
    the game (seen on the T-54, 2026-11-24). None if there is no machine gun."""
    best, seen = None, set()
    for d in walk(unit):
        grp = d.get("triggerGroup")
        if grp not in ("coaxial", "machinegun") or "blk" not in d or d["blk"] in seen:
            continue
        seen.add(d["blk"])
        x = _first(fetch(d["blk"]).get("bullet"))
        if not x or "bulletType" not in x or "speed" not in x or not x.get("caliber"):
            continue
        rnd = (x["bulletType"], float(x["speed"]), x.get("mass"), x["caliber"],
               x.get("Cx") or DEFAULT_CX)
        key = (round(x["caliber"], 5), rnd[1])
        if best is None or key > best[0]:
            best = (key, (rnd, grp, x["caliber"] * 1000))
    return best[1] if best else None


def main_gun_blk(unit):
    for d in walk(unit):
        if d.get("trigger") == "gunner0" and "blk" in d:
            return d["blk"]
    raise SystemExit("no gunner0 weapon found in unit file")


TARGET_WIDTH_M = 3.4       # typical tank hull width, for the ranging bars
TARGET_HEIGHT_M = 2.7      # typical tank height (the real BMP-1 sight uses 2.7 m)

# Drop below the boresight in mils at each range, measured from in-game
# screenshots of the generated ladder (the game computes these positions).
# Needed only for the height posts, which are free-drawn lines and so must be
# told where each row sits. Shells without a table get no height posts.
MEASURED_DROPS = {
    "73mm_pg_9": {200: 2.43, 400: 4.98, 600: 7.85, 800: 10.88, 1000: 14.28,
                  1200: 17.99, 1400: 22.1, 1600: 26.6},
}


def computed_drops(shell, ranges):
    """Aim-off in mils for each range from a point-mass model with the game
    data's own mass, calibre and Cx (sea-level air). Flat-fire approximation:
    angle = fall of a level-fired shell / range. Matched the game's ladder to
    about 0.1 mil for BR-412B (T-54, 2026-11-24)."""
    import math
    _, v0, mass, cal, cx = shell[:5]
    if not (mass and cal and cx):
        return {}
    k = 0.5 * 1.225 * cx * math.pi * cal * cal / 4 / mass
    out, x, y, vx, vy, dt = {}, 0.0, 0.0, v0, 0.0, 0.002
    todo = sorted(ranges)
    while todo and x < todo[-1] + 1 and vx > 50:
        v = math.hypot(vx, vy)
        vx -= k * v * vx * dt
        vy -= (9.81 + k * v * vy) * dt
        x += vx * dt
        y += vy * dt
        while todo and x >= todo[0]:
            out[todo[0]] = -y / todo[0] * 1000
            todo.pop(0)
    return out


def units_per_mil(unit):
    """Screen units (fractions of screen height) per mil in the gunner optic.

    Positions of range ticks are given in screen units, not mils, so bar
    widths depend on the optic's field of view. Screen units are defined at
    the zoomed-out FOV and scale with zoom (measured: BMP-1 positions grew by
    1.083 = 13.2/12.2 when zoomed in). Assumes a 16:9 screen.
    """
    import math
    found = [d for d in walk(unit) if d.get("zoomOutFov")]
    found.sort(key=lambda d: "sightFov" not in d)     # gunner optic first
    if not found:
        raise SystemExit("no gunner optic FOV found in unit file")
    fov = found[0]["zoomOutFov"]
    if isinstance(fov, list):       # several zoom steps: the widest is the base
        fov = max(fov)
    return (16 / 9) / (2 * math.tan(math.radians(fov) / 2) * 1000)


def zoom_ratio(unit):
    """How much larger the sight picture gets at full zoom-in (>= 1)."""
    import math
    found = [d for d in walk(unit) if d.get("zoomOutFov") and d.get("zoomInFov")]
    found.sort(key=lambda d: "sightFov" not in d)
    if not found:
        return 1.0
    out, inn = found[0]["zoomOutFov"], found[0]["zoomInFov"]
    out = max(out) if isinstance(out, list) else out
    inn = min(inn) if isinstance(inn, list) else inn
    return max(1.0, math.tan(math.radians(out) / 2) / math.tan(math.radians(inn) / 2))


def line(x1, y1, x2, y2):
    return (f"  line{{line:p4={x1:.2f}, {y1:.2f}, {x2:.2f}, {y2:.2f}; "
            "move:b=no; thousandth:b=yes;}")


def text(s, x, y, size=0.6):
    return (f'  text{{text:t="{s}"; align:i=0; pos:p2={x:.2f}, {y:.2f}; '
            f"move:b=no; thousandth:b=yes; size:r={size};}}")


TYPE_NAMES = (("apds_fs", "APFSDS"), ("apds", "APDS"), ("apcr", "APCR"),
              ("aphebc", "APHEBC"), ("aphe", "APHE"), ("apcbc", "APCBC"),
              ("apbc", "APBC"), ("apc", "APC"), ("sap", "SAP"), ("ap", "AP"),
              ("heat_fs", "HEAT-FS"), ("heat", "HEAT"), ("hesh", "HESH"), ("he", "HE"))


def shell_label(name, btype, speed):
    """Short on-screen name, e.g. '100mm_br_412d' -> 'BR-412D APCBC 887 m/s'."""
    import re
    short = re.sub(r"^\d+(_\d+)?mm_", "", name).replace("_", "-").upper()
    kind = next((v for k, v in TYPE_NAMES if btype.startswith(k)), "")
    if kind and kind.replace("-", "") in short.replace("-", ""):
        kind = ""
    return " ".join(x for x in (short, kind, f"{speed:.0f} m/s") if x)


def bullet(btype, speed, x, tick, rows, note="", text_x=0.012, trigger=""):
    """One scale for one shell. x is in screen units, positive = left of centre."""
    body = "\n".join(f"      distance:p3={d}, {label}, 0" for d, label in rows)
    trigger = f'    triggerGroup:t="{trigger}"\n' if trigger else ""
    return f"""  bullet{{   {('// ' + note) if note else ''}
{trigger}    bulletType:t="{btype}"
    speed:r={speed:.1f}
    drawAdditionalLines:b=no
    drawDistanceCorrection:b=no
    drawUpward:b=no
    distancePos:p2={x:.4f}, 0
    textAlign:i=2
    textPos:p2={text_x:.4f}, 0
    textShift:r=0
    crosshairDistHorSizeMain:p2={tick:.4f}, {tick:.4f}
    crosshairDistHorSizeAdditional:p2={tick:.4f}, 0
    crosshair_distances{{
{body}
    }}
  }}"""


def build(unit_id, main_name, shells, upm, mg=None, zoom=1.0):
    btype, speed = shells[main_name][:2]

    # Reach of the ladder, then the finest row step that keeps rows at least
    # readable on a 1125-high screen in this vehicle's optic.
    cap = 2000 if speed < 800 else 2400 if speed < 1100 else 3200
    step = 400
    for cand in (50, 100, 200):
        est = computed_drops(shells[main_name], [cand, 2 * cand])
        if est:
            gap = (est[2 * cand] - est[cand]) * upm
        else:   # no drag data: vacuum estimate
            gap = 9.81 * cand / (2 * speed * speed) * 1000 * upm
        # Fine enough if rows are ~6 px apart zoomed out, or ~8 px apart at
        # full zoom-in (high-magnification optics are ranged zoomed in).
        if gap >= 0.005 or gap * zoom >= 0.007:
            step = cand
            break
    if main_name in MEASURED_DROPS:
        step = 100

    # The ladder runs as far as the shell's tier allows, but stops before the
    # rows would fall off the bottom of the screen at full zoom-in (0.40 of
    # the screen height below centre keeps them clear of the HUD).
    all_rows = list(range(step, cap + 1, step))
    drops = computed_drops(shells[main_name], all_rows)
    drops.update(MEASURED_DROPS.get(main_name, {}))
    last = max([d for d in all_rows if d in drops and drops[d] * upm * zoom <= 0.40]
               or [min(cap, 4 * step)])
    last = max(last, min(cap, 4 * step))

    # Tick geometry, measured from in-game screenshots (BMP-1, 2026-11-24):
    # a tick runs from (distancePos - z) to (distancePos + size), where z is
    # the third number of distance:p3 and distancePos is positive to the LEFT.
    # With z = 0 a tick is exactly `size` long, starting at distancePos.
    def seg(x_left, x_right, rows, note, label_gap=0.008):
        """A tick spanning x_left..x_right (screen units, right of centre positive)."""
        return bullet(btype, speed, -x_left, x_right - x_left, rows, note,
                      text_x=(x_right - x_left) + label_gap)

    dot = 0.004
    bullets = [seg(-dot / 2, dot / 2, [(step, 0)], f"{step} m centre mark")]

    # Bars: total width = a tank seen head-on at that range. Slide the target
    # down the ladder until a bar just covers it; that row is the aim point.
    # Wide (close) rows are drawn as two end marks plus a centre mark so the
    # aim point stays visible; narrow rows are one solid bar.
    for d in range(2 * step, last + 1, step):
        hw = TARGET_WIDTH_M / 2 / d * 1000 * upm
        label = d // 100 if d % (2 * step) == 0 else 0
        if 2 * hw > 0.045:
            end = 0.012
            bullets.append(seg(-hw, -hw + end, [(d, 0)], f"{d} m left end"))
            bullets.append(seg(-dot / 2, dot / 2, [(d, 0)], f"{d} m centre mark"))
            bullets.append(seg(hw - end, hw, [(d, label)], f"{d} m right end + label"))
        else:
            bullets.append(seg(-hw, hw, [(d, label)], f"{d} m bar + label"))

    lines, texts = [], []

    # Machine gun ladder, off to the right: same bar style as the main one,
    # each bar as wide as a tank at that range, for the largest machine gun.
    # Walk the tracers onto the target, read the row level with it, then use
    # that range on the main ladder. Its centre is at least 22 mils right of the aim
    # point, so it is read by height; the gun itself still aims at centre.
    if mg:
        rnd, grp, cal_mm = mg
        # Far enough right that its widest bar clears the main ladder's
        # widest bar and that bar's label.
        col_mil = max(22.0, TARGET_WIDTH_M / 2 * 1000 * (1 / (2 * step) + 1 / 200)
                      + 0.03 / upm)
        col = col_mil * upm
        c_step, c_last = 200, (2000 if cal_mm > 10 else 1600)
        est = computed_drops(rnd, [100, 200])
        if est:
            g = (est[200] - est[100]) * upm
            if g >= 0.005 or g * zoom >= 0.007:
                c_step = 100
        for d in range(200, c_last + 1, c_step):
            hw = TARGET_WIDTH_M / 2 / d * 1000 * upm
            label = d // 100 if d % (2 * c_step) == 0 else 0
            bullets.append(bullet(rnd[0], rnd[1], -(col - hw), 2 * hw, [(d, label)],
                                  f"machine gun {d} m bar", text_x=2 * hw + 0.008,
                                  trigger=grp))
        texts.append(text(f"MG {cal_mm:.4g}", col_mil, -2.2, 0.5))

    # Height posts: beside every second row, a vertical post as tall as a
    # 2.7 m tank at that range, centred on that row's aim point. Height does
    # not change with the angle the target is facing, unlike width.
    # Posts are spaced at least 200 m apart so neighbours do not overlap, and
    # start at 200 m for slow shells, which are used at short range.
    gap = max(2 * step, 200)
    first = 200 if speed < 400 else 400
    for d in range(gap, last + 1, gap):
        if d not in drops or d < first:
            continue
        drop = drops[d]
        h = TARGET_HEIGHT_M / d * 1000
        x = -(TARGET_WIDTH_M / 2 / d * 1000 + 1.5)
        for seg_ in ((x, drop - h / 2, x, drop + h / 2),
                     (x, drop - h / 2, x + 1, drop - h / 2),
                     (x, drop + h / 2, x + 1, drop + h / 2)):
            lines.append(line(*seg_).replace("move:b=no", "move:b=yes"))

    # Which shell this ladder is for, left of the ladder just under the
    # horizontal line (screen units, so it sits the same on every optic).
    texts.append(f'  text{{text:t="{shell_label(main_name, btype, speed)}"; align:i=0; '
                 "pos:p2=-0.16, 0.03; move:b=no; thousandth:b=no; size:r=0.6;}")

    # Lead marks for the main shell: target crossing at right angles.
    for kmh in (15, 30):
        mil = kmh / 3.6 / speed * 1000.0
        for sgn in (-1, 1):
            lines.append(line(sgn * mil, -1.6, sgn * mil, -0.4))
            texts.append(text(kmh, sgn * mil, -3.2, 0.5))

    hor = "\n".join(f"  range:p2={m}, {abs(m) if m % 8 == 0 else 0}"
                    for m in range(-32, 33, 4) if m)
    nl = "\n"
    return f"""// Generated by wt-sights/make_sight.py for {unit_id}
// Shell: {main_name} ({btype}, {speed:.0f} m/s)
// Brackets span a {TARGET_WIDTH_M} m wide target; numbers are range in 100 m.

crosshairHorVertSize:p2=3, 2
rangefinderProgressBarColor1:c=0, 255, 0, 64
rangefinderProgressBarColor2:c=255, 255, 255, 64
rangefinderTextScale:r=0.7
rangefinderUseThousandth:b=no
rangefinderVerticalOffset:r=0.1
rangefinderHorizontalOffset:r=5
detectAllyTextScale:r=0.7
detectAllyOffset:p2=4, 0.05
fontSizeMult:r=1
lineSizeMult:r=1
drawCentralLineVert:b=no
drawCentralLineHorz:b=yes
drawSightMask:b=yes
crosshairDistHorSizeMain:p2=0.03, 0.02
crosshairDistHorSizeAdditional:p2=0.012, 0.003
distanceCorrectionPos:p2=-0.2, -0.05
drawDistanceCorrection:b=yes

// Left empty on purpose: every scale is tied to a named shell below.
crosshair_distances{{
}}

// Horizontal scale in mils.
crosshair_hor_ranges{{
{hor}
}}

ballistics{{
{nl.join(bullets)}
}}

drawLines{{
  line{{line:p4=0, -6, 0, -1.5; move:b=no; thousandth:b=yes;}}
{nl.join(lines)}
}}

drawTexts{{
{nl.join(texts)}
}}
"""


def make(unit_id, main_name=None, install=False):
    """Build one vehicle's sight. Returns (path, shell name, shells)."""
    unit = fetch(f"gamedata/units/tankmodels/{unit_id}.blkx")
    shells = available_shells(unit, fetch(main_gun_blk(unit)))
    if not shells:
        raise SystemExit("no gun shells (missile-only or unarmed)")
    main_name = main_name or best_shell(shells)
    if main_name not in shells:
        raise SystemExit(f"{main_name} not in {list(shells)}")
    blk = build(unit_id, main_name, shells, units_per_mil(unit), best_mg(unit),
                zoom_ratio(unit))
    safe = "".join(c if c.isalnum() or c in "_-." else "_" for c in main_name)
    out = HERE / "out" / unit_id / f"ranging_{safe}.blk"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(blk, encoding="utf-8")
    if install:
        for root in sight_roots():
            dest = root / unit_id / out.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(blk, encoding="utf-8")
    return out, main_name, shells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("unit", help="unit id, e.g. ussr_bmp_1")
    ap.add_argument("--main", help="bulletName to build for (default: best shell)")
    ap.add_argument("--install", action="store_true",
                    help="also copy into the game's UserSights folder")
    a = ap.parse_args()
    out, name, shells = make(a.unit, a.main, a.install)
    for n, sh in sorted(shells.items(), key=lambda kv: -kv[1][5]):
        print(f"  {'*' if n == name else ' '} {n:28} {sh[0]:22} {sh[1]:6.0f} m/s  ~{sh[5]:.0f} mm")
    print("wrote", out, "(installed)" if a.install else "")


if __name__ == "__main__":
    main()
