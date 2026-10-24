# ANVITRACE

ANVITRACE is an offline forensic workstation for investigating financial
transactions. It provides transaction ingestion, mule-account detection,
fund-flow tracing, case management, and forensic report generation.

The application combines a React/Vite frontend with a FastAPI backend and a
DuckDB-based analysis engine. The Windows desktop build packages these
components into a standalone executable.

## Run the Windows executable

If you have the `ANVITRACE-v1.0-Offline` distribution folder, double-click
`ANVITRACE.exe`. The application starts its local backend and opens the
workstation in Microsoft Edge app mode or your default browser at
`http://127.0.0.1:8000`.

The executable and runtime data are not stored in this source repository.
GitHub limits individual Git files to 100 MB, and the generated executable is
larger than that. Build the distribution from source as described below, or
download a separately published release when one is available.

## Build the Windows distribution

Requirements:

- Windows
- Python 3.12 or newer
- Node.js and npm

From the repository root, install and build the frontend:

```powershell
cd frontend
npm install
npm run build
cd ..
```

Create the backend environment at the path expected by the packaging script,
install the backend dependencies and PyInstaller, then build the distribution:

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
backend\.venv\Scripts\python.exe -m pip install pyinstaller
backend\.venv\Scripts\python.exe build_offline_package.py
```

The script creates `ANVITRACE-v1.0-Offline` in the repository root. Open
`ANVITRACE.exe` in that folder to start the application. Generated executables,
databases, datasets, and local environment files are intentionally excluded
from Git.

## Case Summarizer & "Explain this case" Assistant

An offline, read-only AI case summarizer and interactive explainer designed for investigating police officers. Generates a structured 7-section case brief and answers forensic queries with bracketed record citations.

### Fixed 7-Section Summary Structure
1. **Overview**: Victim account, disputed amount, chronological time window, and initial transaction record.
2. **Money Trail**: Traversal depth (hops), key transaction dispersal paths, and attributable volume.
3. **Flagged Accounts by Layer**: Layer 1 (Collector), Layer 2 (Distributor/Layering), and Layer 3 (Inferred Terminal) accounts with risk rationale.
4. **Risk Indicators**: Rule-based detection triggers alongside statistical/ML mule risk scoring.
5. **Evidence & Data-Quality Notes**: SHA-256 dataset integrity hash, result digest, and duplicate record audits.
6. **Limitations**: Standard forensic disclaimer (Layer 3 accounts are "inferred terminal"; all entities are "flagged for review" and do not constitute proof of guilt).
7. **Suggested Next Steps**: Actionable police workflows (Section 102 CrPC / Section 106 BNSS debit freeze requisitions, KYC summons, IP gateway audits).

### Run the chatbot and summary generator

Execute the following exact Windows PowerShell commands from the repository root:

1. **Pull the offline model**:
   ```powershell
   ollama pull llama3.2:3b
   ```

2. **Set environment variables**:
   ```powershell
   $env:OLLAMA_MODEL = "llama3.2:3b"
   $env:OLLAMA_TIMEOUT = "30"
   $env:DEMO_MODE = "0"
   ```

3. **Start backend with single script or manual command**:
   ```powershell
   # Option A: Automated launcher script (checks Ollama, pulls model if missing, starts 1-worker backend, prints health JSON)
   powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1

   # Option B: Manual backend launch (1 worker)
   cd backend
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
   ```

4. **Start the frontend**:
   ```powershell
   cd frontend
   npm run dev
   ```

5. **Verify health check endpoint**:
   ```powershell
   curl http://127.0.0.1:8000/api/health
   # Expected response:
   # {"status":"ok","project":"ANVITRACE","environment":"development","database":"DuckDB (Embedded)","offline":true,"model_ready":true,"model_hash_ok":true,"mode":"model","ollama_available":true,"warmed_up":true}
   ```

6. **Run demo rehearsal (measures latency, cold vs warm, sources, rejections, citations)**:
   ```powershell
   python scripts\rehearse_chatbot.py --runs 5 --account HDFC10000336
   ```

7. **Run unit test suite**:
   ```powershell
   python -m pytest backend\tests\unit\ -v
   ```

8. **Run side-by-side evaluation harness (Accurate by Construction vs OLD Flow)**:
   ```powershell
   python scripts\eval_chatbot.py
   ```

### Accurate by Construction Architecture (Deterministic Answer Engine)
- **Engine First**: High-stakes forensic facts, rankings, directions, counts, and amounts are computed directly by code (`backend/app/explainer/answer_engine.py`) from DuckDB/case facts. The model never writes factual numbers or directions.
- **Intent Routing**:
  - **Fast Path**: Regex/keyword router supporting standard queries, paraphrases, typos, and Hinglish (`"paisa kahan gaya"`, `"sabse jyada risk wala account"`, etc.) with 0ms LLM overhead.
  - **Fallback Path**: Ollama local model with structured output (`format` JSON schema) returning strictly `{"intent": <enum>, "account_id": <str|null>}`. Model never generates prose here.
  - **Refusal Guard**: Out-of-scope queries (general knowledge, sports, poems) and non-existent accounts trigger immediate refusal: `"That is not available in this case."`. Action requests (freeze, delete, export) trigger: `"I can only explain this case; I cannot take actions."`.
- **Claim-Level Validator**:
  - Verifies factual relationships: `(from -> to)` directional edge validity, highest-risk score validation against actual maximums, Layer 3 accounts marked as "inferred terminal", and mandatory bracketed `[record_id]` citation per factual sentence.
- **Guarded Optional Phrasing (`EXPLAINER_LLM_PHRASING=0|1`)**:
  - Default OFF (`0`): Directly returns verified sentences rendered from deterministic templates.
  - When ON (`1`): LLM is permitted only to phrase verified sentences. A strict multiset token comparison checks all account IDs, amounts, dates, record IDs, and directions. Any missing, extra, or modified entity immediately discards the model phrasing.
- **Truthful UI Badges**:
  - Answers and summary sections display truthful source provenance:
    - `"Verified facts (computed)"`
    - `"Phrased by local model"`
    - `"Cached"`
- **Blob-Level Model Integrity**:
  - `verify_model_integrity()` streams 8 MB chunks directly from the Ollama model blob on disk (`%USERPROFILE%\.ollama\models\blobs\sha256-<digest>`) and validates via `hmac.compare_digest` against `backend/models_manifest.json`. Tampered or missing blobs trigger fallback to engine answers and log `MODEL_HASH_MISMATCH` audit events without crashing.

---

## Project layout

```text
backend/                  FastAPI application and analysis engine
frontend/                 React and Vite user interface
anvitrace.spec            PyInstaller executable specification
build_offline_package.py  Windows offline distribution builder
launcher.py               Root launcher entry point
```

## Development

Install frontend dependencies with `npm install` in `frontend/`. Install the
backend dependencies from `backend/requirements.txt` using Python 3.12 or
newer. Keep local credentials and environment files out of version control;
configure local settings in an untracked `.env` file as needed.

