// Multi-file upload with drag and drop.
//
// Per-file results are shown rather than a single success or failure, because
// a batch where five of six files were accepted is the normal case and the
// reviewer needs to know which one to re-supply.

import { useRef, useState } from 'react'
import type { UploadResult } from '../../types'
import { formatBytes } from '../../utils/format'

const ACCEPTED = '.pdf,.doc,.docx,.xls,.xlsx,.jpg,.jpeg,.png'

interface Props {
  onUpload: (files: File[], analyze: boolean) => void
  uploading?: boolean
  results?: UploadResult[]
  disabled?: boolean
}

export function UploadPanel({ onUpload, uploading, results, disabled }: Props) {
  const [staged, setStaged] = useState<File[]>([])
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function addFiles(list: FileList | null) {
    if (!list) return
    setStaged((current) => [...current, ...Array.from(list)])
  }

  return (
    <div className="card">
      <div className="card-header">Documents</div>
      <div className="space-y-3 p-4">
        <div
          onDragOver={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault()
            setDragging(false)
            addFiles(event.dataTransfer.files)
          }}
          onClick={() => inputRef.current?.click()}
          className={`cursor-pointer rounded-lg border-2 border-dashed p-6 text-center transition ${
            dragging ? 'border-slate-800 bg-slate-50' : 'border-slate-300 hover:border-slate-400'
          }`}
        >
          <p className="text-sm font-medium text-slate-700">
            Drop documents here, or click to choose
          </p>
          <p className="mt-1 text-xs text-slate-500">
            PDF, scanned PDF, DOC, DOCX, XLS, XLSX, JPG, JPEG, PNG
          </p>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPTED}
            className="hidden"
            onChange={(event) => {
              addFiles(event.target.files)
              event.target.value = ''
            }}
          />
        </div>

        {staged.length > 0 && (
          <ul className="divide-y divide-slate-100 rounded border border-slate-200">
            {staged.map((file, index) => (
              <li key={`${file.name}-${index}`} className="flex items-center justify-between px-3 py-2">
                <span className="truncate text-sm text-slate-700">{file.name}</span>
                <span className="flex items-center gap-3 text-xs text-slate-500">
                  {formatBytes(file.size)}
                  <button
                    className="text-red-600 hover:underline"
                    onClick={() => setStaged(staged.filter((_, i) => i !== index))}
                  >
                    remove
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}

        <div className="flex flex-wrap gap-2">
          <button
            className="btn-primary"
            disabled={!staged.length || uploading || disabled}
            onClick={() => {
              onUpload(staged, false)
              setStaged([])
            }}
          >
            {uploading ? 'Uploading…' : `Upload ${staged.length || ''}`.trim()}
          </button>
          <button
            className="btn-secondary"
            disabled={!staged.length || uploading || disabled}
            onClick={() => {
              onUpload(staged, true)
              setStaged([])
            }}
          >
            Upload and analyse
          </button>
        </div>

        {results && results.length > 0 && (
          <ul className="space-y-1 text-xs">
            {results.map((result) => (
              <li
                key={result.filename}
                className={result.accepted ? 'text-emerald-700' : 'text-red-700'}
              >
                {result.accepted ? '✓' : '✕'} {result.filename}
                {result.error_detail ? ` — ${result.error_detail}` : ''}
                {result.duplicate_of ? ' — duplicate of an existing upload' : ''}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
