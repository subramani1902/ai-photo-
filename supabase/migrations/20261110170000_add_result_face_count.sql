alter table public.photo_finder_results
  add column if not exists face_count integer not null default 1
  check (face_count >= 1);

notify pgrst, 'reload schema';
