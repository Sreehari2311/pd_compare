#!/usr/bin/env python3
"""pd_compare v2.0 — OpenLane run comparison tool with config diff"""

import re
import sys
import csv
import glob
import argparse
from pathlib import Path
from io import StringIO

# ── ANSI colours ────────────────────────────────────────────────────────────
R  = "\033[91m"; G  = "\033[92m"; Y  = "\033[93m"
C  = "\033[96m"; DIM= "\033[2m";  B  = "\033[1m"
RST= "\033[0m";  MAG= "\033[95m"

# ── OutputWriter ─────────────────────────────────────────────────────────────
class OutputWriter:
    def __init__(self, save: bool, fname: str):
        self.save = save
        self.fname = fname
        self.buf   = StringIO() if save else None

    def _strip(self, s: str) -> str:
        return re.sub(r'\033\[[0-9;]*m', '', s)

    def p(self, s: str = ""):
        print(s)
        if self.buf:
            self.buf.write(self._strip(s) + "\n")

    def flush(self):
        if self.save and self.buf:
            with open(self.fname, "w") as f:
                f.write(self.buf.getvalue())
            print(f"\n{G}Report saved → {self.fname}{RST}")

# ── helpers ──────────────────────────────────────────────────────────────────
def _glob1(pattern: str):
    hits = glob.glob(pattern)
    return hits[0] if hits else None

def _read(path) -> str:
    try:
        return Path(path).read_text(errors='replace') if path else ""
    except Exception:
        return ""

def _fmt_runtime(s: str) -> str:
    m = re.search(r'(\d+)h(\d+)m(\d+)s', s)
    if m:
        return f"{m.group(1)}h{m.group(2)}m{m.group(3)}s"
    return s or "N/A"

def _safe_float(v, default=None):
    try:
        return float(v)
    except Exception:
        return default

def _safe_int(v, default=None):
    try:
        return int(float(v))
    except Exception:
        return default

# ── parse config_in.tcl ──────────────────────────────────────────────────────
SKIP_INFRA = {
    "PDK","PDKPATH","STD_CELL_LIBRARY","SCLPATH","DESIGN_DIR","DESIGN_NAME",
    "VERILOG_FILES","DESIGN_IS_CORE","CLOCK_PORT","EXTRA_LEFS","EXTRA_GDS_FILES",
    "VERILOG_FILES_BLACKBOX","EXTRA_LIBS","EXTRA_SPEFS"
}

TIMING_CRITICAL = {
    "CLOCK_PERIOD","MAX_FANOUT_CONSTRAINT","MAX_TRANSITION_CONSTRAINT",
    "MAX_CAPACITANCE_CONSTRAINT","SYNTH_MAX_FANOUT","SYNTH_MAX_TRAN",
    "SYNTH_CLOCK_UNCERTAINTY","SYNTH_CLOCK_TRANSITION","SYNTH_TIMING_DERATE",
    "SYNTH_STRATEGY","CLOCK_UNCERTAINTY","CLOCK_TRANSITION"
}

FLOORPLAN_PARAMS = {
    "FP_CORE_UTIL","FP_ASPECT_RATIO","FP_PDN_HPITCH","FP_PDN_VPITCH",
    "FP_PDN_HOFFSET","FP_PDN_VOFFSET","DIE_AREA","CORE_AREA"
}

ROUTING_PARAMS = {
    "GRT_ADJUSTMENT","GRT_ANTENNA_ITERS","GRT_LAYER_ADJUSTMENTS",
    "DRT_THREADS","ROUTING_CORES","PL_TARGET_DENSITY","PL_RANDOM_GLB_PLACEMENT"
}

def parse_config(run_path: Path) -> dict:
    cfg = run_path / "config_in.tcl"
    if not cfg.exists():
        return {}
    result = {}
    for line in cfg.read_text(errors='replace').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = re.match(r'set\s+::env\((\w+)\)\s+"?(.*?)"?\s*$', line)
        if m:
            key = m.group(1)
            val = m.group(2).strip('"').strip()
            if key not in SKIP_INFRA:
                result[key] = val
    return result

def diff_configs(cfg_a: dict, cfg_b: dict):
    all_keys = sorted(set(cfg_a) | set(cfg_b))
    changed, added, removed = [], [], []
    for k in all_keys:
        if k in cfg_a and k in cfg_b:
            if cfg_a[k] != cfg_b[k]:
                changed.append((k, cfg_a[k], cfg_b[k]))
        elif k in cfg_b:
            added.append((k, cfg_b[k]))
        else:
            removed.append((k, cfg_a[k]))
    return changed, added, removed

def _param_category(key: str) -> str:
    if key in TIMING_CRITICAL:  return "TIMING"
    if key in FLOORPLAN_PARAMS: return "FLOORPLAN"
    if key in ROUTING_PARAMS:   return "ROUTING"
    return "OTHER"

