# pd_compare

A CLI tool for comparing OpenLane physical design runs on the same design.  
Built for engineers who run multiple SDC/config iterations and need a fast, structured diff of what changed — without manually digging through report files.

---

## What it does

Given two OpenLane run tags, `pd_compare` parses all relevant report files and produces a structured side-by-side comparison across:

- Flow status and runtime
- Antenna violations (pin and net)
- DRC and LVS signoff
- Timing — setup slack, hold slack, WNS, TNS, critical path
- Signal integrity — slew, cap, fanout violations
- Design metrics — cell count, wire length, vias, die area

With `--fingerprint`, it goes deeper:

- Classifies every antenna violation as **FIXED / NEW / WORSENED / PERSISTENT** across the two runs
- Identifies the exact net, pin, layer, and cell type for each violation
- Suggests a specific remedy based on severity, layer physics, and cell type

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
pd_compare run1 time_25

# With per-net violation fingerprinting and remedies
pd_compare run1 time_25 --fingerprint

# Explicit design (from anywhere)
pd_compare picorv32 run1 time_25
```

---

## How it works

pd_compare reads directly from OpenLane's output report structure:

| Source file | What it parses |
|---|---|
| reports/manufacturability.rpt | DRC, LVS, antenna pin/net counts |
| reports/metrics.csv | Cell counts, wire length, vias, die area, runtime |
| reports/signoff/*rcx_sta.summary.rpt | Setup slack, hold slack, WNS, TNS |
| reports/signoff/*rcx_sta.checks.rpt | Slew, cap, fanout violation counts |
| reports/signoff/*antenna_violators.rpt | Per-net antenna violation details |
| logs/signoff/*arc.log | Cell type enrichment for each violating pin |

File names are matched with glob patterns so any OpenLane step-number prefix is handled automatically.

---

## Remedy engine

The --fingerprint mode classifies each antenna violation by:

- Severity: MILD (1.0-1.5x), MODERATE (1.5-2.5x), SEVERE (2.5x+)
- Layer: met1 gets layer-hop advice; met2/met3 gets buffer insertion advice
- Cell type: MUX inputs, flip-flop inputs, buffers, and logic gates each get different placement guidance

---

## Tested on

- OpenLane 1 (Makefile-based)
- sky130A PDK (sky130_fd_sc_hd)
- Designs: picorv32, softmax_top

---

## Sample snippet
<img width="1644" height="1220" alt="Screenshot 2026-09-16 133856" src="https://github.com/user-attachments/assets/ee61b2b1-6587-444c-8b93-b58b140438c1" />



## Roadmap (v2)

- Multi-corner timing (min / typical / max)
- Power breakdown (internal / switching / leakage per corner)
- IR drop comparison (VPWR / VGND)
- Pre-violation margin warnings (nets at 80-90% of antenna limit)
- Clock tree analysis (skew, insertion delay, buffer depth)
- --save to export report to file
- pd_compare design run1 run2 run3 — multi-run table

---

## Author
Sreehari - fellow VLSI engineer
Purpose of this tool is that I got tired of jumping between different OpenLane run paths and manually comparing report values. What started as “I’m too lazy to type these paths again” turned into pd_compare.
