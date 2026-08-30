// Application shell and routes.

import { Link, Route, Routes, useLocation } from 'react-router-dom'
import Analysis from './pages/Analysis'
import CaseDetails from './pages/CaseDetails'
import Dashboard from './pages/Dashboard'
import NewCase from './pages/NewCase'
import ReportPage from './pages/Report'

export default function App() {
  const location = useLocation()

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
          <Link to="/" className="flex items-center gap-2">
            <span className="text-base font-semibold text-slate-900">Legal Document AI</span>
            <span className="badge bg-slate-100 text-slate-500">decision support</span>
          </Link>
          <nav className="flex items-center gap-4 text-sm">
            <Link
              to="/"
              className={location.pathname === '/' ? 'font-medium text-slate-900' : 'text-slate-600'}
            >
              Cases
            </Link>
            <Link to="/cases/new" className="btn-primary py-1.5">
              New case
            </Link>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/cases/new" element={<NewCase />} />
          <Route path="/cases/:caseId" element={<CaseDetails />} />
          <Route path="/cases/:caseId/analysis" element={<Analysis />} />
          <Route path="/cases/:caseId/report" element={<ReportPage />} />
          <Route
            path="*"
            element={
              <div className="card p-8 text-center text-sm text-slate-600">
                That page does not exist. <Link to="/" className="text-sky-700">Back to cases</Link>
              </div>
            }
          />
        </Routes>
      </main>

      <footer className="mx-auto max-w-7xl px-4 py-6 text-xs text-slate-400">
        Findings identify inconsistencies between documents. They are not determinations of
        fraud, and every case requires review by a person.
      </footer>
    </div>
  )
}
