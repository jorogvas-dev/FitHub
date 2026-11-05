"""Explicit database upgrades and administrator promotion; never run at request time."""
from pathlib import Path
import json
from decimal import Decimal

import click
from psycopg.types.json import Jsonb
from app.db import connect

DB_ROOT = Path(__file__).resolve().parents[1] / 'db'


def upgrade(conn):
    # Serialize operators/replicas and commit the ledger together with all migrations.
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (734104021,))
    conn.execute('CREATE TABLE IF NOT EXISTS schema_migration '
                 '(version text PRIMARY KEY,applied_at timestamptz NOT NULL DEFAULT now())')
    applied = {row[0] for row in conn.execute('SELECT version FROM schema_migration').fetchall()}
    changed = []
    for path in sorted((DB_ROOT / 'migrations').glob('*.sql')):
        if path.stem not in applied:
            conn.execute(path.read_text(encoding='utf-8'))
            conn.execute('INSERT INTO schema_migration(version) VALUES (%s)', (path.stem,))
            changed.append(path.stem)
    return changed


def seed_exercises(conn):
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (734104021,))
    conn.execute((DB_ROOT / 'migrations/002_exercise_library.sql').read_text(encoding='utf-8'))


def seed_foods(conn):
    """Insert missing permanent source IDs; never undo administrator edits/status."""
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (734104021,))
    data = json.loads((DB_ROOT / 'data/usda-sr-2018.json').read_text(encoding='utf-8'))
    for food in data['foods']:
        provenance = {key: value for key, value in data.items() if key != 'foods'}
        provenance['publication_date'] = food['publication_date']
        conn.execute('INSERT INTO food_catalogue(name,preparation,kcal_100g,protein_100g,carbs_100g,fat_100g,'
                     'source,source_id,dataset_version,source_url,provenance) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                     'ON CONFLICT(source,source_id) DO NOTHING',
                     (food['name'], food['preparation'],
                      *(None if food[key] is None else Decimal(food[key]) for key in
                        ('kcal_100g','protein_100g','carbs_100g','fat_100g')),
                      data['source'], food['source_id'], data['dataset'], food['source_url'], Jsonb(provenance)))


def register_commands(app):
    @app.cli.command('seed-foods')
    def food_seed():
        """Import the bundled USDA subset offline, preserving existing entries."""
        with connect() as conn:
            seed_foods(conn)
        click.echo('USDA food library seeded; existing IDs, edits and status preserved.')

    @app.cli.command('db-upgrade')
    def db_upgrade():
        """Apply versioned migrations to the existing database without resetting data."""
        with connect() as conn:
            changed = upgrade(conn)
        click.echo('Applied: ' + ', '.join(changed) if changed else 'Database is up to date.')

    @app.cli.command('seed-exercises')
    def seed():
        """Add missing library entries; preserve IDs, edits, custom entries and status."""
        with connect() as conn:
            seed_exercises(conn)
        click.echo('Exercise library seeded; existing entries preserved.')

    @app.cli.command('promote-admin')
    @click.argument('username')
    def promote_admin(username):
        """Explicitly promote an existing account. No account/password is created."""
        with connect() as conn:
            row = conn.execute('UPDATE app_user SET is_admin=true WHERE username=%s RETURNING username',
                               (username.strip().lower(),)).fetchone()
            if not row:
                raise click.ClickException('Existing username not found; no account was changed.')
        click.echo(f'Administrator enabled for {row[0]}.')
