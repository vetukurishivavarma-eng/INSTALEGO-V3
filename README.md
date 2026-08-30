# Legal Document AI

Banking document analysis and discrepancy detection. A reviewer creates a case
for one applicant, uploads their documents in whatever formats arrived, and
gets back a list of inconsistencies — each one showing both conflicting values,
the documents and pages they came from, and what to do about it.

**This is decision support, not a decision maker.** It reports that two
documents disagree. It never concludes that a document is fraudulent, and every
case ends in a human review.

---

## What makes this different from asking a model to read the documents

The architecture separates what models are good at from what they are not.

| Layer | Who does it | Why |
|---|---|---|
| Parse, OCR | Python (PyMuPDF, python-docx, openpyxl, Pillow) | Deterministic and cheap |
| Classify, extract | The model, once per document, on named fields | Reading a document is what a VLM is for |
| Normalise | Python | Dates and money have correct answers |
| Compare | Python (exact / fuzzy) | A PAN either matches or it does not |
| Rules | Python | A bank will audit these; they must be reproducible |
| Judge ambiguous cases | The model, on one candidate at a time | Only where deterministic work ran out |
| Verify evidence | The model, on HIGH findings only | The finding that changes a decision gets re-read |
| Report | Python, from a template | Formatting must be identical every time |
| QA | Python first, then the model | Identifier and severity drift have right answers |

Two rules hold throughout:

- **The model never invents a finding.** Candidates come from rules and
  comparisons. The reasoning agent may confirm, downgrade or dismiss one; it
  cannot add one, and anything it returns about a finding it was not given is
  discarded.
- **Nothing reaches a report unvalidated.** Model output passes through Pydantic
  validation into the canonical analysis, then through the rule engine and
  evidence verification, and only then into a deterministic renderer.

---

## Quick start

### With Docker

```bash
cp .env.example .env
docker compose up --build
```

- UI: http://localhost:5173
- API and docs: http://localhost:8000/docs
- MinIO console: http://localhost:9001 (`minioadmin` / `minioadmin`)

That brings up the frontend, API, worker, PostgreSQL, Redis and MinIO. It does
**not** start the model server, which needs a GPU. Point the stack at any
OpenAI-compatible endpoint:

```bash
LLM_BASE_URL=https://your-endpoint/v1 LLM_MODEL=your-model docker compose up
```

Or run the local model alongside everything else:

```bash
docker compose --profile gpu up --build
```

### Without Docker

```bash
make setup                       # virtualenv, Python deps, npm install
cd backend && .venv/Scripts/python -m alembic upgrade head
make dev                         # API on :8000
make ui                          # UI on :5173
```

The bundled `.env` uses SQLite, local-filesystem storage, in-process job
execution and the mock model client, so it runs with no Postgres, no Redis and
no GPU.

---

## Running it without a GPU

`LLM_USE_MOCK=true` swaps in a deterministic stand-in for the model. It is not
a model and does not pretend to be: classification is keyword matching,
extraction is a regular expression over label/value lines, and every judgement
call returns the cautious answer.

What that exercises is everything else — ingestion, parsing, normalisation, the
rule engine, comparison, the canonical analysis, report rendering, the API and
the UI, all against real files. What it cannot tell you is whether the real
model extracts correctly. That is what the evaluation suite against a live
endpoint is for. The mock refuses to start when `ENVIRONMENT=production`.

---

## Connecting a real model

The stub proves the pipeline. A real endpoint is what proves the system. Three
steps, in this order.

### 1. Preflight the endpoint

    cd backend
    LLM_USE_MOCK=false python scripts/check_endpoint.py \
        --base-url http://localhost:8001/v1 --model Qwen3-VL-8B-Instruct

Checks reachability, that the served model name matches `LLM_MODEL`, plain
completion, schema-valid structured output, **vision**, and what a realistic
page costs in tokens and seconds. Every check reports independently, so a
working text model with broken vision is diagnosed as exactly that. Exit code
is non-zero if anything failed.

