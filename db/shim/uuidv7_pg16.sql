-- Test-only stand-in for PostgreSQL 18 pg_catalog.uuidv7() on PG16 (time-ordered v7 layout).
CREATE OR REPLACE FUNCTION ext.uuidv7() RETURNS uuid LANGUAGE sql VOLATILE AS $$
SELECT encode(set_bit(set_bit(overlay(uuid_send(gen_random_uuid())
       PLACING substring(int8send((extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3) FROM 1 FOR 6),
       52, 1), 53, 1), 'hex')::uuid
$$;
