"""
Minimal mysqldump replacement using pymysql.
Writes SQL directly to output file (UTF-8) to avoid Windows console encoding issues.
Usage: python backup_mysql.py <host> <port> <user> <password> <database> <output_file>
"""
import sys
import datetime
import pymysql


def escape_val(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return "0x" + v.hex()
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


def dump(host, port, user, password, database):
    conn = pymysql.connect(
        host=host, port=int(port),
        user=user, password=password,
        database=database,
        charset="utf8mb4",
        connect_timeout=10,
    )
    cur = conn.cursor()

    lines = [
        f"-- AI Quotation DB backup",
        f"-- Generated: {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"-- Database: {database}",
        "SET NAMES utf8mb4;",
        "SET FOREIGN_KEY_CHECKS=0;",
        "",
    ]

    cur.execute("SHOW TABLES")
    tables = [r[0] for r in cur.fetchall()]

    for table in tables:
        cur.execute(f"SHOW CREATE TABLE `{table}`")
        create_sql = cur.fetchone()[1]
        lines.append(f"-- Table: {table}")
        lines.append(f"DROP TABLE IF EXISTS `{table}`;")
        lines.append(create_sql + ";")
        lines.append("")

        cur.execute(f"SELECT * FROM `{table}`")
        rows = cur.fetchall()
        if rows:
            col_names = ", ".join(f"`{d[0]}`" for d in cur.description)
            for row in rows:
                vals = ", ".join(escape_val(v) for v in row)
                lines.append(f"INSERT INTO `{table}` ({col_names}) VALUES ({vals});")
            lines.append("")

    lines.append("SET FOREIGN_KEY_CHECKS=1;")
    cur.close()
    conn.close()
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 7:
        print("Usage: backup_mysql.py <host> <port> <user> <password> <database> <output_file>", file=sys.stderr)
        sys.exit(1)
    sql = dump(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    with open(sys.argv[6], "w", encoding="utf-8") as f:
        f.write(sql)
