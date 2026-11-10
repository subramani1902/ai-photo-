create table if not exists public.photo_finder_showcases (
  event_id uuid not null references public.photo_finder_events(id) on delete cascade,
  photo_id bigint not null check (photo_id > 0),
  object_path text not null unique,
  filename text not null,
  updated_at timestamptz not null default now(),
  primary key (event_id, photo_id)
);

alter table public.photo_finder_showcases enable row level security;
revoke all on public.photo_finder_showcases from public, anon, authenticated;
grant select, insert, update, delete on public.photo_finder_showcases to service_role;

notify pgrst, 'reload schema';
