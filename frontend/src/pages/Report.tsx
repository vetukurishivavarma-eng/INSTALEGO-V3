// Report screen: generate, preview, download.

import { Link, useParams } from 'react-router-dom'
import { ReportViewer } from '../components/ReportViewer/ReportViewer'
import { useGenerateReport, useReports } from '../hooks'

export default function ReportPage() {
  const { caseId = '' } = useParams()
  const { data: reports } = useReports(caseId)
  const generate = useGenerateReport(caseId)

  // Reports are returned newest first, so the head is the current one.
  const latest = reports?.[0] ?? null

  return (
    <div className="space-y-4">
      <div>
        <Link to={`/cases/${caseId}`} className="text-xs text-sky-700 hover:underline">
          ← Back to case
        </Link>
        <h1 className="text-xl font-semibold text-slate-900">Report</h1>
      </div>

      {generate.error && (
        <div className="card border-red-200 p-4 text-sm text-red-700">
          {(generate.error as Error).message}
        </div>
      )}

      <ReportViewer
        report={latest}
        generating={generate.isPending}
        onGenerate={() => generate.mutate(undefined)}
      />

      {reports && reports.length > 1 && (
        <div className="card">
          <div className="card-header">Earlier versions ({reports.length - 1})</div>
          <ul className="divide-y divide-slate-100 text-sm">
            {reports.slice(1).map((report) => (
              <li key={report.id} className="flex justify-between px-4 py-2">
                <span className="font-mono text-xs">{report.id.slice(0, 8)}</span>
                <span className="text-xs text-slate-500">
                  {report.template_id} · {report.status} ·{' '}
                  {new Date(report.created_at).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
