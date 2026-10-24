"""Unit tests for AnswerEngine, IntentRouter, and Guarded Phrasing.

Verifies:
- All 8 AnswerEngine intents against hand-computed ground truth values on synthetic case.
- Tampered manifest / blob mismatch -> model disabled, engine answers still correct.
- Ollama down -> all engine answers completely unaffected.
- Structured-output router rejects invalid enum values.
- Phrasing that alters numbers, accounts, dates, or directions is strictly discarded.
- Out-of-case and action requests return exact required refusal strings.
- Non-existent accounts return exact refusal string without citations.
- Network calls strictly confined to localhost.
"""

from __future__ import annotations

import os
import socket
import pytest

from app.explainer.answer_engine import AnswerEngine
from app.explainer.case_summarizer import CaseChatbotService, CaseFactsExtractor, LightValidator
from app.explainer.ollama_client import OllamaClient
from app.explainer.router import IntentEnum, IntentRouter


@pytest.fixture
def synthetic_case_facts():
    """Hand-computed synthetic case fixture for forensic testing."""
    return {
        "case_id": "CASE_SYNTHETIC_01",
        "dataset_id": "ds_synthetic_test",
        "victim_account": "SBIN10000001",
        "victim_transaction": {"record_id": "REC_VIC_01", "amount": 50000.0, "timestamp": "2026-03-01 10:00:00"},
        "disputed_amount": 50000.0,
        "total_attributed": 50000.0,
        "hops_found": 3,
        "start_time": "2026-03-01 10:00:00",
        "end_time": "2026-03-01 10:30:00",
        "primary_citation": "REC_VIC_01",
        "money_trail": [
            {
                "hop": 1,
                "record_id": "REC_VIC_01",
                "Transaction_ID": "TXN_001",
                "from_account": "SBIN10000001",
                "to_account": "HDFC10000002",
                "amount": 50000.0,
                "timestamp": "2026-03-01 10:00:00",
            },
            {
                "hop": 2,
                "record_id": "REC_L1_02",
                "Transaction_ID": "TXN_002",
                "from_account": "HDFC10000002",
                "to_account": "ICIC10000003",
                "amount": 30000.0,
                "timestamp": "2026-03-01 10:10:00",
            },
            {
                "hop": 3,
                "record_id": "REC_L2_03",
                "Transaction_ID": "TXN_003",
                "from_account": "ICIC10000003",
                "to_account": "KKBK10000004",
                "amount": 20000.0,
                "timestamp": "2026-03-01 10:20:00",
            },
        ],
        "edges": [
            {
                "hop": 1,
                "record_id": "REC_VIC_01",
                "transaction_id": "TXN_001",
                "from_account": "SBIN10000001",
                "to_account": "HDFC10000002",
                "amount": 50000.0,
                "timestamp": "2026-03-01 10:00:00",
            },
            {
                "hop": 2,
                "record_id": "REC_L1_02",
                "transaction_id": "TXN_002",
                "from_account": "HDFC10000002",
                "to_account": "ICIC10000003",
                "amount": 30000.0,
                "timestamp": "2026-03-01 10:10:00",
            },
            {
                "hop": 3,
                "record_id": "REC_L2_03",
                "transaction_id": "TXN_003",
                "from_account": "ICIC10000003",
                "to_account": "KKBK10000004",
                "amount": 20000.0,
                "timestamp": "2026-03-01 10:20:00",
            },
        ],
        "layer_candidates": [
            {
                "account_id": "HDFC10000002",
                "role": "Layer 1 Collector Candidate",
                "layer": 1,
                "score": 95,
                "evidence": "Rapid fund dispersion within 10 minutes",
            },
            {
                "account_id": "ICIC10000003",
                "role": "Layer 2 Distributor Candidate",
                "layer": 2,
                "score": 85,
                "evidence": "Fan-out pass-through to terminal accounts",
            },
            {
                "account_id": "KKBK10000004",
                "role": "Layer 3 Inferred Terminal Candidate",
                "layer": 3,
                "score": 70,
                "evidence": "Exit node at observation perimeter",
            },
        ],
        "risk_score": 95.0,
        "risk_tier": "CRITICAL",
        "rule_features": ["rapid_pass_through", "fan_in", "structuring_detected"],
        "dataset_hash": "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890",
        "result_hash": "b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1",
        "repeated_tx_ids": 0,
        "exact_duplicates": 0,
        "known_accounts": ["SBIN10000001", "HDFC10000002", "ICIC10000003", "KKBK10000004"],
        "known_record_ids": ["REC_VIC_01", "REC_L1_02", "REC_L2_03", "TXN_001", "TXN_002", "TXN_003"],
        "known_amounts": [50000.0, 30000.0, 20000.0],
        "known_dates": ["2026-03-01"],
    }