The vision check is the one to watch. It renders a PAN-shaped identifier into
an image and requires the model to read it back character for character. A
model that returns `ABCDE1Z34F` will misread real identifiers, and no amount of
downstream logic recovers from that.

### 2. Run the evaluation cases against it

    make eval-live

Same nine cases, same expectations, real extraction. The runner writes each
case to `var/evaluation-live.json` as it finishes and takes `--resume`, because
a hundred throttled calls take a quarter of an hour and an interrupted run
should not lose the cases that already succeeded.

Last measured run, `minimax/minimax-m3:free`, 9 cases in 7.3 minutes:

| Metric | Result |
|---|---|
| Field extraction accuracy | 1.00 |
| Discrepancy precision / recall | 1.00 / 1.00 |
| False-positive rate | 0.00 |
| Severity accuracy | 1.00 |
| Evidence accuracy | 1.00 |

Read that with the fixtures in mind. These documents are generated with clean
text layers and unambiguous `Label: value` lines, so they measure whether the
pipeline holds together with a real model in it — not whether extraction
survives a skewed phone photo of a stamped Aadhaar card. That is measured
separately, by the degradation sweep below.

### 3. Point the stack at it

    LLM_BASE_URL=http://localhost:8001/v1 \
      LLM_MODEL=Qwen3-VL-8B-Instruct LLM_USE_MOCK=false docker compose up

### Choosing a model on a gateway

Free model availability rotates without notice. When this was last checked
(Aug 2026) the Qwen VL models had left OpenRouter's free tier entirely, and of
eight free models advertising image input:

| Model | Result |
|---|---|
| `minimax/minimax-m3:free` | passed every check, including reading an identifier off an image |
| `google/gemma-4-31b-it:free` | 429 rate limited, unusable at the time of testing |
| `thinkingmachines/inkling:free` | 403 — restricted to "agentic harnesses", not open API access |

Do not take that table as current. Run the preflight against the candidates
instead; three models cost about fifteen calls and the vision check is the
discriminator that matters.

The exact target model, `qwen/qwen3-vl-8b-instruct`, is available as paid at
roughly $0.12 per million prompt tokens — about three cents for a full
evaluation run. For validating against the model you actually intend to
deploy, that is a better use of a few dollars than any amount of free-tier
substitution.

### Testing the live path without a GPU

There is a stub OpenAI-compatible server for exercising the real HTTP client —
wire format, retries, JSON-mode negotiation — with no model attached:

    python -m tests.fixtures.fake_model_server --port 8001
    python -m tests.fixtures.fake_model_server --port 8001 --no-json-mode

The second form rejects `response_format`, reproducing servers that do not
support JSON mode, so the client's fallback gets exercised. It is a test double:
it does not read images and says so, which is how the preflight script's vision
check gets tested for its failure path.

---

## Measuring where extraction degrades

The nine evaluation cases answer "is the right discrepancy found". They cannot
answer "does the model still read the page", because their fixtures are as easy
as a document can be. So there is a second suite that renders the same
applicant as documents laid out the way real ones are — an identity card with
the label above the value, a payslip whose amounts are right-aligned a column
away from the words that name them, a sanction letter that states the amount
mid-sentence with no label anywhere — and then degrades the image.

    make hard-fixtures          # render them to var/hard-fixtures and look
    make eval-extraction        # check the harness against the stub
    make eval-extraction-live   # measure, against the configured endpoint

Twenty-five conditions, each one named and deterministic: skew, blur, fade,
underexposure, jpeg, grain, photocopier speckle, low resolution, uneven
lighting, keystone from an angled photograph, fold lines, bleed-through from
thin paper, an office stamp across the values, a handwritten margin note, fax
binarisation, and four composites of the lot. Severity is baked into the name
(`blur_mild` against `blur_severe`), and randomness is seeded from the name, so
a change in the numbers is a change in the system rather than a different
sample.

