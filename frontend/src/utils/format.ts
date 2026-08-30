// Presentation helpers shared across the screens.

import type { FieldStatus, OverallStatus, Severity } from '../types'

export function severityClasses(severity: Severity): string {
  switch (severity) {
    case 'HIGH':
      return 'bg-red-100 text-red-800 border-red-200'
    case 'MEDIUM':
      return 'bg-amber-100 text-amber-800 border-amber-200'
    default:
      return 'bg-slate-100 text-slate-700 border-slate-200'
  }
}

export function statusClasses(status: OverallStatus | string | null): string {
  switch (status) {
    case 'CLEAR':
      return 'bg-emerald-100 text-emerald-800'
    case 'HIGH_RISK':
      return 'bg-red-100 text-red-800'
    case 'REVIEW_REQUIRED':
      return 'bg-amber-100 text-amber-800'
    case 'FAILED':
      return 'bg-red-100 text-red-800'
    case 'COMPLETED':
      return 'bg-emerald-100 text-emerald-800'
    default:
      return 'bg-slate-100 text-slate-700'
  }
}

export function fieldStatusClasses(status: FieldStatus): string {
  switch (status) {
    case 'CONFIRMED':
      return 'bg-emerald-100 text-emerald-800'
    case 'CONFLICTING':
      return 'bg-red-100 text-red-800'
    case 'UNCERTAIN':
      return 'bg-amber-100 text-amber-800'
    default:
      return 'bg-slate-100 text-slate-500'
  }
}

export function titleCase(value: string): string {
  return value
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, (character) => character.toUpperCase())
}

export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`
}

export function formatDate(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