# ── data collection ───────────────────────────────────────────────────────────
def collect_run_data(run_dir: Path) -> dict:
    d: dict = {}
    rpt_dir  = run_dir / "reports"
    sign_dir = rpt_dir / "signoff"

    # manufacturability.rpt
    mfg_txt = _read(_glob1(str(rpt_dir / "manufacturability.rpt")) or
                    str(rpt_dir / "manufacturability.rpt"))
    d['flow_status'] = "flow completed" if "flow completed" in mfg_txt.lower() else \
                       ("flow failed"   if "flow failed"    in mfg_txt.lower() else "unknown")
    m = re.search(r'Total Magic DRC violations is (\d+)', mfg_txt)
    d['magic_drc'] = int(m.group(1)) if m else None
    m = re.search(r'Total Short violations is (\d+)', mfg_txt)
    d['shorts'] = int(m.group(1)) if m else 0
    m = re.search(r'tritonRoute violations (\d+)', mfg_txt, re.I)
    if not m:
        m = re.search(r'TritonRoute violations[:\s]+(\d+)', mfg_txt, re.I)
    d['triton_drc'] = int(m.group(1)) if m else 0
    m = re.search(r'Design is LVS clean', mfg_txt)
    d['lvs'] = "Clean" if m else "Issues"
    m = re.search(r'Pin violations:\s*(\d+)', mfg_txt)
    d['pin_ant'] = int(m.group(1)) if m else None
    m = re.search(r'Net violations:\s*(\d+)', mfg_txt)
    d['net_ant'] = int(m.group(1)) if m else None

    # metrics.csv
    metrics_file = _glob1(str(rpt_dir / "metrics.csv"))
    if metrics_file:
        try:
            with open(metrics_file) as f:
                reader = csv.DictReader(f)
                row = next(reader, {})
            if d.get('flow_status','unknown') == 'unknown':
                d['flow_status'] = row.get('flow_status','unknown').strip()
            d['runtime']   = _fmt_runtime(row.get('total_runtime',''))
            d['synth_cells'] = _safe_int(row.get('synth_cell_count',''))
            d['total_cells'] = _safe_int(row.get('TotalCells',''))
            d['diode_cells'] = _safe_int(row.get('DiodeCells',''))
            d['wire_length'] = _safe_int(row.get('wire_length',''))
            d['vias']        = _safe_int(row.get('vias',''))
            d['die_area']    = _safe_float(row.get('DIEAREA_mm^2',''))
            d['critical_path'] = _safe_float(row.get('critical_path_ns',''))
            d['clock_period']  = row.get('CLOCK_PERIOD','').strip()
            d['max_fanout_constraint'] = row.get('MAX_FANOUT_CONSTRAINT','').strip()
            d['utilization'] = _safe_float(row.get('Final_Util',''))
        except Exception:
            pass

    # STA summary → setup/hold slack, WNS, TNS
    sta_sum = _glob1(str(sign_dir / "*rcx_sta.summary.rpt"))
    if not sta_sum:
        sta_sum = _glob1(str(sign_dir / "*sta.summary.rpt"))
    if sta_sum:
        txt = _read(sta_sum)
        lines = txt.splitlines()
        context = None
        for line in lines:
            if 'report_worst_slack' in line and '-max' in line:
                context = 'setup'
            elif 'report_worst_slack' in line and '-min' in line:
                context = 'hold'
            elif 'report_wns' in line:
                context = 'wns'
            elif 'report_tns' in line:
                context = 'tns'
            m = re.search(r'worst slack\s+(-?[\d.]+)', line)
            if m:
                val = float(m.group(1))
                if context == 'setup': d['setup_slack'] = val
                elif context == 'hold': d['hold_slack'] = val
                context = None
            m2 = re.search(r'^wns\s+(-?[\d.]+)', line)
            if m2: d['wns'] = float(m2.group(1))
            m3 = re.search(r'^tns\s+(-?[\d.]+)', line)
            if m3: d['tns'] = float(m3.group(1))

    # MCMM corners → min / max / nom
    def _parse_corner_summary(path):
        if not path: return {}
        txt = _read(path); ctx = None; res = {}
        for line in txt.splitlines():
            if 'report_worst_slack' in line and '-max' in line: ctx = 'setup'
            elif 'report_worst_slack' in line and '-min' in line: ctx = 'hold'
            elif 'report_wns' in line: ctx = 'wns'
            elif 'report_tns' in line: ctx = 'tns'
            m = re.search(r'worst slack\s+(-?[\d.]+)', line)
            if m:
                val = float(m.group(1))
                if ctx == 'setup': res['setup'] = val
                elif ctx == 'hold': res['hold'] = val
                ctx = None
            m2 = re.search(r'^wns\s+(-?[\d.]+)', line)
            if m2: res['wns'] = float(m2.group(1))
            m3 = re.search(r'^tns\s+(-?[\d.]+)', line)
            if m3: res['tns'] = float(m3.group(1))
        return res

    d['corner_min'] = _parse_corner_summary(
        _glob1(str(sign_dir / "*/rcx_min_sta.summary.rpt")))
    d['corner_max'] = _parse_corner_summary(
        _glob1(str(sign_dir / "*/rcx_max_sta.summary.rpt")))
    d['corner_nom'] = _parse_corner_summary(
        _glob1(str(sign_dir / "*/rcx_nom_sta.summary.rpt")))

    def _parse_corner_checks(path):
        if not path: return {}
        txt = _read(path); res = {}
        m = re.search(r'max slew violation count\s+(\d+)', txt, re.I)
        if m: res['slew'] = int(m.group(1))
        m = re.search(r'max cap violation count\s+(\d+)', txt, re.I)
        if m: res['cap'] = int(m.group(1))
        m = re.search(r'max fanout violation count\s+(\d+)', txt, re.I)
        if m: res['fanout'] = int(m.group(1))
        return res

    d['corner_min'].update(_parse_corner_checks(
        _glob1(str(sign_dir / "*/rcx_min_sta.checks.rpt"))))
    d['corner_max'].update(_parse_corner_checks(
        _glob1(str(sign_dir / "*/rcx_max_sta.checks.rpt"))))
    d['corner_nom'].update(_parse_corner_checks(
        _glob1(str(sign_dir / "*/rcx_nom_sta.checks.rpt"))))

    # STA checks → slew/cap/fanout violations
    sta_chk = _glob1(str(sign_dir / "*rcx_sta.checks.rpt"))
    if not sta_chk:
        sta_chk = _glob1(str(sign_dir / "*sta.checks.rpt"))
    if sta_chk:
        txt = _read(sta_chk)
        m = re.search(r'max slew violation count\s+(\d+)', txt, re.I)
        d['slew_viol'] = int(m.group(1)) if m else 0
        m = re.search(r'max cap violation count\s+(\d+)', txt, re.I)
        d['cap_viol'] = int(m.group(1)) if m else 0
        m = re.search(r'max fanout violation count\s+(\d+)', txt, re.I)
        d['fanout_viol'] = int(m.group(1)) if m else 0

        # per-pin fanout data for fingerprinting
        fanout_pins = {}
        in_fo_section = False
        for line in txt.splitlines():
            if 'max fanout' in line.lower() and 'Pin' not in line and 'Limit' not in line:
                in_fo_section = True; continue
            if in_fo_section:
                m2 = re.match(
                    r'\s*(\S+)\s+(\d+)\s+(\d+)\s+(-?\d+)\s+\(VIOLATED\)', line)
                if m2:
                    pin, limit, fanout, slack = m2.groups()
                    fanout_pins[pin] = {
                        'limit': int(limit), 'fanout': int(fanout), 'slack': int(slack)}
                elif line.strip() == '':
                    in_fo_section = False
        d['fanout_pins'] = fanout_pins

    # antenna violators
    ant_file = _glob1(str(sign_dir / "*antenna_violators.rpt"))
    if ant_file:
        ant_nets = {}
        for line in _read(ant_file).splitlines():
            m = re.search(
                r'Partial/Required:\s+([\d.]+).*?Net:\s+(\S+),\s+Pin:\s+(\S+),\s+Layer:\s+(\S+)', line)
            if m:
                ratio, net, pin, layer = m.groups()
                ant_nets[f"{net}::{pin}"] = {
                    'net': net, 'pin': pin,
                    'layer': layer, 'ratio': float(ratio)}
        d['ant_nets'] = ant_nets

    # cell types from arc.log
    arc_log = _glob1(str(run_dir / "logs" / "signoff" / "*arc.log"))
    cell_map = {}
    if arc_log:
        for line in _read(arc_log).splitlines():
            m = re.match(r'\s*(\S+)\s+\((\S+)\)\s+.*Layer.*Ratio', line)
            if m:
                cell_map[m.group(1)] = m.group(2)
    d['cell_map'] = cell_map

    return d

