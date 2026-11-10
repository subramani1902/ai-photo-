create table if not exists public.photo_finder_events (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  guest_token_hash text unique,
  worker_token_hash text unique,
  active boolean not null default false,
  created_at timestamptz not null default now(),
  constraint photo_finder_events_guest_token_hash_check
    check (guest_token_hash is null or guest_token_hash ~ '^[0-9a-f]{64}$'),
  constraint photo_finder_events_worker_token_hash_check
    check (worker_token_hash is null or worker_token_hash ~ '^[0-9a-f]{64}$')
);

create table if not exists public.photo_finder_jobs (
  id uuid primary key,
  event_id uuid not null references public.photo_finder_events(id) on delete cascade,
  guest_token_hash text not null
    check (guest_token_hash ~ '^[0-9a-f]{64}$'),
  selfie_path text not null unique,
  status text not null default 'uploading'
    check (status in ('uploading', 'pending', 'processing', 'completed', 'failed')),
  error_message text,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default (now() + interval '24 hours'),
  completed_at timestamptz,
  lease_id uuid,
  lease_expires_at timestamptz,
  attempt integer not null default 0 check (attempt >= 0)
);

create index if not exists photo_finder_jobs_event_status_created_idx
  on public.photo_finder_jobs (event_id, status, created_at);

create index if not exists photo_finder_jobs_expiry_idx
  on public.photo_finder_jobs (expires_at);

create table if not exists public.photo_finder_results (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.photo_finder_jobs(id) on delete cascade,
  photo_id bigint not null check (photo_id > 0),
  object_path text not null unique,
  filename text not null,
  similarity double precision not null check (similarity >= 0.45 and similarity <= 1.001),
  uploaded boolean not null default false,
  created_at timestamptz not null default now(),
  constraint photo_finder_results_job_photo_key unique (job_id, photo_id)
);

create index if not exists photo_finder_results_job_uploaded_similarity_idx
  on public.photo_finder_results (job_id, uploaded, similarity desc);

create table if not exists public.photo_finder_maintenance (
  id smallint primary key check (id = 1),
  secret_hash text not null check (secret_hash ~ '^[0-9a-f]{64}$')
);

alter table public.photo_finder_events enable row level security;
alter table public.photo_finder_jobs enable row level security;
alter table public.photo_finder_results enable row level security;
alter table public.photo_finder_maintenance enable row level security;

revoke all on public.photo_finder_events from public, anon, authenticated;
revoke all on public.photo_finder_jobs from public, anon, authenticated;
revoke all on public.photo_finder_results from public, anon, authenticated;
revoke all on public.photo_finder_maintenance from public, anon, authenticated;
grant select, insert, update, delete on public.photo_finder_events to service_role;
grant select, insert, update, delete on public.photo_finder_jobs to service_role;
grant select, insert, update, delete on public.photo_finder_results to service_role;
grant select, insert, update, delete on public.photo_finder_maintenance to service_role;

create or replace function public.photo_finder_reserve_job(
  p_event_id uuid,
  p_job_id uuid,
  p_guest_token_hash text,
  p_selfie_path text
)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, public
as $function$
declare
  v_event_id uuid;
  v_recent_jobs bigint;
  v_active_jobs bigint;
begin
  if p_event_id is null
     or p_job_id is null
     or p_guest_token_hash is null
     or p_selfie_path is null
     or p_guest_token_hash !~ '^[0-9a-f]{64}$'
     or p_selfie_path <> 'selfies/' || p_job_id::text then
    return false;
  end if;

  select id
    into v_event_id
    from public.photo_finder_events
   where id = p_event_id
     and active
   for update;

  if not found then
    return false;
  end if;

  select count(*)
    into v_recent_jobs
    from public.photo_finder_jobs
   where event_id = v_event_id
     and created_at >= now() - interval '1 minute';

  if v_recent_jobs >= 20 then
    return false;
  end if;

  select count(*)
    into v_active_jobs
    from public.photo_finder_jobs
   where event_id = v_event_id
     and status in ('uploading', 'pending', 'processing')
     and expires_at > now();

  if v_active_jobs >= 100 then
    return false;
  end if;

  insert into public.photo_finder_jobs (
    id,
    event_id,
    guest_token_hash,
    selfie_path,
    status
  )
  values (
    p_job_id,
    v_event_id,
    p_guest_token_hash,
    p_selfie_path,
    'uploading'
  );

  return true;
end;
$function$;

revoke all on function public.photo_finder_reserve_job(uuid, uuid, text, text)
  from public, anon, authenticated;
grant execute on function public.photo_finder_reserve_job(uuid, uuid, text, text)
  to service_role;

insert into public.photo_finder_events (id, name, active)
values ('d3228167-5500-4a72-bda0-2f7249a938aa', 'Candid Test', false)
on conflict (id) do nothing;

notify pgrst, 'reload schema';
