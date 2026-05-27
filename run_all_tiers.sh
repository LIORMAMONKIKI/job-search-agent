#!/bin/bash
# Run enrichment + Notion write for the 7 remaining tiers.
set -e
cd "$(dirname "$0")"
for tier in T1 T2 T4 T5 T6 T7 T8; do
    echo ""
    echo "======================================================"
    echo "===== TIER $tier ====="
    echo "======================================================"
    .venv/bin/python -u enrich.py --tier "$tier" --resume
    csv="enrichment_${tier}_$(date +%Y-%m-%d).csv"
    if [ -f "$csv" ]; then
        echo ""
        echo "----- Writing $tier results to Notion -----"
        .venv/bin/python write_enrichment.py "$csv"
    else
        echo "no CSV produced for $tier, skipping write"
    fi
done
echo ""
echo "======================================================"
echo "ALL TIERS DONE"
echo "======================================================"