# ── config diff display ───────────────────────────────────────────────────────
def print_config_diff(w, run_a: str, run_b: str,
                      path_a: Path, path_b: Path):
    cfg_a = parse_config(path_a)
    cfg_b = parse_config(path_b)

    if not cfg_a and not cfg_b:
        return  # no config_in.tcl in either run, skip silently

    changed, added, removed = diff_configs(cfg_a, cfg_b)
    total_changes = len(changed) + len(added) + len(removed)

    w.p(f"\n  {B}-- CONFIG CHANGES{RST}  "
        f"{DIM}(config_in.tcl: {run_a} → {run_b}){RST}")

    if total_changes == 0:
        w.p(f"  {DIM}No parameter changes between runs.{RST}")
        return

    name_a = run_a[:10]
    name_b = run_b[:10]

    # header row
    w.p(f"  {'Parameter':<38}  {'Baseline':<20}  {'New':<20}")
    w.p("  " + "-"*80)

    def _row(key, va, vb, action):
        cat = _param_category(key)
        kc = f"{R}{B}" if cat == "TIMING" else (Y if action == "changed" else G if action == "added" else R)
        if action == "changed":
            w.p(f"  {kc}{key:<38}{RST}  {DIM}{va:<20}{RST}  {G}{vb:<20}{RST}")
        elif action == "added":
            w.p(f"  {kc}{key:<38}{RST}  {DIM}{'(not set)':<20}{RST}  {G}{vb:<20}{RST}")
        elif action == "removed":
            w.p(f"  {kc}{key:<38}{RST}  {DIM}{va:<20}{RST}  {R}{'(removed)':<20}{RST}")

    # group by category for readability
    all_rows = (
        [(k, va, vb, "changed")  for k,va,vb in changed] +
        [(k, "",  v,  "added")   for k,v    in added]   +
        [(k, v,   "",  "removed") for k,v    in removed]
    )
    for cat_label in ["TIMING", "FLOORPLAN", "ROUTING", "OTHER"]:
        rows = [(k,va,vb,act) for k,va,vb,act in all_rows
                if _param_category(k) == cat_label]
        if rows:
            for k,va,vb,act in rows:
                _row(k, va, vb, act)

    # summary line
    timing_count = sum(1 for k,*_ in all_rows if _param_category(k) == "TIMING")
    parts = []
    if changed: parts.append(f"{Y}{len(changed)} changed{RST}")
    if added:   parts.append(f"{G}{len(added)} added{RST}")
    if removed: parts.append(f"{R}{len(removed)} removed{RST}")
    w.p(f"\n  {DIM}Config summary:{RST}  " + "  ".join(parts))
    if timing_count:
        w.p(f"  {R}{B}⚠  {timing_count} timing-critical parameter(s) changed "
            f"— slack deltas may reflect constraint change, not just implementation.{RST}")

