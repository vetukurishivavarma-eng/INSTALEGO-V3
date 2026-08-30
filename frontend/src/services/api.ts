// The only module that talks to the backend.
//
// Errors carry the API's own error_code where there is one, because "PAN
// mismatch" and "the file was renamed" need different words in the UI.

import type {
  AuditEntry,
  BankOption,
  CanonicalAnalysis,
  CaseListResponse,
  CaseProgress,
  CaseSummary,
  Discrepancy,
  DocumentSummary,
  Report,
  ReviewDecision,
  UploadResponse,
} from '../types'

const BASE = '/api'

export class ApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: init?.body instanceof FormData ? {} : { 'content-type': 'application/json' },
    ...init,
  })

  if (!response.ok) {
    let detail = response.statusText
    let code: string | undefined
    try {
      const body = await response.json()
      detail = body.detail ?? body.error ?? detail
      code = body.error_code
    } catch {
      // A non-JSON error body is still an error; the status text will do.
    }
    throw new ApiError(detail, response.status, code)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  listCases: (limit = 50, offset = 0) =>
    request<CaseListResponse>(`/cases?limit=${limit}&offset=${offset}`),

  getCase: (caseId: string) => request<CaseSummary>(`/cases/${caseId}`),

  createCase: (payload: { bank_id: string; applicant_name?: string }) =>
    request<CaseSummary>('/cases', { method: 'POST', body: JSON.stringify(payload) }),

  deleteCase: (caseId: string) => request<void>(`/cases/${caseId}`, { method: 'DELETE' }),

  getStatus: (caseId: string) => request<CaseProgress>(`/cases/${caseId}/status`),

  listDocuments: (caseId: string) => request<DocumentSummary[]>(`/cases/${caseId}/documents`),

  uploadDocuments: (caseId: string, files: File[], analyze = false) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    return request<UploadResponse>(`/cases/${caseId}/documents?analyze=${analyze}`, {
      method: 'POST',
      body: form,
    })
  },

  analyze: (caseId: string) =>
    request<{ case_id: string; queued: boolean; backend: string }>(
      `/cases/${caseId}/analyze`,
      { method: 'POST' },
    ),

  getAnalysis: (caseId: string) => request<CanonicalAnalysis>(`/cases/${caseId}/analysis`),

  getDiscrepancies: (caseId: string) =>
    request<Discrepancy[]>(`/cases/${caseId}/discrepancies`),

  reviewDiscrepancy: (caseId: string, code: string, decision: ReviewDecision, note?: string) =>
    request<Discrepancy>(`/cases/${caseId}/discrepancies/${code}/review`, {
      method: 'POST',
      body: JSON.stringify({ decision, note }),
    }),

  getAudit: (caseId: string) => request<AuditEntry[]>(`/cases/${caseId}/audit`),

  listReports: (caseId: string) => request<Report[]>(`/cases/${caseId}/reports`),

  generateReport: (caseId: string, templateId?: string) =>
    request<Report>(`/cases/${caseId}/reports/generate`, {
      method: 'POST',
      body: JSON.stringify({ template_id: templateId ?? null }),
    }),

  listBanks: () => request<BankOption[]>('/banks'),

  // Direct URLs for the viewer and the download buttons.
  documentFileUrl: (documentId: string) => `${BASE}/documents/${documentId}/file`,
  pageImageUrl: (documentId: string, page: number) =>
    `${BASE}/documents/${documentId}/pages/${page}/image`,
  pageTextUrl: (documentId: string, page: number) =>
    `${BASE}/documents/${documentId}/pages/${page}`,
  reportDownloadUrl: (reportId: string, format: 'pdf' | 'docx') =>
    `${BASE}/reports/${reportId}/download/${format}`,

  getPageText: (documentId: string, page: number) =>
    request<{ document_id: string; page_number: number; text: string; tables: unknown[] }>(
      `/documents/${documentId}/pages/${page}`,
    ),
}
