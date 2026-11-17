alter table public.photo_finder_gallery_photos
  add column if not exists source_path text not null default '';

update public.photo_finder_gallery_photos
set source_path = filename
where source_path = '';

notify pgrst, 'reload schema';
