// The raw canonical analysis, plus the audit chain.
//
// This is the "show me everything" screen: the exact structure the report was
// built from, and the trail of who did what. It exists so a reviewer or an
// auditor never has to take the summary on trust.

import { Link, useParams } from 'react-router-dom'
import { useAnalysis, useAudit } from '../hooks'
import { formatDate, formatPercent } from '../utils/format'

export default function Analysis() {
  const { caseId = '' } = useParams()
  const { data: analysis, error } = useAnalysis(caseId)
  const { data: audit } = useAudit(caseId)

  return (
    <div className="space-y-4">
      <div>
        <Link to={`/cases/${caseId}`} className="text-xs text-sky-700 hover:underline">
          ← Back to case
        </Link>
        <h1 className="text-xl font-semibold text-slate-900">Analysis detail</h1>
      </div>

      {error && (
        <div className="card border-amber-200 p-4 text-sm text-amber-800">
          This case has not been analysed yet.
        </div>
      )}

      {analysis && (
        <>
          <div className="card">
            <div className="card-header">Summary</div>
            <dl className="grid gap-3 p-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
              {[
                ['Status', analysis.final_status],
                ['Extraction confidence', formatPercent(analysis.overall_confidence)],
                ['Findings', String(analysis.discrepancies.length)],
                ['Checks run', String(analysis.validations.length)],
                ['Model', analysis.versions.model],
                ['Rules version', analysis.versions.rules_version],
                ['Prompt version', analysis.versions.prompt_version || '—'],
                ['Analysis version', analysis.versions.analysis_version],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs uppercase tracking-wide text-slate-400">{label}</dt>
                  <dd className="mono break-words text-slate-800">{value}</dd>
                </div>
              ))}
            </dl>
          </div>

          <div className="card">
            <div className="card-header">Canonical analysis JSON</div>
            <pre className="max-h-[520px] overflow-auto p-4 text-xs leading-relaxed text-slate-700">
              {JSON.stringify(analysis, null, 2)}
            </pre>
          </div>
        </>
      )}

      <AuditTrail audit={audit} />
    </div>
  )
}

function AuditTrail({ audit }: { audit: ReturnType<typeof useAudit>['data'] }) {
  if (!audit?.length) return null
  return (
    <div className="card">
      <div className="card-header">Audit trail ({audit.length} entries)</div>
      <ul className="divide-y divide-slate-100 text-xs">
        {audit.map((entry, index) => (
          <li key={index} className="flex flex-wrap gap-x-3 px-4 py-2">
            <span className="text-slate-400">{formatDate(entry.at)}</span>
            <span className="font-medium text-slate-800">{entry.action}</span>
            <span className="text-slate-500">{entry.actor}</span>
            {entry.rules_version && (
              <span className="text-slate-400">rules {entry.rules_version}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
