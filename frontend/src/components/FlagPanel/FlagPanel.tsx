// The flag list.
//
// A flag never reads "DOB mismatch". It shows both values, the document and
// page each came from, how confident the system is, which rule fired, and what
// to do next. Clicking a citation opens that page in the viewer, because a
// reviewer's first question is always "show me".

import { useState } from 'react'
import type { Discrepancy, EvidenceRef, ReviewDecision, Severity } from '../../types'
import { formatPercent, severityClasses, titleCase } from '../../utils/format'

interface Props {
  discrepancies: Discrepancy[]
  onOpenEvidence: (reference: EvidenceRef) => void
  onReview?: (code: string, decision: ReviewDecision, note?: string) => void
  reviewing?: boolean
}

const SEVERITY_ORDER: Severity[] = ['HIGH', 'MEDIUM', 'LOW']

export function FlagPanel({ discrepancies, onOpenEvidence, onReview, reviewing }: Props) {
  const [filter, setFilter] = useState<Severity | 'ALL'>('ALL')
  const [expanded, setExpanded] = useState<string | null>(
    discrepancies.length ? discrepancies[0].code : null,
  )

  const counts = SEVERITY_ORDER.reduce<Record<string, number>>((totals, severity) => {
    totals[severity] = discrepancies.filter((item) => item.severity === severity).length
    return totals
  }, {})

  const visible =
    filter === 'ALL' ? discrepancies : discrepancies.filter((item) => item.severity === filter)

  if (!discrepancies.length) {
    return (
      <div className="card">
        <div className="card-header">Findings</div>
        <div className="p-6 text-sm text-slate-600">
          No discrepancies were detected between these documents. This is not a clearance
          decision; it means the checks that ran found nothing inconsistent.
        </div>
      </div>
    )
  }

  return (
    <div className="card">
      <div className="card-header flex items-center justify-between">
        <span>Findings ({discrepancies.length})</span>
        <div className="flex gap-1">
          {(['ALL', ...SEVERITY_ORDER] as const).map((option) => (
            <button
              key={option}
              onClick={() => setFilter(option)}
              className={`rounded px-2 py-1 text-xs font-medium ${
                filter === option
                  ? 'bg-slate-900 text-white'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              }`}
            >
              {option === 'ALL' ? `All ${discrepancies.length}` : `${option} ${counts[option] ?? 0}`}
            </button>
          ))}
        </div>
      </div>

      <ul className="divide-y divide-slate-200">
        {visible.map((finding) => (
          <FlagRow
            key={finding.code}
            finding={finding}
            open={expanded === finding.code}
            onToggle={() => setExpanded(expanded === finding.code ? null : finding.code)}
            onOpenEvidence={onOpenEvidence}
            onReview={onReview}
            reviewing={reviewing}
          />
        ))}
      </ul>
    </div>
  )
}

function FlagRow({
  finding,
  open,
  onToggle,
  onOpenEvidence,
  onReview,
  reviewing,
}: {
  finding: Discrepancy
  open: boolean
  onToggle: () => void
  onOpenEvidence: (reference: EvidenceRef) => void
  onReview?: (code: string, decision: ReviewDecision, note?: string) => void
  reviewing?: boolean
}) {
  const [note, setNote] = useState('')

  return (
    <li className="px-4 py-3">
      <button onClick={onToggle} className="flex w-full items-start gap-3 text-left">
        <span
          className={`badge border ${severityClasses(finding.severity)} shrink-0`}
        >
          {finding.severity}
        </span>
        <span className="flex-1">
          <span className="block text-sm font-medium text-slate-900">
            {finding.code} · {titleCase(finding.type)}
            {finding.field ? ` · ${titleCase(finding.field)}` : ''}
          </span>
          <span className="mt-0.5 block text-xs text-slate-500">
            {finding.classification} · confidence {formatPercent(finding.confidence)}
            {finding.verified ? ' · evidence verified' : ''}
            {finding.review_decision !== 'PENDING' ? ` · ${finding.review_decision}` : ''}
          </span>
        </span>
        <span className="text-slate-400">{open ? '−' : '+'}</span>
      </button>

      {open && (
        <div className="mt-3 space-y-3 pl-1">
          <div className="grid gap-2 sm:grid-cols-2">
            {finding.evidence.map((reference, index) => (
              <button
                key={`${reference.document_id}-${reference.page}-${index}`}
                onClick={() => onOpenEvidence(reference)}
                className="rounded-md border border-slate-200 bg-slate-50 p-3 text-left
                           transition hover:border-slate-400 hover:bg-white"
              >
                <div className="text-xs font-medium text-slate-500">
                  {reference.document_name || 'Document'}
                  {reference.page ? ` · page ${reference.page}` : ''}
                </div>
                <div className="mt-1 break-words font-mono text-sm text-slate-900">
                  {reference.value || '—'}
                </div>
                {reference.snippet && (
                  <div className="mt-1 truncate text-xs italic text-slate-500">
                    “{reference.snippet}”
                  </div>
                )}
                <div className="mt-1 text-xs text-sky-700">Open this page →</div>
              </button>
            ))}
          </div>

          {finding.explanation && (
            <p className="text-sm text-slate-700">{finding.explanation}</p>
          )}

          <div className="rounded-md bg-slate-50 p-3 text-sm">
            <span className="font-medium text-slate-700">Recommended action: </span>
            <span className="text-slate-700">
              {finding.recommended_action || 'Verify against the source documents.'}
            </span>
          </div>

          <div className="text-xs text-slate-400">
            Raised by {finding.rule_id ?? finding.origin}
          </div>

          {onReview && (
            <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
              <input
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="Reviewer note (optional)"
                className="input mt-0 flex-1 min-w-[12rem]"
              />
              {(['ACCEPTED', 'REJECTED', 'NEEDS_INFO'] as ReviewDecision[]).map((decision) => (
                <button
                  key={decision}
                  disabled={reviewing}
                  onClick={() => onReview(finding.code, decision, note || undefined)}
                  className="btn-secondary"
                >
                  {titleCase(decision)}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </li>
  )
}
