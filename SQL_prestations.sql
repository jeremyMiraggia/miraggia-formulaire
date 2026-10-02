-- ════════════════════════════════════════════════════════════════════
-- MIRAGGIA — Table "prestations"
-- À coller dans Supabase → SQL Editor → Run (une seule fois)
-- (même projet que la table "briefs")
-- ════════════════════════════════════════════════════════════════════

create table prestations (
  id              bigserial primary key,
  created_at      timestamptz default now(),
  brief_id        bigint references briefs(id),  -- lien vers le brief/la marque
  brand_name      text,
  contact_name    text,
  contact_email   text,
  members         jsonb default '[]',            -- équipe projet : [{name, email, role}]
  casting_type    text,                          -- 'miraggia' | 'custom'
  mannequins      text[],                         -- mannequins catalogue choisis (plusieurs possibles)
  mannequin_inspi text[],                         -- inspirations casting
  fond_type       text,                          -- 'miraggia' | 'propre' | 'inspiration'
  fonds           text[],                         -- fonds catalogue sélectionnés
  fond_link       text,                          -- lien Drive/WeTransfer du fond propre
  fond_inspi      text[],                         -- liens/références d'inspiration
  fond_desc       text,                          -- ambiance souhaitée
  poses           text[],                         -- poses sélectionnées
  format          text,                          -- JPG | PNG
  resolution      text,                          -- 1K | 2K | 4K
  ratio           text,                          -- 1:1, 2:3, 3:4, 9:16 ou personnalisé
  nb_visuels      int,
  precisions      text,
  deadline        date,
  launch_date     date,
  planning_notes  text
);

-- Sécurité (Row Level Security) — même logique que la table briefs :
alter table prestations enable row level security;

-- Le formulaire client peut insérer une fiche
create policy "insert_public" on prestations
  for insert to anon with check (true);

-- Le dashboard peut lire les fiches
create policy "read_anon" on prestations
  for select to anon using (true);

-- Le dashboard peut supprimer une fiche
create policy "delete_anon" on prestations
  for delete to anon using (true);
