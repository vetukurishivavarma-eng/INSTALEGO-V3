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

| Metric | First run (39 variants) | After all four fixes (33) |
|---|---|---|
| Field accuracy | 0.85 | **0.95** |
| Wrong (a false value returned) | 0.019 | 0.007 |
| **Silently wrong** (false and unflagged) | **0.013** | **0.000** |
| Classification accuracy | 0.82 | **0.97** |
| Variants that behaved honestly | 29/39 | **33/33** |

Not a like-for-like comparison: the day's free-tier quota ran out, so six of
the thirteen sanction-letter conditions were not re-measured and are absent
from the right-hand column rather than failing in it. The sanction letter
itself went from 0.49 to 0.86 on the conditions that were re-run, and that is
where nearly all of the movement is.

Finding 1 was verified with `--rescore`, which re-applies the scoring rules to
the values a run already recorded — a fix to normalisation costs no quota to
evaluate. The others changed a prompt or the parser, so they were re-run
against the model.

Accuracy by condition on the first run, worst first. This is the table that
shows where things broke, so it is left as measured before the fixes:

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

Four findings came out of it, in order of what they cost. All four are fixed,
and the sweep is what verified the fixes:

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

**2. Classification of the prose letter was not stable between runs, and the
type decides which fields are even requested. Fixed.**

The sanction letter came back as `AGREEMENT` on one run and `LOAN_APPLICATION`
on the next. Re-running the same thirteen fixtures with an *unchanged*
classifier prompt flipped six of them, `clean` among them — so this was
run-to-run nondeterminism rather than an effect of degradation, and
`LLM_TEMPERATURE` was already `0.0`. Because the type selects the field list,
`party_two` and `agreement_date` were requested on one run and not the next,
which is why that document sat near 0.5 and why its score moved with nothing
having changed. The consequence reached further than a metric: under `bank_b`,
whose `required_documents` includes `LOAN_APPLICATION`, a sanction letter
classified that way satisfies the requirement for an application form the
applicant never submitted.

The cause was the same as finding 3's. The classifier prompt listed eighteen
bare type names and never said what separates them, and a sanction letter is
genuinely *about* a loan application. `app/prompts/classifier.txt` now defines
the confusable types by **who issued the document and what it does** — an
application is submitted by the applicant, an agreement is issued by the
lender, and a letter that refers to an application is not itself an
application.

Separately, `CONFUSABLE_TYPES` in the extractor requests the union of the
group's fields, so what gets extracted no longer depends on which way a
borderline call went. Sharpening the prompt fixes the label; the union fixes
the consequence, because no classifier will be perfectly repeatable on a
genuinely borderline document, and a field list that hinges on a coin flip is
fragile regardless.

| Condition | Run 1 | Run 2 | After the fix |
|---|---|---|---|
| `clean` | AGREEMENT | LOAN_APPLICATION | AGREEMENT |
| `dark` | AGREEMENT | LOAN_APPLICATION | AGREEMENT |
| `very_faded` | AGREEMENT | LOAN_APPLICATION | AGREEMENT |
| `stamped` | LOAN_APPLICATION | LOAN_APPLICATION | AGREEMENT |
| `upside_down` | LOAN_APPLICATION | AGREEMENT | AGREEMENT |
| `bad_photocopy` | LOAN_APPLICATION | AGREEMENT | AGREEMENT |
| `worst_case` | LEGAL_DOCUMENT | AGREEMENT | OTHER |

`worst_case` landing on `OTHER` is the right answer, not a miss: that page is
barely legible, it is flagged, and declining to name it beats guessing. Field
extraction on the six readable conditions went from 1 of 3 correct to 3 of 3.

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

**4. Two image-quality checks did not fire. Fixed.**

*Exposure* was measured as the mean brightness of the page. A document is
mostly blank sheet, so that measured the sheet: a page photographed at a third
of the exposure it needed still averaged 86, nowhere near a threshold meant for
a black image, and the check never fired on a real underexposure. Exposure is
now read off the paper — the 95th percentile of luminance. An underexposed page
reads 88 there, and the darkest still-legible one, a page with a shadow thrown
across it, reads 224.

*Focus* was Laplacian variance, which counts any high-frequency energy as
detail and cannot tell a sharp stroke from a speck of photocopier dust. Raw, it
scored a speckled page seven times sharper than the clean original, so a noisy
out-of-focus scan — the common case — read as sharper than a clean one. A 3x3
median pass now removes isolated outliers first and leaves real strokes; the
speckled page scores within a few per cent of the clean one, and genuinely soft
pages stay well under the threshold.

Both were strict xfails and are now ordinary passing tests, with the opposite
side of each threshold covered too: a shadowed page must *not* be called
underexposed, and the median pass must not blunt a genuinely sharp page.

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
- **Classification is not fully reproducible run to run.** The prompt now
  defines the confusable types and the extractor requests the union of a
  group's fields, so a borderline call no longer changes what is extracted —
  but the label itself is still a model output and is not guaranteed stable.
