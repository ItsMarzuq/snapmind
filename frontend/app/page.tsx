'use client';

import type { CSSProperties } from 'react';
import {
  ChangeEvent,
  DragEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

type Shot = {
  id: string;
  filename: string;
  created_at: string;
  image_url: string;
  status: 'queued' | 'processing' | 'ready' | 'failed';
  ocr_text: string | null;
  processing_error: string | null;
  vision_status: 'queued' | 'processing' | 'ready' | 'failed';
  vision_data: {
    description: string;
    category: string;
    platform: string;
    tags: string[];
    notable_details: string[];
  } | null;
  vision_error: string | null;
  embedding_status: 'queued' | 'processing' | 'ready' | 'failed';
  match_reason: string | null;
};

type Mode = 'login' | 'register';
type DateFilter = 'all' | 'today' | '7d' | '30d';
type SortOrder = 'newest' | 'oldest';

type EditDraft = {
  description: string;
  category: string;
  platform: string;
  tags: string;
  details: string;
};

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: 'same-origin',
    ...options,
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === 'string'
        ? body.detail
        : `Request failed (${response.status})`,
    );
  }

  return response.status === 204 ? (undefined as T) : response.json();
}

function beginningOfToday() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
}

function matchesDateFilter(createdAt: string, filter: DateFilter) {
  if (filter === 'all') return true;

  const created = new Date(createdAt).getTime();
  const now = Date.now();

  if (filter === 'today') {
    return created >= beginningOfToday();
  }

  const days = filter === '7d' ? 7 : 30;
  return created >= now - days * 24 * 60 * 60 * 1000;
}

function processingLabel(shot: Shot) {
  if (shot.vision_status === 'failed') return 'Visual analysis failed';
  if (shot.status === 'failed') return 'OCR failed';
  if (shot.embedding_status === 'failed') return 'Search indexing failed';
  if (shot.embedding_status === 'ready') return 'Ready to search';
  if (shot.vision_status === 'ready') return 'Indexing search…';
  if (shot.vision_status === 'processing') return 'Analyzing image…';
  if (shot.status === 'processing') return 'Reading text…';
  return 'Queued';
}

function processingTone(shot: Shot) {
  if (
    shot.status === 'failed' ||
    shot.vision_status === 'failed' ||
    shot.embedding_status === 'failed'
  ) {
    return 'failed';
  }

  if (shot.embedding_status === 'ready') return 'ready';
  return 'processing';
}

