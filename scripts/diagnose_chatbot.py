import sys
import json
import urllib.request
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.explainer.case_summarizer import CaseChatbotService, CaseFactsExtractor
from app.explainer.ollama_client import OllamaClient
from rehearse_chatbot import build_case_trace

def run_diagnostics():
    client = OllamaClient()
    chatbot = CaseChatbotService(ollama_client=client)
    trace = build_case_trace("HDFC10000336")
    facts = CaseFactsExtractor.extract_case_facts(trace)
    
    questions = [
        "Where did the money go?",
        "Which account is highest risk?",
        "Why was this account flagged?",
        "What are the recommended next steps?",
        "Track the stolen funds",
        "Who received the money from victim?",
        "Which account has the highest risk score in this case?",
        "Explain the risk factors for this account",
        "What are the next police actions?",
        "Show me the money trail",
        "What accounts are in layer 1?",
        "Tell me the lineage of funds",
        "Is the evidence verified?",
        "Paisa kahan gaya",
    ]
    
    results = []
    for q in questions:
        ans = chatbot.answer_question(q, trace)
        results.append({
            "question": q,
            "answer": ans.answer,
            "source": ans.source,
            "validation_passed": ans.validation_passed,
            "citations": ans.citations,
        })
        print(f"\n[Q]: {q}\n[A ({ans.source}, valid={ans.validation_passed})]: {ans.answer}")

    with open("diagnostic_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_diagnostics()
