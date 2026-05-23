from django.db import migrations


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE EXTENSION IF NOT EXISTS vector;

            CREATE TABLE IF NOT EXISTS recommendation_graph (
                id BIGSERIAL PRIMARY KEY,
                client_id INTEGER NOT NULL,
                predicate VARCHAR(64) NOT NULL,
                object TEXT NOT NULL,
                weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_rec_graph_client ON recommendation_graph(client_id);
            CREATE INDEX IF NOT EXISTS idx_rec_graph_client_predicate ON recommendation_graph(client_id, predicate);

            CREATE TABLE IF NOT EXISTS client_embeddings (
                client_id INTEGER PRIMARY KEY,
                embedding vector(64),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS property_embeddings (
                property_guid UUID PRIMARY KEY,
                property_kind VARCHAR(16) NOT NULL DEFAULT 'apartment',
                embedding vector(64),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            reverse_sql="""
            DROP TABLE IF EXISTS property_embeddings;
            DROP TABLE IF EXISTS client_embeddings;
            DROP TABLE IF EXISTS recommendation_graph;
            """,
        ),
    ]