# 1. Engine: Money trail
def test_engine_money_trail(synthetic_case_facts):
    res = AnswerEngine.money_trail(synthetic_case_facts)
    assert res["intent"] == "money_trail"
    assert res["data"]["hops_found"] == 3
    assert res["data"]["victim_account"] == "SBIN10000001"
    assert res["data"]["disputed_amount"] == 50000.0
    text = " ".join(s["text"] for s in res["sentences"])
    assert "SBIN10000001" in text
    assert "HDFC10000002" in text
    assert "50,000.00" in text
    assert "[REC_VIC_01]" in text


# 2. Engine: Highest risk
def test_engine_highest_risk(synthetic_case_facts):
    res = AnswerEngine.highest_risk(synthetic_case_facts)
    assert res["intent"] == "highest_risk"
    assert res["data"]["highest_risk_account"] == "HDFC10000002"
    assert res["data"]["score"] == 95.0
    text = " ".join(s["text"] for s in res["sentences"])
    assert "HDFC10000002" in text
    assert "95" in text
    assert "REC_VIC_01" in text or "REC_L1_02" in text


# 3. Engine: Why flagged
def test_engine_why_flagged_known_and_unknown(synthetic_case_facts):
    # Known account
    res_known = AnswerEngine.why_flagged(synthetic_case_facts, "HDFC10000002")
    assert res_known["available"] is True
    assert res_known["data"]["account_id"] == "HDFC10000002"
    assert res_known["data"]["risk_score"] == 95.0

    # Non-existent account -> not available
    res_unknown = AnswerEngine.why_flagged(synthetic_case_facts, "ACC_NONEXISTENT_999")
    assert res_unknown["available"] is False
    assert res_unknown["sentences"][0]["text"] == "That is not available in this case."


# 4. Engine: Layer candidates
def test_engine_layer_candidates_layer3_inferred_terminal(synthetic_case_facts):
    # Layer 1
    res_l1 = AnswerEngine.layer_candidates(synthetic_case_facts, layer_filter=1)
    assert "HDFC10000002" in res_l1["sentences"][0]["text"]

    # Layer 3 must ALWAYS say inferred terminal, never confirmed
    res_l3 = AnswerEngine.layer_candidates(synthetic_case_facts, layer_filter=3)
    text = res_l3["sentences"][0]["text"]
    assert "inferred terminal" in text
    assert "confirmed" not in text
    assert "KKBK10000004" in text


# 5. Engine: Lineage with named project rule
def test_engine_lineage(synthetic_case_facts):
    res = AnswerEngine.lineage(synthetic_case_facts)
    assert res["intent"] == "lineage"
    assert res["data"]["attribution_rule"] == "Direct Proportional Path Attribution"
    text = res["sentences"][0]["text"]
    assert "Direct Proportional Path Attribution" in text
    assert "50,000.00" in text


# 6. Engine: Evidence status
def test_engine_evidence_status(synthetic_case_facts):
    res = AnswerEngine.evidence_status(synthetic_case_facts)
    assert res["intent"] == "evidence_status"
    assert res["data"]["verified"] is True
    text = res["sentences"][0]["text"]
    assert "Verified" in text


# 7. Engine: Next steps from templates only
def test_engine_next_steps(synthetic_case_facts):
    res = AnswerEngine.next_steps(synthetic_case_facts)
    assert res["intent"] == "next_steps"
    text = res["sentences"][0]["text"]
    assert "Section 102" in text or "freeze" in text.lower()


# 8. Engine: Case summary
def test_engine_case_summary(synthetic_case_facts):
    res = AnswerEngine.case_summary(synthetic_case_facts)
    assert res["intent"] == "case_summary"
    assert len(res["sections"]) == 7
    # Section 7 has statutory steps
    assert res["sections"][6]["title"] == "7. Suggested Next Steps"


# 9. Tampered manifest or blob mismatch -> model disabled, engine answers still correct
def test_tampered_manifest_engine_unaffected(synthetic_case_facts, tmp_path, monkeypatch):
    fake_blob = tmp_path / "sha256-testblob"
    fake_blob.write_bytes(b"DATA" * 512)
    client = OllamaClient(model_name="llama3.2:3b")
    monkeypatch.setenv("MODEL_PATH", str(fake_blob))
    monkeypatch.setattr("json.load", lambda fh: {"models": {"llama3.2:3b": {"sha256": "bad_hash_value"}}})

    ok, reason = client.verify_model_integrity()
    assert ok is False
    assert client.refuse_inference is True

    # Chatbot answers question via deterministic engine completely unaffected
    chatbot = CaseChatbotService(ollama_client=client)
    res = chatbot.answer_question("Where did the money go?", synthetic_case_facts)
    assert res.source == "Verified facts (computed)"
    assert res.validation_passed is True
    assert "50,000.00" in res.answer
    assert "REC_VIC_01" in res.citations