export default function Home() {
  const [email, setEmail] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>('register');
  const [shots, setShots] = useState<Shot[]>([]);
  const [selected, setSelected] = useState<Shot | null>(null);

  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const [uploadNotice, setUploadNotice] = useState('');

  const [query, setQuery] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [dateFilter, setDateFilter] = useState<DateFilter>('all');
  const [sortOrder, setSortOrder] = useState<SortOrder>('newest');
  const [results, setResults] = useState<Shot[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');

  const [editing, setEditing] = useState(false);
  const [editError, setEditError] = useState('');
  const [draft, setDraft] = useState<EditDraft>({
    description: '',
    category: '',
    platform: '',
    tags: '',
    details: '',
  });

  const picker = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    const updated = await api<Shot[]>('/screenshots');
    setShots(updated);
    setSelected((current) =>
      current ? updated.find((shot) => shot.id === current.id) ?? null : null,
    );
  }, []);

  useEffect(() => {
    api<{ email: string }>('/auth/me')
      .then(async (user) => {
        setEmail(user.email);
        await refresh();
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setSelected(null);
        setEditing(false);
      }
    };

    window.addEventListener('keydown', handle);
    return () => window.removeEventListener('keydown', handle);
  }, []);

  useEffect(() => {
    if (
      !email ||
      !shots.some(
        (shot) =>
          shot.status === 'queued' ||
          shot.status === 'processing' ||
          shot.vision_status === 'queued' ||
          shot.vision_status === 'processing' ||
          shot.embedding_status === 'queued' ||
          shot.embedding_status === 'processing',
      )
    ) {
      return;
    }

    const timer = window.setInterval(() => {
      refresh().catch(() => {});
    }, 2500);

    return () => window.clearInterval(timer);
  }, [email, shots, refresh]);

  useEffect(() => {
    const term = query.trim();

    if (!email || !term) {
      setResults([]);
      setSearching(false);
      setSearchError('');
      return;
    }

    const controller = new AbortController();
    setSearching(true);
    setSearchError('');

    const timer = window.setTimeout(() => {
      api<Shot[]>(`/screenshots/search?q=${encodeURIComponent(term)}`, {
        signal: controller.signal,
      })
        .then(setResults)
        .catch((e) => {
          if ((e as Error).name !== 'AbortError') {
            setSearchError((e as Error).message);
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearching(false);
        });
    }, 250);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [email, query, shots]);

  async function authenticate(form: FormData) {
    setBusy(true);
    setError('');

    try {
      const result = await api<{ email: string }>(`/auth/${mode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: form.get('email'),
          password: form.get('password'),
        }),
      });

      setEmail(result.email);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function upload(files: FileList | File[]) {
    if (!files.length) return;

    setBusy(true);
    setError('');
    setUploadNotice('');

    try {
      const images = Array.from(files);
      let duplicateCount = 0;
      let addedCount = 0;

      for (let i = 0; i < images.length; i += 20) {
        const body = new FormData();

        images
          .slice(i, i + 20)
          .forEach((file) => body.append('files', file));

        const outcome = await api<{
          added: Shot[];
          duplicates: Shot[];
        }>('/screenshots', {
          method: 'POST',
          body,
        });

        duplicateCount += outcome.duplicates.length;
        addedCount += outcome.added.length;
      }

      await refresh();

      setUploadNotice(
        `${addedCount} added${
          duplicateCount
            ? ` · ${duplicateCount} already in your library`
            : ''
        }`,
      );
    } catch (e) {
      setError((e as Error).message);
      await refresh();
    } finally {
      setBusy(false);
      if (picker.current) picker.current.value = '';
    }
  }

  async function remove(shot: Shot) {
    if (!window.confirm(`Delete “${shot.filename}”?`)) return;

    setBusy(true);
    setError('');

    try {
      await api<void>(`/screenshots/${shot.id}`, {
        method: 'DELETE',
      });

      setSelected(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function retry(shot: Shot) {
    setBusy(true);
    setError('');

    try {
      await api<Shot>(`/screenshots/${shot.id}/retry`, {
        method: 'POST',
      });
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function openShot(shot: Shot) {
    setSelected(shot);
    setEditing(false);
    setEditError('');

    setDraft({
      description: shot.vision_data?.description ?? '',
      category: shot.vision_data?.category ?? '',
      platform: shot.vision_data?.platform ?? '',
      tags: shot.vision_data?.tags.join(', ') ?? '',
      details: shot.vision_data?.notable_details.join('\n') ?? '',
    });
  }

  async function saveVision() {
    if (!selected) return;

    setBusy(true);
    setEditError('');

    try {
      await api<Shot>(`/screenshots/${selected.id}/vision`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          description: draft.description.trim(),
          category: draft.category.trim(),
          platform: draft.platform.trim(),
          tags: draft.tags
            .split(',')
            .map((tag) => tag.trim())
            .filter(Boolean),
          notable_details: draft.details
            .split('\n')
            .map((detail) => detail.trim())
            .filter(Boolean),
        }),
      });

      setEditing(false);
      await refresh();
    } catch (e) {
      setEditError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    await api('/auth/logout', { method: 'POST' });

    setEmail(null);
    setShots([]);
    setResults([]);
    setQuery('');
    setCategoryFilter('');
    setTagFilter('');
    setDateFilter('all');
    setSortOrder('newest');
    setSelected(null);
    setError('');
    setUploadNotice('');
  }

  function handleDrop(e: DragEvent<HTMLElement>) {
    e.preventDefault();
    setDragging(false);

    if (email && !busy) {
      void upload(e.dataTransfer.files);
    }
  }

  function handleFiles(e: ChangeEvent<HTMLInputElement>) {
    if (e.target.files) {
      void upload(e.target.files);
    }
  }

  const categories = useMemo(
    () =>
      [
        ...new Map(
          shots.flatMap((shot) => {
            const value = shot.vision_data?.category?.trim();
            return value
              ? [[value.toLocaleLowerCase(), value] as const]
              : [];
          }),
        ).values(),
      ].sort((a, b) => a.localeCompare(b)),
    [shots],
  );

  const tags = useMemo(
    () =>
      [
        ...new Map(
          shots.flatMap((shot) =>
            (shot.vision_data?.tags ?? [])
              .map((tag) => tag.trim())
              .filter(Boolean)
              .map((tag) => [tag.toLocaleLowerCase(), tag] as const),
          ),
        ).values(),
      ].sort((a, b) => a.localeCompare(b)),
    [shots],
  );

  useEffect(() => {
    if (
      categoryFilter &&
      !categories.some(
        (category) => category.toLocaleLowerCase() === categoryFilter,
      )
    ) {
      setCategoryFilter('');
    }

    if (
      tagFilter &&
      !tags.some((tag) => tag.toLocaleLowerCase() === tagFilter)
    ) {
      setTagFilter('');
    }
  }, [categories, tags, categoryFilter, tagFilter]);

  const hasFilters = Boolean(
    query.trim() ||
      categoryFilter ||
      tagFilter ||
      dateFilter !== 'all',
  );

  const isSearching = Boolean(query.trim());

  const visible = (isSearching ? results : shots)
    .filter(
      (shot) =>
        (!categoryFilter ||
          shot.vision_data?.category?.trim().toLocaleLowerCase() ===
            categoryFilter) &&
        (!tagFilter ||
          shot.vision_data?.tags?.some(
            (tag) => tag.trim().toLocaleLowerCase() === tagFilter,
          )) &&
        matchesDateFilter(shot.created_at, dateFilter),
    )
    .sort((a, b) => {
      const difference =
        new Date(a.created_at).getTime() - new Date(b.created_at).getTime();

      return sortOrder === 'oldest' ? difference : -difference;
    });

  const readyCount = shots.filter(
    (shot) => shot.embedding_status === 'ready',
  ).length;

  const processingCount = shots.filter(
    (shot) =>
      shot.status === 'queued' ||
      shot.status === 'processing' ||
      shot.vision_status === 'queued' ||
      shot.vision_status === 'processing' ||
      shot.embedding_status === 'queued' ||
      shot.embedding_status === 'processing',
  ).length;

  if (loading) {
    return (
      <main className="loading-screen">
        <div className="loading-mark">✳</div>
        <p>Opening SnapMind…</p>
      </main>
    );
  }

  if (!email) {
    return (
      <main className="auth-shell">
        <section className="auth-intro">
          <div className="brand">
            <span className="brand-mark">✳</span>
            <span>snapmind</span>
          </div>

          <div className="intro-body">
            <p className="eyebrow">YOUR VISUAL MEMORY</p>
            <h1>
              Saved it.
              <br />
              <em>Found it.</em>
            </h1>
            <p>
              Turn your screenshot pile into a searchable visual memory.
            </p>

            <div className="feature-chips" aria-label="SnapMind features">
              <span>Natural-language search</span>
              <span>OCR</span>
              <span>Visual understanding</span>
            </div>
          </div>

          <p className="intro-footer">
            Your screenshots stay organized around what they contain.
          </p>
        </section>

        <section className="auth-panel">
          <div className="auth-card">
            <div className="tiny-mark">✳</div>
            <p className="eyebrow">WELCOME TO SNAPMIND</p>
            <h2>
              {mode === 'register'
                ? 'Build your memory'
                : 'Welcome back'}
            </h2>
            <p className="muted">
              {mode === 'register'
                ? 'Create an account and start making screenshots useful again.'
                : 'Your visual library is waiting.'}
            </p>

            <form action={authenticate}>
              <label>
                Email address
                <input
                  name="email"
                  type="email"
                  autoComplete="email"
                  placeholder="you@example.com"
                  required
                />
              </label>

              <label>
                Password
                <input
                  name="password"
                  type="password"
                  minLength={8}
                  maxLength={128}
                  autoComplete={
                    mode === 'register'
                      ? 'new-password'
                      : 'current-password'
                  }
                  placeholder="At least 8 characters"
                  required
                />
              </label>

              {error && (
                <p className="error" role="alert">
                  {error}
                </p>
              )}

              <button className="primary full-width" disabled={busy}>
                <span>
                  {busy
                    ? 'One moment…'
                    : mode === 'register'
                      ? 'Create account'
                      : 'Sign in'}
                </span>
                <span>↗</span>
              </button>
            </form>

            <p className="switch">
              {mode === 'register'
                ? 'Already have an account?'
                : 'New to SnapMind?'}{' '}
              <button
                onClick={() => {
                  setMode(mode === 'register' ? 'login' : 'register');
                  setError('');
                }}
              >
                {mode === 'register' ? 'Sign in' : 'Create account'}
              </button>
            </p>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main
      className="app-shell"
      onDragEnter={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) {
          setDragging(false);
        }
      }}
      onDrop={handleDrop}
    >
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">✳</span>
          <span>snapmind</span>
        </div>

        <div className="top-actions">
          <span className="account">{email}</span>
          <button className="ghost-button" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>

      <div className="content">
        <section className="hero">
          <div className="hero-copy">
            <div className="hero-badge">
              <span className="pulse-dot" />
              Visual memory, indexed locally
            </div>

            <h1>
              Find the screenshot
              <br />
              <em>you vaguely remember.</em>
            </h1>

            <p>
              SnapMind reads text, understands what is visible, and lets
              you search your screenshot library in natural language.
            </p>

            <div className="hero-stats">
              <div>
                <strong>{shots.length}</strong>
                <span>screenshots</span>
              </div>
              <div>
                <strong>{readyCount}</strong>
                <span>searchable</span>
              </div>
              <div>
                <strong>{categories.length}</strong>
                <span>categories</span>
              </div>
            </div>
          </div>

          <div className="hero-orbit" aria-hidden="true">
            <div className="orbit-card orbit-card-one">⌕</div>
            <div className="orbit-card orbit-card-two">▧</div>
            <div className="orbit-core">✳</div>
          </div>
        </section>

        <section className="search-panel">
          <div className="search-box">
            <span className="search-icon" aria-hidden="true">
              ⌕
            </span>

            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setResults([]);
              }}
              placeholder="Try “headphones I wanted” or “airport booking”…"
              aria-label="Search screenshots"
              maxLength={120}
            />

            {searching && <span className="mini-loader" aria-hidden="true" />}

            {query && (
              <button
                className="clear-search"
                onClick={() => setQuery('')}
                aria-label="Clear search"
              >
                ✕
              </button>
            )}
          </div>

          <div className="filters-row">
            <label className="filter-control">
              <span>Category</span>
              <select
                aria-label="Filter by category"
                value={categoryFilter}
                onChange={(e) => setCategoryFilter(e.target.value)}
              >
                <option value="">All categories</option>
                {categories.map((category) => (
                  <option
                    key={category.toLocaleLowerCase()}
                    value={category.toLocaleLowerCase()}
                  >
                    {category}
                  </option>
                ))}
              </select>
            </label>

            <label className="filter-control">
              <span>Tag</span>
              <select
                aria-label="Filter by tag"
                value={tagFilter}
                onChange={(e) => setTagFilter(e.target.value)}
              >
                <option value="">All tags</option>
                {tags.map((tag) => (
                  <option
                    key={tag.toLocaleLowerCase()}
                    value={tag.toLocaleLowerCase()}
                  >
                    {tag}
                  </option>
                ))}
              </select>
            </label>

            <label className="filter-control">
              <span>Date added</span>
              <select
                aria-label="Filter by date added"
                value={dateFilter}
                onChange={(e) =>
                  setDateFilter(e.target.value as DateFilter)
                }
              >
                <option value="all">Any time</option>
                <option value="today">Today</option>
                <option value="7d">Last 7 days</option>
                <option value="30d">Last 30 days</option>
              </select>
            </label>

            <label className="filter-control">
              <span>Sort by</span>
              <select
                aria-label="Sort screenshots"
                value={sortOrder}
                onChange={(e) =>
                  setSortOrder(e.target.value as SortOrder)
                }
              >
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
              </select>
            </label>

            {hasFilters && (
              <button
                type="button"
                className="clear-filters"
                onClick={() => {
                  setQuery('');
                  setResults([]);
                  setCategoryFilter('');
                  setTagFilter('');
                  setDateFilter('all');
                }}
              >
                Clear filters
              </button>
            )}
          </div>
        </section>

        {searchError && (
          <p className="error" role="alert">
            Search failed: {searchError}
          </p>
        )}

        {uploadNotice && (
          <div className="notice" role="status">
            <span>✓</span>
            {uploadNotice}
          </div>
        )}

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}

        <section className="collection-section">
          <div className="collection-head">
            <div>
              <p className="eyebrow">
                {hasFilters ? 'FILTERED SCREENSHOTS' : 'YOUR LIBRARY'}
              </p>

              <div className="collection-title-row">
                <h2>
                  {hasFilters
                    ? searching
                      ? 'Searching…'
                      : `${visible.length} result${
                          visible.length === 1 ? '' : 's'
                        }`
                    : 'All screenshots'}
                </h2>

                {!hasFilters && (
                  <span className="count-pill">{shots.length}</span>
                )}

                {processingCount > 0 && (
                  <span className="processing-pill">
                    <span className="mini-loader" />
                    {processingCount} processing
                  </span>
                )}
              </div>
            </div>

            <button
              className="primary upload-button"
              onClick={() => picker.current?.click()}
              disabled={busy}
            >
              <span>＋</span>
              {busy ? 'Uploading…' : 'Add screenshots'}
            </button>

            <input
              ref={picker}
              hidden
              type="file"
              multiple
              accept="image/png,image/jpeg,image/webp"
              onChange={handleFiles}
            />
          </div>

          {shots.length === 0 ? (
            <button
              type="button"
              className="empty-state"
              onClick={() => picker.current?.click()}
            >
              <span className="empty-icon">▧</span>
              <h3>Your screenshot memory starts here</h3>
              <p>
                Drop screenshots anywhere on this page or choose files
                from your device.
              </p>
              <span className="empty-cta">Add your first screenshots →</span>
            </button>
          ) : hasFilters &&
            !searching &&
            !searchError &&
            visible.length === 0 ? (
            <div className="empty-state static-empty">
              <span className="empty-icon">⌕</span>
              <h3>No matches yet</h3>
              <p>
                Try a broader search, another filter, or a different date
                range.
              </p>
              <button
                className="ghost-button"
                onClick={() => {
                  setQuery('');
                  setResults([]);
                  setCategoryFilter('');
                  setTagFilter('');
                  setDateFilter('all');
                }}
              >
                Reset filters
              </button>
            </div>
          ) : (
            <div className="gallery">
              {visible.map((shot, index) => {
                const tone = processingTone(shot);

                return (
                  <button
                    className="shot-card"
                    key={shot.id}
                    onClick={() => openShot(shot)}
                    aria-label={`View ${shot.filename}`}
                    style={
                      {
                        '--delay': `${Math.min(index, 10) * 45}ms`,
                      } as CSSProperties
                    }
                  >
                    <div className="shot-image">
                      <img
                        src={shot.image_url}
                        alt={shot.filename}
                        loading="lazy"
                      />

                      <div className="image-scrim" />

                      <span className={`status-badge ${tone}`}>
                        {tone === 'ready' && '✓ '}
                        {processingLabel(shot)}
                      </span>
                    </div>

                    <div className="shot-card-body">
                      <div className="shot-title-row">
                        <span title={shot.filename}>{shot.filename}</span>
                        <small>
                          {new Date(shot.created_at).toLocaleDateString(
                            undefined,
                            {
                              month: 'short',
                              day: 'numeric',
                            },
                          )}
                        </small>
                      </div>

                      {shot.vision_data?.description && (
                        <p className="shot-description">
                          {shot.vision_data.description}
                        </p>
                      )}

                      <div className="shot-meta-row">
                        {shot.vision_data?.category && (
                          <span className="meta-chip">
                            {shot.vision_data.category}
                          </span>
                        )}

                        {shot.vision_data?.platform && (
                          <span className="meta-chip muted-chip">
                            {shot.vision_data.platform}
                          </span>
                        )}
                      </div>

                      {isSearching && shot.match_reason && (
                        <div className="match-reason">
                          <span>↳</span>
                          {shot.match_reason}
                        </div>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        <footer>
          <span>✳</span>
          SnapMind · Made to remember
        </footer>
      </div>

      {dragging && (
        <div className="drop-overlay" aria-hidden="true">
          <div className="drop-card">
            <span>＋</span>
            <strong>Drop screenshots to add them</strong>
            <small>PNG, JPEG or WebP · up to 10 MB each</small>
          </div>
        </div>
      )}

      {selected && (
        <div
          className="modal-backdrop"
          onMouseDown={() => setSelected(null)}
        >
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label={selected.filename}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="modal-head">
              <div className="modal-title">
                <strong>{selected.filename}</strong>
                <small>
                  {new Date(selected.created_at).toLocaleString()}
                </small>
              </div>

              <div className="modal-actions">
                {(selected.status === 'failed' ||
                  selected.vision_status === 'failed' ||
                  selected.embedding_status === 'failed') && (
                  <button
                    onClick={() => retry(selected)}
                    disabled={busy}
                    className="ghost-button"
                  >
                    Retry analysis
                  </button>
                )}

                <button
                  onClick={() => remove(selected)}
                  disabled={busy}
                  className="delete-button"
                >
                  Delete
                </button>

                <button
                  className="close-button"
                  onClick={() => setSelected(null)}
                  aria-label="Close"
                >
                  ✕
                </button>
              </div>
            </div>

            <div className="modal-body">
              <div className="modal-image-wrap">
                <img src={selected.image_url} alt={selected.filename} />
              </div>

              <section className="info-panel">
                <div className="panel-section">
                  <div className="panel-heading">
                    <p className="eyebrow">VISUAL SUMMARY</p>

                    {!editing &&
                      (selected.vision_status === 'ready' ||
                        selected.vision_status === 'failed') &&
                      selected.embedding_status !== 'processing' && (
                        <button
                          className="text-button"
                          onClick={() => {
                            setEditing(true);
                            setEditError('');
                          }}
                        >
                          Edit
                        </button>
                      )}
                  </div>

                  {editing ? (
                    <form
                      className="edit-form"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void saveVision();
                      }}
                    >
                      <label>
                        Description
                        <textarea
                          required
                          maxLength={1000}
                          value={draft.description}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              description: e.target.value,
                            })
                          }
                        />
                      </label>

                      <div className="edit-grid">
                        <label>
                          Category
                          <input
                            maxLength={80}
                            value={draft.category}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                category: e.target.value,
                              })
                            }
                          />
                        </label>

                        <label>
                          Website or app
                          <input
                            maxLength={80}
                            value={draft.platform}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                platform: e.target.value,
                              })
                            }
                          />
                        </label>
                      </div>

                      <label>
                        Tags
                        <textarea
                          value={draft.tags}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              tags: e.target.value,
                            })
                          }
                          placeholder="shopping, headphones, sony"
                        />
                      </label>

                      <label>
                        Notable details
                        <textarea
                          value={draft.details}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              details: e.target.value,
                            })
                          }
                          placeholder="One detail per line"
                        />
                      </label>

                      {editError && (
                        <p className="error" role="alert">
                          {editError}
                        </p>
                      )}

                      <div className="edit-actions">
                        <button
                          type="submit"
                          disabled={busy}
                          className="primary"
                        >
                          {busy ? 'Saving…' : 'Save changes'}
                        </button>

                        <button
                          type="button"
                          className="ghost-button"
                          onClick={() => setEditing(false)}
                        >
                          Cancel
                        </button>
                      </div>
                    </form>
                  ) : selected.vision_data ? (
                    <div className="summary-content">
                      <p className="summary-description">
                        {selected.vision_data.description}
                      </p>

                      <dl className="summary-list">
                        {selected.vision_data.category && (
                          <div>
                            <dt>Category</dt>
                            <dd>{selected.vision_data.category}</dd>
                          </div>
                        )}

                        {selected.vision_data.platform && (
                          <div>
                            <dt>Source</dt>
                            <dd>{selected.vision_data.platform}</dd>
                          </div>
                        )}
                      </dl>

                      {selected.vision_data.tags.length > 0 && (
                        <div className="tag-cloud">
                          {selected.vision_data.tags.map((tag) => (
                            <span key={tag}>{tag}</span>
                          ))}
                        </div>
                      )}

                      {selected.vision_data.notable_details.length > 0 && (
                        <ul className="detail-list">
                          {selected.vision_data.notable_details.map(
                            (detail, index) => (
                              <li key={index}>{detail}</li>
                            ),
                          )}
                        </ul>
                      )}
                    </div>
                  ) : (
                    <p className="panel-placeholder">
                      {selected.vision_status === 'failed'
                        ? `Analysis failed: ${
                            selected.vision_error || 'Unknown error'
                          }`
                        : 'Analyzing image…'}
                    </p>
                  )}
                </div>

                {selected.embedding_status === 'failed' && (
                  <p className="error">
                    Search indexing failed. Use Retry analysis above.
                  </p>
                )}

                <div className="panel-section text-section">
                  <p className="eyebrow">TEXT IN THIS SCREENSHOT</p>

                  {selected.status === 'ready' ? (
                    <pre>
                      {selected.ocr_text || 'No readable text found.'}
                    </pre>
                  ) : (
                    <p className="panel-placeholder">
                      {selected.status === 'failed'
                        ? 'Could not read this screenshot.'
                        : 'Reading text…'}
                    </p>
                  )}
                </div>
              </section>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
