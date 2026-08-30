// Shapes returned by the API. Kept in one file so a backend schema change
// surfaces as a compile error rather than as a blank panel.

export type CaseStatus =
  | 'CREATED'
  | 'UPLOADING'
  | 'PROCESSING'
  | 'EXTRACTING'
  | 'VALIDATING'
  | 'REVIEW_REQUIRED'
  | 'COMPLETED'
  | 'FAILED'

export type OverallStatus = 'CLEAR' | 'REVIEW_REQUIRED' | 'HIGH_RISK'
export type Severity = 'HIGH' | 'MEDIUM' | 'LOW'
export type FieldStatus = 'CONFIRMED' | 'CONFLICTING' | 'UNCERTAIN' | 'NOT_FOUND'
export type Classification = 'CONFIRMED' | 'POTENTIAL' | 'NOT_A_DISCREPANCY' | 'UNCERTAIN'
export type ReviewDecision = 'PENDING' | 'ACCEPTED' | 'REJECTED' | 'NEEDS_INFO'

export interface CaseSummary {
  id: string
  case_ref: string
  bank_id: string
  applicant_name_hint: string | null
  status: CaseStatus
  current_step: string | null
  error_code: string | null
  error_detail: string | null
  document_count: number
  high_flags: number
  medium_flags: number
  low_flags: number
  overall_status: OverallStatus | null
  created_at: string
  updated_at: string
}

export interface CaseListResponse {
  items: CaseSummary[]
  total: number
  limit: number
  offset: number
}

export interface CaseProgress {
  case_id: string
  status: CaseStatus
  current_step: string | null
  progress: number
  documents_total: number
  documents_processed: number
  documents_failed: number
  error_code: string | null
  error_detail: string | null
  updated_at: string | null
}

export interface DocumentSummary {
  id: string
  case_id: string
  filename: string
  mime_type: string
  size_bytes: number
  sha256: string
  page_count: number
  status: string
  document_type: string
  document_subtype: string | null
  classification_confidence: number
  classification_reason: string | null
  is_readable: boolean
  quality_status: string | null
  quality_flags: string[]
  quality_notes: string | null
  error_code: string | null
  error_detail: string | null
  created_at: string
}

export interface EvidenceRef {
  document_id: string
  document_name: string
  document_type: string
  page: number
  field: string
  value: string
  snippet: string
  bbox: number[]
}

export interface Discrepancy {
  id: string
  code: string
  type: string
  field: string | null
  severity: Severity
  classification: Classification
  confidence: number
  explanation: string | null
  recommended_action: string | null
  origin: string
  rule_id: string | null
  verified: boolean
  review_decision: ReviewDecision
  evidence: EvidenceRef[]
}

export interface ProfileSource {
  document_id: string
  document_name: string
  document_type: string
  page: number
  value: string
  normalized_value: string
  confidence: number
  snippet: string
  bbox: number[]
}

export interface ProfileField {
  value: string
  normalized_value: string
  status: FieldStatus
  confidence: number
  sources: ProfileSource[]
  candidates: string[]
}

export interface ValidationSummary {
  rule_id: string
  rule_category: string
  result: 'PASS' | 'FAIL' | 'REVIEW' | 'NOT_APPLICABLE'
  field: string | null
  severity: Severity | null
  reason: string | null
}

export interface MissingDocument {
  document_type: string
  severity: Severity
  reason: string
  required_by: string
}

export interface AnalysisDocument {
  document_id: string
  filename: string
  document_type: string
  pages: number
  classification_confidence: number
  is_readable: boolean
  status: string
  quality_status: string
  quality_flags: string[]
  quality_notes: string | null
  error_code: string | null
}

export interface CanonicalAnalysis {
  case_id: string
  case_ref: string
  bank_id: string
  applicant: { fields: Record<string, ProfileField> }
  documents: AnalysisDocument[]
  validations: ValidationSummary[]
  discrepancies: Discrepancy[]
  missing_documents: MissingDocument[]
  document_quality: AnalysisDocument[]
  final_status: OverallStatus
  overall_confidence: number
  manual_review_required: boolean
  versions: {
    analysis_version: string
    model: string
    prompt_version: string
    rules_version: string
    generated_at: string | null
  }
}

export interface Report {
  id: string
  case_id: string
  bank_id: string
  template_id: string
  status: string
  overall_status: string | null
  qa_passed: boolean | null
  qa_errors: Array<{ type: string; field: string; description: string; severity: Severity }>
  report_json: Record<string, unknown>
  has_docx: boolean
  has_pdf: boolean
  created_at: string
}

export interface UploadResult {
  filename: string
  document_id: string | null
  accepted: boolean
  error_code: string | null
  error_detail: string | null
  duplicate_of: string | null
}

export interface UploadResponse {
  case_id: string
  accepted: number
  rejected: number
  results: UploadResult[]
  analysis_queued: boolean
}

export interface BankOption {
  bank_id: string
  name: string
  version: string
  required_documents: string[]
  report_template: string
}

export interface AuditEntry {
  at: string
  actor: string
  action: string
  entity_type: string | null
  entity_id: string | null
  details: Record<string, unknown>
  analysis_version: string | null
  model: string | null
  prompt_version: string | null
  rules_version: string | null
}