# ── comparison print ──────────────────────────────────────────────────────────
def _delta(a, b, higher_is_better=True, fmt=None, show_was=False):
    if a is None or b is None:
        return DIM + "(N/A)" + RST
    if isinstance(a, float) or isinstance(b, float):
        diff = b - a
        if fmt == 'area':
            ds = f"{diff:+.4f}"
            val_s = f"({ds})"
        elif fmt == 'float':
            ds = f"{diff:+.3f}"
            val_s = f"({ds})"
        else:
            ds = f"{diff:+.1f}"
            val_s = f"({ds})"
        if diff == 0:
            return DIM + "(no change)" + RST
        color = G if (diff > 0) == higher_is_better else R
        return color + val_s + RST
    elif isinstance(a, int) and isinstance(b, int):
        diff = b - a
        if diff == 0:
            return DIM + "(no change)" + RST
        color = G if (diff < 0) == (not higher_is_better) else R
        if higher_is_better:
            color = G if diff > 0 else R
        else:
            color = G if diff < 0 else R
        return color + f"({diff:+d})" + RST
    else:
        if str(a) == str(b):
            return DIM + "(no change)" + RST
        if show_was:
            return Y + f"(was: {a})" + RST
        return Y + f"(was: {a})" + RST

def _strip_ansi(s):
    return re.sub(r'\x1b\[[0-9;]*m', '', s)

def _row(w, label, va, vb, delta_s, width=30):
    la = str(va) if va is not None else "N/A"
    lb = str(vb) if vb is not None else "N/A"
    # pad by visible length so ANSI codes don't break column alignment
    la_pad = la + " " * max(0, 14 - len(_strip_ansi(la)))
    lb_pad = lb + " " * max(0, 14 - len(_strip_ansi(lb)))
    w.p(f"  {label:<{width}}  {la_pad} {lb_pad} {delta_s}")

