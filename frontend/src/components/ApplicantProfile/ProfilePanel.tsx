// The canonical applicant profile.
//
// A conflicting field shows both values side by side rather than a single
// chosen one. Hiding the disagreement behind a "best" value would defeat the
// point of building the profile at all.

import type { EvidenceRef, ProfileField } from '../../types'
import { fieldStatusClasses, formatPercent, titleCase } from '../../utils/format'

interface Props {
  fields: Record<string, ProfileField>
  onOpenEvidence?: (reference: EvidenceRef) => void
}

export function ProfilePanel({ fields, onOpenEvidence }: Props) {
  const present = Object.entries(fields).filter(([, field]) => field.status !== 'NOT_FOUND')
  const absent = Object.entries(fields).filter(([, field]) => field.status === 'NOT_FOUND')

  return (
    <div className="card">
      <div className="card-header">Applicant profile</div>

      {present.length === 0 ? (
        <div className="p-6 text-sm text-slate-500">
          No applicant details were extracted from these documents.
        </div>
      ) : (
        <ul className="divide-y divide-slate-200">
          {present.map(([name, field]) => (
            <li key={name} className="px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium text-slate-800">{titleCase(name)}</span>
                <span className={`badge ${fieldStatusClasses(field.status)}`}>
                  {field.status} · {formatPercent(field.confidence)}
                </span>
              </div>

              {field.status === 'CONFLICTING' ? (
                <div className="mt-2 space-y-1">
                  {field.sources.map((source, index) => (
                    <button
                      key={`${source.document_id}-${index}`}
                      onClick={() =>
                        onOpenEvidence?.({
                          document_id: source.document_id,
                          document_name: source.document_name,
                          document_type: source.document_type,
                          page: source.page,
                          field: name,
                          value: source.value,
                          snippet: source.snippet,
                          bbox: source.bbox,
                        })
                      }
                      className="flex w-full items-center justify-between rounded border
                                 border-red-200 bg-red-50 px-3 py-1.5 text-left text-sm
                                 hover:border-red-400"
                    >
                      <span className="font-mono">{source.value}</span>
                      <span className="text-xs text-slate-500">
                        {source.document_name} · p{source.page}
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="mt-1 flex flex-wrap items-baseline gap-2">
                  <span className="font-mono text-sm text-slate-900">{field.value || '—'}</span>
                  {field.normalized_value && field.normalized_value !== field.value && (
                    <span className="text-xs text-slate-400">
                      normalised: {field.normalized_value}
                    </span>
                  )}
                </div>
              )}

              {field.status !== 'CONFLICTING' && field.sources.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
                  {field.sources.map((source, index) => (
                    <button
                      key={`${source.document_id}-${index}`}
                      onClick={() =>
                        onOpenEvidence?.({
                          document_id: source.document_id,
                          document_name: source.document_name,
                          document_type: source.document_type,
                          page: source.page,
                          field: name,
                          value: source.value,
                          snippet: source.snippet,
                          bbox: source.bbox,
                        })
                      }
                      className="rounded bg-slate-100 px-2 py-0.5 hover:bg-slate-200"
                    >
                      {source.document_name} · p{source.page}
                    </button>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {absent.length > 0 && (
        <div className="border-t border-slate-200 px-4 py-3 text-xs text-slate-500">
          Not found in any document: {absent.map(([name]) => titleCase(name)).join(', ')}
        </div>
      )}
    </div>
  )
}
