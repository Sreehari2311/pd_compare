#!/usr/bin/env python3
"""
pd_compare - OpenLane Run Comparison Tool
Usage (from inside the design directory):
    pd_compare <run1> <run2>

Example:
    cd ~/ic_design/OpenLane/designs/picorv32
    pd_compare run1 time_25
"""

import sys
import os
import csv
import re
from pathlib import Path

# ─────────────────────────────────────────────
# ANSI colours (plain text but with highlights)
# ─────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
DIM    = "\033[2m"

def col(text, colour): return f"{colour}{text}{RESET}"

# ─────────────────────────────────────────────
# METRICS.CSV parser
# ─────────────────────────────────────────────
def parse_metrics_csv(run_path: Path) -> dict:
    csv_file = run_path / "reports" / "metrics.csv"
    if not csv_file.exists():
        return {}
    with open(csv_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            return dict(row)   # only one data row
    return {}

# ─────────────────────────────────────────────
# MANUFACTURABILITY.RPT parser
# ─────────────────────────────────────────────
def parse_manufacturability(run_path: Path) -> dict:
    rpt = run_path / "reports" / "manufacturability.rpt"
    data = {}
    if not rpt.exists():
        return data
    text = rpt.read_text()

    m = re.search(r"Total Magic DRC violations is (\d+)", text)
    if m: data["drc_violations"] = int(m.group(1))

    m = re.search(r"Pin violations:\s*(\d+)", text)
    if m: data["antenna_pin_violations"] = int(m.group(1))

    m = re.search(r"Net violations:\s*(\d+)", text)
    if m: data["antenna_net_violations"] = int(m.group(1))

    if "Design is LVS clean." in text:
        data["lvs_status"] = "Clean"
    elif "LVS" in text:
        data["lvs_status"] = "ERRORS"

    return data

# ─────────────────────────────────────────────
# STA CHECKS RPT parser  (slew / cap / fanout)
# Tries rcx_sta first, then rsz, then grt
# ─────────────────────────────────────────────
def parse_sta_checks(run_path: Path) -> dict:
    candidates = [
        run_path / "reports" / "signoff" / "31-rcx_sta.checks.rpt",
        run_path / "reports" / "routing"  / "18-rsz_timing_sta.checks.rpt",
        run_path / "reports" / "routing"  / "16-rsz_design_sta.checks.rpt",
        run_path / "reports" / "routing"  / "21-grt_sta.checks.rpt",
    ]
    text = ""
    for c in candidates:
        if c.exists():
            text = c.read_text()
            break
    if not text:
        return {}

    data = {}

    # count slew violations
    slew_blocks = re.findall(
        r"(Max slew violation|slew\s+violation|VIOLATED.*slew)", text, re.IGNORECASE)
    # Better: count lines with "VIOLATED" that contain slew
    slew_lines = [l for l in text.splitlines()
                  if "slew" in l.lower() and ("violated" in l.lower() or "violation" in l.lower())]
    data["slew_violations"] = len(slew_lines)

    cap_lines = [l for l in text.splitlines()
                 if ("cap" in l.lower() or "capacitance" in l.lower())
                 and ("violated" in l.lower() or "violation" in l.lower())]
    data["cap_violations"] = len(cap_lines)

    fanout_lines = [l for l in text.splitlines()
                    if "fanout" in l.lower()
                    and ("violated" in l.lower() or "violation" in l.lower())]
    data["fanout_violations"] = len(fanout_lines)

    return data

# ─────────────────────────────────────────────
# STA SUMMARY parser  (WNS / TNS / setup/hold)
# ─────────────────────────────────────────────
def parse_sta_summary(run_path: Path) -> dict:
    candidates = [
        run_path / "reports" / "signoff" / "31-rcx_sta.summary.rpt",
        run_path / "reports" / "routing"  / "18-rsz_timing_sta.summary.rpt",
        run_path / "reports" / "routing"  / "21-grt_sta.summary.rpt",
    ]
    text = ""
    for c in candidates:
        if c.exists():
            text = c.read_text()
            break
    if not text:
        return {}

    data = {}
    for line in text.splitlines():
        wns_m = re.search(r"wns\s+([-\d.]+)", line, re.IGNORECASE)
        if wns_m: data.setdefault("wns", wns_m.group(1))
        tns_m = re.search(r"tns\s+([-\d.]+)", line, re.IGNORECASE)
        if tns_m: data.setdefault("tns", tns_m.group(1))
    return data

# ─────────────────────────────────────────────
# Combine all sources for one run
# ─────────────────────────────────────────────
def collect_run_data(run_path: Path) -> dict:
    d = {}
    d.update(parse_manufacturability(run_path))
    d.update(parse_sta_checks(run_path))
    d.update(parse_sta_summary(run_path))

    csv_data = parse_metrics_csv(run_path)
    # Pull key fields from metrics.csv (already have richer types)
    csv_map = {
        "flow_status":           "flow_status",
        "total_runtime":         "total_runtime",
        "synth_cell_count":      "synth_cell_count",
        "wire_length":           "wire_length",
        "vias":                  "vias",
        "wns":                   "wns",
        "tns":                   "tns",
        "spef_wns":              "spef_wns",
        "spef_tns":              "spef_tns",
        "tritonRoute_violations":"tritonRoute_violations",
        "Short_violations":      "short_violations",
        "pin_antenna_violations":"pin_antenna_violations",
        "net_antenna_violations":"net_antenna_violations",
        "DiodeCells":            "diode_cells",
        "TotalCells":            "total_cells",
        "critical_path_ns":      "critical_path_ns",
        "CLOCK_PERIOD":          "clock_period",
        "DIEAREA_mm^2":          "die_area_mm2",
        "Final_Util":            "final_utilisation",
        "MAX_FANOUT_CONSTRAINT": "max_fanout_constraint",
    }
    for csv_key, our_key in csv_map.items():
        if csv_key in csv_data and csv_data[csv_key] not in ("", "-1", "-"):
            d[our_key] = csv_data[csv_key]

    return d

# ─────────────────────────────────────────────
# Delta formatter
# ─────────────────────────────────────────────
def fmt_delta(a, b, lower_is_better=True, is_string=False):
    """Return (value_b_str, delta_str, colour)"""
    if is_string or a is None or b is None:
        same = str(a) == str(b)
        return str(b), ("(unchanged)" if same else f"(was: {a})"), (RESET if same else YELLOW)

    try:
        fa, fb = float(a), float(b)
    except (ValueError, TypeError):
        same = str(a) == str(b)
        return str(b), ("(unchanged)" if same else f"(was: {a})"), (RESET if same else YELLOW)

    delta = fb - fa
    if abs(delta) < 1e-9:
        return f"{fb:g}", "(no change)", DIM

    sign = "+" if delta > 0 else ""
    delta_str = f"({sign}{delta:g})"

    if lower_is_better:
        colour = GREEN if delta < 0 else RED
    else:
        colour = GREEN if delta > 0 else RED

    return f"{fb:g}", delta_str, colour

# ─────────────────────────────────────────────
# Print comparison table
# ─────────────────────────────────────────────
def print_comparison(run_a: str, run_b: str, data_a: dict, data_b: dict):
    W = 80
    print()
    print(col("=" * W, CYAN))
    print(col(f"  OpenLane Run Comparison", BOLD))
    print(col(f"  Baseline : {run_a}", DIM))
    print(col(f"  Compare  : {run_b}", DIM))
    print(col("=" * W, CYAN))

    sections = [
        ("FLOW STATUS", [
            ("Flow status",        "flow_status",           True,  True),
            ("Total runtime",      "total_runtime",         True,  True),
        ]),
        ("ANTENNA VIOLATIONS", [
            ("Pin antenna violations", "antenna_pin_violations", True,  False),
            ("Net antenna violations", "antenna_net_violations", True,  False),
        ]),
        ("DRC / LVS", [
            ("Magic DRC violations",   "drc_violations",         True,  False),
            ("TritonRoute violations",  "tritonRoute_violations", True,  False),
            ("Short violations",        "short_violations",       True,  False),
            ("LVS status",             "lvs_status",             True,  True),
        ]),
        ("TIMING  (post-route, RCX)", [
            ("WNS  (ns)",          "spef_wns",              True,  False),
            ("TNS  (ns)",          "spef_tns",              True,  False),
            ("Critical path (ns)", "critical_path_ns",      True,  False),
            ("Clock period (ns)",  "clock_period",          False, True),
        ]),
        ("SIGNAL INTEGRITY VIOLATIONS", [
            ("Slew violations",    "slew_violations",       True,  False),
            ("Cap violations",     "cap_violations",        True,  False),
            ("Fanout violations",  "fanout_violations",     True,  False),
            ("Max fanout constraint","max_fanout_constraint",False, True),
        ]),
        ("DESIGN METRICS", [
            ("Synth cell count",   "synth_cell_count",      False, False),
            ("Total cells",        "total_cells",           False, False),
            ("Diode cells",        "diode_cells",           True,  False),
            ("Wire length (um)",   "wire_length",           True,  False),
            ("Vias",               "vias",                  True,  False),
            ("Die area (mm²)",     "die_area_mm2",          False, True),
            ("Final utilisation%", "final_utilisation",     False, False),
        ]),
    ]

    COL1 = 30   # metric name
    COL2 = 14   # baseline value
    COL3 = 14   # new value
    COL4 = 18   # delta

    header = (f"  {'Metric':<{COL1}} {'Baseline':<{COL2}} {'New':<{COL3}} {'Delta':<{COL4}}")
    sep    = "  " + "-" * (COL1 + COL2 + COL3 + COL4 + 3)

    for section_name, rows in sections:
        print()
        print(col(f"  ── {section_name} ", BOLD))
        print(col(header, DIM))
        print(col(sep, DIM))

        any_row = False
        for label, key, lower_is_better, is_string in rows:
            va = data_a.get(key)
            vb = data_b.get(key)
            if va is None and vb is None:
                continue
            any_row = True
            val_b, delta_str, delta_col = fmt_delta(
                va, vb, lower_is_better=lower_is_better, is_string=is_string)
            va_str = str(va) if va is not None else "N/A"
            line = (f"  {label:<{COL1}} {va_str:<{COL2}} {val_b:<{COL3}}")
            print(line + col(f" {delta_str}", delta_col))

        if not any_row:
            print(col("  (no data found for this section)", DIM))

    print()
    print(col("=" * W, CYAN))
    print(col("  Legend: " + col("green = improved", GREEN) +
              "  " + col("red = degraded", RED) +
              "  " + col("yellow = changed (string)", YELLOW), DIM))
    print(col("=" * W, CYAN))
    print()

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    args = sys.argv[1:]

    # Flexible invocation:
    #   pd_compare run1 run2                     (inside design dir)
    #   pd_compare picorv32 run1 run2            (explicit design)
    #   pd_compare --design picorv32 run1 run2
    design_name = None
    if "--design" in args:
        idx = args.index("--design")
        design_name = args[idx + 1]
        args = args[:idx] + args[idx+2:]

    if len(args) == 3 and design_name is None:
        design_name, run_a, run_b = args
    elif len(args) == 2:
        run_a, run_b = args
    else:
        print(f"Usage: pd_compare <run1> <run2>")
        print(f"   or: pd_compare <design> <run1> <run2>")
        sys.exit(1)

    # Resolve base path
    cwd = Path.cwd()

    if design_name:
        # Try to find designs root
        # Look upward for a directory containing the design
        search_roots = [
            cwd,
            cwd.parent,
            Path.home() / "ic_design" / "OpenLane" / "designs",
            Path("/openlane/designs"),
        ]
        base = None
        for root in search_roots:
            candidate = root / design_name / "runs"
            if candidate.exists():
                base = candidate
                break
        if base is None:
            print(col(f"ERROR: Could not find design '{design_name}' in known paths.", RED))
            sys.exit(1)
    else:
        # Auto-detect: cwd should be inside the design folder
        # Accept: .../designs/<design>/   or   .../designs/<design>/runs/
        if cwd.name == "runs":
            base = cwd
        elif (cwd / "runs").exists():
            base = cwd / "runs"
        else:
            print(col("ERROR: Run this from inside your design directory, e.g.:", RED))
            print(col("  cd ~/ic_design/OpenLane/designs/picorv32", YELLOW))
            print(col("  pd_compare run1 time_25", YELLOW))
            sys.exit(1)

    path_a = base / run_a
    path_b = base / run_b

    for tag, p in [(run_a, path_a), (run_b, path_b)]:
        if not p.exists():
            print(col(f"ERROR: Run '{tag}' not found at {p}", RED))
            sys.exit(1)

    print(col(f"\nParsing {run_a} ...", DIM), end="\r")
    data_a = collect_run_data(path_a)
    print(col(f"Parsing {run_b} ...", DIM), end="\r")
    data_b = collect_run_data(path_b)
    print(" " * 40, end="\r")   # clear line

    print_comparison(run_a, run_b, data_a, data_b)

if __name__ == "__main__":
    main()
