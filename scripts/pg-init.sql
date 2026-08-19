-- Runs once at cluster init (docker-entrypoint-initdb.d), as the
-- bootstrap superuser. Creates the non-superuser role the app and its
-- migrations run as — superusers bypass RLS, so nothing in the
-- application may ever connect as one. Render's managed Postgres hands
-- us exactly this shape: an owner role without SUPERUSER.
CREATE ROLE becca_app LOGIN PASSWORD 'becca';
CREATE DATABASE becca OWNER becca_app;
-- Tests get their own database: the suite deletes every row before each
-- test, and must never do that to the dev data.
CREATE DATABASE becca_test OWNER becca_app;