def print_comparison(w, run_a, run_b, da, db):
    W = "="*80
    w.p(f"\n{W}")
    w.p(f"  {B}OpenLane Run Comparison  v2.0{RST}")
    w.p(f"  Baseline : {C}{run_a}{RST}")
    w.p(f"  Compare  : {C}{run_b}{RST}")
    w.p(W)

    # FLOW STATUS
    w.p(f"\n  {B}-- FLOW STATUS{RST}")
    w.p(f"  {'Metric':<30}  {'Baseline':<14} {'New':<14} {'Delta'}")
    w.p("  " + "-"*79)
    fs_a = da.get('flow_status','unknown'); fs_b = db.get('flow_status','unknown')
    _row(w, "Flow status", fs_a, fs_b,
         DIM+"(no change)"+RST if fs_a==fs_b else Y+f"(was: {fs_a})"+RST)
    _row(w, "Total runtime",
         da.get('runtime','N/A'), db.get('runtime','N/A'),
         Y+f"(was: {da.get('runtime','N/A')})"+RST
         if da.get('runtime') != db.get('runtime') else DIM+"(no change)"+RST)

    # ANTENNA VIOLATIONS
    w.p(f"\n  {B}-- ANTENNA VIOLATIONS{RST}")
    w.p(f"  {'Metric':<30}  {'Baseline':<14} {'New':<14} {'Delta'}")
    w.p("  " + "-"*79)
    _row(w, "Pin antenna violations", da.get('pin_ant'), db.get('pin_ant'),
         _delta(da.get('pin_ant'), db.get('pin_ant'), higher_is_better=False))
    _row(w, "Net antenna violations", da.get('net_ant'), db.get('net_ant'),
         _delta(da.get('net_ant'), db.get('net_ant'), higher_is_better=False))

    # DRC / LVS
    w.p(f"\n  {B}-- DRC / LVS{RST}")
    w.p(f"  {'Metric':<30}  {'Baseline':<14} {'New':<14} {'Delta'}")
    w.p("  " + "-"*79)
    _row(w, "Magic DRC violations", da.get('magic_drc'), db.get('magic_drc'),
         _delta(da.get('magic_drc'), db.get('magic_drc'), higher_is_better=False))
    _row(w, "TritonRoute violations", da.get('triton_drc'), db.get('triton_drc'),
         _delta(da.get('triton_drc'), db.get('triton_drc'), higher_is_better=False))
    _row(w, "Short violations", da.get('shorts'), db.get('shorts'),
         _delta(da.get('shorts'), db.get('shorts'), higher_is_better=False))
    lvs_a = da.get('lvs','N/A'); lvs_b = db.get('lvs','N/A')
    _row(w, "LVS status", lvs_a, lvs_b,
         DIM+"(no change)"+RST if lvs_a==lvs_b else Y+f"(was: {lvs_a})"+RST)

    # TIMING
    w.p(f"\n  {B}-- TIMING  (post-route, RCX -- Typical Corner){RST}")
    w.p(f"  {'Metric':<30}  {'Baseline':<14} {'New':<14} {'Delta'}")
    w.p("  " + "-"*79)
    ss_a = da.get('setup_slack'); ss_b = db.get('setup_slack')
    hs_a = da.get('hold_slack'); hs_b = db.get('hold_slack')
    wns_a= da.get('wns',0.0); wns_b= db.get('wns',0.0)
    tns_a= da.get('tns',0.0); tns_b= db.get('tns',0.0)
    cp_a = da.get('critical_path'); cp_b = db.get('critical_path')
    ck_a = da.get('clock_period',''); ck_b = db.get('clock_period','')

    _row(w, "Setup slack (ns)",
         f"{ss_a:.3f}" if ss_a is not None else "N/A",
         f"{ss_b:.3f}" if ss_b is not None else "N/A",
         _delta(ss_a, ss_b, higher_is_better=True, fmt='float'))
    _row(w, "Hold slack  (ns)",
         f"{hs_a:.3f}" if hs_a is not None else "N/A",
         f"{hs_b:.3f}" if hs_b is not None else "N/A",
         _delta(hs_a, hs_b, higher_is_better=True, fmt='float'))

    wns_both_zero = (wns_a == 0.0 and wns_b == 0.0)
    wns_delta = DIM+"(no change)"+RST if wns_both_zero else \
        _delta(wns_a, wns_b, higher_is_better=True, fmt='float')
    tns_delta = DIM+"(no change)"+RST if (tns_a==0.0 and tns_b==0.0) else \
        _delta(tns_a, tns_b, higher_is_better=True, fmt='float')
    _row(w, "WNS  (ns)",
         DIM+f"{wns_a:.3f}"+RST if wns_both_zero else f"{wns_a:.3f}",
         DIM+f"{wns_b:.3f}"+RST if wns_both_zero else f"{wns_b:.3f}",
         wns_delta)
    _row(w, "TNS  (ns)",
         DIM+f"{tns_a:.3f}"+RST if (tns_a==0.0 and tns_b==0.0) else f"{tns_a:.3f}",
         DIM+f"{tns_b:.3f}"+RST if (tns_a==0.0 and tns_b==0.0) else f"{tns_b:.3f}",
         tns_delta)
    _row(w, "Critical path (ns)",
         f"{cp_a:.3f}" if cp_a is not None else "N/A",
         f"{cp_b:.3f}" if cp_b is not None else "N/A",
         _delta(cp_a, cp_b, higher_is_better=False, fmt='float'))
    _row(w, "Clock period (ns)", ck_a or "N/A", ck_b or "N/A",
         DIM+"(no change)"+RST if ck_a==ck_b else Y+f"(was: {ck_a})"+RST)

    # MCMM corners
    corner_defs = [
        ('corner_min', 'Min  (rcx_min -- best RC)'),
        ('corner_nom', 'Nom  (rcx_nom -- nominal RC)'),
        ('corner_max', 'Max  (rcx_max -- worst RC)'),
    ]
    has_mcmm = any(da.get(k) or db.get(k) for k,_ in corner_defs)
    if has_mcmm:
        w.p(f"\n  {B}-- TIMING  (MCMM corners){RST}")
        w.p(f"  {'Corner / Metric':<34}  {'Baseline':<14} {'New':<14} {'Delta'}")
        w.p("  " + "-"*79)
        for key, label in corner_defs:
            ca = da.get(key, {}); cb = db.get(key, {})
            if not ca and not cb:
                continue
            w.p(f"  {DIM}{label}{RST}")
            setup_a = ca.get('setup'); setup_b = cb.get('setup')
            hold_a  = ca.get('hold');  hold_b  = cb.get('hold')
            wns_a2  = ca.get('wns',0); wns_b2  = cb.get('wns',0)
            tns_a2  = ca.get('tns',0); tns_b2  = cb.get('tns',0)
            _row(w, "    Setup slack (ns)",
                 f"{setup_a:.3f}" if setup_a is not None else "N/A",
                 f"{setup_b:.3f}" if setup_b is not None else "N/A",
                 _delta(setup_a, setup_b, higher_is_better=True, fmt='float'))
            _row(w, "    Hold slack  (ns)",
                 f"{hold_a:.3f}" if hold_a is not None else "N/A",
                 f"{hold_b:.3f}" if hold_b is not None else "N/A",
                 _delta(hold_a, hold_b, higher_is_better=True, fmt='float'))
            _row(w, "    WNS  (ns)",
                 f"{wns_a2:.3f}", f"{wns_b2:.3f}",
                 _delta(wns_a2, wns_b2, higher_is_better=True, fmt='float'))
            _row(w, "    TNS  (ns)",
                 f"{tns_a2:.3f}", f"{tns_b2:.3f}",
                 _delta(tns_a2, tns_b2, higher_is_better=True, fmt='float'))
            slew_a = ca.get('slew'); slew_b = cb.get('slew')
            cap_a  = ca.get('cap');  cap_b  = cb.get('cap')
            fo_a2  = ca.get('fanout'); fo_b2 = cb.get('fanout')
            if any(v is not None for v in [slew_a, slew_b, cap_a, cap_b, fo_a2, fo_b2]):
                _row(w, "    Slew violations",
                     str(slew_a) if slew_a is not None else "N/A",
                     str(slew_b) if slew_b is not None else "N/A",
                     _delta(slew_a, slew_b, higher_is_better=False))
                _row(w, "    Cap violations",
                     str(cap_a) if cap_a is not None else "N/A",
                     str(cap_b) if cap_b is not None else "N/A",
                     _delta(cap_a, cap_b, higher_is_better=False))
                _row(w, "    Fanout violations",
                     str(fo_a2) if fo_a2 is not None else "N/A",
                     str(fo_b2) if fo_b2 is not None else "N/A",
                     _delta(fo_a2, fo_b2, higher_is_better=False))

    # SIGNAL INTEGRITY
    w.p(f"\n  {B}-- SIGNAL INTEGRITY VIOLATIONS{RST}")
    w.p(f"  {'Metric':<30}  {'Baseline':<14} {'New':<14} {'Delta'}")
    w.p("  " + "-"*79)
    _row(w, "Slew violations", da.get('slew_viol'), db.get('slew_viol'),
         _delta(da.get('slew_viol'), db.get('slew_viol'), higher_is_better=False))
    _row(w, "Cap violations", da.get('cap_viol'), db.get('cap_viol'),
         _delta(da.get('cap_viol'), db.get('cap_viol'), higher_is_better=False))
    _row(w, "Fanout violations", da.get('fanout_viol'), db.get('fanout_viol'),
         _delta(da.get('fanout_viol'), db.get('fanout_viol'), higher_is_better=False))
    fo_a = da.get('max_fanout_constraint',''); fo_b = db.get('max_fanout_constraint','')
    _row(w, "Max fanout constraint", fo_a or "N/A", fo_b or "N/A",
         DIM+"(no change)"+RST if fo_a==fo_b else Y+f"(was: {fo_a})"+RST)

    # DESIGN METRICS
    w.p(f"\n  {B}-- DESIGN METRICS{RST}")
    w.p(f"  {'Metric':<30}  {'Baseline':<14} {'New':<14} {'Delta'}")
    w.p("  " + "-"*79)
    _row(w, "Synth cell count", da.get('synth_cells'), db.get('synth_cells'),
         _delta(da.get('synth_cells'), db.get('synth_cells'), higher_is_better=False))
    _row(w, "Total cells", da.get('total_cells'), db.get('total_cells'),
         _delta(da.get('total_cells'), db.get('total_cells'), higher_is_better=False))
    _row(w, "Diode cells", da.get('diode_cells'), db.get('diode_cells'),
         _delta(da.get('diode_cells'), db.get('diode_cells'), higher_is_better=False))
    _row(w, "Wire length (um)", da.get('wire_length'), db.get('wire_length'),
         _delta(da.get('wire_length'), db.get('wire_length'), higher_is_better=False))
    _row(w, "Vias", da.get('vias'), db.get('vias'),
         _delta(da.get('vias'), db.get('vias'), higher_is_better=False))
    da_a = da.get('die_area'); da_b = db.get('die_area')
    _row(w, "Die area (mm2)",
         f"{da_a:.4f}" if da_a is not None else "N/A",
         f"{da_b:.4f}" if da_b is not None else "N/A",
         _delta(da_a, da_b, higher_is_better=False, fmt='area'))

    # legend
    w.p(f"\n{'='*80}")
    w.p(f"  {DIM}Legend : {G}green = improved{RST}  {DIM}{R}red = degraded{RST}  "
        f"{DIM}{Y}yellow = changed{RST}  {DIM}dim = no change / informational{RST}")
    w.p(f"  {DIM}Timing : higher slack = more margin (setup > 0 and hold > 0 = clean){RST}")
    w.p(f"{'='*80}")

