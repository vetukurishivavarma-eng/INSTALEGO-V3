// Create a case, then upload its documents.
//
// The bank is chosen first because it decides which documents are required and
// how severely a mismatch is treated, and the required list is shown at the
// point of choosing rather than discovered later in a report.

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { UploadPanel } from '../components/Upload/UploadPanel'
import { useBanks, useCreateCase, useUpload } from '../hooks'
import type { UploadResult } from '../types'
import { titleCase } from '../utils/format'

export default function NewCase() {
  const navigate = useNavigate()
  const { data: banks } = useBanks()
  const createCase = useCreateCase()

  const [bankId, setBankId] = useState('default')
  const [applicant, setApplicant] = useState('')
  const [caseId, setCaseId] = useState<string | null>(null)
  const [results, setResults] = useState<UploadResult[]>([])

  const upload = useUpload(caseId ?? '')
  const selectedBank = banks?.find((bank) => bank.bank_id === bankId)

  async function handleCreate() {
    const created = await createCase.mutateAsync({
      bank_id: bankId,
      applicant_name: applicant || undefined,
    })
    setCaseId(created.id)
  }

  async function handleUpload(files: File[], analyze: boolean) {
    if (!caseId) return
    try {
      const response = await upload.mutateAsync({ files, analyze })
      setResults(response.results)
      if (analyze && response.accepted > 0) navigate(`/cases/${caseId}`)
    } catch {
      // mutateAsync rethrows, and an uncaught rejection here would leave the
      // page silent while the mutation's own error state carries the reason.
      // It is rendered below; staying on this page is correct, because the
      // documents may well have been stored.
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-xl font-semibold text-slate-900">New case</h1>

      <div className="card">
        <div className="card-header">Case details</div>
        <div className="space-y-4 p-4">
          <div>
            <label className="label" htmlFor="bank">
              Bank / rule set
            </label>
            <select
              id="bank"
              className="input"
              value={bankId}
              disabled={Boolean(caseId)}
              onChange={(event) => setBankId(event.target.value)}
            >
              {(banks ?? [{ bank_id: 'default', name: 'Default' }]).map((bank) => (
                <option key={bank.bank_id} value={bank.bank_id}>
                  {bank.name}
                </option>
              ))}
            </select>
            {selectedBank && (
              <p className="mt-2 text-xs text-slate-500">
                Requires: {selectedBank.required_documents.map(titleCase).join(', ')} · rules{' '}
                {selectedBank.version} · report template {selectedBank.report_template}
              </p>
            )}
          </div>

          <div>
            <label className="label" htmlFor="applicant">
              Applicant name (optional)
            </label>
            <input
              id="applicant"
              className="input"
              value={applicant}
              disabled={Boolean(caseId)}
              placeholder="Used as a label only; the profile is built from the documents"
              onChange={(event) => setApplicant(event.target.value)}
            />
          </div>

          {!caseId ? (
            <button
              className="btn-primary"
              onClick={handleCreate}
              disabled={createCase.isPending}
            >
              {createCase.isPending ? 'Creating…' : 'Create case'}
            </button>
          ) : (
            <p className="text-sm text-emerald-700">
              Case created. Add the documents below.
            </p>
          )}

          {createCase.error && (
            <p className="text-sm text-red-700">{(createCase.error as Error).message}</p>
          )}

          {upload.error && (
            <p className="text-sm text-red-700">{(upload.error as Error).message}</p>
          )}
        </div>
      </div>

      {caseId && (
        <>
          <UploadPanel
            onUpload={handleUpload}
            uploading={upload.isPending}
            results={results}
          />
          <div className="flex gap-2">
            <button className="btn-secondary" onClick={() => navigate(`/cases/${caseId}`)}>
              Go to case
            </button>
          </div>
        </>
      )}
    </div>
  )
}