`--core` runs three layouts against nine conditions: twenty-seven variants at
roughly three model calls each, which fits inside a free tier's daily
allowance. The full matrix is 125 variants. Only the per-document pipeline runs
— parse, transcribe, classify, extract — because the rule engine and the
verifier have nothing to say about whether a page was read correctly, and
skipping them is what makes the sweep affordable.

**Three outcomes are counted per field, not two.** A field can be read, or
missed, or *wrong*. Missing is recoverable: the case reaches a human with a gap
in it, which is what a review queue is for. Wrong is what costs money, because
nothing downstream can tell a misread date of birth from a correct one, and it
propagates into a discrepancy that either fires against a clean applicant or
stays silent against a dirty one. So the headline number is not accuracy — it
is **silently wrong**: a false value asserted on a page that nothing flagged. A
pipeline that reads nothing off an unreadable scan and says so is behaving
correctly. One that reads three fields off it confidently is not, even if two
of them happen to be right.

Fixture filenames are deliberately uninformative (`IMG_4471.jpg`). A filename
that says "Aadhaar" lets the classifier be right for the wrong reason.

### What the first sweep found

`minimax/minimax-m3:free`, 39 variants (three layouts against thirteen
conditions), 156 fields:

| Metric | First run | After both fixes |
|---|---|---|
| Field accuracy | 0.85 | 0.85 |
| Wrong (a false value returned) | 0.019 | 0.013 |
| **Silently wrong** (false and unflagged) | **0.013** | **0.000** |
| Classification accuracy | 0.82 | 0.80 |

Findings 1 and 3 were re-scored with `--rescore`, which re-applies the scoring
rules to the values the run already recorded — a fix to normalisation costs no
quota to evaluate. Finding 2 changed the extractor prompt, so the sanction
letter was re-run against the model.

Read the two middle rows and ignore the outer ones. Field and classification
accuracy moved by less than the model's own run-to-run variance (see finding 2)
and neither movement is attributable to the fixes.

Accuracy by condition, worst first:

| Condition | Accuracy | Wrong | Silent | Flagged |
|---|---|---|---|---|
| `worst_case` | 0.42 | 1 | 0 | 3/3 |
| `clean`, `skew_severe`, `blur_severe`, `very_faded`, `stamped`, `phone_photo`, `tiny`, `dark` | 0.83 | 0 | 0 | varies |
| `upside_down` | 0.92 | 1 | 0 | 1/3 |
| `low_resolution`, `bad_photocopy`, `rotated_90` | 1.00 | 0 | 0 | varies |

`clean` sitting alongside `skew_severe` and `dark`, and `bad_photocopy` and
`rotated_90` scoring a clean 1.00, is the whole finding in one table: the
ordering is noise, because the thing that moves the number is not the image.

The image degradations barely matter. A card, a payslip and a letter all
survive twelve degrees of skew, severe defocus, a stamp across the values, a
third-generation photocopy and being scanned upside down; the model reads them
anyway. Extraction accuracy tracks **layout**, not image quality: the identity
card scores 0.94 and the payslip 0.98, while the prose sanction letter scores
0.51 — and it scores 0.51 in good conditions too.

Four findings came out of it, in order of what they cost. Two are fixed, and
the sweep is what verified the fixes:

**1. An amount phrased as a sanction letter phrases it was silently
uncomparable. Fixed.** The letter states `Rs. 5,00,000/- (Rupees Five Lakh
only)`. `parse_amount` returned `None` for it: the `/-` was no longer at the
end of the string, so the end-marker strip missed it and it survived digit
stripping as a bare `-`. `compare_amount` then reported `NOT_COMPARABLE` and
`_amount_agreement` skipped the pair without a finding — so a genuine
loan-amount mismatch on a letter phrased the normal Indian way was not reported
at all. Evaluation case 008 did not catch it because its fixture writes a bare
`Rs. 5,00,000`.

`parse_amount` now drops a parenthetical that contains no digits of its own —
the word-form restatement — while leaving the accounting negative `(1,200.00)`
alone, and strips stacked end markers rather than one. Regression tests sit in
`tests/unit/test_number_utils.py` and, at the rule level, in
`tests/unit/test_rules.py`. Where a figure and its word form actually disagree
the words prevail in law; nothing here reads number words, and a rule for that
disagreement would be a separate piece of work.

