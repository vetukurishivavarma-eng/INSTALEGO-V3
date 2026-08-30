// One case: status, documents, profile, findings, viewer.
//
// The layout follows the reviewer's question order — what is this case, what
// was supplied, who does the system think the applicant is, what disagrees,
// and show me the page.

import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ProfilePanel } from '../components/ApplicantProfile/ProfilePanel'
import { CaseStatusBar } from '../components/CaseStatus/CaseStatusBar'
import { DocumentList, DocumentViewer } from '../components/DocumentViewer/DocumentViewer'
import { FlagPanel } from '../components/FlagPanel/FlagPanel'
import { UploadPanel } from '../components/Upload/UploadPanel'
import {
  useAnalysis,
  useAnalyze,
  useCase,
  useCaseProgress,
  useDocuments,
  useReviewFlag,
  useUpload,
} from '../hooks'
import type { EvidenceRef } from '../types'
import { statusClasses, titleCase } from '../utils/format'

export default function CaseDetails() {
  const { caseId = '' } = useParams()
  const { data: caseData } = useCase(caseId)
  const { data: progress } = useCaseProgress(caseId)
  const { data: documents } = useDocuments(caseId)
  const analysed = ['REVIEW_REQUIRED', 'COMPLETED', 'FAILED'].includes(progress?.status ?? '')
  const { data: analysis } = useAnalysis(caseId, analysed)

  const upload = useUpload(caseId)
  const analyze = useAnalyze(caseId)
  const review = useReviewFlag(caseId)

  const [selection, setSelection] = useState<{
    documentId: string
    page: number
    highlight?: EvidenceRef
  } | null>(null)

  useEffect(() => {
    if (!selection && documents?.length) {
      setSelection({ documentId: documents[0].id, page: 1 })
    }
  }, [documents, selection])

  function openEvidence(reference: EvidenceRef) {
    if (!reference.document_id) return
    setSelection({
      documentId: reference.document_id,
      page: reference.page || 1,
      highlight: reference,
    })
    document.getElementById('viewer')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link to="/" className="text-xs text-sky-700 hover:underline">
            ← All cases
          </Link>
          <h1 className="text-xl font-semibold text-slate-900">
            {caseData?.case_ref ?? 'Case'}
          </h1>
          <p className="text-sm text-slate-500">
            {caseData?.applicant_name_hint ?? 'Applicant not named'} ·{' '}
            {titleCase(caseData?.bank_id ?? '')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {analysis && (
            <span className={`badge ${statusClasses(analysis.final_status)}`}>
              {analysis.final_status}
            </span>
          )}
          <button
            className="btn-primary"
            disabled={analyze.isPending || !documents?.length}
            onClick={() => analyze.mutate()}
          >
            {analyze.isPending ? 'Analysing…' : analysed ? 'Re-run analysis' : 'Run analysis'}
          </button>
          {analysed && (
            <Link to={`/cases/${caseId}/report`} className="btn-secondary">
              Report
            </Link>
          )}
        </div>
      </div>

      {progress && <CaseStatusBar progress={progress} />}

      {(analyze.error || upload.error) && (
        <div className="card border-red-200 p-4 text-sm text-red-700">
          {((analyze.error || upload.error) as Error).message}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-1">
          <UploadPanel
            onUpload={(files, runAnalysis) => upload.mutate({ files, analyze: runAnalysis })}
            uploading={upload.isPending}
            results={upload.data?.results}
          />
          {documents && documents.length > 0 && (
            <DocumentList
              documents={documents}
              activeId={selection?.documentId}
              onSelect={(documentId, page) => setSelection({ documentId, page })}
            />
          )}
          {analysis && <ProfilePanel fields={analysis.applicant.fields} onOpenEvidence={openEvidence} />}
        </div>

        <div className="space-y-4 lg:col-span-2">
          {analysis && analysis.missing_documents.length > 0 && (
            <div className="card border-amber-200">
              <div className="card-header text-amber-800">
                Missing documents ({analysis.missing_documents.length})
              </div>
              <ul className="divide-y divide-slate-100">
                {analysis.missing_documents.map((item) => (
                  <li key={item.document_type} className="px-4 py-2 text-sm">
                    <span className="font-medium">{titleCase(item.document_type)}</span>
                    <span className="ml-2 text-xs text-slate-500">{item.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {analysis?.completeness
            ?.filter((area) => !area.satisfied)
            .map((area) => (
              // Deliberately not styled as a warning. Nothing here is wrong
              // with the application: a section of the report is not covered,
              // and this says what would cover it.
              <div key={area.key} className="card border-sky-200">
                <div className="card-header text-sky-800">
                  {area.title} &mdash; not covered
                </div>
                <div className="space-y-2 px-4 py-3 text-sm">
                  <p className="text-slate-700">{area.message}</p>
                  {area.provided.length > 0 && (
                    <p className="text-xs text-slate-500">
                      Received: {area.provided.map(titleCase).join(', ')}
                    </p>
                  )}
                </div>
              </div>
            ))}

          {analysis && (
            <FlagPanel
              discrepancies={analysis.discrepancies}
              onOpenEvidence={openEvidence}
              reviewing={review.isPending}
              onReview={(code, decision, note) => review.mutate({ code, decision, note })}
            />
          )}

          <div id="viewer">
            <DocumentViewer
              documents={documents ?? []}
              selection={selection}
              onSelect={(documentId, page) => setSelection({ documentId, page })}
            />
          </div>

          {analysis && (
            <div className="card">
              <div className="card-header">
                Validation results ({analysis.validations.length} checks run)
              </div>
              <div className="max-h-72 overflow-auto">
                <table className="w-full text-left text-xs">
                  <tbody className="divide-y divide-slate-100">
                    {analysis.validations
                      .filter((validation) => validation.result !== 'NOT_APPLICABLE')
                      .map((validation, index) => (
                        <tr key={`${validation.rule_id}-${index}`}>
                          <td className="px-3 py-1.5 font-mono text-slate-500">
                            {validation.rule_id}
                          </td>
                          <td className="px-3 py-1.5">
                            <span
                              className={`badge ${
                                validation.result === 'PASS'
                                  ? 'bg-emerald-100 text-emerald-800'
                                  : validation.result === 'FAIL'
                                    ? 'bg-red-100 text-red-800'
                                    : 'bg-amber-100 text-amber-800'
                              }`}
                            >
                              {validation.result}
                            </span>
                          </td>
                          <td className="px-3 py-1.5 text-slate-600">{validation.reason}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
