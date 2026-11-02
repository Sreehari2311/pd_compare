#!/usr/bin/env python3
"""pd_compare v3.0 — OpenLane run comparison tool
  2 runs  → side-by-side diff with deltas  (v2 behaviour)
  3-5 runs → parametric sweep table, no deltas
"""

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
        self.save  = save
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
    try:    return float(v)
    except: return default

def _safe_int(v, default=None):
    try:    return int(float(v))
    except: return default

def _strip_ansi(s):
    return re.sub(r'\x1b\[[0-9;]*m', '', s)

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
        context = None
        for line in txt.splitlines():
            if 'report_worst_slack' in line and '-max' in line: context = 'setup'
            elif 'report_worst_slack' in line and '-min' in line: context = 'hold'
            elif 'report_wns' in line: context = 'wns'
            elif 'report_tns' in line: context = 'tns'
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

    # MCMM corners
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

# ══════════════════════════════════════════════════════════════════════════════
# v2 — 2-run side-by-side with deltas
# ══════════════════════════════════════════════════════════════════════════════

def _delta(a, b, higher_is_better=True, fmt=None):
    if a is None or b is None:
        return DIM + "(N/A)" + RST
    if isinstance(a, float) or isinstance(b, float):
        diff = b - a
        if fmt == 'area':  ds = f"{diff:+.4f}"
        elif fmt == 'float': ds = f"{diff:+.3f}"
        else: ds = f"{diff:+.1f}"
        if diff == 0: return DIM + "(no change)" + RST
        color = G if (diff > 0) == higher_is_better else R
        return color + f"({ds})" + RST
    elif isinstance(a, int) and isinstance(b, int):
        diff = b - a
        if diff == 0: return DIM + "(no change)" + RST
        color = G if (diff > 0) == higher_is_better else R
        return color + f"({diff:+d})" + RST
    else:
        if str(a) == str(b): return DIM + "(no change)" + RST
        return Y + f"(was: {a})" + RST

def _row2(w, label, va, vb, delta_s, width=30):
    la = str(va) if va is not None else "N/A"
    lb = str(vb) if vb is not None else "N/A"
    la_pad = la + " " * max(0, 14 - len(_strip_ansi(la)))
    lb_pad = lb + " " * max(0, 14 - len(_strip_ansi(lb)))
    w.p(f"  {label:<{width}}  {la_pad} {lb_pad} {delta_s}")

