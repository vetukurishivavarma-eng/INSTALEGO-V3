// Report preview and downloads.
//
// The QA verdict is shown above the report, not hidden in a log: if the
// generated document failed its own consistency check, that is the first thing
// the person about to send it needs to know.

import type { Report, Severity } from '../../types'
import { api } from '../../services/api'
import { formatDate, severityClasses, statusClasses, titleCase } from '../../utils/format'

interface Props {
  report: Report | null
  onGenerate: () => void
  generating?: boolean
}

export function ReportViewer({ report, onGenerate, generating }: Props) {
  if (!report) {
    return (
      <div className="card">
        <div className="card-header">Report</div>
        <div className="space-y-3 p-6">
          <p className="text-sm text-slate-600">
            No report has been generated for this case yet.
          </p>
          <button className="btn-primary" onClick={onGenerate} disabled={generating}>
            {generating ? 'Generating…' : 'Generate report'}
          </button>
        </div>
      </div>
    )
  }

  const payload = report.report_json as Record<string, any>
  const summary = payload.case_summary ?? {}
  const findings: any[] = payload.discrepancies ?? []
  const assessment = payload.final_assessment ?? {}
  const provenance = payload.provenance ?? {}

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="card-header flex flex-wrap items-center justify-between gap-2">
          <span>{payload.title ?? 'Report'}</span>
          <span className="flex items-center gap-2">
            <span className={`badge ${statusClasses(report.overall_status)}`}>
              {report.overall_status}
            </span>
            <a
              className="btn-secondary"
              href={api.reportDownloadUrl(report.id, 'pdf')}
              aria-disabled={!report.has_pdf}
            >
              Download PDF
            </a>
            <a
              className="btn-secondary"
              href={api.reportDownloadUrl(report.id, 'docx')}
              aria-disabled={!report.has_docx}
            >
              Download DOCX
            </a>
            <button className="btn-primary" onClick={onGenerate} disabled={generating}>
              {generating ? 'Regenerating…' : 'Regenerate'}
            </button>
          </span>
        </div>

        <div className="space-y-4 p-4">
          {report.qa_passed === false && (
            <div className="rounded border border-red-200 bg-red-50 p-3">
              <div className="text-sm font-medium text-red-900">
                Quality assurance found {report.qa_errors.length} issue(s) with this report
              </div>
              <ul className="mt-2 space-y-1 text-xs text-red-800">
                {report.qa_errors.map((error, index) => (
                  <li key={index}>
                    <span className={`badge mr-2 ${severityClasses(error.severity as Severity)}`}>
                      {error.severity}
                    </span>
                    {error.description}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report.qa_passed && (
            <div className="rounded border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">
              Quality assurance passed: every finding in the analysis appears in the report with
              its severity and identifiers unchanged.
            </div>
          )}

          <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ['Applicant', summary.applicant_name],
              ['Case', summary.case_id],
              ['Documents received', summary.documents_received],
              ['Documents required', summary.documents_expected],
            ].map(([label, value]) => (
              <div key={String(label)}>
                <dt className="text-xs uppercase tracking-wide text-slate-400">{label}</dt>
                <dd className="text-sm text-slate-900">{String(value ?? '—')}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>

      {findings.length > 0 && (
        <div className="card">
          <div className="card-header">Findings in this report ({findings.length})</div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-600">
                <tr>
                  {['ID', 'Severity', 'Field', 'Document A', 'Value A', 'Document B', 'Value B'].map(
                    (heading) => (
                      <th key={heading} className="px-3 py-2 font-medium">
                        {heading}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {findings.map((finding) => (
                  <tr key={finding.id}>
                    <td className="px-3 py-2 font-mono">{finding.id}</td>
                    <td className="px-3 py-2">
                      <span className={`badge border ${severityClasses(finding.severity)}`}>
                        {finding.severity}
                      </span>
                    </td>
                    <td className="px-3 py-2">{titleCase(finding.field ?? '')}</td>
                    <td className="px-3 py-2">{finding.document_1}</td>
                    <td className="px-3 py-2 font-mono">{finding.value_1}</td>
                    <td className="px-3 py-2">{finding.document_2}</td>
                    <td className="px-3 py-2 font-mono">{finding.value_2}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-header">Final assessment</div>
        <div className="space-y-3 p-4 text-sm">
          <div>
            <span className="font-medium">Status: </span>
            {assessment.status ?? report.overall_status}
          </div>
          {Array.isArray(assessment.key_findings) && (
            <div>
              <div className="font-medium">Key findings</div>
              <ul className="ml-4 list-disc text-slate-700">
                {assessment.key_findings.map((item: string, index: number) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </div>
          )}
          {Array.isArray(assessment.recommended_actions) && (
            <div>
              <div className="font-medium">Recommended actions</div>
              <ul className="ml-4 list-disc text-slate-700">
                {assessment.recommended_actions.map((item: string, index: number) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </div>
          )}
          {payload.disclaimer && (
            <p className="border-t border-slate-100 pt-3 text-xs text-slate-500">
              {payload.disclaimer}
            </p>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-header">Provenance</div>
        <dl className="grid gap-3 p-4 text-xs sm:grid-cols-2 lg:grid-cols-5">
          {[
            ['Analysis version', provenance.analysis_version],
            ['Model', provenance.model],
            ['Prompt version', provenance.prompt_version],
            ['Rules version', provenance.rules_version],
            ['Generated', formatDate(report.created_at)],
          ].map(([label, value]) => (
            <div key={String(label)}>
              <dt className="uppercase tracking-wide text-slate-400">{label}</dt>
              <dd className="mono mt-0.5 break-words text-slate-700">{String(value ?? '—')}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  )
}
