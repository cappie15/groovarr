import { useMemo, useState, type ReactNode } from 'react';
import './DataTable.css';

/**
 * Dense, information-first table primitive shared by every list-shaped page
 * (Playlists/Tracks/Wanted/Queue/History/... in later passes) — Sonarr/
 * Radarr-style, not a consumer data-grid. Handles its own client-side
 * sorting when the caller doesn't take control of it (`sortKey`/
 * `onSortChange` are both optional and uncontrolled by default), and
 * renders simple prev/next pagination controls when `pagination` is given
 * (for server-paginated endpoints, which is every list endpoint in this
 * API per §94).
 */

export interface DataTableColumn<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  align?: 'left' | 'right' | 'center';
  /** Client-side sort comparator; omit for a non-sortable column. */
  sortAccessor?: (row: T) => string | number | null;
}

export interface DataTablePagination {
  limit: number;
  offset: number;
  /** True when a full page came back, implying more rows may exist — the
   * API doesn't return a total count, so this is the honest signal we have. */
  hasMore: boolean;
  onOffsetChange: (offset: number) => void;
}

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  rows: T[];
  getRowKey: (row: T) => string | number;
  loading?: boolean;
  error?: string | null;
  emptyMessage?: string;
  pagination?: DataTablePagination;
}

export function DataTable<T>({
  columns,
  rows,
  getRowKey,
  loading,
  error,
  emptyMessage = 'Nothing here yet.',
  pagination,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<{ key: string; direction: 'asc' | 'desc' } | null>(null);

  const sortedRows = useMemo(() => {
    if (!sort) return rows;
    const column = columns.find((c) => c.key === sort.key);
    if (!column?.sortAccessor) return rows;
    const { sortAccessor } = column;
    const factor = sort.direction === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = sortAccessor(a);
      const bv = sortAccessor(b);
      if (av === bv) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      return av > bv ? factor : -factor;
    });
  }, [rows, sort, columns]);

  function toggleSort(column: DataTableColumn<T>) {
    if (!column.sortAccessor) return;
    setSort((prev) => {
      if (prev?.key !== column.key) return { key: column.key, direction: 'asc' };
      return prev.direction === 'asc' ? { key: column.key, direction: 'desc' } : null;
    });
  }

  return (
    <div className="data-table">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                className={column.sortAccessor ? 'data-table__sortable' : undefined}
                style={{ textAlign: column.align ?? 'left' }}
                onClick={() => toggleSort(column)}
              >
                {column.header}
                {sort?.key === column.key && (
                  <span className="data-table__sort-arrow">{sort.direction === 'asc' ? ' ↑' : ' ↓'}</span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading && (
            <tr>
              <td colSpan={columns.length} className="data-table__status">
                Loading…
              </td>
            </tr>
          )}
          {!loading && error && (
            <tr>
              <td colSpan={columns.length} className="data-table__status data-table__status--error">
                {error}
              </td>
            </tr>
          )}
          {!loading && !error && sortedRows.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="data-table__status">
                {emptyMessage}
              </td>
            </tr>
          )}
          {!loading &&
            !error &&
            sortedRows.map((row) => (
              <tr key={getRowKey(row)}>
                {columns.map((column) => (
                  <td key={column.key} style={{ textAlign: column.align ?? 'left' }}>
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            ))}
        </tbody>
      </table>

      {pagination && (
        <div className="data-table__pager">
          <button
            type="button"
            disabled={pagination.offset === 0}
            onClick={() => pagination.onOffsetChange(Math.max(0, pagination.offset - pagination.limit))}
          >
            ← Previous
          </button>
          <span className="data-table__pager-range">
            Showing {rows.length === 0 ? 0 : pagination.offset + 1}–{pagination.offset + rows.length}
          </span>
          <button
            type="button"
            disabled={!pagination.hasMore}
            onClick={() => pagination.onOffsetChange(pagination.offset + pagination.limit)}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}