# ── fingerprint ───────────────────────────────────────────────────────────────
def _ant_severity(ratio: float) -> str:
    if ratio > 2.5: return "SEVERE"
    if ratio > 1.5: return "MODERATE"
    return "MILD"

def _ant_remedy(ratio, layer, pin, cell_type, cell_map) -> list:
    sev   = _ant_severity(ratio)
    tips  = []
    cell  = cell_map.get(pin.split('/')[0], cell_type or "")
    if sev == "SEVERE":
        tips.append(f"SEVERE ({ratio:.2f}x) -- Break net before {pin}; "
                    "insert sky130_fd_sc_hd__buf_2 and add DIODE_INSERTION_STRATEGY=4")
    elif sev == "MODERATE":
        tips.append(f"MODERATE ({ratio:.2f}x) -- Layer hop to higher metal "
                    f"(use via stacking on {layer}); or insert diode at {pin}")
    else:
        tips.append(f"MILD ({ratio:.2f}x) -- Insert diode at pin {pin} "
                    "to bleed off charge (DIODE_INSERTION_STRATEGY=3 or 4)")

    if layer == "met1":
        tips.append("met1 tightest antenna rules in sky130 -- move to met2 sooner, "
                    "or reduce met1 usage via GRT_LAYER_ADJUSTMENTS")
    elif layer in ("met2","met3"):
        tips.append(f"{layer} moderate rules -- break net at this layer with buffer insertion")

    if "mux" in cell.lower():
        tips.append(f"MUX cell ({cell}) -- insert buf_2 before select input; "
                    "MUX antenna accumulates on both data and select paths")
    elif "dff" in cell.lower() or "dfxtp" in cell.lower() or "sdff" in cell.lower():
        tips.append(f"Flip-flop ({cell}) -- add jumper diode on D-input; "
                    "flop D is high-impedance during antenna accumulation window")
    elif "buf" in cell.lower() or "clkbuf" in cell.lower():
        tips.append(f"Buffer/clkbuf ({cell}) -- safe to break net here; "
                    "replace with buf_8 for better drive strength")
    elif cell:
        tips.append(f"Logic gate ({cell}) -- safe to break net before this gate; "
                    "insert sky130_fd_sc_hd__buf_2 to split antenna accumulation")
    return tips

