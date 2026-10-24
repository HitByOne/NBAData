-- NBA Ledger: run once in Supabase (SQL Editor -> New query -> paste -> Run), in the SAME project the
-- Gridiron Ledger uses. It needs supabase_accounts.sql to have been run there already (it reuses its
-- accounts, friend groups and the my_group_ids / shares_group_with helpers).
--
-- Why separate tables: the NBA page keeps its own picks, tickets and Best Picks record. Sharing the NFL
-- tables would mix NBA tickets into the NFL page and the other way round. Your sign-in and your friend
-- groups are the same on both pages.
--
-- Safe to run more than once.

create table if not exists public.nba_user_state (
  user_id uuid primary key references auth.users(id) on delete cascade,
  data jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table if not exists public.nba_shared_tickets (
  user_id uuid not null references auth.users(id) on delete cascade,
  ticket text not null,
  data jsonb not null,
  updated_at timestamptz not null default now(),
  primary key (user_id, ticket)
);

alter table public.nba_user_state enable row level security;
alter table public.nba_shared_tickets enable row level security;

drop policy if exists nba_user_state_own on public.nba_user_state;
create policy nba_user_state_own on public.nba_user_state for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists nba_shared_read on public.nba_shared_tickets;
create policy nba_shared_read on public.nba_shared_tickets for select to authenticated
  using (user_id = auth.uid() or public.shares_group_with(user_id));
drop policy if exists nba_shared_write on public.nba_shared_tickets;
create policy nba_shared_write on public.nba_shared_tickets for insert to authenticated
  with check (user_id = auth.uid());
drop policy if exists nba_shared_update on public.nba_shared_tickets;
create policy nba_shared_update on public.nba_shared_tickets for update to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists nba_shared_delete on public.nba_shared_tickets;
create policy nba_shared_delete on public.nba_shared_tickets for delete to authenticated
  using (user_id = auth.uid());

grant select, insert, update, delete on public.nba_user_state to authenticated;
grant select, insert, update, delete on public.nba_shared_tickets to authenticated;
