from src.sql_statements import split_sql_statements


def test_function_bodies_strings_and_comments_are_not_split():
    sql = '''-- semicolon ; in a comment
CREATE FUNCTION example() RETURNS trigger LANGUAGE plpgsql AS $body$
BEGIN
    NEW.email := 'semi;colon';
    RETURN NEW;
END;
$body$;
/* another ; */ SELECT 'it''s;a string', "semi;colon";
SELECT $$untagged;body$$;
'''
    statements = split_sql_statements(sql)
    assert len(statements) == 3
    assert "RETURN NEW;" in statements[0]
    assert "'it''s;a string'" in statements[1]
    assert statements[2] == 'SELECT $$untagged;body$$'
