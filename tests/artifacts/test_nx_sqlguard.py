"""The SQL guard must let read-only analytics through and reject everything else, including the usual bypass tricks."""
import pytest

from backend.artifacts.blocks import nx_sqlguard as g

ALLOW = {"bookings": ["booking_id", "hotel_city", "check_in_date", "nights", "total_amount_eur", "status", "notes"],
         "hotels": ["hotel_id", "city", "name", "stars"]}


def ok(sql, **kw):
    return g.validate(sql, ALLOW, **kw)


def bad(sql, **kw):
    with pytest.raises(g.SqlRejected) as e:
        g.validate(sql, ALLOW, **kw)
    assert len(str(e.value)) < 200 and "\n" not in str(e.value)
    return str(e.value)


GOOD = [
    "SELECT hotel_city, COUNT(*) AS n FROM bookings WHERE status = 'confirmed' AND check_in_date >= '2026-09-01' AND check_in_date < '2026-10-01' GROUP BY hotel_city ORDER BY n DESC LIMIT 3",
    "select booking_id, total_amount_eur from bookings where total_amount_eur between 100 and 500 order by total_amount_eur desc limit 10;",
    "SELECT b.booking_id, h.name FROM bookings b JOIN hotels h ON h.city = b.hotel_city WHERE h.stars >= 4",
    "SELECT status, SUM(total_amount_eur) AS revenue, AVG(nights) FROM bookings GROUP BY status HAVING SUM(total_amount_eur) > 1000",
    "SELECT booking_id FROM bookings WHERE hotel_city IN ('Berlin', 'Munich') AND notes IS NOT NULL",
    "WITH c AS (SELECT hotel_city, COUNT(*) AS n FROM bookings GROUP BY hotel_city) SELECT hotel_city, n FROM c ORDER BY n DESC",
    "SELECT hotel_city FROM bookings WHERE booking_id IN (SELECT booking_id FROM bookings WHERE nights > 2)",
    "SELECT DISTINCT status FROM bookings",
    "SELECT COALESCE(notes, 'none') AS n, LOWER(status) FROM bookings WHERE status LIKE 'conf%'",
    "SELECT CASE WHEN nights > 2 THEN 'long' ELSE 'short' END AS kind, COUNT(*) FROM bookings GROUP BY kind",
    "SELECT hotel_city FROM bookings UNION SELECT city FROM hotels",
    "SELECT 'it''s fine' AS s, booking_id FROM bookings",
    "SELECT booking_id FROM bookings WHERE \"status\" = 'confirmed'",
]


@pytest.mark.parametrize("sql", GOOD)
def test_valid_read_only_queries_pass(sql):
    out = ok(sql)
    assert out.sql.upper().startswith(("SELECT", "WITH")) and " LIMIT " in out.sql.upper() and ";" not in out.sql


WRITES_AND_DDL = ["INSERT INTO bookings VALUES (1)", "UPDATE bookings SET status='x'", "DELETE FROM bookings", "DROP TABLE bookings", "ALTER TABLE bookings ADD x int",
                  "CREATE TABLE t (a int)", "TRUNCATE bookings", "GRANT ALL ON bookings TO x", "REPLACE INTO bookings VALUES (1)", "MERGE INTO bookings USING x ON 1=1",
                  "CALL purge()", "EXEC sp_x", "COPY bookings TO '/tmp/x'", "SET search_path = x", "PRAGMA writable_schema=1", "ATTACH DATABASE 'x' AS y", "VACUUM",
                  "SHOW TABLES", "USE other", "LOAD DATA INFILE 'x' INTO TABLE bookings", "SELECT 1 INTO OUTFILE '/tmp/x'", "SELECT booking_id INTO newtable FROM bookings"]


@pytest.mark.parametrize("sql", WRITES_AND_DDL)
def test_writes_ddl_and_admin_statements_are_rejected(sql):
    bad(sql)


SMUGGLING = [
    "SELECT booking_id FROM bookings; DROP TABLE bookings", "SELECT booking_id FROM bookings;; SELECT 1", "SELECT booking_id FROM bookings; SELECT booking_id FROM bookings",
    "SELECT booking_id FROM bookings -- ; DROP TABLE x", "SELECT booking_id /* hidden */ FROM bookings", "SELECT booking_id FROM bookings # mysql comment",
    "SELECT booking_id FROM bookings WHERE status = 'a'; --'", "SELECT booking_id FROM bookings WHERE status = 'unterminated", "SELECT booking_id FROM bookings WHERE status = \"x",
    "SELECT booking_id FROM bookings WHERE status = 'a\\' OR 1=1 --'", "SELECT booking_id FROM bookings\x00; DROP TABLE x", "SELECT booking_id FROM bookings DROP TABLE x",
    "SELECT booking_id FROM bookings WHERE status = ${x}", "SELECT * FROM bookings /*! DROP TABLE x */", "SELECT booking_id FROM bookings FOR UPDATE", "SELECT booking_id FROM bookings FOR SHARE",
    "SELECT booking_id FROM bookings LOCK IN SHARE MODE", "", "   ", ";", "SELECT", "select from", "SELECT booking_id FROM", "(SELECT booking_id FROM bookings",
]