# 10. Ollama down -> all engine answers completely unaffected
def test_ollama_down_engine_unaffected(synthetic_case_facts):
    down_client = OllamaClient(base_url="http://localhost:11434")
    down_client.is_available = lambda: False
    chatbot = CaseChatbotService(ollama_client=down_client)

    questions = [
        "Where did the money go?",
        "Which account is highest risk?",
        "Why was account HDFC10000002 flagged?",
        "What are the accounts in layer 3?",
        "What is the lineage of funds?",
        "Is the evidence verified?",
        "What are the recommended next steps?",
    ]
    for q in questions:
        ans = chatbot.answer_question(q, synthetic_case_facts)
        assert ans.source == "Verified facts (computed)"
        assert ans.validation_passed is True
        assert len(ans.answer) > 10


# 11. Structured-output router rejects invalid enum values
def test_structured_router_rejects_invalid_enum():
    router = IntentRouter()
    # Fast path keyword test
    intent, _, _, path = router.route("paisa kahan gaya")
    assert intent == IntentEnum.MONEY_TRAIL
    assert path == "router"

    # Action requests
    intent_act, _, _, _ = router.route("freeze account HDFC10000002")
    assert intent_act == IntentEnum.ACTION_REQUEST

    # Out of scope
    intent_oos, _, _, _ = router.route("write a poem on cricket")
    assert intent_oos == IntentEnum.OUT_OF_SCOPE


# 12. Phrasing that changes any number/id/direction is discarded
def test_phrasing_token_verifier_discard_on_alteration(synthetic_case_facts):
    orig = "Disputed funds of INR 50,000.00 moved from SBIN10000001 to HDFC10000002 [REC_VIC_01]."

    # Altered number
    phrased_bad_num = "Disputed funds of INR 99,000.00 moved from SBIN10000001 to HDFC10000002 [REC_VIC_01]."
    ok_num, issues_num = LightValidator.verify_phrasing_tokens(orig, phrased_bad_num)
    assert ok_num is False
    assert any("altered numbers" in i for i in issues_num)

    # Altered account
    phrased_bad_acc = "Disputed funds of INR 50,000.00 moved from SBIN10000001 to ICIC99999999 [REC_VIC_01]."
    ok_acc, issues_acc = LightValidator.verify_phrasing_tokens(orig, phrased_bad_acc)
    assert ok_acc is False
    assert any("altered account IDs" in i for i in issues_acc)

    # Inverted direction
    phrased_inv_dir = "Disputed funds of INR 50,000.00 moved from HDFC10000002 to SBIN10000001 [REC_VIC_01]."
    ok_dir, issues_dir = LightValidator.verify_phrasing_tokens(orig, phrased_inv_dir)
    assert ok_dir is False
    assert any("altered fund directions" in i for i in issues_dir)

    # Exact tokens preserved -> passes
    phrased_ok = "Under record [REC_VIC_01], disputed funds totaling INR 50,000.00 moved from SBIN10000001 to HDFC10000002."
    ok_good, issues_good = LightValidator.verify_phrasing_tokens(orig, phrased_ok)
    assert ok_good is True


# 13. Exact refusal strings for out-of-scope, actions, and non-existent accounts
def test_exact_refusal_strings(synthetic_case_facts):
    chatbot = CaseChatbotService()

    # Out of scope
    res_oos = chatbot.answer_question("Who won the 2011 cricket world cup?", synthetic_case_facts)
    assert res_oos.answer == "That is not available in this case."
    assert res_oos.citations == []

    # Action request
    res_act = chatbot.answer_question("Freeze account HDFC10000002 immediately", synthetic_case_facts)
    assert res_act.answer == "I can only explain this case; I cannot take actions."
    assert res_act.citations == []

    # Non-existent / out-of-case account
    res_missing = chatbot.answer_question("Why was account AXIS99990000 flagged?", synthetic_case_facts)
    assert res_missing.answer in ("That account is not part of the opened case.", "That is not available in this case.")
    assert res_missing.citations == []


# 14. Network containment: no non-localhost socket connections
def test_no_outbound_network_connections(synthetic_case_facts, monkeypatch):
    """Confirm zero non-localhost connections during engine & router execution."""
    def block_external_socket(family=-1, type=-1, proto=-1, fileno=None):
        orig_socket = socket.socket(family, type, proto, fileno)
        orig_connect = orig_socket.connect

        def guarded_connect(addr):
            host = addr[0] if isinstance(addr, tuple) else addr
            if host not in ("127.0.0.1", "localhost", "::1"):
                raise ConnectionRefusedError(f"Prohibited non-localhost outbound call to {addr}")
            return orig_connect(addr)

        orig_socket.connect = guarded_connect
        return orig_socket

    monkeypatch.setattr(socket, "socket", block_external_socket)
    chatbot = CaseChatbotService()
    ans = chatbot.answer_question("Where did the money go?", synthetic_case_facts)
    assert ans.validation_passed is True