**2. Classification of the prose letter is not stable between runs, and the
type decides which fields are even requested.** Still open.

The sanction letter comes back as `AGREEMENT` or as `LOAN_APPLICATION`
depending on the run. Re-running the same thirteen fixtures with an *unchanged*
classifier prompt flipped six of them, `clean` among them — so this is
run-to-run nondeterminism, not an effect of degradation, and `LLM_TEMPERATURE`
is already `0.0`. The extractor asks for a different field list per type, so
`party_two` and `agreement_date` are requested on one run and not the next,
which is why that document sits near 0.5 and why its score moves without
anything changing.

The consequence is not confined to a metric. Under `bank_b`, whose
`required_documents` includes `LOAN_APPLICATION`, a sanction letter classified
that way satisfies the requirement for an application form the applicant never
submitted — on some runs and not others.

Worth noting for anyone reading the first table: this variance is larger than
the difference between the two columns, which is why those rows should not be
read as a result.

**3. A name field swallowed the line beneath it. Fixed.** Asked for
`party_two`, the extractor returned `Ravi Kumar, 12, M.G. Road, Bengaluru -
560001` — the whole addressee block. The prompt passed bare field names, so
the question had no stated boundary and that was a fair answer to it.
`FIELD_DESCRIPTIONS` in `app/agents/document_extractor.py` now carries a
one-line definition for the fields with a demonstrated ambiguity, rendered into
the prompt beside the name, and the prompt states the general rule. Descriptions
are added only where a boundary problem has actually been observed: each one
changes the question being asked, so each costs a re-measurement, and attaching
them speculatively to fields that already extract cleanly risks moving
something that works.

**4. Two image-quality checks do not fire.** `dark` (a page at a third of its
brightness) is not flagged, because a document is mostly white paper and the
mean brightness stays above `DARK_MEAN_THRESHOLD` — the check measures the
sheet rather than the ink. And Laplacian variance counts noise as detail, so
speckle and grain *raise* the focus score. Both are recorded as strict xfails
in `tests/unit/test_hard_fixtures.py`, so fixing either turns the test green.

None of this is visible from the nine end-to-end cases, which score 1.00 across
the board.

## The pipeline

```
create case → upload → parse → classify → extract → normalise → build profile
   → run rules → generate candidates → assess ambiguous ones → verify evidence
   → check missing documents → build canonical analysis → map to bank template
   → render DOCX/PDF → QA → review
```

Every step updates the case status, which the UI polls. A document that fails
does not fail the case: the failure is recorded on that document and the rest
continue.

### Model call budget

For a five-document case: one classification and one extraction call per
document, one profile consolidation call **only if fields conflict**, one call
per genuinely ambiguous discrepancy, one verification call per HIGH finding,
and one QA call. Deterministic checks — required documents, identifier
comparison, expiry, arithmetic — cost nothing.

---

## Configuration

Rules and severities are configuration, not code. `configs/default_rules.yaml`
declares every rule; `configs/banks/*.yaml` deep-merge over it.

```yaml
# configs/banks/bank_b.yaml
identity:
  address_match:
    severity: HIGH        # MEDIUM at bank_a
required_documents:
  - IDENTITY_PROOF
  - PROPERTY_DOCUMENT     # secured lending
```

The same address mismatch is MEDIUM for one bank and HIGH for another with no
code change. Report layouts live in `configs/report_templates/`; a bank without
one falls back to `default.json`.

Both directories are mounted read-only into the containers, so changing a
threshold is a restart, not a rebuild.

---

## Evidence and auditability

Every value keeps its source, and every finding cites two of them:

```
HIGH — Date of birth mismatch
  Aadhaar.pdf, page 1:        12/04/1998
  LoanApplication.docx:       12/04/1997
  Confidence 95% · raised by identity.dob_match
  Action: verify against the original documents
```