def _fo_severity(slack: int) -> str:
    if slack <= -16: return "SEVERE"
    if slack <= -8:  return "MODERATE"
    return "MILD"

def _fo_category(pin: str) -> str:
    p = pin.lower()
    if "/q" in p: return "flop"
    if p.startswith("wire"): return "wire"
    if p.startswith("fanout"): return "fanout"
    if p.startswith("max_length"): return "wire"
    if p.startswith("load_slew"): return "wire"
    return "logic"

def fingerprint_antenna(da, db):
    nets_a = da.get('ant_nets', {}); nets_b = db.get('ant_nets', {})
    all_keys = set(nets_a) | set(nets_b)
    fixed, new_viol, worsened, persistent = [], [], [], []
    for k in all_keys:
        if k in nets_a and k not in nets_b:
            fixed.append(nets_a[k])
        elif k not in nets_a and k in nets_b:
            new_viol.append(nets_b[k])
        else:
            ra = nets_a[k]['ratio']; rb = nets_b[k]['ratio']
            if rb > ra * 1.05:
                worsened.append({**nets_b[k], 'prev_ratio': ra})
            else:
                persistent.append(nets_b[k])
    return fixed, new_viol, worsened, persistent

def fingerprint_fanout(da, db):
    pins_a = da.get('fanout_pins', {}); pins_b = db.get('fanout_pins', {})
    all_pins = set(pins_a) | set(pins_b)
    fixed, new_viol, worsened, persistent = [], [], [], []
    for p in all_pins:
        if p in pins_a and p not in pins_b:
            fixed.append(pins_a[p])
        elif p not in pins_a and p in pins_b:
            new_viol.append({**pins_b[p], 'pin': p})
        else:
            sa = pins_a[p]['slack']; sb = pins_b[p]['slack']
            if sb < sa - 2:
                worsened.append({**pins_b[p], 'pin': p, 'prev_slack': sa})
            else:
                persistent.append({**pins_b[p], 'pin': p})
    return fixed, new_viol, worsened, persistent


