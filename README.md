# pd_compare

A CLI tool for comparing OpenLane physical design runs on the same design.  
Built for engineers who run multiple SDC/config iterations and need a fast, structured diff of what changed — without manually digging through report files.

---

## What it does

Given two or more OpenLane run tags, `pd_compare` parses all relevant report files and produces a structured comparison across:

- Flow status and runtime
- Antenna violations (pin and net) with FIXED / NEW / WORSENED / PERSISTENT fingerprinting
- DRC and LVS signoff
- Timing — setup slack, hold slack, WNS, TNS, critical path
- Multi-corner timing (MCMM) — min, nom, and max RC corners with setup/hold slack, WNS, TNS
- Signal integrity — slew, cap, fanout violation counts per corner
- Design metrics — cell count, wire length, vias, die area
- Config view — every parameter across runs, grouped by category

---

## Modes

| Runs | Mode |
|---|---|
| 2 runs | Side-by-side diff with deltas and color coding |
| 3–5 runs | Parametric sweep table — all runs side by side, no deltas |

Mode is auto-detected from the number of arguments. No flag needed.

---

## Installation

```bash
git clone https://github.com/Sreehari2311/pd_compare.git
mkdir -p ~/tools
cp pd_compare/pd_compare.py ~/tools/pd_compare
chmod +x ~/tools/pd_compare
echo 'export PATH="$HOME/tools:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

No dependencies beyond the Python standard library.

---

## Usage

```bash
# cd into your design directory first
cd ~/ic_design/OpenLane/designs/picorv32

# 2-run comparison
pd_compare run1 fan_out_clean_3

# With per-net violation fingerprinting and remedies
pd_compare run1 fan_out_clean_3 --fingerprint

# With config diff
pd_compare run1 fan_out_clean_3 --config

# Parametric sweep — 3 to 5 runs
pd_compare util_30 util_35 util_40 util_45 util_50

# Config view across all sweep runs
pd_compare util_30 util_35 util_40 util_45 util_50 --config

# Save report to file
pd_compare run1 run2 --save

# Runs directory not at ./runs
pd_compare run1 run2 --runs-dir /path/to/runs
```

---

## Flags

| Flag | What it does |
|---|---|
| `--fingerprint `     | (2-run only) Classifies every antenna violation as FIXED / NEW / WORSENED / PERSISTENT with per-net detail and remedy suggestions |
| `--config` | Shows config parameters across runs grouped by TIMING / FLOORPLAN / ROUTING / OTHER |
| `--save` | Saves plain-text report to `pd_compare_<tags>.txt` |
| `--runs-dir` | Path to runs directory (default: `./runs`) |

---

## How it works

pd_compare reads directly from OpenLane's output report structure:

| Source file | What it parses |
|---|---|
| `reports/manufacturability.rpt` | DRC, LVS, antenna pin/net counts |
| `reports/metrics.csv` | Cell counts, wire length, vias, die area, runtime |
| `reports/signoff/*rcx_sta.summary.rpt` | Setup slack, hold slack, WNS, TNS (typical corner) |
| `reports/signoff/*rcx_*_sta.summary.rpt` | Setup/hold slack, WNS, TNS per MCMM corner |
| `reports/signoff/*rcx_*_sta.checks.rpt` | Slew, cap, fanout violation counts per corner |
| `reports/signoff/*antenna_violators.rpt` | Per-net antenna violation details |
| `logs/signoff/*arc.log` | Cell type enrichment for each violating pin |
| `config_in.tcl` | Config parameters per run |

File names are matched with glob patterns so any OpenLane step-number prefix is handled automatically.

---

## Remedy engine

The `--fingerprint` mode classifies each antenna violation by:

- **Severity**: MILD (1.0–1.5×), MODERATE (1.5–2.5×), SEVERE (2.5×+)
- **Layer**: met1 gets layer-hop advice; met2/met3 gets buffer insertion advice
- **Cell type**: MUX inputs, flip-flop inputs, buffers, and logic gates each get different placement guidance

---

## Tested on

- OpenLane 1 (Makefile-based, Docker and native)
- sky130A PDK (sky130_fd_sc_hd)
- Designs: picorv32, softmax_top

---

## Sample output


<img width="1649" height="1564" alt="Screenshot 2026-11-02 234703" src="https://github.com/user-attachments/assets/1a2a2649-22d8-4880-b829-12ddac090d98" />

<img width="1868" height="836" alt="Screenshot 2026-11-02 234835" src="https://github.com/user-attachments/assets/e55c679a-0ccb-4ef7-899e-b5d5a536b191" />




---
