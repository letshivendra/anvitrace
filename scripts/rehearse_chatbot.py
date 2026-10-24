#!/usr/bin/env python3
"""Demo Rehearsal Script for ANVITRACE Case Explainer & Chatbot.

Measures actual wall-clock execution time, source (model vs template),
validation results, and citations across N rehearsal iterations.
Supports simulating Ollama being down to confirm deterministic fallback behavior.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database.duckdb import get_connection
from app.explainer.case_summarizer import CaseChatbotService, CaseFactsExtractor
from app.explainer.ollama_client import OllamaClient
from app.services.investigation import InvestigationService
from app.services.risk_engine import RiskAssessmentService


def build_case_trace(account_id: str) -> dict:
    """Fetch real case trace from live local API or fallback fixture."""
    import urllib.request
    import json

    # 1. Try live backend server
    try:
        url = f"http://127.0.0.1:8000/api/investigations/trace/{account_id}"
        with urllib.request.urlopen(url, timeout=3.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                # Also fetch risk profile
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:8000/api/accounts/{account_id}/risk", timeout=3.0) as r_resp:
                        if r_resp.status == 200:
                            risk = json.loads(r_resp.read().decode("utf-8"))
                            data["risk_summary"] = {
                                "mule_risk_index": risk.get("mule_risk_index", 90.0),
                                "risk_tier": risk.get("risk_tier", "CRITICAL"),
                                "top_contributing_features": [f["feature_name"] for f in risk.get("features", []) if f.get("score_contribution", 0) > 0],
                            }
                except Exception:
                    pass
                return data
    except Exception:
        pass

    # 2. Try direct DuckDB
    try:
        conn = get_connection()
        ds_row = conn.execute("SELECT dataset_id FROM transactions LIMIT 1").fetchone()
        ds_id = ds_row[0] if ds_row else "ds_2c9f81fd34f728c0"
        inv_svc = InvestigationService(conn)
        trace_resp = inv_svc.run_trace(victim_account=account_id, dataset_id=ds_id, max_hops=2)
        trace_data = trace_resp.model_dump()
        trace_data["case_id"] = f"CASE_{account_id}"
        risk_svc = RiskAssessmentService(conn)
        risk_prof = risk_svc.assess_account_risk(account_id, ds_id)
        if risk_prof:
            trace_data["risk_summary"] = {
                "mule_risk_index": risk_prof.mule_risk_index,
                "risk_tier": risk_prof.risk_tier,
                "top_contributing_features": [f.feature_name for f in risk_prof.features if f.score_contribution > 0],
            }
        return trace_data
    except Exception as exc:
        pass
        print(f"[Notice] Could not load account {account_id} from DB ({exc}); using bounded fallback trace.")
        target_collector = f"ACC_COLLECTOR_{account_id[-4:]}" if len(account_id) >= 4 else "ACC_COLLECTOR_01"
        return {
            "case_id": f"REHEARSAL_CASE_{account_id}",
            "victim_account": account_id,
            "victim_transaction": {"Amount": 50000.0, "timestamp": "2026-09-18 10:00:00"},
            "money_trail": [
                {
                    "record_id": "rec_reh_01",
                    "Transaction_ID": "TXN_REH_01",
                    "from_account": account_id,
                    "to_account": target_collector,
                    "amount": 50000.0,
                    "timestamp": "2026-09-18 10:00:00",
                    "hop": 1,
                }
            ],
            "risk_summary": {"mule_risk_index": 90.0, "risk_tier": "CRITICAL", "top_contributing_features": ["rapid_turnaround"]},
            "layer_candidates": [{"account_id": target_collector, "role": "Layer 1 Collector", "layer": 1, "score": 90, "evidence_summary": "High velocity"}],
            "evidence_metadata": {"record_ids": ["rec_reh_01"], "transaction_ids": ["TXN_REH_01"]},
        }


def rehearse(runs: int = 5, simulate_down: bool = False, account_id: str = "") -> None:
    """Execute rehearsal loop and display strictly measured telemetry."""
    print("=" * 80)
    print(" ANVITRACE CHATBOT & EXPLAINER DEMO REHEARSAL")
    print(f" Target Account: {account_id} | Total Runs: {runs} | Simulate Down: {simulate_down}")
    print("=" * 80)

    # Initialize client
    client = OllamaClient()
    if simulate_down:
        client.refuse_inference = True
        print("[Simulate Down] Ollama inference disabled. All answers will route to template mode.")
    else:
        available = client.is_available()
        print(f"[Ollama Status] Host: {client.base_url} | Model: {client.model_name} | Available: {available}")
        if available:
            print("[Warm-up] Warming up model before rehearsal...")
            client.warmup()

    chatbot = CaseChatbotService(ollama_client=client)
    trace_data = build_case_trace(account_id)
    presets = chatbot.get_presets().presets

    # Tracking per question
    stats_per_q: dict[str, dict[str, Any]] = {
        q: {
            "latencies_cold": [],
            "latencies_warm": [],
            "source_counts": {"model": 0, "template": 0, "cached-model": 0, "cached-template": 0},
            "rejections": 0,
            "citations_list": [],
        }
        for q in presets
    }

    all_latencies: list[float] = []

    print("\n" + "-" * 95)
    print(f"{'Run':<4} | {'Question':<32} | {'Latency':<10} | {'Source':<12} | {'Valid':<5} | {'Cits':<5} | Citations")
    print("-" * 95)

    for run_idx in range(1, runs + 1):
        for q in presets:
            t0 = time.perf_counter()
            resp = chatbot.answer_question(q, trace_data)
            duration_ms = (time.perf_counter() - t0) * 1000.0

            all_latencies.append(duration_ms)
            q_stats = stats_per_q[q]

            if run_idx == 1:
                q_stats["latencies_cold"].append(duration_ms)
            else:
                q_stats["latencies_warm"].append(duration_ms)

            q_stats["source_counts"][resp.source] = q_stats["source_counts"].get(resp.source, 0) + 1
            if not resp.validation_passed or resp.source == "template":
                if not simulate_down and client.is_available():
                    # Fallback triggered because validation or route rejected model output
                    q_stats["rejections"] += 1

            distinct_cits = len(set(resp.citations))
            q_stats["citations_list"].append(distinct_cits)
            cit_str = ", ".join(resp.citations) if resp.citations else "[]"

            short_q = q if len(q) <= 32 else q[:29] + "..."
            print(f"{run_idx:<4} | {short_q:<32} | {duration_ms:>7.1f} ms | {resp.source:<12} | {str(resp.validation_passed):<5} | {distinct_cits:<5} | {cit_str}")

    print("-" * 95)
    print("\n" + "=" * 95)
    print(" DETAILED PER-QUESTION REPORT (DEMO_MODE=0, MEASURED VALUES ONLY)")
    print("=" * 95)
    print(f"{'Question':<35} | {'Cold Latency':<12} | {'Warm Median':<12} | {'Warm Max':<10} | {'Model':<5} | {'Tmpl':<5} | {'Rejects':<7} | {'Avg Cits'}")
    print("-" * 95)

    for q in presets:
        qs = stats_per_q[q]
        cold_str = f"{qs['latencies_cold'][0]:.1f} ms" if qs["latencies_cold"] else "N/A"
        warm_lats = qs["latencies_warm"]
        warm_median = f"{statistics.median(warm_lats):.1f} ms" if warm_lats else "N/A"
        warm_max = f"{max(warm_lats):.1f} ms" if warm_lats else "N/A"
        model_cnt = qs["source_counts"].get("model", 0) + qs["source_counts"].get("cached-model", 0)
        tmpl_cnt = qs["source_counts"].get("template", 0) + qs["source_counts"].get("cached-template", 0)
        avg_cits = f"{sum(qs['citations_list'])/len(qs['citations_list']):.1f}" if qs["citations_list"] else "0"

        short_q = q if len(q) <= 35 else q[:32] + "..."
        print(f"{short_q:<35} | {cold_str:<12} | {warm_median:<12} | {warm_max:<10} | {model_cnt:<5} | {tmpl_cnt:<5} | {qs['rejections']:<7} | {avg_cits}")

    print("=" * 95)
    print(f"Overall Total Questions: {len(all_latencies)} | Global Min: {min(all_latencies):.1f} ms | Global Max: {max(all_latencies):.1f} ms")
    print("=" * 95 + "\n")


if __name__ == "__main__":
    import random

    parser = argparse.ArgumentParser(description="Rehearse AnviTrace Chatbot presets across any account")
    parser.add_argument("--runs", "-n", type=int, default=3, help="Number of test runs per account (default: 3)")
    parser.add_argument("--simulate-down", "--down", action="store_true", help="Simulate Ollama server being unreachable")
    parser.add_argument("--account", "-a", type=str, default=None, help="Target Account ID (required unless --random is provided)")
    parser.add_argument("--random", "-r", type=int, default=None, help="Rehearse on N random accounts from database")
    args = parser.parse_args()

    if not args.account and not args.random:
        parser.error("Either --account <ACCOUNT_ID> (required) or --random <N> must be specified.")

    if args.random:
        conn = get_connection()
        rows = conn.execute("SELECT DISTINCT Sender_Account FROM transactions LIMIT 500").fetchall()
        candidate_accs = [r[0] for r in rows if r[0]]
        sample_size = min(args.random, len(candidate_accs))
        random.seed(42)
        sampled = random.sample(candidate_accs, sample_size)
        print(f"\n[REHEARSAL] Running on {len(sampled)} randomly sampled accounts: {', '.join(sampled)}")
        for acc in sampled:
            print(f"\n==================== REHEARSING ACCOUNT: {acc} ====================")
            rehearse(runs=args.runs, simulate_down=args.simulate_down, account_id=acc)
    else:
        rehearse(runs=args.runs, simulate_down=args.simulate_down, account_id=args.account.strip().upper())