@pytest.mark.parametrize("sql", SMUGGLING)
def test_smuggling_multi_statement_comments_and_malformed_input_are_rejected(sql):
    bad(sql)


DANGEROUS_FUNCTIONS = ["SELECT pg_sleep(10) FROM bookings", "SELECT SLEEP(5) FROM bookings", "SELECT BENCHMARK(1000000, MD5('x')) FROM bookings", "SELECT LOAD_FILE('/etc/passwd') FROM bookings",
                       "SELECT pg_read_file('/etc/passwd') FROM bookings", "SELECT lo_import('/etc/passwd') FROM bookings", "SELECT xp_cmdshell('dir') FROM bookings",
                       "SELECT * FROM dblink('x','select 1') AS t(a int)", "SELECT set_config('a','b',false) FROM bookings", "SELECT nextval('s') FROM bookings",
                       "SELECT readfile('/etc/passwd') FROM bookings", "SELECT load_extension('x') FROM bookings", "SELECT booking_id FROM bookings WHERE randomblob(1000000000) IS NULL",
                       "SELECT system('ls') FROM bookings", "SELECT version() FROM bookings", "SELECT current_user FROM bookings"]


@pytest.mark.parametrize("sql", DANGEROUS_FUNCTIONS)
def test_dangerous_and_unknown_functions_are_rejected(sql):
    bad(sql)


def test_tables_must_be_on_the_allowlist_everywhere():
    for sql in ["SELECT passport_number FROM traveler_passports", "SELECT booking_id FROM bookings JOIN traveler_passports p ON 1=1",
                "SELECT booking_id FROM bookings WHERE booking_id IN (SELECT booking_id FROM secrets)", "SELECT 1 FROM bookings, passwords",
                "WITH x AS (SELECT * FROM secrets) SELECT booking_id FROM bookings", "SELECT booking_id FROM bookings UNION SELECT a FROM secrets",
                "SELECT booking_id FROM other_schema.bookings", "SELECT booking_id FROM information_schema.tables", "SELECT name FROM sqlite_master", "SELECT booking_id FROM pg_catalog.pg_tables",
                "SELECT booking_id FROM (SELECT booking_id FROM secrets) s", "SELECT booking_id FROM bookings b JOIN (SELECT 1 AS x FROM passwords) p ON 1=1", "SELECT x FROM generate_series(1,10) x"]:
        bad(sql)


def test_columns_must_be_on_the_allowlist_and_star_is_refused():
    for sql in ["SELECT traveler_email FROM bookings", "SELECT booking_id FROM bookings WHERE traveler_email = 'a'", "SELECT * FROM bookings", "SELECT b.* FROM bookings b",
                "SELECT COUNT(*), traveler_email FROM bookings GROUP BY traveler_email", "SELECT booking_id FROM bookings ORDER BY traveler_email", "SELECT booking_id FROM bookings b WHERE b.traveler_email IS NULL",
                "SELECT h.secret_col FROM hotels h", "SELECT booking_id FROM bookings WHERE hotel_city IN (SELECT traveler_email FROM bookings)", "SELECT status FROM hotels"]:
        bad(sql)
    ok("SELECT COUNT(*) FROM bookings")                                              # COUNT(*) reveals no column values
    ok("SELECT b.status, COUNT(1) AS c FROM bookings b GROUP BY b.status ORDER BY c DESC")


def test_a_table_may_opt_in_to_star():
    assert g.validate("SELECT * FROM hotels", {"hotels": ["hotel_id", "city"]}, star_tables={"hotels"}).sql
    with pytest.raises(g.SqlRejected):
        g.validate("SELECT * FROM bookings", ALLOW, star_tables={"hotels"})


def test_limit_is_added_clamped_and_cannot_be_dodged():
    assert ok("SELECT booking_id FROM bookings").sql.endswith("LIMIT 1000")
    assert ok("SELECT booking_id FROM bookings", max_limit=50).sql.endswith("LIMIT 50")
    assert ok("SELECT booking_id FROM bookings LIMIT 5").sql.endswith("LIMIT 5")
    assert ok("SELECT booking_id FROM bookings LIMIT 999999").sql.endswith("LIMIT 1000")
    assert ok("SELECT booking_id FROM bookings LIMIT 10 OFFSET 20").sql.endswith("LIMIT 10 OFFSET 20")
    assert ok("SELECT booking_id FROM bookings LIMIT 20, 10").sql.endswith("LIMIT 10 OFFSET 20")
    for sql in ["SELECT booking_id FROM bookings LIMIT -1", "SELECT booking_id FROM bookings LIMIT ALL", "SELECT booking_id FROM bookings LIMIT (SELECT 1)", "SELECT booking_id FROM bookings LIMIT 1e9",
                "SELECT booking_id FROM bookings LIMIT 5 LIMIT 6"]:
        bad(sql)