def print_comparison(w, run_a, run_b, da, db):
    W = "="*80
    w.p(f"\n{W}")
    w.p(f"  {B}OpenLane Run Comparison  v3.0{RST}")
    w.p(f"  Baseline : {C}{run_a}{RST}")
    w.p(f"  Compare  : {C}{run_b}{RST}")
    w.p(W)

    # FLOW STATUS
    w.p(f"\n  {B}-- FLOW STATUS{RST}")
    w.p(f"  {'Metric':<30}  {'Baseline':<14} {'New':<14} {'Delta'}")
    w.p("  " + "-"*79)
    fs_a = da.get('flow_status','unknown'); fs_b = db.get('flow_status','unknown')
    _row2(w, "Flow status", fs_a, fs_b,
          DIM+"(no change)"+RST if fs_a==fs_b else Y+f"(was: {fs_a})"+RST)
    _row2(w, "Total runtime",
          da.get('runtime','N/A'), db.get('runtime','N/A'),
          Y+f"(was: {da.get('runtime','N/A')})"+RST
          if da.get('runtime') != db.get('runtime') else DIM+"(no change)"+RST)

    # ANTENNA VIOLATIONS
    w.p(f"\n  {B}-- ANTENNA VIOLATIONS{RST}")
    w.p(f"  {'Metric':<30}  {'Baseline':<14} {'New':<14} {'Delta'}")
    w.p("  " + "-"*79)
    _row2(w, "Pin antenna violations", da.get('pin_ant'), db.get('pin_ant'),
          _delta(da.get('pin_ant'), db.get('pin_ant'), higher_is_better=False))
    _row2(w, "Net antenna violations", da.get('net_ant'), db.get('net_ant'),
          _delta(da.get('net_ant'), db.get('net_ant'), higher_is_better=False))

    # DRC / LVS
    w.p(f"\n  {B}-- DRC / LVS{RST}")
    w.p(f"  {'Metric':<30}  {'Baseline':<14} {'New':<14} {'Delta'}")
    w.p("  " + "-"*79)
    _row2(w, "Magic DRC violations", da.get('magic_drc'), db.get('magic_drc'),
          _delta(da.get('magic_drc'), db.get('magic_drc'), higher_is_better=False))
    _row2(w, "TritonRoute violations", da.get('triton_drc'), db.get('triton_drc'),
          _delta(da.get('triton_drc'), db.get('triton_drc'), higher_is_better=False))
    _row2(w, "Short violations", da.get('shorts'), db.get('shorts'),
          _delta(da.get('shorts'), db.get('shorts'), higher_is_better=False))
    lvs_a = da.get('lvs','N/A'); lvs_b = db.get('lvs','N/A')
    _row2(w, "LVS status", lvs_a, lvs_b,
          DIM+"(no change)"+RST if lvs_a==lvs_b else Y+f"(was: {lvs_a})"+RST)

    # TIMING
    w.p(f"\n  {B}-- TIMING  (post-route, RCX -- Typical Corner){RST}")
    w.p(f"  {'Metric':<30}  {'Baseline':<14} {'New':<14} {'Delta'}")
    w.p("  " + "-"*79)
    ss_a = da.get('setup_slack'); ss_b = db.get('setup_slack')
    hs_a = da.get('hold_slack');  hs_b = db.get('hold_slack')
    wns_a= da.get('wns',0.0);    wns_b= db.get('wns',0.0)
    tns_a= da.get('tns',0.0);    tns_b= db.get('tns',0.0)
    cp_a = da.get('critical_path'); cp_b = db.get('critical_path')
    ck_a = da.get('clock_period',''); ck_b = db.get('clock_period','')

    _row2(w, "Setup slack (ns)",
          f"{ss_a:.3f}" if ss_a is not None else "N/A",
          f"{ss_b:.3f}" if ss_b is not None else "N/A",
          _delta(ss_a, ss_b, higher_is_better=True, fmt='float'))
    _row2(w, "Hold slack  (ns)",
          f"{hs_a:.3f}" if hs_a is not None else "N/A",
          f"{hs_b:.3f}" if hs_b is not None else "N/A",
          _delta(hs_a, hs_b, higher_is_better=True, fmt='float'))
    wns_both_zero = (wns_a == 0.0 and wns_b == 0.0)
    _row2(w, "WNS  (ns)",
          DIM+f"{wns_a:.3f}"+RST if wns_both_zero else f"{wns_a:.3f}",
          DIM+f"{wns_b:.3f}"+RST if wns_both_zero else f"{wns_b:.3f}",
          DIM+"(no change)"+RST if wns_both_zero else _delta(wns_a, wns_b, higher_is_better=True, fmt='float'))
    tns_both_zero = (tns_a == 0.0 and tns_b == 0.0)
    _row2(w, "TNS  (ns)",
          DIM+f"{tns_a:.3f}"+RST if tns_both_zero else f"{tns_a:.3f}",
          DIM+f"{tns_b:.3f}"+RST if tns_both_zero else f"{tns_b:.3f}",
          DIM+"(no change)"+RST if tns_both_zero else _delta(tns_a, tns_b, higher_is_better=True, fmt='float'))
    _row2(w, "Critical path (ns)",
          f"{cp_a:.3f}" if cp_a is not None else "N/A",
          f"{cp_b:.3f}" if cp_b is not None else "N/A",
          _delta(cp_a, cp_b, higher_is_better=False, fmt='float'))
    _row2(w, "Clock period (ns)", ck_a or "N/A", ck_b or "N/A",
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
            if not ca and not cb: continue
            w.p(f"  {DIM}{label}{RST}")
            setup_a = ca.get('setup'); setup_b = cb.get('setup')
            hold_a  = ca.get('hold');  hold_b  = cb.get('hold')
            wns_a2  = ca.get('wns',0); wns_b2  = cb.get('wns',0)
            tns_a2  = ca.get('tns',0); tns_b2  = cb.get('tns',0)
            _row2(w, "    Setup slack (ns)",
                  f"{setup_a:.3f}" if setup_a is not None else "N/A",
                  f"{setup_b:.3f}" if setup_b is not None else "N/A",
                  _delta(setup_a, setup_b, higher_is_better=True, fmt='float'))
            _row2(w, "    Hold slack  (ns)",
                  f"{hold_a:.3f}" if hold_a is not None else "N/A",
                  f"{hold_b:.3f}" if hold_b is not None else "N/A",
                  _delta(hold_a, hold_b, higher_is_better=True, fmt='float'))
            _row2(w, "    WNS  (ns)", f"{wns_a2:.3f}", f"{wns_b2:.3f}",
                  _delta(wns_a2, wns_b2, higher_is_better=True, fmt='float'))
            _row2(w, "    TNS  (ns)", f"{tns_a2:.3f}", f"{tns_b2:.3f}",
                  _delta(tns_a2, tns_b2, higher_is_better=True, fmt='float'))
            slew_a = ca.get('slew'); slew_b = cb.get('slew')
            cap_a  = ca.get('cap');  cap_b  = cb.get('cap')
            fo_a2  = ca.get('fanout'); fo_b2 = cb.get('fanout')
            if any(v is not None for v in [slew_a, slew_b, cap_a, cap_b, fo_a2, fo_b2]):
                _row2(w, "    Slew violations",
                      str(slew_a) if slew_a is not None else "N/A",
                      str(slew_b) if slew_b is not None else "N/A",
                      _delta(slew_a, slew_b, higher_is_better=False))
                _row2(w, "    Cap violations",
                      str(cap_a) if cap_a is not None else "N/A",
                      str(cap_b) if cap_b is not None else "N/A",
                      _delta(cap_a, cap_b, higher_is_better=False))
                _row2(w, "    Fanout violations",
                      str(fo_a2) if fo_a2 is not None else "N/A",
                      str(fo_b2) if fo_b2 is not None else "N/A",
                      _delta(fo_a2, fo_b2, higher_is_better=False))

    # SIGNAL INTEGRITY
    w.p(f"\n  {B}-- SIGNAL INTEGRITY VIOLATIONS{RST}")
    w.p(f"  {'Metric':<30}  {'Baseline':<14} {'New':<14} {'Delta'}")
    w.p("  " + "-"*79)
    _row2(w, "Slew violations", da.get('slew_viol'), db.get('slew_viol'),
          _delta(da.get('slew_viol'), db.get('slew_viol'), higher_is_better=False))
    _row2(w, "Cap violations", da.get('cap_viol'), db.get('cap_viol'),
          _delta(da.get('cap_viol'), db.get('cap_viol'), higher_is_better=False))
    _row2(w, "Fanout violations", da.get('fanout_viol'), db.get('fanout_viol'),
          _delta(da.get('fanout_viol'), db.get('fanout_viol'), higher_is_better=False))
    fo_a = da.get('max_fanout_constraint',''); fo_b = db.get('max_fanout_constraint','')
    _row2(w, "Max fanout constraint", fo_a or "N/A", fo_b or "N/A",
          DIM+"(no change)"+RST if fo_a==fo_b else Y+f"(was: {fo_a})"+RST)

    # DESIGN METRICS
    w.p(f"\n  {B}-- DESIGN METRICS{RST}")
    w.p(f"  {'Metric':<30}  {'Baseline':<14} {'New':<14} {'Delta'}")
    w.p("  " + "-"*79)
    _row2(w, "Synth cell count", da.get('synth_cells'), db.get('synth_cells'),
          _delta(da.get('synth_cells'), db.get('synth_cells'), higher_is_better=False))
    _row2(w, "Total cells", da.get('total_cells'), db.get('total_cells'),
          _delta(da.get('total_cells'), db.get('total_cells'), higher_is_better=False))
    _row2(w, "Diode cells", da.get('diode_cells'), db.get('diode_cells'),
          _delta(da.get('diode_cells'), db.get('diode_cells'), higher_is_better=False))
    _row2(w, "Wire length (um)", da.get('wire_length'), db.get('wire_length'),
          _delta(da.get('wire_length'), db.get('wire_length'), higher_is_better=False))
    _row2(w, "Vias", da.get('vias'), db.get('vias'),
          _delta(da.get('vias'), db.get('vias'), higher_is_better=False))
    da_a = da.get('die_area'); da_b = db.get('die_area')
    _row2(w, "Die area (mm2)",
          f"{da_a:.4f}" if da_a is not None else "N/A",
          f"{da_b:.4f}" if da_b is not None else "N/A",
          _delta(da_a, da_b, higher_is_better=False, fmt='area'))

    w.p(f"\n{'='*80}")
    w.p(f"  {DIM}Legend : {G}green = improved{RST}  {DIM}{R}red = degraded{RST}  "
        f"{DIM}{Y}yellow = changed{RST}  {DIM}dim = no change / informational{RST}")
    w.p(f"  {DIM}Timing : higher slack = more margin (setup > 0 and hold > 0 = clean){RST}")
    w.p(f"{'='*80}")

# ── v2 config diff ────────────────────────────────────────────────────────────
def print_config_diff(w, run_a, run_b, path_a, path_b):
    cfg_a = parse_config(path_a)
    cfg_b = parse_config(path_b)
    if not cfg_a and not cfg_b:
        return
    changed, added, removed = diff_configs(cfg_a, cfg_b)
    total_changes = len(changed) + len(added) + len(removed)

    w.p(f"\n  {B}-- CONFIG CHANGES{RST}  {DIM}(config_in.tcl: {run_a} → {run_b}){RST}")
    if total_changes == 0:
        w.p(f"  {DIM}No parameter changes between runs.{RST}")
        return

    w.p(f"  {'Parameter':<38}  {'Baseline':<20}  {'New':<20}")
    w.p("  " + "-"*80)

    def _cfg_row(key, va, vb, action):
        cat = _param_category(key)
        kc = f"{R}{B}" if cat == "TIMING" else (Y if action == "changed" else G if action == "added" else R)
        if action == "changed":
            w.p(f"  {kc}{key:<38}{RST}  {DIM}{va:<20}{RST}  {G}{vb:<20}{RST}")
        elif action == "added":
            w.p(f"  {kc}{key:<38}{RST}  {DIM}{'(not set)':<20}{RST}  {G}{vb:<20}{RST}")
        elif action == "removed":
            w.p(f"  {kc}{key:<38}{RST}  {DIM}{va:<20}{RST}  {R}{'(removed)':<20}{RST}")

    all_rows = (
        [(k, va, vb, "changed") for k,va,vb in changed] +
        [(k, "",  v,  "added")  for k,v    in added]   +
        [(k, v,   "",  "removed") for k,v  in removed]
    )
    for cat_label in ["TIMING", "FLOORPLAN", "ROUTING", "OTHER"]:
        rows = [(k,va,vb,act) for k,va,vb,act in all_rows if _param_category(k) == cat_label]
        for k,va,vb,act in rows:
            _cfg_row(k, va, vb, act)

    parts = []
    if changed: parts.append(f"{Y}{len(changed)} changed{RST}")
    if added:   parts.append(f"{G}{len(added)} added{RST}")
    if removed: parts.append(f"{R}{len(removed)} removed{RST}")
    w.p(f"\n  {DIM}Config summary:{RST}  " + "  ".join(parts))
    timing_count = sum(1 for k,*_ in all_rows if _param_category(k) == "TIMING")
    if timing_count:
        w.p(f"  {R}{B}⚠  {timing_count} timing-critical parameter(s) changed "
            f"— slack deltas may reflect constraint change, not just implementation.{RST}")

# ══════════════════════════════════════════════════════════════════════════════
# v3 — multi-run sweep table (3-5 runs)
# ══════════════════════════════════════════════════════════════════════════════

LABEL_W = 32  # width of metric label column
_COL_W  = 14  # default; overridden per-call by auto-sizing

def _col_w(runs):
    """Auto-size column width to the longest run name (min 10, max 22)."""
    return max(10, min(22, max(len(r) for r in runs) + 2))

def _trunc(s, w):
    """Truncate string to w chars, appending … if cut."""
    s = str(s) if s is not None else "N/A"
    if len(s) <= w:
        return s
    return s[:w-1] + "…"

def _cell(v, cw):
    """Format a single table cell, padded to width cw."""
    s = _trunc(v, cw)
    return s + " " * max(0, cw - len(_strip_ansi(s)))

def _hdr_line(runs, cw):
    """Build the header row: blank label + run names."""
    hdr = " " * LABEL_W + "  "
    for r in runs:
        name = _trunc(r, cw)
        hdr += (name + " " * max(0, cw - len(name))) + " "
    return hdr

def _sep(runs, cw):
    return "-" * (LABEL_W + 2 + (cw + 1) * len(runs))

def _trow(w, label, values, cw):
    """Print one metric row across all runs."""
    line = f"  {label:<{LABEL_W}}"
    for v in values:
        line += "  " + _cell(v, cw)
    w.p(line)

def print_sweep_report(w, runs, data):
    """Multi-run parametric sweep — report view."""
    n  = len(runs)
    cw = _col_w(runs)
    W  = "=" * (LABEL_W + 2 + (cw + 1) * n + 4)
    w.p(f"\n{W}")
    w.p(f"  {B}OpenLane Parametric Sweep  v3.0{RST}  {DIM}({n} runs){RST}")
    w.p(W)
    w.p("  " + _hdr_line(runs, cw))
    w.p("  " + _sep(runs, cw))

    # FLOW STATUS
    w.p(f"\n  {B}-- FLOW STATUS{RST}")
    _trow(w, "Flow status",   [d.get('flow_status','N/A') for d in data], cw)
    _trow(w, "Total runtime", [d.get('runtime','N/A')     for d in data], cw)

    # ANTENNA
    w.p(f"\n  {B}-- ANTENNA VIOLATIONS{RST}")
    _trow(w, "Pin antenna violations", [d.get('pin_ant') for d in data], cw)
    _trow(w, "Net antenna violations", [d.get('net_ant') for d in data], cw)

    # DRC / LVS
    w.p(f"\n  {B}-- DRC / LVS{RST}")
    _trow(w, "Magic DRC violations",   [d.get('magic_drc')  for d in data], cw)
    _trow(w, "TritonRoute violations", [d.get('triton_drc') for d in data], cw)
    _trow(w, "Short violations",       [d.get('shorts')     for d in data], cw)
    _trow(w, "LVS status",             [d.get('lvs','N/A')  for d in data], cw)

    # TIMING — typical corner
    w.p(f"\n  {B}-- TIMING  (post-route, RCX -- Typical Corner){RST}")
    _trow(w, "Clock period (ns)",
          [d.get('clock_period','N/A') for d in data], cw)
    _trow(w, "Setup slack (ns)",
          [f"{d['setup_slack']:.3f}" if d.get('setup_slack') is not None else "N/A" for d in data], cw)
    _trow(w, "Hold slack (ns)",
          [f"{d['hold_slack']:.3f}"  if d.get('hold_slack')  is not None else "N/A" for d in data], cw)
    _trow(w, "WNS (ns)",
          [f"{d.get('wns',0.0):.3f}" for d in data], cw)
    _trow(w, "TNS (ns)",
          [f"{d.get('tns',0.0):.3f}" for d in data], cw)
    _trow(w, "Critical path (ns)",
          [f"{d['critical_path']:.3f}" if d.get('critical_path') is not None else "N/A" for d in data], cw)

    # MCMM corners
    corner_defs = [
        ('corner_min', 'Min (rcx_min -- best RC)'),
        ('corner_nom', 'Nom (rcx_nom -- nominal RC)'),
        ('corner_max', 'Max (rcx_max -- worst RC)'),
    ]
    has_mcmm = any(d.get(k) for d in data for k,_ in corner_defs)
    if has_mcmm:
        w.p(f"\n  {B}-- TIMING  (MCMM corners){RST}")
        for ckey, clabel in corner_defs:
            if not any(d.get(ckey) for d in data):
                continue
            w.p(f"\n  {DIM}{clabel}{RST}")
            _trow(w, "    Setup slack (ns)",
                  [f"{d[ckey]['setup']:.3f}" if d.get(ckey,{}).get('setup') is not None else "N/A" for d in data], cw)
            _trow(w, "    Hold slack (ns)",
                  [f"{d[ckey]['hold']:.3f}"  if d.get(ckey,{}).get('hold')  is not None else "N/A" for d in data], cw)
            _trow(w, "    WNS (ns)",
                  [f"{d.get(ckey,{}).get('wns',0):.3f}" for d in data], cw)
            _trow(w, "    TNS (ns)",
                  [f"{d.get(ckey,{}).get('tns',0):.3f}" for d in data], cw)
            _trow(w, "    Slew violations",
                  [d.get(ckey,{}).get('slew','N/A') for d in data], cw)
            _trow(w, "    Cap violations",
                  [d.get(ckey,{}).get('cap','N/A')  for d in data], cw)
            _trow(w, "    Fanout violations",
                  [d.get(ckey,{}).get('fanout','N/A') for d in data], cw)

    # SIGNAL INTEGRITY
    w.p(f"\n  {B}-- SIGNAL INTEGRITY VIOLATIONS{RST}")
    _trow(w, "Slew violations",      [d.get('slew_viol')   for d in data], cw)
    _trow(w, "Cap violations",       [d.get('cap_viol')    for d in data], cw)
    _trow(w, "Fanout violations",    [d.get('fanout_viol') for d in data], cw)
    _trow(w, "Max fanout constraint",[d.get('max_fanout_constraint','N/A') for d in data], cw)

    # DESIGN METRICS
    w.p(f"\n  {B}-- DESIGN METRICS{RST}")
    _trow(w, "Synth cell count", [d.get('synth_cells') for d in data], cw)
    _trow(w, "Total cells",      [d.get('total_cells') for d in data], cw)
    _trow(w, "Diode cells",      [d.get('diode_cells') for d in data], cw)
    _trow(w, "Wire length (um)", [d.get('wire_length') for d in data], cw)
    _trow(w, "Vias",             [d.get('vias')        for d in data], cw)
    _trow(w, "Die area (mm2)",
          [f"{d['die_area']:.4f}" if d.get('die_area') is not None else "N/A" for d in data], cw)

    w.p(f"\n{W}")

def print_sweep_config(w, runs, paths):
    """Multi-run parametric sweep — config view."""
    n  = len(runs)
    cw = _col_w(runs)
    W  = "=" * (LABEL_W + 2 + (cw + 1) * n + 4)
    w.p(f"\n{W}")
    w.p(f"  {B}OpenLane Parametric Sweep — Config  v3.0{RST}  {DIM}({n} runs){RST}")
    w.p(W)

    configs = [parse_config(p) for p in paths]
    all_keys = sorted(set(k for cfg in configs for k in cfg))

    if not all_keys:
        w.p(f"  {DIM}No config_in.tcl found in any run.{RST}")
        return

    w.p("  " + _hdr_line(runs, cw))
    w.p("  " + _sep(runs, cw))

    for cat_label in ["TIMING", "FLOORPLAN", "ROUTING", "OTHER"]:
        keys = [k for k in all_keys if _param_category(k) == cat_label]
        if not keys:
            continue
        w.p(f"\n  {B}-- {cat_label}{RST}")
        for k in keys:
            kc = f"{R}{B}" if cat_label == "TIMING" else ""
            values = [cfg.get(k, "(not set)") for cfg in configs]
            line = f"  {kc}{k:<{LABEL_W}}{RST}"
            for v in values:
                line += "  " + _cell(v, cw)
            w.p(line)

    w.p(f"\n{W}")

# ── fingerprint (v2 only) ─────────────────────────────────────────────────────
def _ant_severity(ratio: float) -> str:
    if ratio > 2.5: return "SEVERE"
    if ratio > 1.5: return "MODERATE"
    return "MILD"

def _ant_remedy(ratio, layer, pin, cell_type, cell_map) -> list:
    sev  = _ant_severity(ratio)
    tips = []
    cell = cell_map.get(pin.split('/')[0], cell_type or "")
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
        tips.append(f"MUX cell ({cell}) -- insert buf_2 before select input")
    elif "dff" in cell.lower() or "dfxtp" in cell.lower() or "sdff" in cell.lower():
        tips.append(f"Flip-flop ({cell}) -- add jumper diode on D-input")
    elif "buf" in cell.lower() or "clkbuf" in cell.lower():
        tips.append(f"Buffer/clkbuf ({cell}) -- safe to break net here")
    elif cell:
        tips.append(f"Logic gate ({cell}) -- safe to break net before this gate")
    return tips

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

def print_fingerprint(w, run_a, run_b, da, db):
    W2 = "="*90
    w.p(f"\n{W2}")
    w.p(f"  {B}ANTENNA VIOLATION FINGERPRINT{RST}")
    w.p(f"  Baseline : {C}{run_a}{RST}   ->   Compare : {C}{run_b}{RST}")
    w.p(W2)

    fixed_a, new_a, worsened_a, persistent_a = fingerprint_antenna(da, db)
    cell_map_b = db.get('cell_map', {})

    w.p(f"\n  {G}OK {len(fixed_a)} fixed{RST}    "
        f"{R}XX {len(new_a)} new{RST}    "
        f"{Y}UP {len(worsened_a)} worsened{RST}    "
        f"{'!! ' + str(len(persistent_a)) + ' persistent' if persistent_a else DIM + '!! 0 persistent' + RST}")

    if fixed_a:
        w.p(f"\n  {DIM}-- FIXED ({len(fixed_a)}) -- these nets were resolved in {run_b}{RST}")
        for v in fixed_a:
            cell = cell_map_b.get(v['pin'].split('/')[0], "")
            cell_s = f"  ({cell})" if cell else ""
            w.p(f"  {G}OK{RST}  Net: {C}{v['net']:<20}{RST}  "
                f"Pin: {C}{v['pin']:<25}{RST}  "
                f"Layer: {v['layer']}   Ratio: {v['ratio']:.2f}x{cell_s}")

    if new_a:
        w.p(f"\n  {R}-- NEW ({len(new_a)}) -- these violations appeared in {run_b}{RST}")
        for v in new_a:
            sev = _ant_severity(v['ratio'])
            cell = cell_map_b.get(v['pin'].split('/')[0], "")
            cell_s = f"  ({cell})" if cell else ""
            sev_color = R if sev=="SEVERE" else Y if sev=="MODERATE" else G
            w.p(f"  {R}NEW{RST}  Net: {C}{v['net']:<20}{RST}  "
                f"Pin: {C}{v['pin']:<25}{RST}  "
                f"Layer: {v['layer']}   Ratio: {sev_color}{v['ratio']:.2f}x  [{sev}]{RST}{cell_s}")
            for tip in _ant_remedy(v['ratio'], v['layer'], v['pin'], cell, cell_map_b):
                w.p(f"      -> {tip}")

    if worsened_a:
        w.p(f"\n  {Y}-- WORSENED ({len(worsened_a)}) -- ratio increased in {run_b}{RST}")
        for v in worsened_a:
            sev = _ant_severity(v['ratio'])
            cell = cell_map_b.get(v['pin'].split('/')[0], "")
            cell_s = f"  ({cell})" if cell else ""
            sev_color = R if sev=="SEVERE" else Y if sev=="MODERATE" else G
            w.p(f"  {Y}UP {RST}  Net: {C}{v['net']:<20}{RST}  "
                f"Pin: {C}{v['pin']:<25}{RST}  "
                f"Layer: {v['layer']}   Ratio: {sev_color}{v['ratio']:.2f}x  [{sev}]{RST}  "
                f"{DIM}(was {v.get('prev_ratio',0):.2f}x){RST}{cell_s}")
            for tip in _ant_remedy(v['ratio'], v['layer'], v['pin'], cell, cell_map_b):
                w.p(f"      -> {tip}")

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
        description="Compare OpenLane physical design runs (2 runs = diff, 3-5 runs = sweep table).")
    ap.add_argument("runs", nargs="+",
                    help="Run tag(s) — directory names under runs/. 2 = comparison, 3-5 = sweep.")
    ap.add_argument("--fingerprint", action="store_true",
                    help="(2-run mode only) Show antenna violation fingerprint + remedies")
    ap.add_argument("--config", action="store_true",
                    help="Show config view instead of report view")
    ap.add_argument("--save", action="store_true",
                    help="Save plain-text report to file")
    ap.add_argument("--runs-dir", default="runs",
                    help="Path to runs directory (default: ./runs)")
    args = ap.parse_args()

    if len(args.runs) < 2:
        print(f"{R}Error: provide at least 2 run tags.{RST}")
        sys.exit(1)
    if len(args.runs) > 5:
        print(f"{R}Error: maximum 5 runs supported.{RST}")
        sys.exit(1)

    base  = Path(args.runs_dir)
    paths = []
    for tag in args.runs:
        p = base / tag
        if not p.exists():
            print(f"{R}Error: run directory not found: {p}{RST}")
            sys.exit(1)
        paths.append(p)

    run_tags = args.runs
    tag_str  = "_vs_".join(run_tags)
    fname    = f"pd_compare_{tag_str}.txt"
    w = OutputWriter(args.save, fname)

    multi = len(run_tags) > 2

    if multi:
        # ── 3-5 run sweep mode ────────────────────────────────────────────────
        if args.fingerprint:
            print(f"{Y}Note: --fingerprint is not available in sweep mode (3+ runs).{RST}")

        data = [collect_run_data(p) for p in paths]

        if args.config:
            print_sweep_config(w, run_tags, paths)
        else:
            print_sweep_report(w, run_tags, data)
            w.p(f"\n  {DIM}Tip: re-run with --config to see parameter differences across runs{RST}")

    else:
        # ── 2-run comparison mode (v2) ────────────────────────────────────────
        da = collect_run_data(paths[0])
        db = collect_run_data(paths[1])

        if args.config:
            print_config_diff(w, run_tags[0], run_tags[1], paths[0], paths[1])
        else:
            print_comparison(w, run_tags[0], run_tags[1], da, db)

            if args.fingerprint:
                print_fingerprint(w, run_tags[0], run_tags[1], da, db)

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
