"""Unit tests for Multi-Account Case Explainer and Chatbot.

Verifies:
1. Parametrized tests over synthetic accounts across different topologies (no outgoing, simple one-hop, multi-hop, hub/fanout, candidate).
2. Grep-style integrity test ensuring no demo account IDs appear in production code.
3. Cache isolation test ensuring distinct cache keys between accounts.
4. Non-existent account refusal without model guesswork.
5. Single-hop grammar and opened-case scope phrasing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import pytest

from app.explainer.answer_engine import AnswerEngine
from app.explainer.case_summarizer import CaseFactsExtractor, CaseChatbotService, LightValidator
from app.explainer.ollama_client import OllamaClient
from app.explainer.router import IntentEnum, IntentRouter


SYNTHETIC_ACCOUNTS = [
    # 1. No outgoing transactions (receiver / terminal node)
    {
        "case_id": "CASE_ACC_RECV_01",
        "victim_account": "ACC_RECV_01",
        "victim_transaction": {"Amount": 0.0, "amount": 0.0},
        "money_trail": [],
        "layer_candidates": [],
        "risk_summary": {"mule_risk_index": 15.0, "risk_tier": "LOW", "top_contributing_features": []},
        "expected_edge_count": 0,
        "is_no_outgoing": True,
    },
    # 2. Simple one-hop
    {
        "case_id": "CASE_ACC_ONEHOP_01",
        "victim_account": "ACC_ONEHOP_01",
        "victim_transaction": {"Amount": 25000.0, "amount": 25000.0, "record_id": "REC_OH_01"},
        "money_trail": [
            {
                "record_id": "REC_OH_01",
                "Transaction_ID": "TXN_OH_01",
                "from_account": "ACC_ONEHOP_01",
                "to_account": "ACC_COLL_01",
                "amount": 25000.0,
                "timestamp": "2026-03-01 10:00:00",
                "hop": 1,
            }
        ],
        "layer_candidates": [
            {"account_id": "ACC_COLL_01", "role": "Layer 1: Collector Candidate", "layer": 1, "score": 85, "evidence_summary": "First receiver"}
        ],
        "risk_summary": {"mule_risk_index": 85.0, "risk_tier": "CRITICAL", "top_contributing_features": ["rapid_pass_through"]},
        "expected_edge_count": 1,
        "is_no_outgoing": False,
    },
    # 3. Multi-hop (3 hops)
    {
        "case_id": "CASE_ACC_MHOP_01",
        "victim_account": "ACC_MHOP_01",
        "victim_transaction": {"Amount": 100000.0, "amount": 100000.0, "record_id": "REC_MH_01"},
        "money_trail": [
            {"record_id": "REC_MH_01", "from_account": "ACC_MHOP_01", "to_account": "ACC_MHOP_L1", "amount": 100000.0, "timestamp": "2026-03-02 10:00:00", "hop": 1},
            {"record_id": "REC_MH_02", "from_account": "ACC_MHOP_L1", "to_account": "ACC_MHOP_L2", "amount": 95000.0, "timestamp": "2026-03-02 10:15:00", "hop": 2},
            {"record_id": "REC_MH_03", "from_account": "ACC_MHOP_L2", "to_account": "ACC_MHOP_L3", "amount": 90000.0, "timestamp": "2026-03-02 10:30:00", "hop": 3},
        ],
        "layer_candidates": [
            {"account_id": "ACC_MHOP_L1", "role": "Layer 1: Collector Candidate", "layer": 1, "score": 88},
            {"account_id": "ACC_MHOP_L2", "role": "Layer 2: Distributor Candidate", "layer": 2, "score": 92},
            {"account_id": "ACC_MHOP_L3", "role": "Layer 3: Terminal Node", "layer": 3, "score": 75},
        ],
        "risk_summary": {"mule_risk_index": 92.0, "risk_tier": "CRITICAL", "top_contributing_features": ["layering", "high_velocity"]},
        "expected_edge_count": 3,
        "is_no_outgoing": False,
    },
    # 4. Hub account / High fan-out (>50 edges)
    {
        "case_id": "CASE_ACC_HUB_01",
        "victim_account": "ACC_HUB_01",
        "victim_transaction": {"Amount": 500000.0, "amount": 500000.0, "record_id": "REC_HUB_00"},
        "money_trail": [
            {
                "record_id": f"REC_HUB_{i:03d}",
                "from_account": "ACC_HUB_01",
                "to_account": f"ACC_MULE_{i:03d}",
                "amount": 5000.0,
                "timestamp": f"2026-03-03 12:{i%60:02d}:00",
                "hop": 1,
            }
            for i in range(1, 80)  # 79 edges > 50
        ],
        "layer_candidates": [
            {"account_id": f"ACC_MULE_{i:03d}", "role": "Layer 1: Collector", "layer": 1, "score": 80}
            for i in range(1, 80)
        ],
        "risk_summary": {"mule_risk_index": 95.0, "risk_tier": "CRITICAL", "top_contributing_features": ["high_fan_out"]},
        "expected_edge_count": 50,  # capped at 50
        "is_no_outgoing": False,
    },
    # 5. Known candidate / mule account under investigation
    {
        "case_id": "CASE_ACC_MULE_CRIT",
        "victim_account": "ACC_MULE_CRIT",
        "victim_transaction": {"Amount": 75000.0, "amount": 75000.0, "record_id": "REC_MC_01"},
        "money_trail": [
            {"record_id": "REC_MC_01", "from_account": "ACC_MULE_CRIT", "to_account": "ACC_TERM_99", "amount": 75000.0, "timestamp": "2026-03-04 09:00:00", "hop": 1}
        ],
        "layer_candidates": [
            {"account_id": "ACC_MULE_CRIT", "role": "Layer 1: Collector Candidate", "layer": 1, "score": 98, "evidence_summary": "Instant drain upon deposit"}
        ],
        "risk_summary": {"mule_risk_index": 98.0, "risk_tier": "CRITICAL", "top_contributing_features": ["instant_pass_through"]},
        "expected_edge_count": 1,
        "is_no_outgoing": False,
    },
]


@pytest.mark.parametrize("acc_case", SYNTHETIC_ACCOUNTS)
def test_case_facts_bounded_and_fast(acc_case: dict[str, Any]):
    """Verify CaseFactsExtractor correctly extracts and caps rows for any account."""
    facts = CaseFactsExtractor.extract_case_facts(acc_case)
    assert facts["victim_account"] == acc_case["victim_account"]
    assert len(facts["edges"]) <= 50

    if len(acc_case["money_trail"]) > 50:
        assert facts["truncated_edges"] is True
        assert facts["total_edges_count"] == len(acc_case["money_trail"])
        assert len(facts["edges"]) == 50
    else:
        assert facts["truncated_edges"] is False
        assert len(facts["edges"]) == acc_case["expected_edge_count"]


@pytest.mark.parametrize("acc_case", SYNTHETIC_ACCOUNTS)
def test_answer_engine_accurate_by_construction(acc_case: dict[str, Any]):
    """Verify AnswerEngine answers for any account contain zero hallucinations and correct scope."""
    facts = CaseFactsExtractor.extract_case_facts(acc_case)
    
    # 1. Money trail
    trail = AnswerEngine.money_trail(facts)
    trail_text = " ".join(s["text"] for s in trail["sentences"])
    assert facts["victim_account"] in trail_text
    if facts.get("truncated_edges"):
        assert "partial list" in trail_text.lower()
    if facts.get("edges") and facts.get("hops_found") == 1:
        assert "1 observed hop" in trail_text
        assert "1 hops" not in trail_text

    # 2. Highest risk
    risk = AnswerEngine.highest_risk(facts)
    risk_text = " ".join(s["text"] for s in risk["sentences"])
    assert "within this opened case" in risk_text.lower()

    # 3. Evidence status
    ev = AnswerEngine.evidence_status(facts)
    assert len(ev["sentences"]) >= 1

    # 4. Next steps
    ns = AnswerEngine.next_steps(facts)
    assert len(ns["sentences"]) >= 1


def test_grep_no_hardcoded_accounts_in_production():
    """Grep-style test ensuring no hardcoded demo account IDs appear in production code."""
    prod_dirs = [
        Path(__file__).resolve().parent.parent.parent / "app" / "explainer",
        Path(__file__).resolve().parent.parent.parent / "app" / "routers",
    ]
    forbidden_tokens = ["HDFC10000336", "BARB10000885", "CNRB10000886", "INDB10000887"]

    for pdir in prod_dirs:
        for py_file in pdir.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for token in forbidden_tokens:
                assert token not in content, f"Forbidden demo token '{token}' found in production file: {py_file.name}"


def test_cache_isolation_between_two_accounts(monkeypatch):
    """Verify that DEMO_MODE cache keys strictly isolate different accounts."""
    monkeypatch.setenv("DEMO_MODE", "1")
    service = CaseChatbotService(OllamaClient())

    case_a = SYNTHETIC_ACCOUNTS[1]  # ACC_ONEHOP_01
    case_b = SYNTHETIC_ACCOUNTS[2]  # ACC_MHOP_01

    resp_a = service.answer_question("Where did the money go?", case_a)
    resp_b = service.answer_question("Where did the money go?", case_b)

    assert "ACC_ONEHOP_01" in resp_a.answer
    assert "ACC_MHOP_01" in resp_b.answer
    assert resp_a.answer != resp_b.answer, "Account B must not receive cached answer from Account A"


def test_different_account_refusal_when_querying_other_account():
    """Verify that asking about an account not in the opened case returns the clear refusal."""
    service = CaseChatbotService(OllamaClient())
    case_a = SYNTHETIC_ACCOUNTS[1]  # ACC_ONEHOP_01

    # Query names a different account not in case_a
    resp = service.answer_question("Why was account UNKNOWN9999 flagged?", case_a)
    assert resp.answer == "That account is not part of the opened case."
    assert len(resp.citations) == 0


def test_highest_risk_excludes_victim_account():
    """Verify that opened victim account is never ranked as candidate in its own case."""
    victim = "ACC_VICTIM_01"
    facts = {
        "victim_account": victim,
        "layer_candidates": [
            {"account_id": victim, "role": "Layer 1 Collector", "score": 95, "evidence_summary": "Victim transfer"},
            {"account_id": "ACC_MULE_REAL", "role": "Layer 1 Collector", "score": 80, "evidence_summary": "Rapid transfer", "record_ids": ["rec_mule_01"]},
        ],
        "edges": [{"from_account": victim, "to_account": "ACC_MULE_REAL", "amount": 1000.0, "record_id": "rec_mule_01"}],
    }

    resp = AnswerEngine.highest_risk(facts)
    full_text = " ".join(s["text"] for s in resp["sentences"])

    assert "ACC_MULE_REAL" in full_text
    assert resp["data"]["highest_risk_account"] == "ACC_MULE_REAL"
    assert f"highest risk score is {victim}" not in full_text
    assert resp["data"]["highest_risk_account"] != victim


def test_highest_risk_deduplicates_accounts():
    """Verify accounts appearing in multiple layers are deduplicated into one entry."""
    victim = "ACC_VICTIM_02"
    facts = {
        "victim_account": victim,
        "layer_candidates": [
            {"account_id": "ACC_MULTI_ROLE", "role": "Layer 1 Collector", "score": 85, "evidence_summary": "First leg", "record_ids": ["rec_01"]},
            {"account_id": "ACC_MULTI_ROLE", "role": "Layer 2 Distributor", "score": 85, "evidence_summary": "Second leg", "record_ids": ["rec_02"]},
            {"account_id": "ACC_OTHER_MULE", "role": "Layer 3 Inferred Terminal", "score": 70, "evidence_summary": "Cashout", "record_ids": ["rec_03"]},
        ],
        "edges": [
            {"from_account": victim, "to_account": "ACC_MULTI_ROLE", "amount": 5000.0, "record_id": "rec_01"},
            {"from_account": "ACC_MULTI_ROLE", "to_account": "ACC_OTHER_MULE", "amount": 5000.0, "record_id": "rec_02"},
        ],
    }

    resp = AnswerEngine.highest_risk(facts)
    top_cands = resp["data"]["top_candidates"]
    accounts_in_ranking = [c["account_id"] for c in top_cands]

    # ACC_MULTI_ROLE must appear exactly ONCE
    assert accounts_in_ranking.count("ACC_MULTI_ROLE") == 1
    # Both roles should be consolidated in roles list
    multi_role_entry = next(c for c in top_cands if c["account_id"] == "ACC_MULTI_ROLE")
    assert "Layer 1 Collector" in multi_role_entry["roles"]
    assert "Layer 2 Distributor" in multi_role_entry["roles"]


def test_highest_risk_supporting_citations_only():
    """Verify risk claims cite only supporting records, or state no record found."""
    victim = "ACC_VICTIM_03"
    facts_with_recs = {
        "victim_account": victim,
        "primary_citation": "rec_victim_unrelated_00",
        "layer_candidates": [
            {"account_id": "ACC_CAND_01", "role": "Layer 1 Collector", "score": 90, "evidence_summary": "Direct receiver", "record_ids": ["rec_cand_support_01"]}
        ],
        "edges": [{"from_account": victim, "to_account": "ACC_CAND_01", "amount": 2000.0, "record_id": "rec_cand_support_01"}],
    }

    resp1 = AnswerEngine.highest_risk(facts_with_recs)
    assert "rec_cand_support_01" in resp1["sentences"][0]["record_ids"]
    assert "rec_victim_unrelated_00" not in resp1["sentences"][0]["record_ids"]

    # When no supporting record exists for the candidate
    facts_no_recs = {
        "victim_account": victim,
        "primary_citation": "rec_victim_unrelated_00",
        "layer_candidates": [
            {"account_id": "ACC_GHOST_CAND", "role": "Layer 1 Collector", "score": 90, "evidence_summary": "Synthetic"}
        ],
        "edges": [],
    }
    resp2 = AnswerEngine.highest_risk(facts_no_recs)
    assert len(resp2["sentences"][0]["record_ids"]) == 0
    assert "No supporting transaction record found in evidence" in resp2["sentences"][0]["text"]
    assert "rec_victim_unrelated_00" not in resp2["sentences"][0]["record_ids"]


def test_highest_risk_tie_wording_and_no_candidates():
    """Verify tie wording when scores match and neutral wording when no candidates exist."""
    victim = "ACC_VICTIM_04"
    facts_tie = {
        "victim_account": victim,
        "layer_candidates": [
            {"account_id": "ACC_TIE_A", "role": "Layer 1 Collector", "score": 85, "evidence_summary": "Hop 1", "record_ids": ["rec_a"]},
            {"account_id": "ACC_TIE_B", "role": "Layer 1 Collector", "score": 85, "evidence_summary": "Hop 1", "record_ids": ["rec_b"]},
        ],
        "edges": [
            {"from_account": victim, "to_account": "ACC_TIE_A", "amount": 1000.0, "record_id": "rec_a"},
            {"from_account": victim, "to_account": "ACC_TIE_B", "amount": 1000.0, "record_id": "rec_b"},
        ],
    }

    resp_tie = AnswerEngine.highest_risk(facts_tie)
    tie_text = resp_tie["sentences"][0]["text"]
    assert "share the highest risk score of 85.0/100" in tie_text
    assert "ACC_TIE_A" in tie_text
    assert "ACC_TIE_B" in tie_text
    assert len(resp_tie["data"]["tied_accounts"]) == 2

    # No candidates beyond victim
    facts_no_cand = {
        "victim_account": victim,
        "layer_candidates": [{"account_id": victim, "role": "Victim", "score": 90}],
        "edges": [],
    }
    resp_none = AnswerEngine.highest_risk(facts_no_cand)
    none_text = resp_none["sentences"][0]["text"]
    assert "no candidate accounts have been identified beyond the opened victim account" in none_text
    assert resp_none["data"]["highest_risk_account"] is None

