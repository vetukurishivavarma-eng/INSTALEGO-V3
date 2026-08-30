// The document viewer.
//
// Opens at the page a flag cites, and shows the extracted text for that page
// beside the document itself so a reviewer can check the value against the
// words it came from. Where the backend rendered a page image and the
// extraction carried a bounding box, the box is drawn over it.

import { useEffect, useState } from 'react'
import { api } from '../../services/api'
import type { DocumentSummary, EvidenceRef } from '../../types'
import { formatBytes, titleCase } from '../../utils/format'

interface Props {
  documents: DocumentSummary[]
  selection: { documentId: string; page: number; highlight?: EvidenceRef } | null
  onSelect: (documentId: string, page: number) => void
}

export function DocumentViewer({ documents, selection, onSelect }: Props) {
  const [pageText, setPageText] = useState<string>('')
  const [imageFailed, setImageFailed] = useState(false)

  const active = documents.find((document) => document.id === selection?.documentId) ?? null
  const page = selection?.page ?? 1

  useEffect(() => {
    setImageFailed(false)
    if (!active) {
      setPageText('')
      return
    }
    let cancelled = false
    api
      .getPageText(active.id, page)
      .then((result) => {
        if (!cancelled) setPageText(result.text)
      })
      .catch(() => {
        if (!cancelled) setPageText('')
      })
    return () => {
      cancelled = true
    }
  }, [active, page])

  if (!active) {
    return (
      <div className="card">
        <div className="card-header">Document viewer</div>
        <div className="p-6 text-sm text-slate-500">
          Select a document, or click the evidence on a finding to open the page it cites.
        </div>
      </div>
    )
  }

  const isPdf = active.mime_type === 'application/pdf'
  const highlight = selection?.highlight

  return (
    <div className="card overflow-hidden">
      <div className="card-header flex flex-wrap items-center justify-between gap-2">
        <span className="truncate">{active.filename}</span>
        <span className="flex items-center gap-2 text-xs font-normal text-slate-500">
          {titleCase(active.document_type)} · {formatBytes(active.size_bytes)}
          <a
            href={api.documentFileUrl(active.id)}
            target="_blank"
            rel="noreferrer"
            className="text-sky-700 hover:underline"
          >
            Open original
          </a>
        </span>
      </div>

      {active.page_count > 1 && (
        <div className="flex items-center gap-2 border-b border-slate-200 px-4 py-2">
          <button
            className="btn-secondary px-2 py-1"
            disabled={page <= 1}
            onClick={() => onSelect(active.id, page - 1)}
          >
            ←
          </button>
          <span className="text-xs text-slate-600">
            Page {page} of {active.page_count}
          </span>
          <button
            className="btn-secondary px-2 py-1"
            disabled={page >= active.page_count}
            onClick={() => onSelect(active.id, page + 1)}
          >
            →
          </button>
        </div>
      )}

      <div className="grid gap-0 lg:grid-cols-2">
        <div className="relative border-r border-slate-200 bg-slate-100" style={{ minHeight: 420 }}>
          {isPdf ? (
            // The browser's own PDF viewer renders the original untouched,
            // which is what a reviewer needs to see; #page jumps to the
            // cited page.
            <iframe
              title={active.filename}
              src={`${api.documentFileUrl(active.id)}#page=${page}`}
              className="h-[520px] w-full"
            />
          ) : imageFailed ? (
            <div className="p-6 text-sm text-slate-500">
              No page image is available for this document. The extracted text is shown
              alongside; open the original to view it as supplied.
            </div>
          ) : (
            <div className="relative">
              <img
                src={api.pageImageUrl(active.id, page)}
                alt={`${active.filename} page ${page}`}
                className="w-full"
                onError={() => setImageFailed(true)}
              />
              {highlight?.bbox?.length === 4 && (
                <div
                  className="pointer-events-none absolute border-2 border-amber-500 bg-amber-300/25"
                  style={{
                    left: `${highlight.bbox[0]}%`,
                    top: `${highlight.bbox[1]}%`,
                    width: `${highlight.bbox[2] - highlight.bbox[0]}%`,
                    height: `${highlight.bbox[3] - highlight.bbox[1]}%`,
                  }}
                />
              )}
            </div>
          )}
        </div>

        <div className="max-h-[520px] overflow-auto p-4">
          {highlight && (
            <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm">
              <div className="text-xs font-medium text-amber-800">Cited value</div>
              <div className="mt-1 font-mono">{highlight.value}</div>
              {highlight.snippet && (
                <div className="mt-1 text-xs italic text-amber-900">“{highlight.snippet}”</div>
              )}
            </div>
          )}
          <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
            Extracted text · page {page}
          </div>
          <pre className="mt-2 whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700">
            {pageText || 'No text layer was extracted for this page.'}
          </pre>
        </div>
      </div>
    </div>
  )
}

export function DocumentList({
  documents,
  activeId,
  onSelect,
}: {
  documents: DocumentSummary[]
  activeId?: string
  onSelect: (documentId: string, page: number) => void
}) {
  return (
    <div className="card">
      <div className="card-header">Documents ({documents.length})</div>
      <ul className="divide-y divide-slate-200">
        {documents.map((document) => (
          <li key={document.id}>
            <button
              onClick={() => onSelect(document.id, 1)}
              className={`w-full px-4 py-3 text-left transition hover:bg-slate-50 ${
                activeId === document.id ? 'bg-slate-50' : ''
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium text-slate-800">
                  {document.filename}
                </span>
                <span className="badge bg-slate-100 text-slate-600">
                  {titleCase(document.document_type)}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
                <span>{document.page_count} page(s)</span>
                <span>{document.status}</span>
                {document.error_code && (
                  <span className="text-red-700">{document.error_code}</span>
                )}
                {document.quality_flags.map((flag) => (
                  <span key={flag} className="text-amber-700">
                    {titleCase(flag)}
                  </span>
                ))}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
