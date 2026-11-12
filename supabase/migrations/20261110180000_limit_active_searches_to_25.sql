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

  if v_active_jobs >= 25 then
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