def print_fingerprint(w, run_a, run_b, da, db):
    W2 = "="*90
    w.p(f"\n{W2}")
    w.p(f"  {B}ANTENNA VIOLATION FINGERPRINT{RST}")
    w.p(f"  Baseline : {C}{run_a}{RST}   ->   Compare : {C}{run_b}{RST}")
    w.p(W2)

    fixed_a, new_a, worsened_a, persistent_a = fingerprint_antenna(da, db)
    cell_map_b = db.get('cell_map', {})

    # ── summary line ─────────────────────────────────────────────────────────
    w.p(f"\n  {G}OK {len(fixed_a)} fixed{RST}    "
        f"{R}XX {len(new_a)} new{RST}    "
        f"{Y}UP {len(worsened_a)} worsened{RST}    "
        f"{'!! ' + str(len(persistent_a)) + ' persistent' if persistent_a else DIM + '!! 0 persistent' + RST}")

    # ── FIXED ─────────────────────────────────────────────────────────────────
    if fixed_a:
        w.p(f"\n  {DIM}-- FIXED ({len(fixed_a)}) -- these nets were resolved in {run_b}{RST}")
        for v in fixed_a:
            cell = cell_map_b.get(v['pin'].split('/')[0], "")
            cell_s = f"  ({cell})" if cell else ""
            w.p(f"  {G}OK{RST}  Net: {C}{v['net']:<20}{RST}  "
                f"Pin: {C}{v['pin']:<25}{RST}  "
                f"Layer: {v['layer']}   Ratio: {v['ratio']:.2f}x{cell_s}")

    # ── NEW ───────────────────────────────────────────────────────────────────
    if new_a:
        w.p(f"\n  {R}-- NEW ({len(new_a)}) -- these violations appeared in {run_b}{RST}")
        for v in new_a:
            net = v['net']; pin = v['pin']
            layer = v['layer']; ratio = v['ratio']
            sev = _ant_severity(ratio)
            cell = cell_map_b.get(pin.split('/')[0], "")
            cell_s = f"  ({cell})" if cell else ""
            sev_color = R if sev=="SEVERE" else Y if sev=="MODERATE" else G
            w.p(f"  {R}NEW{RST}  Net: {C}{net:<20}{RST}  "
                f"Pin: {C}{pin:<25}{RST}  "
                f"Layer: {layer}   Ratio: {sev_color}{ratio:.2f}x  [{sev}]{RST}{cell_s}")
            for tip in _ant_remedy(ratio, layer, pin, cell, cell_map_b):
                w.p(f"      -> {tip}")

    # ── WORSENED ──────────────────────────────────────────────────────────────
    if worsened_a:
        w.p(f"\n  {Y}-- WORSENED ({len(worsened_a)}) -- ratio increased in {run_b}{RST}")
        for v in worsened_a:
            net = v['net']; pin = v['pin']
            layer = v['layer']; ratio = v['ratio']
            prev = v.get('prev_ratio', 0)
            sev = _ant_severity(ratio)
            cell = cell_map_b.get(pin.split('/')[0], "")
            cell_s = f"  ({cell})" if cell else ""
            sev_color = R if sev=="SEVERE" else Y if sev=="MODERATE" else G
            w.p(f"  {Y}UP {RST}  Net: {C}{net:<20}{RST}  "
                f"Pin: {C}{pin:<25}{RST}  "
                f"Layer: {layer}   Ratio: {sev_color}{ratio:.2f}x  [{sev}]{RST}  "
                f"{DIM}(was {prev:.2f}x){RST}{cell_s}")
            for tip in _ant_remedy(ratio, layer, pin, cell, cell_map_b):
                w.p(f"      -> {tip}")

    # ── PERSISTENT ────────────────────────────────────────────────────────────
    if persistent_a:
        w.p(f"\n  {DIM}-- PERSISTENT ({len(persistent_a)}) -- unchanged in both runs{RST}")
        for v in persistent_a:
            cell = cell_map_b.get(v['pin'].split('/')[0], "")
            cell_s = f"  ({cell})" if cell else ""
            w.p(f"  {DIM}!!   Net: {v['net']:<20}  "
                f"Pin: {v['pin']:<25}  "
                f"Layer: {v['layer']}   Ratio: {v['ratio']:.2f}x{cell_s}{RST}")

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Compare two OpenLane physical design runs.")
    ap.add_argument("run_a", help="Baseline run tag (directory name under runs/)")
    ap.add_argument("run_b", help="Compare run tag")
    ap.add_argument("--fingerprint", action="store_true",
                    help="Show violation fingerprint breakdown")
    ap.add_argument("--config", action="store_true",
                    help="Show config_in.tcl parameter diff between runs")
    ap.add_argument("--save", action="store_true",
                    help="Save plain-text report to file")
    ap.add_argument("--runs-dir", default="runs",
                    help="Path to runs directory (default: ./runs)")
    args = ap.parse_args()

    base = Path(args.runs_dir)
    path_a = base / args.run_a
    path_b = base / args.run_b

    for tag, p in [(args.run_a, path_a), (args.run_b, path_b)]:
        if not p.exists():
            print(f"{R}Error: run directory not found: {p}{RST}")
            sys.exit(1)

    fname = f"pd_compare_{args.run_a}_vs_{args.run_b}.txt"
    w = OutputWriter(args.save, fname)

    da = collect_run_data(path_a)
    db = collect_run_data(path_b)

    print_comparison(w, args.run_a, args.run_b, da, db)

    if args.config:
        print_config_diff(w, args.run_a, args.run_b, path_a, path_b)

    if args.fingerprint:
        print_fingerprint(w, args.run_a, args.run_b, da, db)

    # contextual tips — only show flags the user hasn't used yet
    tips = []
    if not args.fingerprint:
        tips.append("--fingerprint  for violation breakdown + antenna remedies")
    if not args.config:
        tips.append("--config       for config_in.tcl parameter diff")
    if tips:
        w.p(f"\n  {DIM}Tip: re-run with " + "  |  ".join(tips) + RST)

    w.flush()

if __name__ == "__main__":
    main()
