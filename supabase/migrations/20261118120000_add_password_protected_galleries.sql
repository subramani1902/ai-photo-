create table if not exists public.photo_finder_gallery_links (
  id uuid primary key default gen_random_uuid(),
  event_id uuid not null references public.photo_finder_events(id) on delete cascade,
  token_hash text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
  password_salt text not null check (password_salt ~ '^[0-9a-f]{64}$'),
  password_hash text not null check (password_hash ~ '^[0-9a-f]{64}$'),
  password_iterations integer not null check (password_iterations between 100000 and 1000000),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null
);

create index if not exists photo_finder_gallery_links_event_active_idx
  on public.photo_finder_gallery_links (event_id, active, expires_at);

create table if not exists public.photo_finder_gallery_sessions (
  id uuid primary key default gen_random_uuid(),
  gallery_id uuid not null references public.photo_finder_gallery_links(id) on delete cascade,
  token_hash text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null
);

create index if not exists photo_finder_gallery_sessions_expiry_idx
  on public.photo_finder_gallery_sessions (expires_at);

create table if not exists public.photo_finder_gallery_login_attempts (
  id bigint generated always as identity primary key,
  gallery_id uuid not null references public.photo_finder_gallery_links(id) on delete cascade,
  attempted_at timestamptz not null default now()
);

create index if not exists photo_finder_gallery_login_attempts_window_idx
  on public.photo_finder_gallery_login_attempts (gallery_id, attempted_at);

create table if not exists public.photo_finder_gallery_photos (
  event_id uuid not null references public.photo_finder_events(id) on delete cascade,
  photo_id bigint not null check (photo_id > 0),
  filename text not null check (length(filename) between 1 and 255),
  sync_generation uuid not null,
  updated_at timestamptz not null default now(),
  primary key (event_id, photo_id)
);

create table if not exists public.photo_finder_gallery_jobs (
  id uuid primary key default gen_random_uuid(),
  gallery_id uuid not null references public.photo_finder_gallery_links(id) on delete cascade,
  event_id uuid not null references public.photo_finder_events(id) on delete cascade,
  photo_id bigint not null check (photo_id > 0),
  filename text not null check (length(filename) between 1 and 255),
  variant text not null check (variant in ('thumbnail', 'original')),
  status text not null default 'pending'
    check (status in ('pending', 'processing', 'ready', 'failed', 'expired')),
  object_path text unique,
  error_message text,
  lease_id uuid,
  lease_expires_at timestamptz,
  attempt integer not null default 0 check (attempt >= 0),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default (now() + interval '1 hour')
);

create index if not exists photo_finder_gallery_jobs_claim_idx
  on public.photo_finder_gallery_jobs (event_id, status, created_at);

create index if not exists photo_finder_gallery_jobs_lookup_idx
  on public.photo_finder_gallery_jobs (gallery_id, photo_id, variant, status, expires_at);

create unique index if not exists photo_finder_gallery_jobs_active_asset_idx
  on public.photo_finder_gallery_jobs (gallery_id, photo_id, variant)
  where status in ('pending', 'processing', 'ready');

alter table public.photo_finder_gallery_links enable row level security;
alter table public.photo_finder_gallery_sessions enable row level security;
alter table public.photo_finder_gallery_login_attempts enable row level security;
alter table public.photo_finder_gallery_photos enable row level security;
alter table public.photo_finder_gallery_jobs enable row level security;

revoke all on public.photo_finder_gallery_links from public, anon, authenticated;
revoke all on public.photo_finder_gallery_sessions from public, anon, authenticated;
revoke all on public.photo_finder_gallery_login_attempts from public, anon, authenticated;
revoke all on public.photo_finder_gallery_photos from public, anon, authenticated;
revoke all on public.photo_finder_gallery_jobs from public, anon, authenticated;
grant select, insert, update, delete on public.photo_finder_gallery_links to service_role;
grant select, insert, update, delete on public.photo_finder_gallery_sessions to service_role;
grant select, insert, update, delete on public.photo_finder_gallery_login_attempts to service_role;
grant select, insert, update, delete on public.photo_finder_gallery_photos to service_role;
grant select, insert, update, delete on public.photo_finder_gallery_jobs to service_role;
grant usage, select on sequence public.photo_finder_gallery_login_attempts_id_seq to service_role;

notify pgrst, 'reload schema';
