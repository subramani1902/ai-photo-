alter table public.photo_finder_events
  add column if not exists current_folder text,
  add column if not exists is_current boolean not null default false,
  add column if not exists current_updated_at timestamptz;

create unique index if not exists photo_finder_events_one_current_idx
  on public.photo_finder_events (is_current)
  where is_current;

create or replace function public.photo_finder_set_current_folder(
  p_event_id uuid,
  p_folder_name text
)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, public
as $function$
declare
  v_updated integer;
begin
  if p_folder_name is null
     or length(p_folder_name) < 1
     or length(p_folder_name) > 255
     or p_folder_name in ('.', '..')
     or position('/' in p_folder_name) > 0
     or position(chr(92) in p_folder_name) > 0
     or p_folder_name ~ '[[:cntrl:]]' then
    raise exception 'Invalid current folder name';
  end if;

  perform pg_advisory_xact_lock(73401920001);

  update public.photo_finder_events
     set is_current = false,
         current_folder = null,
         current_updated_at = null
   where is_current;

  update public.photo_finder_events
     set is_current = true,
         current_folder = p_folder_name,
         current_updated_at = now()
   where id = p_event_id
     and active;

  get diagnostics v_updated = row_count;
  if v_updated <> 1 then
    raise exception 'Active event not found';
  end if;

  return true;
end;
$function$;

revoke all on function public.photo_finder_set_current_folder(uuid, text)
  from public, anon, authenticated;
grant execute on function public.photo_finder_set_current_folder(uuid, text)
  to service_role;

notify pgrst, 'reload schema';
