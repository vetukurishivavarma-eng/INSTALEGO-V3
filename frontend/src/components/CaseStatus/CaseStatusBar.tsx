// Pipeline progress.
//
// Named steps rather than a bare spinner: a reviewer waiting on a 40-page file
// should be able to see that it is extracting rather than stuck.

import type { CaseProgress } from '../../types'
import { statusClasses, titleCase } from '../../utils/format'

const STEPS = [
  ['uploading', 'Upload'],
  ['parsing', 'Parse'],
  ['extracting', 'Extract'],
  ['building_profile', 'Profile'],
  ['running_rules', 'Rules'],
  ['reasoning', 'Assess'],
  ['verifying_evidence', 'Verify'],
  ['finalised', 'Done'],
] as const

export function CaseStatusBar({ progress }: { progress: CaseProgress }) {
  const currentIndex = STEPS.findIndex(([key]) => key === progress.current_step)

  return (
    <div className="card">
      <div className="card-header flex items-center justify-between">
        <span>Analysis status</span>
        <span className={`badge ${statusClasses(progress.status)}`}>{progress.status}</span>
      </div>
      <div className="space-y-3 p-4">
        <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
          <div
            className="h-full bg-slate-800 transition-all duration-500"
            style={{ width: `${Math.round(progress.progress * 100)}%` }}
          />
        </div>

        <ol className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {STEPS.map(([key, label], index) => {
            const done = currentIndex >= 0 && index < currentIndex
            const active = key === progress.current_step
            return (
              <li
                key={key}
                className={
                  active
                    ? 'font-semibold text-slate-900'
                    : done
                      ? 'text-emerald-700'
                      : 'text-slate-400'
                }
              >
                {done ? '✓ ' : ''}
                {label}
              </li>
            )
          })}
        </ol>

        <div className="flex flex-wrap gap-4 text-xs text-slate-600">
          <span>
            {progress.documents_processed} of {progress.documents_total} document(s) processed
          </span>
          {progress.documents_failed > 0 && (
            <span className="text-amber-700">
              {progress.documents_failed} could not be processed
            </span>
          )}
        </div>

        {progress.error_code && (
          <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            <span className="font-medium">{titleCase(progress.error_code)}</span>
            {progress.error_detail ? `: ${progress.error_detail}` : ''}
          </div>
        )}
      </div>
    </div>
  )
}
