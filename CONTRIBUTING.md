# Contributing to pd_compare

## Running locally

git clone https://github.com/Sreehari2311/pd_compare.git
cd pd_compare
chmod +x pd_compare.py

## How to test

Point it at any two OpenLane run directories:

cd ~/ic_design/OpenLane/designs/picorv32
python3 /path/to/pd_compare.py run1 time_25

## What a good contribution looks like

- Bug fix with a clear description of what was broken
- New metric added to the comparison table
- Support for a new report file format
- Improved remedy suggestions in the fingerprint engine

## Reporting issues

Open a GitHub issue with:
- Your OpenLane version
- The design name
- The exact command you ran
- The error or unexpected output

## Commit message style

feat: add --save flag to export report
fix: handle missing rcx_sta.summary.rpt gracefully
docs: update roadmap with v2 features