Clicking either citation opens that page in the viewer. Stored alongside every
result: analysis version, model name, prompt version (a hash of the prompt
file), and rules version. A finding produced last month still says which prompt
and which rule set produced it.

`GET /api/cases/{id}/audit` returns the chain from upload through analysis to
report download. Identifiers are masked before anything is logged or audited.

---

## Testing

```bash
make test        # unit + integration
make eval        # evaluation suite with metrics
make test-all    # everything
```

The evaluation suite runs nine cases end to end and asserts thresholds:
precision ≥ 0.90, recall ≥ 0.85, evidence accuracy 1.00, false-positive rate
≤ 0.10. Precision is held above recall deliberately — a queue with fabricated
findings gets ignored, and an ignored queue catches nothing.

Half the cases exist to check that nothing is flagged: `Ravi Kumar` against
`Ravi K Kumar`, `12/04/1998` against `1998-04-12`, `Rs. 5,00,000` against
`500000.00`, `12 MG Road` against `12, M.G. Rd.`.

---

## Project layout

```
backend/app/
  api/          FastAPI routes (thin; no business logic)
  agents/       Classifier, extractor, profile builder, reasoner, verifier, QA
  prompts/      One .txt per agent; version = hash of the file
  workflows/    Orchestration: what runs, in what order
  extraction/   PDF, DOCX, XLSX, image parsing and OCR
  rules/        The deterministic engine, by category
  comparison/   exact / fuzzy / semantic
  reports/      Generator plus DOCX and PDF renderers
  models/       SQLAlchemy; schemas/ Pydantic; services/ data access
  llm/          BaseLLMClient, Qwen client, structured output, retry, mock
frontend/src/   React, TypeScript, Tailwind, React Query
configs/        Rules and report templates (mounted, not baked in)
model-server/   vLLM image for Qwen3-VL
```

---

## Swapping the model

Nothing outside `app/llm/` knows which model is in use. To move from 8B to 32B:

```bash
MODEL_ID=Qwen/Qwen3-VL-32B-Instruct TENSOR_PARALLEL_SIZE=2 \
  docker compose --profile gpu up model-server
```

Set `LLM_MODEL` to the same served name. A different provider needs only
`LLM_BASE_URL` and `LLM_API_KEY`, as long as it speaks
`/v1/chat/completions`.

---

## Security posture

Authentication is a seam, not an implementation: every route already depends on
`current_principal` and every case lookup passes through `authorise_case`.
`AUTH_ENABLED=false` is the development default, and the application **refuses
to start** in production with it off, or with the mock model enabled.

Originals are immutable — the storage layer refuses to overwrite an existing
key, and derived artefacts get their own keys. Logs and audit rows are masked
for PAN, Aadhaar, passport, phone, email and account numbers. Uploads are
validated on extension, size and magic bytes.

---

## Known limitations

- **Legacy `.doc` and `.xls`** are accepted at upload but need LibreOffice on
  the path to convert (`.doc`) or are rejected with guidance (`.xls`). Both fail
  with a clear message rather than partially parsing.
- **Bounding boxes** are stored and rendered when a model supplies them; page
  images and quoted text carry the evidence when it does not.
- **No vector database.** Bank policies and SOPs would need one; the schema and
  storage layer leave room to add pgvector or Qdrant without restructuring.
- **Single tenant.** `Principal.tenant` exists and is checked, but nothing
  populates it yet.
- **Report QA regenerates once.** A report failing QA twice is stored as
  `QA_FAILED` rather than released.
- **A figure and its word form are never cross-checked.** Both are read, the
  digits are used, and a document whose words say something else than its
  numerals passes unremarked — the opposite of the legal convention.
- **Document type drives which fields are extracted**, so an unstable
  classification quietly changes what gets asked for. The prose sanction letter
  is the case where this shows.
- **Two image-quality checks do not fire**: an underexposed page and a noisy
  one both pass. Recorded as strict xfails.
- **Classification is not reproducible run to run**, and it selects the field
  list, so what gets extracted from a borderline document varies between
  identical runs. See finding 2.
