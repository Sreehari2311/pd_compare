# pd_compare

A CLI tool for comparing OpenLane physical design runs on the same design.  
Built for engineers who run multiple SDC/config iterations and need a fast, structured diff of what changed — without manually digging through report files.

---

## What it does

Given two OpenLane run tags, `pd_compare` parses all relevant report files and produces a structured side-by-side comparison across:

- Flow status and runtime
- Antenna violations (pin and net) with FIXED / NEW / WORSENED / PERSISTENT fingerprinting
- DRC and LVS signoff
- Timing — setup slack, hold slack, WNS, TNS, critical path
- Multi-corner timing (MCMM) — min, nom, and max RC corners with setup/hold slack, WNS, TNS
- Signal integrity — slew, cap, fanout violation counts per corner
- Design metrics — cell count, wire length, vias, die area
- Config diff — every parameter added, removed, or changed between runs

---

## Installation

```bash
mkdir -p ~/tools
cp pd_compare.py ~/tools/pd_compare
chmod +x ~/tools/pd_compare
echo 'export PATH="$HOME/tools:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

---

## Usage

```bash
# cd into your design directory first
cd ~/ic_design/OpenLane/designs/picorv32

# Basic comparison
pd_compare run1 fan_out_clean_3

# With per-net violation fingerprinting and remedies
pd_compare run1 fan_out_clean_3 --fingerprint

# With config diff
pd_compare run1 fan_out_clean_3 --config

# Explicit design (from anywhere)
pd_compare picorv32 run1 fan_out_clean_3
```

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
| `config.json` | Config parameter diff between runs |

File names are matched with glob patterns so any OpenLane step-number prefix is handled automatically.

---

## Flags

| Flag | What it does |
|---|---|
| `--fingerprint` | Classifies every antenna violation as FIXED / NEW / WORSENED / PERSISTENT with per-net detail and remedy suggestions |
| `--config` | Shows a side-by-side diff of every config parameter that changed between runs |

---

## Remedy engine

The `--fingerprint` mode classifies each antenna violation by:

- **Severity**: MILD (1.0–1.5×), MODERATE (1.5–2.5×), SEVERE (2.5×+)
- **Layer**: met1 gets layer-hop advice; met2/met3 gets buffer insertion advice
- **Cell type**: MUX inputs, flip-flop inputs, buffers, and logic gates each get different placement guidance

---

## Tested on

- OpenLane 1 (Makefile-based)
- sky130A PDK (sky130_fd_sc_hd)
- Designs: picorv32, softmax_top

---

## Sample output

<img width="2208" height="1541" alt="image" src="https://github.com/user-attachments/assets/2f01e8c9-47d7-4c9e-95da-0600add0c1ab" />
<img width="2120" height="873" alt="image" src="https://github.com/user-attachments/assets/21a38c1e-730e-4439-9309-3a11fceaa25d" />
<img width="2841" height="935" alt="image" src="https://github.com/user-attachments/assets/4a17aa65-b895-4639-a43e-1b0339269e5a" />

---

## Roadmap

**v3 — Multi-run comparison**
- `pd_compare run1 run2 run3` — compare 3+ runs in one table
- Useful for parameter sweeps where you need to see the trend across iterations, not just A vs B

**Future**
- Power breakdown (internal / switching / leakage per corner)
- IR drop comparison (VPWR / VGND)
- Pre-violation margin warnings (nets at 80–90% of antenna limit)
- Clock tree analysis (skew, insertion delay, buffer depth)
- `--save` to export report to file
