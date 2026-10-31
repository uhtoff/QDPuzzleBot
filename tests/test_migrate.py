"""migrate.py's pure halves: which files it finds, in what order, and how it splits
one into statements. CI applies the real migrations through `python migrate.py`,
so this pins the parts a green run wouldn't single out."""
import migrate


def test_discovers_sql_files_in_filename_order_and_nothing_else(tmp_path):
    for name in ("010_b.sql", "002_a.sql", "100_c.sql", "notes.md"):
        (tmp_path / name).write_text("SELECT 1;")
    (tmp_path / "005_dir.sql").mkdir()
    found = [p.name for p in migrate.discover_migrations(tmp_path)]
    assert found == ["002_a.sql", "010_b.sql", "100_c.sql"]


def test_the_real_migrations_start_at_the_base_schema():
    found = [p.name for p in migrate.discover_migrations()]
    assert found[0] == "000_base_schema.sql"
    assert found == sorted(found)


def test_splits_on_semicolons_and_drops_full_line_comments():
    sql = """-- a header comment
CREATE TABLE t (
  id INT  -- not stripped: inline comments stay
);

  -- an indented comment line
ALTER TABLE t ADD COLUMN x INT;
;
"""
    assert migrate.split_statements(sql) == [
        "CREATE TABLE t (\n  id INT  -- not stripped: inline comments stay\n)",
        "ALTER TABLE t ADD COLUMN x INT",
    ]


def test_a_comment_only_file_has_no_statements():
    assert migrate.split_statements("-- nothing here\n\n-- still nothing\n") == []
