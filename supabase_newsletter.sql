-- Hardwood Ledger newsletter list. Run once in Supabase (SQL Editor -> New query -> paste -> Run).
-- Safe to run more than once.
--
-- Manage the list in Table Editor -> newsletter_subscribers:
--   add a person    -> Insert row, fill in name + email (active is on by default)
--   pause a person  -> set active to false (keeps them on the list, stops the emails)
--   remove a person -> delete the row
--
-- Privacy: row level security is ON with no policies, so the public key in site/config.js can't read or
-- change this table at all. Only the secret (service) key -- kept as a GitHub secret -- can read it.

create table if not exists public.newsletter_subscribers (
  id bigint generated always as identity primary key,
  name text,
  email text not null,
  active boolean not null default true,
  note text,
  added_at timestamptz not null default now()
);

create unique index if not exists newsletter_subscribers_email_key
  on public.newsletter_subscribers (lower(email));

alter table public.newsletter_subscribers enable row level security;
revoke all on public.newsletter_subscribers from anon, authenticated;

-- Example (edit, then run, or just use Table Editor):
-- insert into public.newsletter_subscribers (name, email) values ('Me', 'you@gmail.com');
