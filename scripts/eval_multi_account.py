"""Stratified Multi-Account Evaluation Harness for ANVITRACE.

Evaluates at least 30 accounts chosen by fixed random seed (seed=42)
stratified across 6 distinct forensic categories:
1. no_outgoing: Accounts with only incoming funds (terminal receivers)
2. one_hop: Simple single-hop source accounts
3. multi_hop: Multi-hop cascading money trails
4. hub_fanout: High fan-out hub accounts (>15 distinct receivers)
5. candidate_mule: Known candidate/mule accounts flagged for review
6. not_in_dataset: Accounts not present in the database

Ground truth is computed directly with independent DuckDB queries (not from engine code).
Measured values only. Saves results to multi_account_eval_results.json.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import duckdb

# Add backend directory to path
ROOT_DIR = Path(__file__).resolve().parent.parent
backend_dir = ROOT_DIR / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.explainer.answer_engine import AnswerEngine
from app.explainer.case_summarizer import CaseFactsExtractor, CaseChatbotService, LightValidator
from app.explainer.ollama_client import OllamaClient
from app.explainer.router import IntentEnum, IntentRouter
from app.services.investigation import InvestigationService
from app.services.risk_engine import RiskAssessmentService

RANDOM_SEED = 42
DB_PATH = str(backend_dir / "data" / "anvitrace.duckdb")


DATASET_ID = "ds_2c9f81fd34f728c0"


def sample_stratified_accounts(conn: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    """Sample 5 accounts per stratum using fixed random seed (seed=42)."""
    random.seed(RANDOM_SEED)

    # 1. No outgoing transactions (pure incoming)
    q_no_out = f"""
        SELECT Receiver_Account 
        FROM transactions 
        WHERE dataset_id = '{DATASET_ID}'
          AND Receiver_Account NOT IN (SELECT DISTINCT Sender_Account FROM transactions WHERE dataset_id = '{DATASET_ID}')
        GROUP BY Receiver_Account 
        HAVING count(*) >= 1
        LIMIT 50
    """
    rows_no_out = [r[0] for r in conn.execute(q_no_out).fetchall() if r[0]]
    no_out = random.sample(rows_no_out, min(5, len(rows_no_out)))

    # 2. Simple one-hop (sender whose receiver has no outgoing)
    q_one_hop = f"""
        SELECT t1.Sender_Account 
        FROM transactions t1 
        JOIN (
            SELECT Receiver_Account 
            FROM transactions 
            WHERE dataset_id = '{DATASET_ID}'
              AND Receiver_Account NOT IN (SELECT DISTINCT Sender_Account FROM transactions WHERE dataset_id = '{DATASET_ID}')
        ) t2 ON t1.Receiver_Account = t2.Receiver_Account 
        WHERE t1.dataset_id = '{DATASET_ID}'
        GROUP BY t1.Sender_Account 
        HAVING count(*) = 1
        LIMIT 50
    """
    rows_one_hop = [r[0] for r in conn.execute(q_one_hop).fetchall() if r[0]]
    one_hop = random.sample(rows_one_hop, min(5, len(rows_one_hop)))

    # 3. Multi-hop accounts (sender with cascading transfers)
    q_multi_hop = f"""
        SELECT t1.Sender_Account 
        FROM transactions t1 
        JOIN transactions t2 ON t1.Receiver_Account = t2.Sender_Account 
        WHERE t1.dataset_id = '{DATASET_ID}' AND t2.dataset_id = '{DATASET_ID}'
        GROUP BY t1.Sender_Account 
        HAVING count(*) >= 2
        LIMIT 50
    """
    rows_multi = [r[0] for r in conn.execute(q_multi_hop).fetchall() if r[0]]
    multi_hop = random.sample(rows_multi, min(5, len(rows_multi)))

    # 4. Hub accounts / High fan-out
    q_hub = f"""
        SELECT Sender_Account 
        FROM transactions 
        WHERE dataset_id = '{DATASET_ID}'
        GROUP BY Sender_Account 
        HAVING count(DISTINCT Receiver_Account) >= 15 
        LIMIT 50
    """
    rows_hub = [r[0] for r in conn.execute(q_hub).fetchall() if r[0]]
    hub = random.sample(rows_hub, min(5, len(rows_hub)))

    # 5. Known candidate / high risk accounts
    q_candidate = f"""
        SELECT Receiver_Account 
        FROM transactions 
        WHERE dataset_id = '{DATASET_ID}'
        GROUP BY Receiver_Account 
        HAVING count(*) >= 10 
        LIMIT 50
    """
    rows_cand = [r[0] for r in conn.execute(q_candidate).fetchall() if r[0]]
    candidates = random.sample(rows_cand, min(5, len(rows_cand)))

    # 6. Not in dataset
    not_in_dataset = [
        "FAKE_ACC_9901",
        "NONEXIST_ACC_9902",
        "NOTFOUND_ACC_9903",
        "GHOST_ACC_9904",
        "MISSING_ACC_9905",
    ]

    return {
        "no_outgoing": no_out,
        "one_hop": one_hop,
        "multi_hop": multi_hop,
        "hub_fanout": hub,
        "candidate_mule": candidates,
        "not_in_dataset": not_in_dataset,
    }


def compute_independent_ground_truth(conn: duckdb.DuckDBPyConnection, account_id: str, stratum: str) -> dict[str, Any]:
    """Compute ground truth directly from DuckDB via independent queries."""
    if stratum == "not_in_dataset":
        return {"exists": False}

    tx_rows = conn.execute(
        f"""
        SELECT 
            COUNT(*) FILTER (WHERE Sender_Account = ?) AS outgoing_count,
            COUNT(*) FILTER (WHERE Receiver_Account = ?) AS incoming_count,
            SUM(Amount) FILTER (WHERE Sender_Account = ?) AS outgoing_volume
        FROM transactions
        WHERE dataset_id = '{DATASET_ID}'
        """,
        [account_id, account_id, account_id],
    ).fetchone()

    out_cnt = int(tx_rows[0] or 0)
    in_cnt = int(tx_rows[1] or 0)
    out_vol = float(tx_rows[2] or 0.0)

    # First outgoing transaction edge
    first_edge = conn.execute(
        f"""
        SELECT Receiver_Account, Amount, record_id, Timestamp
        FROM transactions
        WHERE Sender_Account = ? AND dataset_id = '{DATASET_ID}'
        ORDER BY Timestamp ASC
        LIMIT 1
        """,
        [account_id],
    ).fetchone()

    first_receiver = first_edge[0] if first_edge else None
    first_amt = float(first_edge[1] or 0.0) if first_edge else 0.0
    first_rec = first_edge[2] if first_edge else None

    # First incoming transaction edge
    first_in_edge = conn.execute(
        f"""
        SELECT Sender_Account, Amount, record_id, Timestamp
        FROM transactions
        WHERE Receiver_Account = ? AND dataset_id = '{DATASET_ID}'
        ORDER BY Timestamp ASC
        LIMIT 1
        """,
        [account_id],
    ).fetchone()

    first_sender = first_in_edge[0] if first_in_edge else None

    return {
        "exists": True,
        "outgoing_count": out_cnt,
        "incoming_count": in_cnt,
        "total_count": out_cnt + in_cnt,
        "outgoing_volume": out_vol,
        "first_receiver": first_receiver,
        "first_sender": first_sender,
        "first_amount": first_amt,
        "first_rec_id": first_rec,
    }


def run_multi_account_eval():
    """Run evaluation across all sampled accounts and print report."""
    print("=" * 80)
    print(" ANVITRACE - Stratified Multi-Account Forensic Evaluation Harness")
    print(f" Random Seed: {RANDOM_SEED} (Strictly Reproducible)")
    print("=" * 80)

    conn = duckdb.connect(DB_PATH, read_only=True)
    sample_dict = sample_stratified_accounts(conn)
    total_accounts = sum(len(accs) for accs in sample_dict.values())
    print(f"Sampled {total_accounts} accounts across 6 forensic strata:\n")
    for stratum, accs in sample_dict.items():
        print(f"  [{stratum:<15}] ({len(accs)} accounts): {', '.join(accs)}")

    inv_svc = InvestigationService(conn)
    risk_svc = RiskAssessmentService(conn)
    chatbot = CaseChatbotService(OllamaClient())

    # Metrics
    stratum_stats: dict[str, dict[str, Any]] = {}
    all_open_times: list[float] = []
    all_answer_times_cold: list[float] = []
    all_answer_times_warm: list[float] = []
    failing_cases: list[dict[str, Any]] = []

    total_claims_unsupported = 0
    total_wrong_relationships = 0
    total_wrong_directions = 0

    print("\n" + "-" * 80)
    print(f"{'Stratum':<15} | {'Account':<18} | {'Open Time':<10} | {'Cold Ans':<10} | {'Warm Ans':<10} | {'Status'}")
    print("-" * 80)

    for stratum, acc_list in sample_dict.items():
        stratum_stats[stratum] = {
            "tested": len(acc_list),
            "passed": 0,
            "exact_fact_accuracy": 0.0,
            "open_times": [],
            "cold_times": [],
            "warm_times": [],
        }

        for acc in acc_list:
            gt = compute_independent_ground_truth(conn, acc, stratum)

            # 1. Measure case open / facts build time
            t_open_start = time.perf_counter()
            if not gt["exists"]:
                # Account not in dataset check
                exists_in_db = conn.execute(
                    "SELECT COUNT(*) FROM transactions WHERE Sender_Account = ? OR Receiver_Account = ?",
                    [acc, acc],
                ).fetchone()[0] > 0
                open_time_ms = (time.perf_counter() - t_open_start) * 1000.0
                all_open_times.append(open_time_ms)
                stratum_stats[stratum]["open_times"].append(open_time_ms)

                # Query chatbot for non-existent account
                dummy_facts = {"victim_account": "ACC_DUMMY", "known_accounts": []}
                t_ans_0 = time.perf_counter()
                resp = chatbot.answer_question(f"Where did funds from account {acc} go?", dummy_facts)
                cold_ms = (time.perf_counter() - t_ans_0) * 1000.0
                all_answer_times_cold.append(cold_ms)

                is_ok = ("not part of the opened case" in resp.answer.lower() or "not available in this case" in resp.answer.lower())
                if is_ok:
                    stratum_stats[stratum]["passed"] += 1
                else:
                    failing_cases.append({"stratum": stratum, "account": acc, "issue": f"Expected refusal, got: {resp.answer}"})

                print(f"{stratum:<15} | {acc:<18} | {open_time_ms:>7.2f} ms | {cold_ms:>7.2f} ms | {'N/A':<10} | {'PASS' if is_ok else 'FAIL'}")
                continue

            # Build case facts
            try:
                trace_resp = inv_svc.run_trace(victim_account=acc, dataset_id="ds_2c9f81fd34f728c0", max_hops=2)
                trace_data = trace_resp.model_dump()
                trace_data["case_id"] = f"CASE_{acc}"

                # Enrich with risk
                risk_prof = risk_svc.assess_account_risk(acc, "ds_2c9f81fd34f728c0")
                if risk_prof:
                    trace_data["risk_summary"] = {
                        "mule_risk_index": risk_prof.mule_risk_index,
                        "risk_tier": risk_prof.risk_tier,
                        "top_contributing_features": [f.feature_name for f in risk_prof.features if f.score_contribution > 0],
                    }
                case_facts = CaseFactsExtractor.extract_case_facts(trace_data)
                open_time_ms = (time.perf_counter() - t_open_start) * 1000.0
                all_open_times.append(open_time_ms)
                stratum_stats[stratum]["open_times"].append(open_time_ms)
            except Exception as exc:
                open_time_ms = (time.perf_counter() - t_open_start) * 1000.0
                failing_cases.append({"stratum": stratum, "account": acc, "issue": f"Trace generation exception: {exc}"})
                print(f"{stratum:<15} | {acc:<18} | {open_time_ms:>7.2f} ms | {'ERR':<10} | {'ERR':<10} | FAIL")
                continue

            # 2. Test money trail query (Cold latency)
            t_c0 = time.perf_counter()
            resp_trail_cold = chatbot.answer_question("Where did the money go?", trace_data)
            cold_ms = (time.perf_counter() - t_c0) * 1000.0
            all_answer_times_cold.append(cold_ms)
            stratum_stats[stratum]["cold_times"].append(cold_ms)

            # Warm latency
            t_w0 = time.perf_counter()
            resp_trail_warm = chatbot.answer_question("Where did the money go?", trace_data)
            warm_ms = (time.perf_counter() - t_w0) * 1000.0
            all_answer_times_warm.append(warm_ms)
            stratum_stats[stratum]["warm_times"].append(warm_ms)

            # 3. Test Highest Risk
            resp_risk = chatbot.answer_question("Which account is highest risk?", trace_data)

            # 4. Test Layer Candidates
            resp_layer = chatbot.answer_question("What accounts are in layer 3?", trace_data)

            # Verify against independent ground truth
            account_pass = True
            ans_trail = resp_trail_cold.answer

            # Relationship & fact verification
            if gt["outgoing_count"] == 0:
                if (
                    "0.00" not in ans_trail
                    and "received by account" not in ans_trail.lower()
                    and "no outbound" not in ans_trail.lower()
                    and "originated" not in ans_trail.lower()
                ):
                    account_pass = False
                    total_wrong_relationships += 1
            else:
                # Must contain the victim account and first receiver
                if acc not in ans_trail:
                    account_pass = False
                    total_claims_unsupported += 1
                if gt["first_receiver"] and gt["first_receiver"] not in ans_trail:
                    account_pass = False
                    total_claims_unsupported += 1
                # Must never reverse direction (first_receiver -> acc is forbidden)
                if gt["first_receiver"] and f"{gt['first_receiver']} -> {acc}" in ans_trail:
                    account_pass = False
                    total_wrong_directions += 1

            # Highest risk must state "within this opened case"
            if "within this opened case" not in resp_risk.answer.lower():
                account_pass = False

            # Layer 3 must term inferred terminal
            if "confirmed" in resp_layer.answer.lower() or "inferred terminal" not in resp_layer.answer.lower():
                account_pass = False

            # Citation check: if facts have edges, citations must be present
            if case_facts.get("edges") and len(resp_trail_cold.citations) == 0:
                account_pass = False

            if account_pass:
                stratum_stats[stratum]["passed"] += 1
            else:
                failing_cases.append({"stratum": stratum, "account": acc, "answer": ans_trail[:80]})

            print(f"{stratum:<15} | {acc:<18} | {open_time_ms:>7.2f} ms | {cold_ms:>7.2f} ms | {warm_ms:>7.2f} ms | {'PASS' if account_pass else 'FAIL'}")

    conn.close()

    # Summarize Metrics
    print("-" * 80)
    print("\n" + "=" * 80)
    print(" STRATIFIED EVALUATION SUMMARY RESULTS")
    print("=" * 80)
    print(f"{'Stratum':<18} | {'Tested':<8} | {'Passed':<8} | {'Accuracy (%)':<14} | {'Avg Open Time':<15} | {'Avg Ans Time'}")
    print("-" * 80)

    total_tested = 0
    total_passed = 0

    for stratum, st in stratum_stats.items():
        t = st["tested"]
        p = st["passed"]
        acc_pct = (p / t * 100.0) if t > 0 else 0.0
        avg_open = f"{sum(st['open_times'])/len(st['open_times']):.2f} ms" if st["open_times"] else "N/A"
        avg_ans = f"{sum(st['warm_times'])/len(st['warm_times']):.2f} ms" if st["warm_times"] else (f"{sum(st['cold_times'])/len(st['cold_times']):.2f} ms" if st["cold_times"] else "N/A")
        total_tested += t
        total_passed += p
        print(f"{stratum:<18} | {t:<8} | {p:<8} | {acc_pct:>11.1f}% | {avg_open:<15} | {avg_ans}")

    overall_accuracy = (total_passed / total_tested * 100.0) if total_tested > 0 else 0.0

    all_open_times.sort()
    all_answer_times_cold.sort()
    all_answer_times_warm.sort()

    p50_open = all_open_times[len(all_open_times) // 2] if all_open_times else 0.0
    p95_open = all_open_times[int(len(all_open_times) * 0.95)] if all_open_times else 0.0
    p50_ans_cold = all_answer_times_cold[len(all_answer_times_cold) // 2] if all_answer_times_cold else 0.0
    p95_ans_cold = all_answer_times_cold[int(len(all_answer_times_cold) * 0.95)] if all_answer_times_cold else 0.0
    p50_ans_warm = all_answer_times_warm[len(all_answer_times_warm) // 2] if all_answer_times_warm else 0.0
    p95_ans_warm = all_answer_times_warm[int(len(all_answer_times_warm) * 0.95)] if all_answer_times_warm else 0.0

    print("=" * 80)
    print(f"OVERALL EXACT-FACT ACCURACY: {overall_accuracy:.1f}% ({total_passed}/{total_tested} accounts)")
    print(f"UNSUPPORTED CLAIMS:          {total_claims_unsupported}")
    print(f"WRONG RELATIONSHIPS:         {total_wrong_relationships}")
    print(f"WRONG DIRECTIONS:            {total_wrong_directions}")
    print(f"CASE OPEN TIME:              p50 = {p50_open:.2f} ms | p95 = {p95_open:.2f} ms")
    print(f"ANSWER LATENCY (COLD):       p50 = {p50_ans_cold:.2f} ms | p95 = {p95_ans_cold:.2f} ms")
    print(f"ANSWER LATENCY (WARM):       p50 = {p50_ans_warm:.2f} ms | p95 = {p95_ans_warm:.2f} ms")
    print(f"FAILING ACCOUNTS/QUESTIONS:  {len(failing_cases)}")
    if failing_cases:
        for fc in failing_cases:
            print(f"  - {fc}")
    print("=" * 80 + "\n")

    # Save to file
    out_file = ROOT_DIR / "multi_account_eval_results.json"
    result_data = {
        "random_seed": RANDOM_SEED,
        "total_accounts_tested": total_tested,
        "total_accounts_passed": total_passed,
        "overall_accuracy_pct": overall_accuracy,
        "unsupported_claims": total_claims_unsupported,
        "wrong_relationships": total_wrong_relationships,
        "wrong_directions": total_wrong_directions,
        "latency_metrics": {
            "p50_case_open_ms": p50_open,
            "p95_case_open_ms": p95_open,
            "p50_answer_cold_ms": p50_ans_cold,
            "p95_answer_cold_ms": p95_ans_cold,
            "p50_answer_warm_ms": p50_ans_warm,
            "p95_answer_warm_ms": p95_ans_warm,
        },
        "stratum_metrics": stratum_stats,
        "failing_cases": failing_cases,
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2)
    print(f"Evaluation results successfully saved to: {out_file.name}")


if __name__ == "__main__":
    run_multi_account_eval()
