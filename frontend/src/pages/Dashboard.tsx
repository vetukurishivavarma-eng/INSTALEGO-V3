// Case list.

import { Link } from 'react-router-dom'
import { useCases } from '../hooks'
import { formatDate, statusClasses, titleCase } from '../utils/format'

export default function Dashboard() {
  const { data, isLoading, error } = useCases()

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Cases</h1>
          <p className="text-sm text-slate-500">
            One case is one applicant and the documents filed for them.
          </p>
        </div>
        <Link to="/cases/new" className="btn-primary">
          New case
        </Link>
      </div>

      {isLoading && <div className="card p-6 text-sm text-slate-500">Loading cases…</div>}
      {error && (
        <div className="card border-red-200 p-6 text-sm text-red-700">
          Could not load cases: {(error as Error).message}
        </div>
      )}

      {data && data.items.length === 0 && (
        <div className="card p-8 text-center">
          <p className="text-sm text-slate-600">No cases yet.</p>
          <Link to="/cases/new" className="btn-primary mt-3">
            Create the first case
          </Link>
        </div>
      )}

      {data && data.items.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                {['Case', 'Applicant', 'Bank', 'Documents', 'Findings', 'Status', 'Created'].map(
                  (heading) => (
                    <th key={heading} className="px-4 py-2 font-medium">
                      {heading}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.items.map((item) => (
                <tr key={item.id} className="hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <Link
                      to={`/cases/${item.id}`}
                      className="font-medium text-sky-700 hover:underline"
                    >
                      {item.case_ref}
                    </Link>
                  </td>
                  <td className="px-4 py-3">{item.applicant_name_hint ?? '—'}</td>
                  <td className="px-4 py-3">{titleCase(item.bank_id)}</td>
                  <td className="px-4 py-3">{item.document_count}</td>
                  <td className="px-4 py-3">
                    <span className="flex gap-1">
                      {item.high_flags > 0 && (
                        <span className="badge bg-red-100 text-red-800">{item.high_flags} high</span>
                      )}
                      {item.medium_flags > 0 && (
                        <span className="badge bg-amber-100 text-amber-800">
                          {item.medium_flags} med
                        </span>
                      )}
                      {item.low_flags > 0 && (
                        <span className="badge bg-slate-100 text-slate-600">
                          {item.low_flags} low
                        </span>
                      )}
                      {item.high_flags + item.medium_flags + item.low_flags === 0 && (
                        <span className="text-slate-400">—</span>
                      )}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`badge ${statusClasses(item.overall_status ?? item.status)}`}>
                      {item.overall_status ?? item.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {formatDate(item.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