def test_result_is_rebuilt_from_tokens_and_reports_what_it_touches():
    r = ok("select  b.booking_id ,\tCOUNT(*) as n   from bookings   b where b.status='ok' group by b.booking_id")
    assert "  " not in r.sql and "\t" not in r.sql and r.tables == {"bookings"} and "status" in r.columns and "booking_id" in r.columns


def test_length_and_complexity_caps_and_non_ascii_identifiers():
    bad("SELECT booking_id FROM bookings WHERE status IN (" + ",".join("'x'" for _ in range(20000)) + ")")
    bad("SELECT booking_id FROM bookings WHERE " + " AND ".join(["nights > 1"] * 200))
    bad("SELECT " + "(" * 60 + "1" + ")" * 60 + " FROM bookings")
    bad("SELECT booking_id FROM bookings WHERE status = 'é' AND нights > 1")
    ok("SELECT booking_id FROM bookings WHERE notes = 'café ☕'")               # non-ASCII is fine inside string literals


def test_case_and_quoting_cannot_hide_names():
    bad("SELECT booking_id FROM BOOKINGS_SECRET")
    bad("SELECT booking_id FROM `traveler_passports`")
    bad('SELECT booking_id FROM "traveler passports"')
    bad("SELECT booking_id FROM bookings WHERE `traveler_email` = 'x'")
    ok("SELECT `booking_id` FROM `bookings` WHERE \"status\" = 'x'")
    ok("SELECT BOOKING_ID FROM BOOKINGS")


def test_comparison_operators_and_arithmetic_are_allowed_but_not_assignment_or_shell_chars():
    ok("SELECT booking_id FROM bookings WHERE nights >= 2 AND nights <> 5 AND total_amount_eur / nights > 10 AND nights % 2 = 0")
    for sql in ["SELECT booking_id FROM bookings WHERE nights = 1 | 2", "SELECT @x := 1", "SELECT booking_id FROM bookings WHERE status = $1", "SELECT booking_id FROM bookings WHERE status = ?",
                "SELECT booking_id FROM bookings WHERE status = :p", "SELECT booking_id FROM bookings WHERE status = `a`b`"]:
        bad(sql)


def test_random_mutations_never_get_a_dangerous_query_through():
    """Property test: whatever the mutation, an ACCEPTED query contains no banned word, no stray ';', only allowed tables and columns."""
    import random
    import re
    rnd = random.Random(1234)
    snippets = ["; DROP TABLE bookings", " UNION SELECT passport_number FROM traveler_passports", " -- x", " /* y */", " INTO OUTFILE '/tmp/x'", " FOR UPDATE", "'", '"', "`", "\\",
                " OR 1=1", "; SELECT 1", " FROM secrets", ", traveler_email", " WHERE traveler_email = 'a'", "(SELECT 1)", " LIMIT 99999999", "\x00", " ", " @x", " ::int",
                " ; ", " pg_sleep(5)", " GRANT ALL", " EXEC x", " JOIN passwords ON 1=1", " , other_table", " AND (SELECT COUNT(*) FROM secrets) > 0"]
    banned = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|call|exec|execute|merge|replace|into|outfile|load|copy|set|pragma|attach)\b", re.I)
    accepted = 0
    for _ in range(4000):
        q = rnd.choice(GOOD)
        pos = rnd.randrange(len(q) + 1)
        q = q[:pos] + rnd.choice(snippets) + (q[pos:] if rnd.random() < 0.8 else q[pos + rnd.randrange(1, 6):])
        try:
            r = g.validate(q, ALLOW)
        except g.SqlRejected:
            continue
        accepted += 1
        body = re.sub(r"'(?:[^']|'')*'", "''", r.sql)
        assert not banned.search(body) and ";" not in body and "--" not in body and "/*" not in body, (q, r.sql)
        assert r.tables <= set(ALLOW) and re.search(r"LIMIT (\d+)( OFFSET \d+)?$", r.sql) and int(re.search(r"LIMIT (\d+)", r.sql[r.sql.upper().rfind(" LIMIT "):]).group(1)) <= 1000 and r.columns <= set().union(*map(set, ALLOW.values())), (q, r.sql)
        assert "traveler" not in body and "secrets" not in body and "passwords" not in body, (q, r.sql)
    assert accepted > 100                                                                  # the mutations are not all trivially rejected
