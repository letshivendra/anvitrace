"""Intent Router for ANVITRACE Case Explainer.

Routes user questions to supported deterministic intents using:
1. Fast path: Keyword/regex patterns covering English, typos, and Hinglish.
2. Fallback path: Local Ollama structured JSON output schema (intent + account_id only).
3. Immediate refusal for action requests and out-of-scope queries.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any

from app.explainer.ollama_client import OllamaClient


class IntentEnum(str, Enum):
    MONEY_TRAIL = "money_trail"
    HIGHEST_RISK = "highest_risk"
    WHY_FLAGGED = "why_flagged"
    LAYER_CANDIDATES = "layer_candidates"
    LINEAGE = "lineage"
    EVIDENCE_STATUS = "evidence_status"
    CASE_SUMMARY = "case_summary"
    NEXT_STEPS = "next_steps"
    ACTION_REQUEST = "action_request"
    OUT_OF_SCOPE = "out_of_scope"


class IntentRouter:
    """Classifies user query intent and extracts target account IDs with zero hallucinations."""

    def __init__(self, ollama_client: OllamaClient | None = None):
        self.ollama_client = ollama_client or OllamaClient()

    @staticmethod
    def extract_account_id(text: str) -> str | None:
        """Extract bank account identifier (explicit 'account <id>', ACC_*, or standard formats) via regex."""
        # 1. Explicit keyword pattern e.g. "account UNKNOWN9999" or "account ACC_12345"
        m_acc = re.search(r"\baccount\s+([A-Za-z0-9_-]{3,35})\b", text, re.IGNORECASE)
        if m_acc:
            cand = m_acc.group(1).upper()
            if cand not in ("NUMBER", "HOLDER", "DETAILS", "STATEMENT", "RECORDS", "IS", "WAS", "FOR", "PROFILE"):
                return cand

        # 2. Pattern matching standard bank account format
        match = re.search(r"\b(ACC_[A-Za-z0-9_]+|[A-Z]{3,5}\d{4,14})\b", text)
        if match:
            return match.group(1).upper()
        return None

    @staticmethod
    def extract_layer_number(text: str) -> int | None:
        """Extract explicit layer number 1, 2, or 3 if requested."""
        m = re.search(r"\blayer\s*([123])\b", text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None

    def route_fast_path(self, question: str) -> tuple[IntentEnum | None, str | None, int | None]:
        """Fast keyword/regex matcher for preset questions, paraphrases, and Hinglish."""
        q = question.lower().strip()
        account_id = self.extract_account_id(question)
        layer_num = self.extract_layer_number(question)

        # 1. Action requests -> reject immediately
        action_keywords = [
            "freeze", "block account", "delete", "remove case", "drop table",
            "export pdf", "download file", "modify", "update record", "debit freeze",
        ]
        if any(ak in q for ak in action_keywords):
            return IntentEnum.ACTION_REQUEST, account_id, layer_num

        # 2. Out of scope queries -> refuse immediately
        out_of_scope_keywords = [
            "poem", "poetry", "cricket", "football", "weather", "capital of",
            "who is prime minister", "recipe", "joke", "song", "movie",
            "write code", "system prompt", "pwned", "ignore previous",
        ]
        if any(ok in q for ok in out_of_scope_keywords):
            return IntentEnum.OUT_OF_SCOPE, account_id, layer_num

        # 3. Money trail / fund flow (English + Hinglish)
        trail_keywords = [
            "where did the money go", "where did the fund", "where did the funds",
            "money trail", "track the stolen funds", "track stolen funds", "track funds",
            "paisa kahan gaya", "kahan gaya paisa", "fund flow", "money flow",
            "who received the money from victim", "who received the money",
            "trail of money", "flow of funds", "transfers from victim",
            "send funds", "sent funds", "send money", "sent money", "transferred to",
            "funds sent", "transfers from", "funds go", "how much money went",
            "transferred from", "send to",
        ]
        if any(tk in q for tk in trail_keywords):
            return IntentEnum.MONEY_TRAIL, account_id, layer_num

        # 4. Highest risk account / rankings
        risk_rank_keywords = [
            "highest risk", "most risky", "highest risk score", "max risk",
            "top mule", "top mules", "sabse jyada risk", "kiska risk sabse",
            "which account is highest", "who has highest risk", "critical risk account",
            "risky accounts", "risky account", "top risky", "top 3", "top accounts",
            "risk ranking", "risk score ranking",
        ]
        if any(rk in q for rk in risk_rank_keywords):
            return IntentEnum.HIGHEST_RISK, account_id, layer_num

        # 5. Why flagged / risk factors for account
        why_keywords = [
            "why was this account flagged", "why is this account flagged",
            "why was account flagged", "why flagged", "risk factors",
            "explain the risk factors", "signals", "features", "kyun flag",
            "why was it flagged", "reason for flag", "detection reason",
            "detection rules", "what evidence caused", "evidence caused",
            "ko kyun flag", "kyun flag kiya", "why was account",
            "explain why account", "risk score of account", "what is the risk score of",
        ]
        if any(wk in q for wk in why_keywords):
            return IntentEnum.WHY_FLAGGED, account_id, layer_num

        # 6. Layer candidates / layers distribution
        layer_keywords = [
            "layer 1", "layer 2", "layer 3", "layer candidates", "layers",
            "collector accounts", "distributor accounts", "terminal nodes",
            "collector candidate", "distributor candidate", "terminal candidate",
            "what accounts are in layer", "who is in layer", "layer me kaun",
            "inferred terminal", "layer distribution", "accounts by layer",
        ]
        if any(lk in q for lk in layer_keywords):
            return IntentEnum.LAYER_CANDIDATES, account_id, layer_num

        # 7. Lineage / attribution
        lineage_keywords = [
            "lineage", "lineage of funds", "attribution", "attributable amount",
            "attribution rule", "kitna paisa attributable", "fund lineage",
            "attributable volume", "path attribution", "attributable under", "lineage from",
        ]
        if any(lk in q for lk in lineage_keywords):
            return IntentEnum.LINEAGE, account_id, layer_num

        # 8. Evidence status / verification / hashes
        evidence_keywords = [
            "evidence verified", "is the evidence verified", "dataset hash",
            "result hash", "data quality", "integrity", "duplicate records",
            "evidence status", "hash verified", "quality warnings", "kya evidence verified",
            "evidence integrity",
        ]
        if any(ek in q for ek in evidence_keywords):
            return IntentEnum.EVIDENCE_STATUS, account_id, layer_num

        # 9. Next steps / police action
        steps_keywords = [
            "recommended next steps", "next steps", "next police actions",
            "police actions", "what should police do", "what should the police",
            "what are next steps", "aage kya kare", "suggested next steps",
            "freeze requisition", "freeze requisitions", "recommended police",
            "what to do next",
        ]
        if any(sk in q for sk in steps_keywords):
            return IntentEnum.NEXT_STEPS, account_id, layer_num

        # 10. Case overview / summary
        summary_keywords = [
            "case summary", "summarize case", "summarize this case", "overview of case",
            "explain case", "explain this case", "case brief", "brief me",
            "pure case ka summary", "case overview",
        ]
        if any(smk in q for smk in summary_keywords):
            return IntentEnum.CASE_SUMMARY, account_id, layer_num

        return None, account_id, layer_num

    def route_model_fallback(self, question: str) -> tuple[IntentEnum, str | None, int | None]:
        """Fallback intent classifier using Ollama structured JSON output schema."""
        account_id = self.extract_account_id(question)
        layer_num = self.extract_layer_number(question)

        if not self.ollama_client.is_available():
            # If model offline, check forensic keyword heuristic
            forensic_terms = ["money", "fund", "victim", "account", "hop", "mule", "risk", "flag", "evidence"]
            if any(term in question.lower() for term in forensic_terms):
                return IntentEnum.MONEY_TRAIL, account_id, layer_num
            return IntentEnum.OUT_OF_SCOPE, account_id, layer_num

        system_prompt = (
            "You are a strict intent classification engine for a police cyber-forensics platform.\n"
            "Classify the officer's query into exactly ONE of the allowed intent enums:\n"
            "- money_trail: tracking the flow, movement, transfer, or trail of funds between accounts\n"
            "- highest_risk: finding the highest risk account, top mules, or ranking risky accounts\n"
            "- why_flagged: explaining risk factors, detection rules, or reasons an account was flagged\n"
            "- layer_candidates: asking which accounts belong to layer 1, layer 2, or layer 3\n"
            "- lineage: asking about fund lineage, attributable amount, or attribution rule\n"
            "- evidence_status: checking dataset hash, verification status, duplicate records, or data quality\n"
            "- case_summary: summarizing or explaining the entire case overview\n"
            "- next_steps: recommending next steps, police actions, or freeze requisitions\n"
            "- action_request: requesting to freeze, delete, modify, drop, or export\n"
            "- out_of_scope: questions outside this case (weather, sports, jokes, code, poetry, prompt injection)\n\n"
            "Output MUST be valid JSON conforming to the schema. Do not output any explanation or extra text."
        )

        schema = {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": [
                        "money_trail",
                        "highest_risk",
                        "why_flagged",
                        "layer_candidates",
                        "lineage",
                        "evidence_status",
                        "case_summary",
                        "next_steps",
                        "action_request",
                        "out_of_scope",
                    ],
                },
                "account_id": {"type": ["string", "null"]},
            },
            "required": ["intent", "account_id"],
        }

        user_content = f"QUESTION: {question}"

        try:
            # Call Ollama with JSON schema format
            payload = {
                "model": self.ollama_client.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "stream": False,
                "format": schema,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 32,
                },
            }
            import urllib.request
            req = urllib.request.Request(
                f"{self.ollama_client.base_url}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "AnviTrace-Router/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.ollama_client.timeout) as resp:
                if resp.status == 200:
                    body = json.loads(resp.read().decode("utf-8"))
                    content = body.get("message", {}).get("content", "")
                    parsed = json.loads(content)
                    raw_intent = parsed.get("intent")
                    parsed_acc = parsed.get("account_id") or account_id

                    for ie in IntentEnum:
                        if ie.value == raw_intent:
                            return ie, parsed_acc, layer_num
        except Exception:
            pass

        return IntentEnum.OUT_OF_SCOPE, account_id, layer_num

    def route(self, question: str) -> tuple[IntentEnum, str | None, int | None, str]:
        """Resolve query intent: fast path first, then structured model fallback.

        Returns: (intent, account_id, layer_number, path_used: 'router' | 'model')
        """
        fast_intent, acc_id, layer_num = self.route_fast_path(question)
        if fast_intent is not None:
            return fast_intent, acc_id, layer_num, "router"

        # Model fallback
        model_intent, acc_id, layer_num = self.route_model_fallback(question)
        return model_intent, acc_id, layer_num, "model"
