// Data hooks. Each one owns a query key so invalidation stays predictable.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../services/api'
import type { ReviewDecision } from '../types'

export const keys = {
  cases: ['cases'] as const,
  case: (id: string) => ['case', id] as const,
  status: (id: string) => ['status', id] as const,
  documents: (id: string) => ['documents', id] as const,
  analysis: (id: string) => ['analysis', id] as const,
  discrepancies: (id: string) => ['discrepancies', id] as const,
  reports: (id: string) => ['reports', id] as const,
  audit: (id: string) => ['audit', id] as const,
  banks: ['banks'] as const,
}

// Statuses that mean the pipeline is still working. Polling stops otherwise,
// so a finished case does not keep a request loop running all afternoon.
const ACTIVE = new Set(['UPLOADING', 'PROCESSING', 'EXTRACTING', 'VALIDATING'])

export function useCases() {
  return useQuery({ queryKey: keys.cases, queryFn: () => api.listCases() })
}

export function useCase(caseId: string) {
  return useQuery({ queryKey: keys.case(caseId), queryFn: () => api.getCase(caseId) })
}

export function useCaseProgress(caseId: string, enabled = true) {
  return useQuery({
    queryKey: keys.status(caseId),
    queryFn: () => api.getStatus(caseId),
    enabled,
    refetchInterval: (query) =>
      query.state.data && ACTIVE.has(query.state.data.status) ? 2000 : false,
  })
}

export function useDocuments(caseId: string) {
  return useQuery({ queryKey: keys.documents(caseId), queryFn: () => api.listDocuments(caseId) })
}

export function useAnalysis(caseId: string, enabled = true) {
  return useQuery({
    queryKey: keys.analysis(caseId),
    queryFn: () => api.getAnalysis(caseId),
    enabled,
    retry: false,
  })
}

export function useReports(caseId: string) {
  return useQuery({ queryKey: keys.reports(caseId), queryFn: () => api.listReports(caseId) })
}

export function useAudit(caseId: string) {
  return useQuery({ queryKey: keys.audit(caseId), queryFn: () => api.getAudit(caseId) })
}

export function useBanks() {
  return useQuery({ queryKey: keys.banks, queryFn: () => api.listBanks(), staleTime: 300_000 })
}

export function useCreateCase() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (payload: { bank_id: string; applicant_name?: string }) =>
      api.createCase(payload),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.cases }),
  })
}

export function useUpload(caseId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ files, analyze }: { files: File[]; analyze: boolean }) => {
      const result = await api.uploadDocuments(caseId, files, analyze)
      // The upload can succeed while the handoff to analysis fails: the files
      // are stored, the response is 200, and analysis_queued says it never
      // started. Ignoring that field left the page waiting on a pipeline that
      // was never running, with the reason only in the server log.
      if (analyze && result.accepted > 0 && !result.analysis_queued) {
        throw new Error(
          'The documents were uploaded, but the analysis could not be started. ' +
            'Check the API log, then use Run analysis to retry.',
        )
      }
      return result
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: keys.documents(caseId) })
      client.invalidateQueries({ queryKey: keys.status(caseId) })
      client.invalidateQueries({ queryKey: keys.cases })
    },
  })
}

export function useAnalyze(caseId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.analyze(caseId),
    onSuccess: () => {
      // The inline backend finishes before this resolves, so everything the
      // pipeline touches is refreshed rather than only the progress endpoint.
      client.invalidateQueries({ queryKey: keys.status(caseId) })
      client.invalidateQueries({ queryKey: keys.analysis(caseId) })
      client.invalidateQueries({ queryKey: keys.documents(caseId) })
      client.invalidateQueries({ queryKey: keys.case(caseId) })
      client.invalidateQueries({ queryKey: keys.cases })
    },
  })
}

export function useReviewFlag(caseId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      code,
      decision,
      note,
    }: {
      code: string
      decision: ReviewDecision
      note?: string
    }) => api.reviewDiscrepancy(caseId, code, decision, note),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: keys.analysis(caseId) })
      client.invalidateQueries({ queryKey: keys.audit(caseId) })
    },
  })
}

export function useGenerateReport(caseId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (templateId?: string) => api.generateReport(caseId, templateId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: keys.reports(caseId) })
      client.invalidateQueries({ queryKey: keys.audit(caseId) })
    },
  })
}
