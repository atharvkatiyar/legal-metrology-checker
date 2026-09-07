# Legal Metrology Compliance Checker

**Smart India Hackathon 2026 — Problem Statement #26034**

An automated tool that checks whether a packaged product's label complies with India's Legal Metrology (Packaged Commodities) Rules — upload a photo of a product label, and get back a pass/fail verdict, a compliance score, and a specific list of what's missing or wrong, with legal citations.

---

## What it does

Take a photo of a product's label → read what's written on it → understand what each piece of information means → check it against Legal Metrology rules → tell the user whether it's compliant and why.

The system checks for mandatory declarations required by law: manufacturer details, net quantity, MRP, manufacturing date, and consumer care information — reading them directly from photos of real product packaging, in both English and Hindi.

---

## How it works — the pipeline

```
                    PRODUCT PHOTO(S)
                          │
                          ▼
              ┌───────────────────────┐
              │   OCR & EXTRACTION    │   Reads all text in the image
              │  (EasyOCR, en )   │   (English ), with
              └───────────────────────┘   bounding boxes + confidence
                          │
                          ▼
              ┌───────────────────────┐
              │    FIELD MAPPING      │   Sorts raw text into labeled
              │ (regex + LLM fallback)│   fields: MRP, net quantity,
              └───────────────────────┘   manufacturer, date, etc.
                          │
                          ▼
              ┌───────────────────────┐
              │    RULE ENGINE &      │   Checks each field against
              │  COMPLIANCE LOGIC     │   Legal Metrology rules.
              └───────────────────────┘   Outputs violations + score.
                          │
                          ▼
              ┌───────────────────────┐
              │  FULL-STACK BACKEND   │   Saves results to database,
              │     & FRONTEND        │   serves the report to the user.
              └───────────────────────┘

     (Font-Size & Readability runs as a separate, optional check —
      measures whether label text is physically large enough, using
      a coin in the photo as a size reference.)
```

---

## Team & responsibilities

| Role | Responsibility |
|---|---|
| **Data & Rules Lead** | Extracted the actual legal requirements from the Legal Metrology Act/Rules into `app/core/rules.json`; built and labeled the real-world test dataset (`labeled_batch/`). |
| **OCR & Extraction Engineer** | Reads text out of label photos with EasyOCR, including bounding boxes and confidence scores. Handles image preprocessing (resizing, format conversion). |
| **Field Mapping Engineer** | Converts raw OCR text into structured fields (MRP, net quantity, manufacturer, etc.), using layered extraction: anchor-based regex, a full-text regex sweep, and an LLM (Gemini) fallback for anything the regex passes miss. |
| **Rule Engine & Compliance Logic** | Takes the structured fields and decides pass/fail against the legal spec in `rules.json` — checks presence of mandatory fields and validates formats (e.g. valid units) where a checkable rule exists. Produces the final violations list and compliance score. |
| **Font-Size & Readability Analyst** | Separately measures whether label text meets minimum legal font-height requirements, using a coin in the photo as a physical size reference. |
| **Full-Stack Integration Lead** | Built the FastAPI backend, database schema, API endpoints, and frontend that ties every module together into a working app. |

---

## Repository structure

```
legal-metrology-checker/
├── backend/
│   ├── app/
│   │   ├── api/v1/
│   │   │   ├── router.py         # Main API endpoints
│   │   │   └── auth.py           # Officer login
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── database.py
│   │   │   └── rules.json        # The legal spec — single source of truth
│   │   ├── field_mapping/
│   │   │   └── field_mapping.py  # Baseline regex field extraction
│   │   ├── models/
│   │   │   └── schema.py         # Database tables (User, Product, ScanResult, ViolationRecord)
│   │   ├── schemas/
│   │   │   └── contracts.py      # Shared Pydantic data contracts between all modules
│   │   ├── services/
│   │   │   ├── ocr.py                    # OCR (Role 2)
│   │   │   ├── field_mapping_adapter.py  # Field Mapping → shared contract translator
│   │   │   ├── field_mapping_fallback.py # Field Mapping's regex + LLM fallback logic (Role 3)
│   │   │   ├── rule_engine.py            # Compliance logic (Role 4)
│   │   │   ├── font_size.py              # Font-size measurement (Role 5)
│   │   │   ├── font_size_adapter.py
│   │   │   ├── pdf_generator.py          # Compliance report PDF export
│   │   │   └── geocoding.py
│   │   └── main.py               # FastAPI app entry point
│   ├── tests/
│   ├── requirements.txt
│   └── compliance.db             # SQLite database
├── docs/
│   ├── font_size_limitations.md
│   └── rule_engine_limitations.md
├── frontend/
│   └── index.html
└── labeled_batch/
    └── labels.json                # Real product photos + hand-labeled ground truth
```

---

## Setup & running locally

### 1. Clone the repo
```
git clone https://github.com/atharvkatiyar/legal-metrology-checker.git
cd legal-metrology-checker
```

### 2. Set up the backend
```
cd backend
pip install -r requirements.txt --break-system-packages
```

### 3. Run the backend
> Note: confirm the exact entry point/command with the Full-Stack Integration Lead — this is the standard FastAPI convention, adjust if `main.py` names the app instance differently.
```
uvicorn app.main:app --reload
```

### 4. Open the frontend
Open `frontend/index.html` in a browser, or serve it via the backend if configured to do so.

---

## Key API endpoints

| Endpoint | Purpose |
|---|---|
| `POST /scans/init` | Main pipeline — upload one or more product images, get back the full compliance report (OCR → Field Mapping → Rule Engine, all in one call). |
| `GET /scans/{scan_id}` | Retrieve a previously completed scan's full result. |
| `GET /scans/history` | List past scans. |
| `POST /scans/{scan_id}/font-check` | Optional, separate font-size check — requires a manually supplied tap point locating a reference coin in the photo. |
| `GET /health` | Health check. |

---

## The legal spec (`rules.json`)

All legal requirements — which fields are mandatory, their severity if missing, format rules where they exist, and legal citations — live in `backend/app/core/rules.json`, not hardcoded into any module. This means the legal spec can be updated (a new valid unit, a changed severity, a new mandatory field) without changing code in the Rule Engine or Font-Size modules, both of which read this file at runtime.

**This file is a living document** and was updated multiple times during development as gaps were found through testing against real product photos — see `docs/rule_engine_limitations.md` for specifics on what's currently covered and what's still open.

---

## Known limitations

Each module's specific limitations are documented separately:
- [`docs/rule_engine_limitations.md`](docs/rule_engine_limitations.md) — Rule Engine & Compliance Logic
- [`docs/font_size_limitations.md`](docs/font_size_limitations.md) — Font-Size & Readability

Broadly: this is a hackathon prototype, tested against a real but limited set of product photos (`labeled_batch/`), not an exhaustive or legally certified compliance tool.

---

## Scope decisions

Per the team's task list, the following were included as stretch scope and may be partially or fully descoped depending on time: Hindi-language OCR/field mapping, LLM-assisted extraction fallback, red-box visual annotation on the compliance report image, role-based authentication, and the font-size module itself (documented as future work if incomplete). See individual module documentation for current status of each.